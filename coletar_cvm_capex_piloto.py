#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PILOTO — não roda agendado, não alimenta o site ainda.

Testa se dá pra extrair CAPEX e Depreciação/Amortização (D&A) de um
pequeno grupo de empresas direto dos dados abertos da CVM, como
alternativa a abrir o PDF de resultados de cada empresa na mão.

POR QUE A CVM, E NÃO O PDF DE CADA EMPRESA
-------------------------------------------
Cada companhia publica seu release com layout e nomenclatura próprios,
sem padrão de URL — não dá pra manter um scraper por empresa pras
~400+ companhias abertas da B3. A CVM exige que toda companhia aberta
entregue as mesmas demonstrações em formato ESTRUTURADO (ITR
trimestral), publicado como CSV aberto em dados.cvm.gov.br — a mesma
fonte usada por ferramentas públicas como o pacote R GetDFPData2.

FORMATO ESPERADO (documentado publicamente; ESTE AMBIENTE NÃO TEM
ACESSO À REDE DO GOV.BR PRA TESTAR AO ESCREVER ISTO — rodar de verdade
no GitHub Actions, via workflow_dispatch, e ajustar pelos logs reais)
-------------------------------------------------------------------
    ZIP:  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_<ANO>.zip
    Dentro, um CSV por demonstração; o que interessa aqui é o de fluxo
    de caixa pelo método indireto, consolidado:
        itr_cia_aberta_DFC_MI_con_<ANO>.csv
    Colunas: CNPJ_CIA, DT_REFER, VERSAO, DENOM_CIA, CD_CONTA, DS_CONTA,
             VL_CONTA, ORDEM_EXERC (ÚLTIMO / PENÚLTIMO)
    Separador ';', encoding ISO-8859-1. Decimal: a documentar ao rodar
    (CSVs da CVM costumam vir com PONTO, diferente do CSV do Tesouro).

CAPEX = soma das contas dentro de "Atividades de Investimento" (grupo
6.02) cuja descrição bate com "aquisição de imobilizado/intangível".
D&A = a conta de depreciação/amortização/exaustão usada como AJUSTE do
resultado em "Atividades Operacionais" (grupo 6.01) — é o lugar mais
padronizado entre empresas de setores diferentes pra achar esse valor
(no DRE, cada empresa apresenta — ou não — de um jeito distinto).

ESCOPO DESTE PILOTO: 3 CNPJs fixos (Petrobras, Vale, PetroReconcavo —
os dois últimos citados pelo usuário), sem agendamento. Por padrão só
IMPRIME o resultado nos logs do Actions, pra conferência manual contra
o PDF de resultados antes de decidir se vale expandir pra toda a B3.
Passe --salvar pra também gravar em data/capex-piloto.json.

USO
---
    python3 coletar_cvm_capex_piloto.py              # só imprime (dry-run)
    python3 coletar_cvm_capex_piloto.py --salvar      # imprime e grava o JSON
    python3 coletar_cvm_capex_piloto.py --ano 2025    # força o ano do ITR
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
from datetime import date

TIMEOUT = 90

# CNPJ -> ticker/nome. Chave normalizada (só dígitos) pra não depender de
# o CSV da CVM vir com ou sem pontuação.
PILOTO = {
    "33000167000101": {"ticker": "PETR4", "nome": "Petrobras"},
    "33592510000154": {"ticker": "VALE3", "nome": "Vale"},
    "03342704000130": {"ticker": "RECV3", "nome": "PetroReconcavo"},
}

RE_CAPEX = re.compile(
    r"aquisi[cç][ãa]o de (ativo )?(imobilizado|intang[ií]vel)", re.IGNORECASE
)
RE_DA = re.compile(
    r"deprecia[cç][ãa]o.{0,25}(amortiza[cç][ãa]o|exaust[ãa]o)", re.IGNORECASE
)


def so_digitos(txt):
    return re.sub(r"\D", "", txt or "")


def sem_acento_maiusculo(txt):
    nfkd = unicodedata.normalize("NFKD", txt or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()


def baixar_zip_itr(ano):
    url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{ano}.zip"
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


def extrair_pilotos(linhas):
    por_cnpj = {}
    exemplos_ordem_exerc = set()
    for linha in linhas:
        cnpj = so_digitos(linha.get("CNPJ_CIA", ""))
        if cnpj not in PILOTO:
            continue
        exemplos_ordem_exerc.add(linha.get("ORDEM_EXERC", ""))
        if sem_acento_maiusculo(linha.get("ORDEM_EXERC", "")) != "ULTIMO":
            continue  # pula a coluna de comparação do ano anterior
        por_cnpj.setdefault(cnpj, []).append(linha)

    print(f"Valores de ORDEM_EXERC vistos pros CNPJs do piloto: {exemplos_ordem_exerc}")

    resultado = {}
    for cnpj, linhas_empresa in por_cnpj.items():
        dt_mais_recente = max(l["DT_REFER"] for l in linhas_empresa)
        candidatas = [l for l in linhas_empresa if l["DT_REFER"] == dt_mais_recente]
        try:
            versao_mais_recente = max(int(l["VERSAO"]) for l in candidatas)
            linhas_finais = [l for l in candidatas if int(l["VERSAO"]) == versao_mais_recente]
        except (KeyError, ValueError):
            linhas_finais = candidatas  # sem coluna VERSAO utilizável — segue com tudo do período

        capex_contas = []
        da_conta = None
        contas_vistas = set()
        for l in linhas_finais:
            desc = l.get("DS_CONTA", "")
            grupo = l.get("CD_CONTA", "")
            valor = numero(l.get("VL_CONTA"))
            contas_vistas.add((grupo, desc))
            if valor is None:
                continue
            if grupo.startswith("6.02") and RE_CAPEX.search(desc):
                capex_contas.append({"conta": grupo, "descricao": desc, "valor": valor})
            elif grupo.startswith("6.01") and RE_DA.search(desc) and da_conta is None:
                da_conta = {"conta": grupo, "descricao": desc, "valor": valor}

        capex_total = sum(abs(c["valor"]) for c in capex_contas) if capex_contas else None

        resultado[cnpj] = {
            "dt_refer": dt_mais_recente,
            "capex_total": round(capex_total, 2) if capex_total is not None else None,
            "capex_contas": capex_contas,
            "depreciacao_amortizacao": round(abs(da_conta["valor"]), 2) if da_conta else None,
            "da_conta": da_conta,
            "qtd_contas_do_periodo": len(contas_vistas),
        }
    return resultado


def main():
    salvar = "--salvar" in sys.argv
    ano = date.today().year
    if "--ano" in sys.argv:
        ano = int(sys.argv[sys.argv.index("--ano") + 1])

    try:
        zf = baixar_zip_itr(ano)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} para {ano} — tentando {ano - 1} "
              f"(início de ano costuma não ter ITR do ano corrente ainda)")
        zf = baixar_zip_itr(ano - 1)

    nomes = zf.namelist()
    print(f"{len(nomes)} arquivos no ZIP. Relevantes:")
    for n in nomes:
        if "DFC_MI" in n.upper():
            print(f"  - {n}")

    candidatos = [n for n in nomes if "DFC_MI_con" in n] or [n for n in nomes if "DFC_MI_ind" in n]
    if not candidatos:
        print("::error::Não achei nenhum arquivo DFC_MI no ZIP. Lista completa:")
        for n in nomes:
            print(f"  - {n}")
        sys.exit(1)

    arquivo = candidatos[0]
    linhas = ler_csv_do_zip(zf, arquivo)
    print(f"{len(linhas)} linhas lidas de {arquivo}")
    if linhas:
        print(f"Colunas disponíveis: {list(linhas[0].keys())}")

    resultado = extrair_pilotos(linhas)

    saida = {}
    for cnpj, info in PILOTO.items():
        dados = resultado.get(cnpj)
        if dados is None:
            print(f"AVISO: nenhuma linha encontrada para {info['ticker']} ({info['nome']}, CNPJ {cnpj})")
            continue
        saida[info["ticker"]] = {"empresa": info["nome"], "cnpj": cnpj, **dados}

    print("\n=== RESULTADO (conferir contra o release em PDF antes de confiar) ===")
    print(json.dumps(saida, ensure_ascii=False, indent=2))

    if salvar:
        with open("data/capex-piloto.json", "w", encoding="utf-8") as f:
            json.dump(saida, f, ensure_ascii=False, indent=2)
        print("\nGravado em data/capex-piloto.json")


if __name__ == "__main__":
    main()
