#!/usr/bin/env python3
"""
Fase 4 — ÚLTIMA investigação. Depois desta eu escrevo o robô definitivo.

O CAMINHO COMPLETO, JÁ DESCOBERTO
----------------------------------
    catálogo:  GET /ifdata/rest/relatorios2025a2030
    arquivo:   GET /ifdata/rest/arquivos?nomeArquivo=<f>

O parâmetro é 'nomeArquivo' — veio do JavaScript embutido na página:

    $.ajax({ url: urlArquivos + "?nomeArquivo=" + dadosFile[i].f,
             type: "GET", dataType: "json" })

Repare que a página concatena o caminho CRU, sem codificar. Por isso a
barra dupla de "ifdata_2025_2030//202603/..." vai literal — este script
faz igual, pra não inventar diferença onde o site não faz.

Data-base mais recente: 202603 (1º tri/2026).
Tipo de instituição: 1009 = Conglomerados Prudenciais (o que os bancos
divulgam). 1005 = Financeiros, 1006 = Individuais.

O QUE FALTA — e é só isto
--------------------------
Saber onde, dentro dos arquivos, estão (a) a razão social por código e
(b) o Índice de Basileia. Há cinco arquivos 'dados' e não se sabe qual
traz o quê. Este script baixa todos, imprime a forma de cada um e
PROCURA a palavra "Basileia", dizendo em que caminho do JSON ela aparece.

Não grava nada.

    python3 descobrir_ifdata_web.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("IFDATA_BASE", "https://www3.bcb.gov.br/ifdata/")
CATALOGO = BASE + "rest/relatorios2025a2030"
ARQUIVOS = BASE + "rest/arquivos?nomeArquivo="
TEMPO_LIMITE = 90
CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE,
    "X-Requested-With": "XMLHttpRequest",
}


def log(m):
    print(m, flush=True)


def buscar(url):
    req = urllib.request.Request(url, headers=CABECALHOS)
    try:
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as r:
            bruto = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(bruto), len(bruto)
            except Exception:  # noqa: BLE001
                return r.status, None, len(bruto)
    except urllib.error.HTTPError as e:
        return e.code, None, 0
    except Exception as e:  # noqa: BLE001
        log(f"        erro: {e}")
        return None, None, 0


def arquivo(f):
    # sem codificar, igual à página
    return buscar(ARQUIVOS + f)


def forma(valor, prefixo="", nivel=0):
    """Descreve a estrutura sem despejar o conteúdo inteiro."""
    tab = "    " + "  " * nivel
    if isinstance(valor, list):
        log(f"{tab}{prefixo}lista[{len(valor)}]")
        if valor:
            amostra = json.dumps(valor[0], ensure_ascii=False)
            log(f"{tab}  1º item: {amostra[:400]}")
            if len(valor) > 1:
                log(f"{tab}  2º item: {json.dumps(valor[1], ensure_ascii=False)[:200]}")
    elif isinstance(valor, dict):
        log(f"{tab}{prefixo}objeto{list(valor.keys())[:15]}")
        if nivel < 2:
            for chave, dentro in list(valor.items())[:6]:
                if isinstance(dentro, (list, dict)):
                    forma(dentro, f"'{chave}': ", nivel + 1)
                else:
                    log(f"{tab}  '{chave}': {str(dentro)[:100]}")
    else:
        log(f"{tab}{prefixo}{str(valor)[:120]}")


def caçar(valor, alvo, caminho="raiz", achados=None, limite=6):
    """Onde, na árvore do JSON, aparece o texto procurado."""
    if achados is None:
        achados = []
    if len(achados) >= limite:
        return achados
    if isinstance(valor, str):
        if alvo.lower() in valor.lower():
            achados.append((caminho, valor[:150]))
    elif isinstance(valor, list):
        for i, item in enumerate(valor[:400]):
            caçar(item, alvo, f"{caminho}[{i}]", achados, limite)
    elif isinstance(valor, dict):
        for chave, dentro in valor.items():
            caçar(dentro, alvo, f"{caminho}.{chave}", achados, limite)
    return achados


def main():
    status, catalogo, _ = buscar(CATALOGO)
    log(f"catálogo: [{status}]")
    if not isinstance(catalogo, list) or not catalogo:
        return 1

    catalogo.sort(key=lambda b: int(b.get("dt", 0)), reverse=True)
    bloco = catalogo[0]
    dt = bloco.get("dt")
    arquivos = [x.get("f") for x in bloco.get("files", []) if x.get("f")]
    log(f"data-base mais recente: {dt}\n")

    # --- os relatórios disponíveis (trel = tipo de relatório) ---
    log("=" * 70)
    log("RELATÓRIOS DISPONÍVEIS (id -> nome)")
    log("=" * 70)
    for f in [a for a in arquivos if "/trel" in a]:
        st, dados, _ = arquivo(f)
        nome = "?"
        if isinstance(dados, dict):
            nome = dados.get("n") or dados.get("nome") or "?"
        elif isinstance(dados, list) and dados and isinstance(dados[0], dict):
            nome = dados[0].get("n", "?")
        log(f"  [{st}] {f.split('/')[-1]:28s} -> {nome}")

    # --- cadastro do tipo 1009 (Conglomerados Prudenciais) ---
    log("\n" + "=" * 70)
    log("CADASTRO (razão social por código)")
    log("=" * 70)
    for f in [a for a in arquivos if "/cadastro" in a]:
        st, dados, tamanho = arquivo(f)
        log(f"\n  {f.split('/')[-1]}  [{st}]  {tamanho} bytes")
        if dados is not None:
            forma(dados)

    # --- os arquivos de dados: qual deles tem a Basileia? ---
    log("\n" + "=" * 70)
    log("DADOS — e onde está a Basileia")
    log("=" * 70)
    for f in [a for a in arquivos if "/dados" in a]:
        st, dados, tamanho = arquivo(f)
        log(f"\n  {f.split('/')[-1]}  [{st}]  {tamanho} bytes")
        if dados is None:
            continue
        forma(dados)
        achados = caçar(dados, "Basileia")
        if achados:
            log("    >>> ACHOU 'Basileia' em:")
            for caminho, texto in achados:
                log(f"          {caminho}  =  {texto}")
        else:
            log("    (sem 'Basileia' neste arquivo)")

    # --- sel e info, pra fechar o entendimento ---
    log("\n" + "=" * 70)
    log("SEL e INFO")
    log("=" * 70)
    for f in [a for a in arquivos if "/sel" in a or "/info" in a]:
        st, dados, _ = arquivo(f)
        log(f"\n  {f.split('/')[-1]}  [{st}]")
        if dados is not None:
            forma(dados)

    log("\nCom isto eu escrevo o robô definitivo: dois arquivos estáticos por")
    log("trimestre, sem Olinda.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
