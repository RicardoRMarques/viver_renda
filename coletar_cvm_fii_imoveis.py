#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletar_cvm_fii_imoveis.py
---------------------------------------------------------------------
Vacância e inadimplência dos FIIs de tijolo, direto dos dados abertos
trimestrais da CVM, com um resumo em linguagem simples gerado por IA
(o mesmo Worker que já atende o Assistente de IA do site). Grava
data/fii-imoveis.json, consumido pela Consulta Rápida (ver
renderizarFundamentosFII no index.html — é exatamente onde o site hoje
avisa "sem dados de... vacância" pra FII).

Por que a CVM, e não o Fnet/B3
-------------------------------
O pedido original também incluía inquilinos, relatórios gerenciais e
fatos relevantes "via Fnet/B3". Não dá: o fnet.bmfbovespa.com.br tem
Cloudflare bloqueando acesso automatizado (até a URL direta com CNPJ
devolve 403 "Attention Required") e CAPTCHA no formulário de busca —
confirmado tentando de verdade em 21/11/2026, sem tentar contornar
nenhum dos dois.

A CVM, por outro lado, publica a MESMA informação de vacância e
inadimplência (por imóvel, não só por fundo) como dado aberto
estruturado, sem captcha nem bloqueio — o Informe Trimestral de FIIs,
arquivo "imovel" (mesmo portal e mesmo padrão já usado por
coletar_cvm_capex.py pra CAPEX/D&A de ações):

    ZIP:  https://dados.cvm.gov.br/dados/FII/DOC/INF_TRIMESTRAL/DADOS/
          inf_trimestral_fii_<ANO>.zip
    CSV:  inf_trimestral_fii_imovel_<ANO>.csv
    Colunas usadas: CNPJ_Fundo_Classe, Data_Referencia, Classe,
        Nome_Imovel, Endereco, Area, Percentual_Vacancia,
        Percentual_Inadimplencia, Percentual_Receitas_FII.
    Separador ';', encoding ISO-8859-1, decimal com PONTO (diferente do
    CZ-down.asp da ANBIMA, que usa vírgula — confirmado lendo o
    arquivo de verdade).

O mesmo ZIP também tem um arquivo "imovel_renda_acabado_inquilino" com
o SETOR de atuação de cada inquilino por imóvel — mas na prática a
maioria dos fundos preenche com valores genéricos tipo "Setor1",
"SetorN" ou "-" (confirmado olhando as linhas de verdade), sem nome de
inquilino nenhum. Não virou feature por não ter dado de verdade por
trás; ver conversa que motivou este robô.

Só entra na conta a Classe "Imóveis para renda acabados" — vacância e
inadimplência não fazem sentido pras outras classes do mesmo arquivo
(terrenos, imóveis em construção, imóveis pra venda), que usam outras
colunas (% de obra concluída, etc.).

Resumo por IA
-------------
Reaproveita o MESMO Cloudflare Worker que atende o Assistente de IA do
site (CHAT_IA_ENDPOINT no index.html) — é um proxy público já no ar,
sem chave de API exposta aqui. Chama 1x por fundo, só quando o
trimestre de referência OU os números de vacância/inadimplência mudam
desde a última execução (evita gastar a cota gratuita do Worker
toda vez que o robô roda sem ter dado novo pra resumir).

Uso
---
    python3 coletar_cvm_fii_imoveis.py              # grava data/fii-imoveis.json
    python3 coletar_cvm_fii_imoveis.py --dry-run     # só imprime, não grava
    python3 coletar_cvm_fii_imoveis.py --sem-ia       # pula a chamada de IA (teste rápido)
---------------------------------------------------------------------
"""

import csv
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timezone

TIMEOUT = 120
MERCADO_FIIS_FILE = "mercado-fiis.json"
SAIDA_FILE = "data/fii-imoveis.json"

CLASSE_ALVO = "Imóveis para renda acabados"

CHAT_IA_ENDPOINT = "https://viverderenda-chat.marquesr.workers.dev"
TIMEOUT_IA = 30


def normalizar_cnpj(valor):
    """Só os dígitos — mercado-fiis.json grava '44625826000111', a CVM
    grava '00.332.266/0001-31'."""
    return re.sub(r"\D", "", valor or "")


def carregar_fiis_do_site():
    """{cnpj (só dígitos) -> {"ticker": ..., "nome": ...}} a partir dos
    FIIs que o site já rastreia (mercado-fiis.json, gerado por
    coletar_mercado.py) — o nome do FUNDO vem daqui, não do CSV da CVM
    (que só tem o nome de cada IMÓVEL, não do fundo)."""
    with open(MERCADO_FIIS_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    alvo = {}
    for ativo in dados.get("ativos", []):
        cnpj = normalizar_cnpj(ativo.get("cnpj"))
        if cnpj:
            alvo[cnpj] = {"ticker": ativo["ticker"], "nome": ativo.get("nome") or ativo["ticker"]}
    return alvo


def baixar_zip_imoveis(ano):
    url = f"https://dados.cvm.gov.br/dados/FII/DOC/INF_TRIMESTRAL/DADOS/inf_trimestral_fii_{ano}.zip"
    print(f"Baixando {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return zipfile.ZipFile(io.BytesIO(resp.read()))


def numero(valor_str):
    """Decimal com PONTO neste arquivo da CVM (diferente do padrão
    brasileiro usado noutros robôs — confirmado lendo o CSV de
    verdade). Vazio ou '-' quer dizer "não se aplica"."""
    texto = (valor_str or "").strip()
    if not texto or texto == "-":
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def ler_linhas_imovel(zf, ano):
    nome_arquivo = f"inf_trimestral_fii_imovel_{ano}.csv"
    if nome_arquivo not in zf.namelist():
        print(f"  {nome_arquivo} não está no ZIP de {ano}.")
        return []
    with zf.open(nome_arquivo) as f:
        texto = io.TextIOWrapper(f, encoding="ISO-8859-1", newline="")
        linhas = list(csv.DictReader(texto, delimiter=";"))
    print(f"  {len(linhas)} linhas em {nome_arquivo}")
    return linhas


def coletar_por_fundo(cnpjs_alvo):
    """Baixa o ano atual e o anterior, filtra pelos CNPJs que o site
    rastreia e pela classe "renda acabados", e devolve
    {cnpj: [linha, linha, ...]} só com a Data_Referencia MAIS RECENTE
    disponível por fundo (o ano atual pode não ter a última versão de
    um fundo que ainda não entregou — por isso olha os dois anos antes
    de decidir qual data é a mais nova de cada um)."""
    por_cnpj = {}  # cnpj -> {data_referencia: [linha, ...]}
    ano_atual = date.today().year

    for ano in (ano_atual, ano_atual - 1):
        try:
            zf = baixar_zip_imoveis(ano)
        except urllib.error.HTTPError as exc:
            print(f"  HTTP {exc.code} pro ZIP de {ano} — pulando (pode não existir ainda)")
            continue
        except urllib.error.URLError as exc:
            print(f"  Falha de rede pro ZIP de {ano}: {exc} — pulando")
            continue

        for linha in ler_linhas_imovel(zf, ano):
            if linha.get("Classe") != CLASSE_ALVO:
                continue
            cnpj = normalizar_cnpj(linha.get("CNPJ_Fundo_Classe"))
            if cnpj not in cnpjs_alvo:
                continue
            if numero(linha.get("Percentual_Vacancia")) is None:
                continue  # sem vacância preenchida, não serve pra média
            data_ref = linha.get("Data_Referencia", "")
            por_cnpj.setdefault(cnpj, {}).setdefault(data_ref, []).append(linha)

    resultado = {}
    for cnpj, por_data in por_cnpj.items():
        data_mais_recente = max(por_data.keys())
        resultado[cnpj] = (data_mais_recente, por_data[data_mais_recente])
    return resultado


def agregar_fundo(linhas):
    """De uma lista de imóveis (linhas do CSV) de UM fundo, num trimestre
    só, calcula a vacância/inadimplência média ponderada pela
    participação de cada imóvel na receita do fundo — um imóvel que
    responde por 40% da receita pesa mais que um que responde por 2%.
    Cai pra média simples se nenhum imóvel tiver o peso preenchido."""
    imoveis = []
    peso_total = 0.0
    vacancia_ponderada = 0.0
    inadimplencia_ponderada = 0.0
    tem_inadimplencia = False

    for linha in linhas:
        vac = numero(linha.get("Percentual_Vacancia"))
        inad = numero(linha.get("Percentual_Inadimplencia"))
        peso = numero(linha.get("Percentual_Receitas_FII")) or 0.0

        imoveis.append({
            "nome": (linha.get("Nome_Imovel") or "").strip() or None,
            "endereco": (linha.get("Endereco") or "").strip() or None,
            "area_m2": numero(linha.get("Area")),
            "vacancia_pct": round(vac * 100, 2) if vac is not None else None,
            "inadimplencia_pct": round(inad * 100, 2) if inad is not None else None,
            "participacao_receita_pct": round(peso * 100, 2),
        })

        vacancia_ponderada += vac * peso
        peso_total += peso
        if inad is not None:
            inadimplencia_ponderada += inad * peso
            tem_inadimplencia = True

    imoveis.sort(key=lambda i: i["participacao_receita_pct"] or 0, reverse=True)

    if peso_total > 0:
        vacancia_pct = vacancia_ponderada / peso_total * 100
        inadimplencia_pct = inadimplencia_ponderada / peso_total * 100 if tem_inadimplencia else None
    else:
        # Nenhum imóvel com participação de receita preenchida: cai pra
        # média simples, melhor que não mostrar nada.
        vacs = [numero(l.get("Percentual_Vacancia")) for l in linhas]
        vacs = [v for v in vacs if v is not None]
        vacancia_pct = (sum(vacs) / len(vacs) * 100) if vacs else None
        inads = [numero(l.get("Percentual_Inadimplencia")) for l in linhas]
        inads = [v for v in inads if v is not None]
        inadimplencia_pct = (sum(inads) / len(inads) * 100) if inads else None

    return {
        "n_imoveis": len(imoveis),
        "vacancia_pct": round(vacancia_pct, 2) if vacancia_pct is not None else None,
        "inadimplencia_pct": round(inadimplencia_pct, 2) if inadimplencia_pct is not None else None,
        "imoveis": imoveis,
    }


def gerar_resumo_ia(ticker, nome_fundo, data_referencia, agregado):
    """Pede pro Worker (mesma IA do Assistente do site) escrever 2-3
    frases em português simples sobre a vacância/inadimplência do
    fundo. None se a chamada falhar — o site mostra os números mesmo
    sem o resumo, nunca trava por causa disso."""
    imoveis_txt = "; ".join(
        f"{i['nome'] or 'imóvel sem nome'} ({i['participacao_receita_pct']}% da receita, "
        f"vacância {i['vacancia_pct']}%)"
        for i in agregado["imoveis"][:5]
        if i["vacancia_pct"] is not None
    )
    prompt = (
        f"Resuma em português, em no máximo 3 frases curtas e sem jargão, a situação de "
        f"vacância e inadimplência do Fundo Imobiliário {ticker} ({nome_fundo}) para um "
        f"investidor não especialista. NÃO dê recomendação de compra ou venda, só descreva "
        f"os números abaixo.\n\n"
        f"IMPORTANTE — não confunda os dois conceitos:\n"
        f"- Vacância = percentual da área do(s) imóvel(is) que está DESOCUPADA, sem "
        f"inquilino nenhum pagando aluguel por ela.\n"
        f"- Inadimplência = percentual dos aluguéis COBRADOS que não foram pagos por "
        f"inquilinos que ESTÃO ocupando o imóvel. São coisas diferentes: um imóvel pode "
        f"ter vacância baixa e inadimplência alta (ocupado, mas inquilino não paga), ou o "
        f"contrário.\n\n"
        f"Dados do trimestre encerrado em {data_referencia}:\n"
        f"- Vacância média do fundo (ponderada pela receita de cada imóvel): "
        f"{agregado['vacancia_pct']}%\n"
        f"- Inadimplência média: {agregado['inadimplencia_pct']}%\n"
        f"- Número de imóveis considerados: {agregado['n_imoveis']}\n"
        f"- Principais imóveis: {imoveis_txt or 'sem detalhamento disponível'}"
    )
    payload = json.dumps({"messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_IA_ENDPOINT,
        data=payload,
        # Sem um User-Agent de navegador, o Cloudflare do Worker barra com
        # "error code: 1010" (bloqueio por assinatura do cliente) — mesmo
        # ajuste que os outros robôs do site já fazem pra URLs atrás de
        # proteção (ver CABECALHOS em coletar_curva_juros.py).
        headers={
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_IA) as resp:
            corpo = json.loads(resp.read().decode("utf-8"))
        resumo = (corpo or {}).get("reply", "").strip()
        return resumo or None
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  AVISO: resumo por IA falhou pra {ticker}: {exc}")
        return None


def carregar_saida_anterior():
    try:
        with open(SAIDA_FILE, encoding="utf-8") as f:
            return (json.load(f) or {}).get("fundos", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    dry_run = "--dry-run" in sys.argv
    sem_ia = "--sem-ia" in sys.argv

    cnpj_para_ticker = carregar_fiis_do_site()
    print(f"{len(cnpj_para_ticker)} FIIs com CNPJ mapeado em {MERCADO_FIIS_FILE}")

    por_cnpj = coletar_por_fundo(set(cnpj_para_ticker.keys()))
    print(f"\n{len(por_cnpj)} fundos com vacância preenchida na CVM (de {len(cnpj_para_ticker)} rastreados).")

    fundos_anteriores = carregar_saida_anterior()

    fundos = {}
    chamadas_ia = 0
    for cnpj, (data_referencia, linhas) in por_cnpj.items():
        ticker = cnpj_para_ticker[cnpj]["ticker"]
        nome_fundo = cnpj_para_ticker[cnpj]["nome"]
        agregado = agregar_fundo(linhas)

        anterior = fundos_anteriores.get(ticker)
        mudou = (
            not anterior
            or anterior.get("data_referencia") != data_referencia
            or anterior.get("vacancia_pct") != agregado["vacancia_pct"]
            or anterior.get("inadimplencia_pct") != agregado["inadimplencia_pct"]
        )

        if sem_ia:
            resumo_ia = (anterior or {}).get("resumo_ia")
        elif mudou:
            resumo_ia = gerar_resumo_ia(ticker, nome_fundo, data_referencia, agregado)
            chamadas_ia += 1
        else:
            resumo_ia = (anterior or {}).get("resumo_ia")

        fundos[ticker] = {
            "data_referencia": data_referencia,
            **agregado,
            "resumo_ia": resumo_ia,
        }

    print(f"\n{chamadas_ia} chamada(s) de IA feitas (só fundos com dado novo).")
    print(json.dumps({k: fundos[k] for k in list(fundos)[:3]}, ensure_ascii=False, indent=2))

    saida = {
        "_leia_me": (
            "Vacância e inadimplência de FIIs de tijolo, por imóvel e agregado por fundo "
            "(média ponderada pela participação de cada imóvel na receita do fundo), direto "
            "do Informe Trimestral de FIIs da CVM (dados abertos, não scraping) — ver "
            "coletar_cvm_fii_imoveis.py. 'resumo_ia' é um texto curto gerado pelo mesmo "
            "Worker de IA que atende o Assistente do site, descrevendo os números em "
            "linguagem simples; null quando a chamada falhou ou ainda não rodou pra esse "
            "fundo. Só fundos com propriedades 'Imóveis para renda acabados' aparecem aqui "
            "— fundos de papel/recebíveis (sem imóvel físico) não têm esse dado na CVM, por "
            "isso não entram neste arquivo. Consulte 'fatos relevantes' e 'relatórios "
            "gerenciais' direto no Fundos.NET (fnet.bmfbovespa.com.br) — não automatizado "
            "aqui (Cloudflare + CAPTCHA na fonte)."
        ),
        "fonte": "CVM — Dados Abertos, Informe Trimestral de FIIs (inf_trimestral_fii_imovel)",
        "fonte_url": "https://dados.cvm.gov.br/dados/FII/DOC/INF_TRIMESTRAL/DADOS/",
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fundos": fundos,
    }

    if not dry_run:
        import os
        os.makedirs("data", exist_ok=True)
        with open(SAIDA_FILE, "w", encoding="utf-8") as f:
            json.dump(saida, f, ensure_ascii=False, indent=1)
        print(f"\nGravado em {SAIDA_FILE}")


if __name__ == "__main__":
    main()
