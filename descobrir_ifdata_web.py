#!/usr/bin/env python3
"""
Fase 3 da investigação da interface web do IF.data (www3.bcb.gov.br/ifdata).

O QUE JÁ SABEMOS (fases 1 e 2, execuções reais de 09/09/2026)
--------------------------------------------------------------
  GET rest/relatorios2025a2030  -> 200, catálogo com 5 data-bases.
      A mais recente é 202603 (1º tri/2026) — ou seja, 202606 realmente
      ainda não saiu, e o seletor do site deve mostrar "1T26".

  Cada data-base lista 33 arquivos, com caminho no formato
      "ifdata_2025_2030//202603/cadastro202603_1009.json"   (barra dupla!)

  sel202603.json revelou os tipos de instituição, e AQUI corrigimos um
  engano que vinha do Olinda:
      1009 = Conglomerados Prudenciais e Instituições Independentes  <- o nosso
      1005 = Conglomerados Financeiros e Instituições Independentes
      1006 = Instituições Individuais

  trel<dt>_<id>.json descreve cada relatório (105 = "Ativo", etc.).
  dados<dt>_1..5.json devem ser os valores.

O QUE FALTA
-----------
A rota de download. Os seis prefixos óbvios deram 404. Mas o HTML da
página cita /ifdata/rest/arquivos e /ifdata/rest/pdf — então a resposta
está no JavaScript embutido na própria página, não nos arquivos .js
externos (esses são só jQuery, bootstrap e menu).

Este script mostra o TRECHO do HTML em volta dessas citações — é ali que
vai estar a forma exata da chamada — e testa um leque de variações,
inclusive POST, que é o formato provável para "me entregue este arquivo".

Não grava nada. Só olha e imprime.

    python3 descobrir_ifdata_web.py
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("IFDATA_BASE", "https://www3.bcb.gov.br/ifdata/")
TEMPO_LIMITE = 60
CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": BASE,
    "X-Requested-With": "XMLHttpRequest",
}


def log(m):
    print(m, flush=True)


def bater(url, corpo=None, tipo=None, metodo=None):
    """(status, texto). Erro vira resultado, não exceção."""
    cabecalhos = dict(CABECALHOS)
    if tipo:
        cabecalhos["Content-Type"] = tipo
    dados = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
    req = urllib.request.Request(url, data=dados, headers=cabecalhos,
                                 method=metodo or ("POST" if dados is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def parece_json(texto):
    t = (texto or "").lstrip()
    return t.startswith("{") or t.startswith("[")


def main():
    log("=" * 70)
    log("1) COMO O HTML USA 'rest/arquivos' — a forma da chamada está aqui")
    log("=" * 70)
    status, html = bater(BASE)
    log(f"  página: status {status}, {len(html)} caracteres\n")
    if status != 200:
        return 1

    for termo in ("rest/arquivos", "rest/pdf", "rest/relatorios"):
        posicoes = [m.start() for m in re.finditer(re.escape(termo), html)]
        log(f"  '{termo}': {len(posicoes)} ocorrência(s)")
        for pos in posicoes[:4]:
            trecho = html[max(0, pos - 320): pos + 320]
            trecho = re.sub(r"\s+", " ", trecho)
            log(f"    ...{trecho}...")
        log("")

    # qualquer função JS que monte URL de arquivo
    for padrao, rotulo in [
        (r"function\s+(\w*[Aa]rquivo\w*)\s*\([^)]*\)\s*\{[^}]{0,400}\}", "função com 'arquivo' no nome"),
        (r"\$\.(?:get|post|ajax)\(\s*\{?[^)]{0,320}", "chamada jQuery"),
        (r"(?:url|href|src)\s*[:=]\s*[^,;\n]{0,160}rest[^,;\n]{0,160}", "montagem de URL com 'rest'"),
    ]:
        achados = re.findall(padrao, html)
        if achados:
            log(f"  {rotulo}: {len(achados)} achado(s)")
            for a in achados[:6]:
                texto = re.sub(r"\s+", " ", a if isinstance(a, str) else str(a))
                log(f"    {texto[:300]}")
            log("")

    log("=" * 70)
    log("2) TESTANDO ROTAS DE DOWNLOAD")
    log("=" * 70)
    bruto = "ifdata_2025_2030//202603/info202603.json"
    simples = bruto.replace("//", "/")
    so_nome = bruto.split("/")[-1]
    codificado = urllib.parse.quote(bruto, safe="")

    tentativas = [
        ("GET", BASE + "rest/arquivos", None, None),
        ("GET", BASE + "rest/arquivos/" + simples, None, None),
        ("GET", BASE + "rest/arquivos?f=" + codificado, None, None),
        ("GET", BASE + "rest/arquivos?arquivo=" + codificado, None, None),
        ("GET", BASE + "rest/arquivos?nome=" + codificado, None, None),
        ("GET", BASE + simples, None, None),
        ("GET", BASE + "dados/" + simples, None, None),
        ("GET", BASE + "json/" + simples, None, None),
        ("GET", BASE + "rest/arquivos/" + so_nome, None, None),
        ("POST", BASE + "rest/arquivos", json.dumps({"f": bruto}), "application/json"),
        ("POST", BASE + "rest/arquivos", json.dumps([{"f": bruto}]), "application/json"),
        ("POST", BASE + "rest/arquivos", "f=" + codificado,
         "application/x-www-form-urlencoded"),
        ("POST", BASE + "rest/arquivos", bruto, "text/plain"),
    ]
    for metodo, url, corpo, tipo in tentativas:
        st, texto = bater(url, corpo, tipo, metodo)
        marca = " <<< JSON!" if st == 200 and parece_json(texto) else ""
        log(f"  [{st}] {metodo} {url}{marca}")
        if corpo:
            log(f"        corpo enviado: {str(corpo)[:90]}")
        if st == 200 and texto:
            log(f"        resposta: {texto[:260]}".replace("\n", " "))

    log("\n" + "=" * 70)
    log("COMO LER: a linha marcada com '<<< JSON!' é a rota boa. Se nenhuma")
    log("marcar, o trecho de HTML da seção 1 mostra como a página monta a")
    log("chamada — me mande essa parte que eu leio e acerto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
