#!/usr/bin/env python3
"""Descartável — testa se o endpoint
https://www.anbima.com.br/pt_br/anbima/TaxasCriCraExport/downloadExterno
(capturado ao clicar no botão CSV da página de busca de CRI/CRA) funciona
com um requests.get() simples, sem navegador."""
import requests

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

URL_BASE = "https://www.anbima.com.br/pt_br/anbima/TaxasCriCraExport/downloadExterno"


def mostrar(resp):
    print(f"  HTTP {resp.status_code}  ({len(resp.content)} bytes)")
    print(f"  headers relevantes: Content-Type={resp.headers.get('Content-Type')}  "
          f"Content-Disposition={resp.headers.get('Content-Disposition')}")
    if resp.status_code == 200 and len(resp.content) > 0:
        resp.encoding = "latin-1"
        print("  primeiros 500 chars:")
        print("  " + resp.text[:500].replace("\r\n", " | "))


def main():
    print(f"\n===== GET simples, sem parâmetro nenhum =====\n{URL_BASE}")
    mostrar(requests.get(URL_BASE, headers=CABECALHOS, timeout=20))

    # Tenta alguns nomes de parâmetro de data plausíveis, pro dia de hoje (26-10-23)
    for params in (
        {"data": "23/10/2026"},
        {"data": "2026-10-23"},
        {"dataReferencia": "23/10/2026"},
        {"date": "23/10/2026"},
    ):
        print(f"\n===== GET com params={params} =====")
        mostrar(requests.get(URL_BASE, headers=CABECALHOS, params=params, timeout=20))


if __name__ == "__main__":
    main()
