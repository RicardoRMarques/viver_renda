#!/usr/bin/env python3
"""Descartável — testa se existe um arquivo .txt/.csv público e estável
pra CRI/CRA na ANBIMA, no mesmo padrão do db{AAMMDD}.txt usado pra
debêntures (coletar_debentures.py). O usuário mandou uma planilha de
preços de CRI/CRA tirada do site deles (data.anbima.com.br/busca/
certificado-de-recebiveis?view=precos) com datas de 16 a 22/10/2026 —
testamos vários dias e vários padrões de nome plausíveis."""
import sys
from datetime import date, timedelta

import requests

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/plain,*/*",
}

# Padrões de nome plausíveis, inspirados no de debêntures
# (merc-sec-debentures/arqs/db{AAMMDD}.txt).
PADROES = [
    "https://www.anbima.com.br/informacoes/merc-sec-cri-cra/arqs/crcr{data}.txt",
    "https://www.anbima.com.br/informacoes/merc-sec-cri-cra/arqs/cricra{data}.txt",
    "https://www.anbima.com.br/informacoes/merc-sec-cri/arqs/cri{data}.txt",
    "https://www.anbima.com.br/informacoes/merc-sec-cra/arqs/cra{data}.txt",
    "https://www.anbima.com.br/informacoes/merc-sec-certificados-recebiveis/arqs/cr{data}.txt",
    "https://www.anbima.com.br/informacoes/merc-sec-cri-cra/arqs/cr{data}.txt",
]

# Datas que sabemos ter dado (vieram na planilha que o usuário mandou).
DIAS_PARA_TESTAR = [date(2026, 10, d) for d in (16, 19, 20, 21, 22, 23)]


def testar(url):
    try:
        resp = requests.get(url, headers=CABECALHOS, timeout=20)
    except requests.RequestException as e:
        print(f"  ERRO DE REDE: {e}")
        return
    print(f"  HTTP {resp.status_code}  ({len(resp.content)} bytes)")
    if resp.status_code == 200 and len(resp.content) > 0:
        resp.encoding = "latin-1"
        print(f"  >>> PAREEEECE TER DADO! Primeiros 300 chars:")
        print("  " + resp.text[:300].replace("\n", " | "))


def main():
    for padrao in PADROES:
        print(f"\n===== padrão: {padrao} =====")
        for dia in DIAS_PARA_TESTAR:
            url = padrao.format(data=dia.strftime("%y%m%d"))
            print(f"\n{url}")
            testar(url)


if __name__ == "__main__":
    main()
