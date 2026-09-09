#!/usr/bin/env python3
"""
Descobre como a interface web do IF.data (www3.bcb.gov.br/ifdata) busca os
dados — se existe uma API REST por trás ou se é só tela.

POR QUE ISSO EXISTE
-------------------
A API oficial (olinda.bcb.gov.br, OData) provou-se instável: em oito
execuções reais, duas responderam e seis devolveram HTTP 500 em tudo. A
interface web, no mesmo período, continuava de pé — é outro serviço.

Se ela tiver uma API REST por trás, trocamos de fonte e o robô fica
simples. Se for só JavaScript de tela, a alternativa seria automação de
navegador, que é frágil em CI e quebra quando o BC mexe no layout — e aí
vale mais atualizar o JSON à mão quatro vezes por ano.

Este script não decide nada: ele só OLHA e imprime. Nada é gravado.

COMO USAR
---------
    python3 descobrir_ifdata_web.py

Precisa rodar de onde haja acesso à rede do BC (o GitHub Actions serve).
"""

import gzip
import io
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin

BASE = "https://www3.bcb.gov.br/ifdata/"
TEMPO_LIMITE = 45

CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def log(m):
    print(m, flush=True)


def baixar(url, dados=None, tipo=None):
    """Devolve (status, texto). Não levanta exceção — o erro é o resultado."""
    cabecalhos = dict(CABECALHOS)
    if tipo:
        cabecalhos["Content-Type"] = tipo
    req = urllib.request.Request(url, data=dados, headers=cabecalhos,
                                 method="POST" if dados is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as r:
            bruto = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                bruto = gzip.decompress(bruto)
            return r.status, bruto.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            corpo = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            corpo = ""
        return e.code, corpo
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main():
    log("=" * 68)
    log("1) A PÁGINA PRINCIPAL")
    log("=" * 68)
    status, html = baixar(BASE)
    log(f"  {BASE} -> status {status}, {len(html)} caracteres")
    if status != 200:
        log(f"  não deu pra seguir. Resposta: {html[:300]}")
        return 1

    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I)
    log(f"\n  {len(scripts)} arquivos de script na página:")
    for s in scripts:
        log(f"    {s}")

    # pistas de endpoint no próprio HTML
    for padrao, rotulo in [(r'["\'](/?[\w./-]*rest/[\w./{}-]+)["\']', "caminho com /rest/"),
                           (r'["\'](/?[\w./-]*api/[\w./{}-]+)["\']', "caminho com /api/")]:
        achados = sorted(set(re.findall(padrao, html)))
        if achados:
            log(f"\n  {rotulo} no HTML: {achados[:15]}")

    log("\n" + "=" * 68)
    log("2) DENTRO DOS SCRIPTS (é aqui que o endereço da API costuma estar)")
    log("=" * 68)
    candidatos = set()
    for src in scripts:
        url = urljoin(BASE, src)
        st, corpo = baixar(url)
        log(f"\n  {url}\n    status {st}, {len(corpo)} caracteres")
        if st != 200:
            continue
        for padrao in (r'["\']([\w./-]*rest/[\w./{}$-]+)["\']',
                       r'["\']([\w./-]*api/[\w./{}$-]+)["\']',
                       r'\$http\.(?:get|post)\(\s*["\']([^"\']+)["\']',
                       r'url\s*[:=]\s*["\']([^"\']{4,120})["\']'):
            for achado in re.findall(padrao, corpo):
                if any(t in achado.lower() for t in ("rest", "api", "servico", "dados",
                                                     "relatorio", "cadastro", "consulta")):
                    candidatos.add(achado)
        if candidatos:
            log(f"    candidatos até agora: {sorted(candidatos)[:20]}")

    log("\n" + "=" * 68)
    log("3) TESTANDO OS CANDIDATOS")
    log("=" * 68)
    if not candidatos:
        log("  Nenhum candidato encontrado nos scripts.")
    for caminho in sorted(candidatos)[:25]:
        if "{" in caminho or "$" in caminho:
            log(f"  (ignorado, tem placeholder) {caminho}")
            continue
        url = urljoin(BASE, caminho)
        st, corpo = baixar(url)
        amostra = corpo[:200].replace("\n", " ")
        log(f"  [{st}] {url}")
        log(f"        {amostra}")

    log("\n" + "=" * 68)
    log("4) CHUTES INFORMADOS (nomes usuais em aplicação do BC)")
    log("=" * 68)
    for caminho in ("rest/dadosDisponiveis", "rest/datasBase", "rest/tiposIf",
                    "rest/relatorios", "rest/cadastro", "rest/instituicoes",
                    "rest/versao", "rest/parametros"):
        url = urljoin(BASE, caminho)
        st, corpo = baixar(url)
        log(f"  [{st}] {url}")
        if st == 200:
            log(f"        {corpo[:300]}".replace("\n", " "))

    log("\nCOMO LER: qualquer linha [200] com JSON de verdade em (3) ou (4) é")
    log("uma API utilizável — me mande a saída e eu escrevo o robô em cima")
    log("dela. Se tudo der 404/erro, a página é só tela e a automação exigiria")
    log("navegador — aí vale conversar sobre atualizar à mão por trimestre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
