#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coleta CAPEX e Depreciação/Amortização (D&A) de TODAS as ações
rastreadas pelo site, direto dos dados abertos da CVM, e grava
`data/capex-da.json` (alimenta a Análise Fundamentalista).

POR QUE A CVM, E NÃO O PDF DE CADA EMPRESA
-------------------------------------------
Cada companhia publica seu release com layout e nomenclatura próprios,
sem padrão de URL — não dá pra manter um scraper por empresa pras
~400+ companhias abertas da B3. A CVM exige que toda companhia aberta
entregue as mesmas demonstrações em formato ESTRUTURADO (ITR
trimestral e DFP anual), publicado como CSV aberto em dados.cvm.gov.br
— a mesma fonte usada por ferramentas públicas como o pacote R
GetDFPData2.

VALIDADO num piloto (coletar_cvm_capex_piloto.py, histórico do
commit) com PETR4/VALE3/RECV3: a Depreciação/Amortização bateu exato
com o release da RECV3 (333.214, mesma unidade), e o CAPEX bateu com
0,3% de diferença (explicável pela empresa separar "Investimentos em
Midstream" à parte no detalhamento voluntário do próprio PDF).

ONDE ENTRA O CÓDIGO CVM DE CADA AÇÃO
-------------------------------------
`mercado-acoes.json` (escrito por coletar_mercado.py) já traz
`codigo_cvm` pra cada ticker que o site rastreia — é exatamente a
coluna CD_CVM dos arquivos da CVM. Não precisa de uma tabela CNPJ↔ticker
separada: este robô lê esse arquivo pra saber QUAIS empresas buscar.

FORMATO DOS DADOS DA CVM (confirmado rodando de verdade, ver histórico
do piloto)
-------------------------------------------------------------------
    ZIP:  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{ITR,DFP}/DADOS/{itr,dfp}_cia_aberta_<ANO>.zip
    Dentro, um CSV de fluxo de caixa por método (consolidado E
    individual — uma empresa aparece em UM dos dois, não nos dois):
        {itr,dfp}_cia_aberta_DFC_MI_con_<ANO>.csv
        {itr,dfp}_cia_aberta_DFC_MI_ind_<ANO>.csv
    Colunas: CNPJ_CIA, DT_REFER, VERSAO, DENOM_CIA, CD_CVM, CD_CONTA,
             DS_CONTA, VL_CONTA, ESCALA_MOEDA, ORDEM_EXERC.
    Separador ';', encoding ISO-8859-1, decimal com PONTO.
    O valor do DFC é sempre ACUMULADO NO ANO (YTD) até DT_REFER — nunca
    isolado por trimestre, por convenção contábil brasileira.

CAPEX = soma das contas em "Atividades de Investimento" (grupo 6.02)
cuja descrição bate com "adições/aquisições de imobilizado/intangível"
— o verbo e o plural variam por empresa (testado: RECV3 "Adições ao
imobilizado e intangível", PETR4 "Aquisições de ativos imobilizados e
intangíveis", VALE3 "Adições ao Imobilizado").
D&A = a conta de depreciação/amortização/exaustão usada como AJUSTE do
resultado em "Atividades Operacionais" (grupo 6.01) — mais padronizada
entre setores que a apresentação (às vezes inexistente) no DRE.

O QUE ESTE ROBÔ NÃO FAZ (de propósito, v1)
-------------------------------------------
- Não isola o valor por trimestre (mostra acumulado no ano, com a data
  de referência deixando isso explícito pro leitor).
- Não cobre FIIs/Fiagro/FI-Infra (CAPEX não é um conceito usual pra
  fundo; o pedido original era sobre ações).
- Cobre só os CD_CVM que já aparecem em mercado-acoes.json — uma ação
  nova no site começa a aparecer aqui assim que aparecer lá.

USO
---
    python3 coletar_cvm_capex.py              # grava data/capex-da.json
    python3 coletar_cvm_capex.py --dry-run    # só imprime, não grava
"""

import csv
import io
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timezone

TIMEOUT = 120
MERCADO_ACOES_FILE = "mercado-acoes.json"
SAIDA_FILE = "data/capex-da.json"

RE_CAPEX = re.compile(
    r"(adi[cç][ãa]o|adi[cç][õo]es|aquisi[cç][ãa]o|aquisi[cç][õo]es)"
    r".{0,15}(imobilizad[oa]s?|intang[ií]ve(l|is))",
    re.IGNORECASE,
)
RE_DA = re.compile(
    r"deprecia[cç][ãa]o.{0,25}(amortiza[cç][ãa]o|exaust[ãa]o)", re.IGNORECASE
)

ESCALA_MULTIPLICADOR = {
    "MIL": 1_000,
    "MILHAO": 1_000_000,
    "MILHÃO": 1_000_000,
    "UNIDADE": 1,
}


def sem_acento_maiusculo(txt):
    nfkd = unicodedata.normalize("NFKD", txt or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()


def normalizar_cod_cvm(valor):
    """A CVM grava CD_CVM com zero à esquerda ('009512' pra Petrobras);
    mercado-acoes.json grava como int sem padding (9512). Normaliza os
    dois lados removendo o padding, via int(), em vez de comparar string
    crua — foi exatamente essa diferença que zerou a primeira execução
    em escala real (0 de 299 ações, apesar dos arquivos da CVM baixarem
    e lerem certinho)."""
    try:
        return str(int(str(valor).strip()))
    except (TypeError, ValueError):
        return ""


def carregar_codigos_cvm_alvo():
    """{codigo_cvm (str, sem zero à esquerda) -> [tickers]}, a partir dos
    ativos que o site já rastreia. Uma empresa pode ter mais de uma
    classe de ação com o MESMO código CVM (ex: PETR3/PETR4, código
    9512) — por isso é lista, não um ticker só; um dict simples
    sobrescreveria um ticker pelo outro e um dos dois nunca receberia
    dado nenhum."""
    with open(MERCADO_ACOES_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    alvo = {}
    for ativo in dados.get("ativos", []):
        cod = normalizar_cod_cvm(ativo.get("codigo_cvm"))
        if cod:
            alvo.setdefault(cod, []).append(ativo["ticker"])
    return alvo


def baixar_zip(tipo, ano):
    url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{tipo}/DADOS/{tipo.lower()}_cia_aberta_{ano}.zip"
    print(f"Baixando {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return zipfile.ZipFile(io.BytesIO(resp.read()))


def ler_csv_do_zip(zf, nome_arquivo):
    with zf.open(nome_arquivo) as f:
        texto = io.TextIOWrapper(f, encoding="ISO-8859-1", newline="")
        return list(csv.DictReader(texto, delimiter=";"))


def numero(valor_str):
    if not valor_str:
        return None
    limpo = valor_str.strip()
    try:
        return float(limpo)
    except ValueError:
        pass
    try:
        return float(limpo.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def processar_arquivo(zf, nome_arquivo, codigos_alvo, acumulado):
    """Lê um CSV DFC_MI e atualiza `acumulado` ({cd_cvm: {...}}) — só
    substitui uma empresa já presente se este arquivo trouxer uma
    dt_refer mais recente (permite combinar ITR + DFP, ano atual +
    anterior, sem favorecer ordem de leitura)."""
    linhas = ler_csv_do_zip(zf, nome_arquivo)
    print(f"  {len(linhas)} linhas em {nome_arquivo}")

    por_cvm = {}
    for linha in linhas:
        cod = normalizar_cod_cvm(linha.get("CD_CVM", ""))
        if cod not in codigos_alvo:
            continue
        if sem_acento_maiusculo(linha.get("ORDEM_EXERC", "")) != "ULTIMO":
            continue  # pula a coluna de comparação do período anterior
        por_cvm.setdefault(cod, []).append(linha)

    for cod, linhas_empresa in por_cvm.items():
        dt_mais_recente = max(l["DT_REFER"] for l in linhas_empresa)
        candidatas = [l for l in linhas_empresa if l["DT_REFER"] == dt_mais_recente]
        try:
            versao_mais_recente = max(int(l["VERSAO"]) for l in candidatas)
            linhas_finais = [l for l in candidatas if int(l["VERSAO"]) == versao_mais_recente]
        except (KeyError, ValueError):
            linhas_finais = candidatas

        # Já temos uma dt_refer mais nova (ou igual) de outro arquivo —
        # não vale a pena sobrescrever.
        existente = acumulado.get(cod)
        if existente and existente["dt_refer"] >= dt_mais_recente:
            continue

        escala = (linhas_finais[0].get("ESCALA_MOEDA") or "UNIDADE").strip().upper()
        multiplicador = ESCALA_MULTIPLICADOR.get(escala, 1)

        capex_total = 0.0
        tem_capex = False
        da_valor = None
        for l in linhas_finais:
            desc = l.get("DS_CONTA", "")
            grupo = l.get("CD_CONTA", "")
            valor = numero(l.get("VL_CONTA"))
            if valor is None:
                continue
            if grupo.startswith("6.02") and RE_CAPEX.search(desc):
                capex_total += abs(valor) * multiplicador
                tem_capex = True
            elif grupo.startswith("6.01") and RE_DA.search(desc) and da_valor is None:
                da_valor = abs(valor) * multiplicador

        acumulado[cod] = {
            "dt_refer": dt_mais_recente,
            "capex": round(capex_total, 2) if tem_capex else None,
            "depreciacao_amortizacao": round(da_valor, 2) if da_valor is not None else None,
        }


def coletar_ano_tipo(tipo, ano, codigos_alvo, acumulado):
    try:
        zf = baixar_zip(tipo, ano)
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code} para {tipo} {ano} — pulando (pode não existir ainda, ou já não existir mais)")
        return
    except urllib.error.URLError as exc:
        print(f"  Falha de rede pra {tipo} {ano}: {exc} — pulando")
        return

    nomes = zf.namelist()
    candidatos = [n for n in nomes if "DFC_MI_con" in n] + [n for n in nomes if "DFC_MI_ind" in n]
    if not candidatos:
        print(f"  Nenhum arquivo DFC_MI em {tipo} {ano}. Arquivos: {nomes}")
        return
    for nome in candidatos:
        processar_arquivo(zf, nome, codigos_alvo, acumulado)


def main():
    dry_run = "--dry-run" in sys.argv
    codigos_alvo = carregar_codigos_cvm_alvo()
    print(f"{len(codigos_alvo)} ações com código CVM mapeado em {MERCADO_ACOES_FILE}")

    ano_atual = date.today().year
    acumulado = {}  # cd_cvm -> {dt_refer, capex, depreciacao_amortizacao}

    # ITR cobre a maior parte do ano (Q1/Q2/Q3); DFP cobre o ano fechado
    # (equivale ao Q4). Lê os dois, dos dois anos mais recentes, e fica
    # com a dt_refer mais nova por empresa — cobre tanto quem já reportou
    # o trimestre mais recente quanto quem só tem o ano passado fechado.
    for tipo in ("ITR", "DFP"):
        for ano in (ano_atual, ano_atual - 1):
            print(f"\n=== {tipo} {ano} ===")
            coletar_ano_tipo(tipo, ano, codigos_alvo, acumulado)

    saida_ativos = {}
    for cod, tickers in codigos_alvo.items():
        dados = acumulado.get(cod)
        if dados and (dados["capex"] is not None or dados["depreciacao_amortizacao"] is not None):
            for ticker in tickers:
                saida_ativos[ticker] = dados

    print(f"\n{len(saida_ativos)} de {len(codigos_alvo)} ações com CAPEX e/ou D&A extraídos.")

    saida = {
        "_leia_me": (
            "CAPEX e Depreciação/Amortização (D&A) de ações, direto dos dados abertos "
            "da CVM (ITR/DFP) — não da HG Brasil, que não tem esses campos. Valores em "
            "R$, ACUMULADOS NO ANO até 'dt_refer' de cada empresa (convenção do fluxo de "
            "caixa brasileiro — nunca isolado por trimestre). Ver coletar_cvm_capex.py."
        ),
        "fonte": "CVM - Dados Abertos (dados.cvm.gov.br), ITR/DFP",
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "ativos": saida_ativos,
    }

    print(json.dumps({k: saida_ativos[k] for k in list(saida_ativos)[:5]}, ensure_ascii=False, indent=2))

    if not dry_run:
        with open(SAIDA_FILE, "w", encoding="utf-8") as f:
            json.dump(saida, f, ensure_ascii=False, indent=2)
        print(f"\nGravado em {SAIDA_FILE}")


if __name__ == "__main__":
    main()
