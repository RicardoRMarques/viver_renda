#!/usr/bin/env python3
"""Descartável — tenta replicar o download do CSV de Taxas de CRI/CRA da
ANBIMA só com requests.Session (sem navegador): visita a página pra pegar
os cookies de sessão (JSESSIONID, lumUserSessionId — sessão anônima
'Guest', confirmada no teste anterior via Playwright) e depois chama o
endpoint real descoberto: TaxasCriCraExport/exportarCSV."""
import requests

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

URL_PAGINA = "https://www.anbima.com.br/pt_br/informar/precos-e-indices/precos/taxas-de-cri-e-cra/taxas-de-cri-e-cra.htm"
URL_EXPORT = "https://www.anbima.com.br/pt_br/anbima/TaxasCriCraExport/exportarCSV"


def mostrar(resp):
    print(f"  HTTP {resp.status_code}  ({len(resp.content)} bytes)")
    print(f"  Content-Type={resp.headers.get('Content-Type')}  "
          f"Content-Disposition={resp.headers.get('Content-Disposition')}")
    if resp.status_code == 200 and len(resp.content) > 0:
        resp.encoding = "latin-1"
        print("  primeiros 500 chars:")
        print("  " + resp.text[:500].replace("\r\n", " | "))


def main():
    sessao = requests.Session()
    sessao.headers.update(CABECALHOS)

    print(f"1) GET página pra pegar cookies: {URL_PAGINA}")
    resp_pagina = sessao.get(URL_PAGINA, timeout=30)
    print(f"   status={resp_pagina.status_code}  cookies recebidos: {list(sessao.cookies.keys())}")

    print(f"\n2) GET export, mesma sessão, sem filtro:\n   {URL_EXPORT}?filtroTermo=&filtroData=")
    resp = sessao.get(
        URL_EXPORT,
        params={"filtroTermo": "", "filtroData": ""},
        headers={"Referer": URL_PAGINA},
        timeout=30,
    )
    mostrar(resp)

    print("\n3) GET export com filtroData=23/10/2026")
    resp2 = sessao.get(
        URL_EXPORT,
        params={"filtroTermo": "", "filtroData": "23/10/2026"},
        headers={"Referer": URL_PAGINA},
        timeout=30,
    )
    mostrar(resp2)

    print("\n4) Pra comparação: export SEM visitar a página antes (sessão nova, sem cookies)")
    resp3 = requests.get(
        URL_EXPORT,
        params={"filtroTermo": "", "filtroData": ""},
        headers=CABECALHOS,
        timeout=30,
    )
    mostrar(resp3)


if __name__ == "__main__":
    main()
