#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coleta preços e taxas do Tesouro Direto e grava `data/tesouro.json`
(tabela "Tesouro Direto" do site) e `data/tesouro-historico.json`
(série diária, para o gráfico de marcação a mercado).

POR QUE ESTA FONTE, E NÃO O SITE DO TESOURO
-------------------------------------------
O site tesourodireto.com.br expõe um JSON pronto
(`/json/br/com/b3/tesourodireto/service/api/treasurybondsinfo.json`) que seria
o caminho natural. Ele está atrás de **Cloudflare**: funciona no navegador e
devolve `403 Just a moment...` para requisição automatizada desde ago/2024.
GitHub Actions roda em IP de datacenter, que é justamente o perfil barrado.
Não insistir nele sem antes testar de dentro do Actions.

A brapi tem uma API REST boa para isto (`/api/v2/treasury`), mas exige plano
Pro (R$ 139,99/mês); no plano gratuito devolve `403 FEATURE_NOT_AVAILABLE`.

Sobrou a fonte oficial: o CSV do Tesouro Transparente. É o histórico INTEIRO
num arquivo só, então o robô lê em FLUXO (sem guardar o arquivo em disco nem
na memória) e joga fora quase tudo enquanto lê.

FORMATO DO CSV (conferido)
--------------------------
    Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;PU Compra Manha;PU Venda Manha;PU Base Manha
    Tesouro Prefixado;01/01/2012;27/08/2009;11,14;11,20;781,57;...

- separador `;`, decimal com VÍRGULA, datas em dd/mm/aaaa
- as taxas já vêm em PERCENTUAL (11,14 = 11,14% a.a.), diferente do IF.data,
  que entrega fração — mas o robô confere pela mediana assim mesmo
- as linhas NÃO vêm em ordem de data: estão agrupadas por tipo de título.
  Por isso não dá para ler só o fim do arquivo.

DUAS COISAS QUE ESTE CSV **NÃO** RESPONDE
-----------------------------------------
1) "Está à venda hoje?" — não tem resposta aqui, e não por falta de campo.
   O Tesouro SUSPENDE E RETOMA a venda DURANTE o dia (quando os preços
   oscilam muito, a plataforma passa a oferecer só o Selic). O CSV é um
   retrato da manhã, um por dia útil. Uma heurística de "PU de compra
   vazio" foi tentada e marcou 58 de 58 como à venda, o que é falso — o
   IGPM+ 2031, por exemplo, não é emitido há anos. O campo foi REMOVIDO:
   melhor não ter do que ter mentindo em silêncio. A tabela do site se
   apresenta como "preços e taxas de referência".

2) O ANO DO NOME. Em Educa+ e Renda+, o ano que o Tesouro usa no nome
   comercial NÃO é o do vencimento — é a data de CONVERSÃO, quando os
   pagamentos começam:
       Tesouro Educa+ 2026  -> vencimento 15/12/2030  (+4 anos)
       Tesouro Renda+ 2030  -> vencimento 15/12/2049  (+19 anos)
   O Educa+ paga por 5 anos e o Renda+ por 20, e a "Data Vencimento" do
   CSV é o ÚLTIMO pagamento. Sem esse desconto, o site anunciava
   "Renda+ 2049" para o título que o Tesouro vende como "Renda+ 2030" —
   ninguém encontraria o papel que estava procurando.

Uso:
    python coletar_tesouro.py                  # baixa e grava
    python coletar_tesouro.py --dry-run        # mostra o que faria
    python coletar_tesouro.py --arquivo x.csv  # lê um CSV local (teste)
"""

import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

URL_CSV = ("https://www.tesourotransparente.gov.br/ckan/dataset/"
           "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
           "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv")

ARQUIVO = "data/tesouro.json"
ARQUIVO_HISTORICO = "data/tesouro-historico.json"

# Janela de histórico guardada para o gráfico de marcação a mercado. Contada a
# partir de HOJE, não da data-base do arquivo — precisa ser decidida ANTES de
# terminar a leitura, e ler o arquivo duas vezes custaria dois downloads.
DIAS_HISTORICO = 420

CABECALHOS = {
    "User-Agent": "viverderenda.dev.br/1.0 (coletor Tesouro Direto)",
    "Accept": "text/csv, */*",
}
ESPERAS_ENTRE_TENTATIVAS = (0, 5, 20)

DRY_RUN = "--dry-run" in sys.argv


def log(m):
    print(m, flush=True)


def arg(nome):
    """Valor de --opcao valor, ou None."""
    if nome in sys.argv:
        i = sys.argv.index(nome)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


# ----------------------------------------------------------------------
# Leitura
# ----------------------------------------------------------------------

def abrir_fluxo():
    """
    Devolve um arquivo-texto com o CSV. Local quando --arquivo foi passado
    (é assim que o robô é testado sem gastar download), senão a URL oficial.
    """
    local = arg("--arquivo")
    if local:
        log(f"Lendo CSV local: {local}")
        return open(local, encoding="latin-1", newline="")

    ultimo_erro = None
    for tentativa, espera in enumerate(ESPERAS_ENTRE_TENTATIVAS, start=1):
        if espera:
            log(f"    aguardando {espera}s antes de tentar de novo...")
            time.sleep(espera)
        try:
            log(f"Baixando {URL_CSV} (tentativa {tentativa})")
            req = urllib.request.Request(URL_CSV, headers=CABECALHOS)
            resposta = urllib.request.urlopen(req, timeout=120)
            # latin-1 porque o arquivo do Tesouro não é UTF-8 e um acento
            # solto derrubaria a leitura inteira no meio.
            return io.TextIOWrapper(resposta, encoding="latin-1", newline="")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            ultimo_erro = e
            log(f"    falhou: {e}")
    raise SystemExit(f"ERRO: não foi possível baixar o CSV. Último erro: {ultimo_erro}")


def numero(texto):
    """'11,14' -> 11.14 ; vazio -> None."""
    t = (texto or "").strip()
    if not t:
        return None
    try:
        return float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def data_br(texto):
    t = (texto or "").strip()
    try:
        return datetime.strptime(t, "%d/%m/%Y").date()
    except ValueError:
        return None


def normalizar(texto):
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).upper().strip()


# Quantos anos separam o nome comercial do vencimento. Ver docstring: no
# Educa+ e no Renda+ o ano do nome é o da CONVERSÃO (início dos pagamentos),
# e o vencimento é o último pagamento.
DESLOCAMENTO_ANOS = {"educa": 4, "renda": 19}


def familia_de(tipo):
    t = normalizar(tipo)
    if "EDUCA" in t:
        return "educa"
    if "RENDA+" in t or "RENDA +" in t or "APOSENTADORIA" in t:
        return "renda"
    return "comum"


def forma_de_pagamento(tipo):
    """Como o título paga — o site escreve isso embaixo do nome."""
    familia = familia_de(tipo)
    if familia == "educa":
        return "mensal-5anos"
    if familia == "renda":
        return "mensal-20anos"
    if "SEMESTRA" in normalizar(tipo):
        return "semestral"
    return "vencimento"


def indexador_de(tipo):
    """
    Indexador a partir do nome do título. Existe porque a taxa de cada
    indexador SIGNIFICA coisa diferente, e o site precisa saber disso para
    não comparar laranja com banana (ver `rotulo_taxa`).
    """
    t = normalizar(tipo)
    if "SELIC" in t:
        return "selic"
    if "IGPM" in t or "IGP-M" in t:
        return "igpm"
    if "IPCA" in t or "RENDA+" in t or "EDUCA+" in t:
        return "ipca"
    if "PREFIXADO" in t:
        return "prefixado"
    return "outro"


def rotulo_taxa(indexador):
    """
    Como a taxa deve ser LIDA. No Tesouro Selic o número não é a
    rentabilidade: é o spread sobre a Selic, e costuma ficar entre 0% e
    0,30%. Mostrar "0,08%" ao lado de um Prefixado de "14%" faria o Selic
    parecer um péssimo investimento — é o erro mais fácil de cometer aqui.
    """
    return {
        "selic": "spread sobre a Selic",
        "prefixado": "taxa anual contratada",
        "ipca": "taxa real, acima do IPCA",
        "igpm": "taxa real, acima do IGP-M",
    }.get(indexador, "taxa anual")


def identificador(tipo, vencimento):
    """
    Nome curto e estável: 'tesouro-ipca-15052029'. O Tesouro reescreve o
    nome comercial dos papéis de tempos em tempos (com/sem acento, com/sem
    '+'), então a chave é tipo normalizado + data de vencimento.
    """
    base = normalizar(tipo).lower()
    base = base.replace("tesouro ", "").replace("+", "").replace("ipca", "ipca")
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return f"tesouro-{base}-{vencimento.strftime('%d%m%Y')}"


def ler(fluxo):
    """
    Uma passada só pelo arquivo inteiro.

    Guarda apenas as linhas dos últimos DIAS_HISTORICO dias — o resto (quinze
    anos de histórico) é descartado linha a linha, então a memória não cresce
    com o tamanho do arquivo.
    """
    corte = date.today() - timedelta(days=DIAS_HISTORICO)
    leitor = csv.DictReader(fluxo, delimiter=";")

    esperadas = {"Tipo Titulo", "Data Vencimento", "Data Base",
                 "Taxa Compra Manha", "Taxa Venda Manha",
                 "PU Compra Manha", "PU Venda Manha"}
    faltando = esperadas - set(leitor.fieldnames or [])
    if faltando:
        raise SystemExit(
            "ERRO: o CSV mudou de colunas. Faltam: " + ", ".join(sorted(faltando)) +
            "\nColunas recebidas: " + ", ".join(leitor.fieldnames or [])
        )

    series = {}          # id -> {info do título, dias: {data: linha}}
    linhas_lidas = 0
    data_max = None

    for linha in leitor:
        linhas_lidas += 1
        base = data_br(linha.get("Data Base"))
        if base is None or base < corte:
            continue
        vencimento = data_br(linha.get("Data Vencimento"))
        if vencimento is None:
            continue

        tipo = (linha.get("Tipo Titulo") or "").strip()
        ident = identificador(tipo, vencimento)
        registro = series.setdefault(ident, {
            "id": ident, "tipo": tipo, "vencimento": vencimento,
            "indexador": indexador_de(tipo),
            "cupom": "semestral" if "SEMESTRA" in normalizar(tipo) else "zero",
            "dias": {},
        })
        registro["dias"][base] = {
            "taxa_compra": numero(linha.get("Taxa Compra Manha")),
            "taxa_venda": numero(linha.get("Taxa Venda Manha")),
            "pu_compra": numero(linha.get("PU Compra Manha")),
            "pu_venda": numero(linha.get("PU Venda Manha")),
        }
        if data_max is None or base > data_max:
            data_max = base

    log(f"    {linhas_lidas} linhas lidas; {len(series)} títulos na janela de "
        f"{DIAS_HISTORICO} dias")
    return series, data_max


# ----------------------------------------------------------------------
# Conferências
# ----------------------------------------------------------------------

def conferir_escala(valores):
    """
    O CSV entrega taxa em percentual (11,14 = 11,14%), ao contrário do
    IF.data, que entrega fração. Mas confiar na documentação foi exatamente
    o que já publicou "0,15%" no lugar de "15,31%" na tabela de Basileia.
    Então a escala é decidida OLHANDO os números, e não pelo que está escrito.

    A mediana é tirada só dos prefixados: no Selic a taxa é um spread perto
    de zero, e no IPCA+ é uma taxa real — nenhum dos dois serve de régua.
    """
    if not valores:
        return 1.0
    ordenados = sorted(valores)
    mediana = ordenados[len(ordenados) // 2]
    if mediana < 1:
        log(f"    ATENÇÃO: taxas vieram como fração (mediana {mediana:.4f}) — "
            f"multiplicando por 100")
        return 100.0
    return 1.0


def avisar_indexador_desconhecido(titulos):
    """
    O Tesouro cria produto novo de tempos em tempos (Renda+ em 2023,
    Educa+ em 2023, Reserva em 2026). Um tipo que não casa com nenhum
    indexador conhecido cai em "outro" e o site mostraria a taxa SEM o
    indexador na frente, como se fosse rentabilidade cheia — que é
    justamente o erro que a coluna existe para evitar.
    """
    for t in titulos:
        if t["indexador"] == "outro":
            log(f"    ATENÇÃO: '{t['tipo']}' não casou com nenhum indexador "
                f"conhecido. Acrescente a regra em indexador_de().")


def avisar_fora_da_faixa(titulos):
    for t in titulos:
        taxa = t.get("taxa_compra")
        if taxa is None:
            continue
        if t["indexador"] == "selic":
            faixa_ok = -1 <= taxa <= 2      # spread, não rentabilidade
        elif t["indexador"] in ("ipca", "igpm"):
            faixa_ok = 0 <= taxa <= 15      # taxa real
        else:
            faixa_ok = 2 <= taxa <= 30      # taxa nominal
        if not faixa_ok:
            log(f"    ATENÇÃO {t['nome']}: taxa {taxa} fora da faixa plausível "
                f"para {t['indexador']} — conferir.")


# ----------------------------------------------------------------------
# Montagem
# ----------------------------------------------------------------------

def ano_de_referencia(tipo, vencimento):
    """O ano que aparece no nome comercial do título."""
    return vencimento.year - DESLOCAMENTO_ANOS.get(familia_de(tipo), 0)


def nome_comercial(tipo, vencimento):
    """Nome como o Tesouro vende. Ver DESLOCAMENTO_ANOS."""
    return f"{tipo} {ano_de_referencia(tipo, vencimento)}"


def montar(series, data_max):
    # Régua de escala: só prefixados, que têm taxa nominal cheia.
    amostra = []
    for s in series.values():
        if s["indexador"] != "prefixado":
            continue
        dia = s["dias"].get(data_max)
        if dia and dia.get("taxa_compra"):
            amostra.append(dia["taxa_compra"])
    fator = conferir_escala(amostra)

    titulos = []
    for s in sorted(series.values(), key=lambda x: (x["indexador"], x["vencimento"])):
        dia = s["dias"].get(data_max)
        if not dia:
            # Título que não teve linha na data-base mais recente: venceu ou
            # saiu da base. Não entra na tabela.
            continue
        pu_compra = dia.get("pu_compra")
        taxa_compra = dia.get("taxa_compra")
        titulos.append({
            "id": s["id"],
            "nome": nome_comercial(s["tipo"], s["vencimento"]),
            "ano_referencia": ano_de_referencia(s["tipo"], s["vencimento"]),
            "tipo": s["tipo"],
            "familia": familia_de(s["tipo"]),
            "indexador": s["indexador"],
            "rotulo_taxa": rotulo_taxa(s["indexador"]),
            "cupom": s["cupom"],
            "pagamento": forma_de_pagamento(s["tipo"]),
            "vencimento": s["vencimento"].isoformat(),
            "taxa_compra": None if taxa_compra is None else round(taxa_compra * fator, 2),
            "taxa_venda": None if dia.get("taxa_venda") is None else round(dia["taxa_venda"] * fator, 2),
            "pu_compra": pu_compra,
            "pu_venda": dia.get("pu_venda"),
            # 1% do PU é o mínimo de compra no Tesouro Direto.
            "investimento_minimo": None if not pu_compra else round(pu_compra * 0.01, 2),
        })

    avisar_indexador_desconhecido(titulos)
    avisar_fora_da_faixa(titulos)

    historico = {}
    for s in series.values():
        if data_max not in s["dias"]:
            continue
        dias = sorted(s["dias"].items())
        historico[s["id"]] = [
            {"data": d.isoformat(),
             "taxa": None if v.get("taxa_venda") is None else round(v["taxa_venda"] * fator, 2),
             "pu": v.get("pu_venda")}
            for d, v in dias if v.get("pu_venda") is not None
        ]

    return titulos, historico


# ----------------------------------------------------------------------
# Gravação
# ----------------------------------------------------------------------

def gravar(caminho, dados, rotulo):
    """Grava só se o conteúdo mudou; troca atômica para o site nunca ler
    meio arquivo."""
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                atual = json.load(f)
        except (json.JSONDecodeError, OSError):
            atual = None
        if atual is not None:
            copia = dict(atual)
            novo = dict(dados)
            copia.pop("atualizado_em", None)
            novo.pop("atualizado_em", None)
            if copia == novo:
                log(f"Nada mudou em {rotulo} — arquivo mantido como está.")
                return False

    if DRY_RUN:
        log(f"[dry-run] gravaria {caminho}")
        return True

    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporario, caminho)
    log(f"Gravado {caminho}")
    return True


def main():
    fluxo = abrir_fluxo()
    try:
        series, data_max = ler(fluxo)
    finally:
        try:
            fluxo.close()
        except Exception:
            pass

    if not series or data_max is None:
        raise SystemExit("ERRO: nenhuma linha recente no CSV — fonte mudou ou veio vazia.")

    titulos, historico = montar(series, data_max)
    if not titulos:
        raise SystemExit("ERRO: nenhum título na data-base mais recente.")

    from collections import Counter
    por_familia = Counter(t["familia"] for t in titulos)
    log(f"\nData-base {data_max.strftime('%d/%m/%Y')}: {len(titulos)} títulos "
        f"({', '.join(f'{v} {k}' for k, v in sorted(por_familia.items()))})")

    dados = {
        "_leia_me": ("Preços e taxas do Tesouro Direto, usados na tabela do site. "
                     "Fonte: CSV do Tesouro Transparente (histórico completo), lido em "
                     "fluxo pelo robô coletar_tesouro.py. NÃO diz o que está à venda: "
                     "o Tesouro suspende e retoma a venda durante o dia, e este arquivo é "
                     "um retrato da manhã. Em Educa+ e Renda+, 'nome' usa o ano da CONVERSÃO "
                     "(como o Tesouro vende) e 'vencimento' é o último pagamento. "
                     "A taxa significa coisa diferente por indexador — ver 'rotulo_taxa'."),
        "fonte": "Tesouro Transparente — Tesouro Nacional",
        "data_base": data_max.isoformat(),
        "atualizado_em": date.today().isoformat(),
        "titulos": titulos,
    }
    mudou = gravar(ARQUIVO, dados, "tesouro.json")

    dados_hist = {
        "_leia_me": ("Série diária de taxa e PU de venda por título, para o gráfico "
                     "de marcação a mercado. Janela de ~14 meses."),
        "data_base": data_max.isoformat(),
        "atualizado_em": date.today().isoformat(),
        "series": historico,
    }
    mudou = gravar(ARQUIVO_HISTORICO, dados_hist, "tesouro-historico.json") or mudou
    return 0 if mudou or DRY_RUN else 0


if __name__ == "__main__":
    sys.exit(main())
