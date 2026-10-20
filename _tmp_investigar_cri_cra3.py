#!/usr/bin/env python3
"""Descartável — confirma os campos da API pública (sem login) do ANBIMA
Data pra CRI/CRA, achada inspecionando o tráfego real do site com
Playwright. Testa paginação, a view "precos" (que deve trazer taxa
indicativa/PU) e se existe paginação suficiente pra cobrir o mercado todo."""
import json
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://data.anbima.com.br/busca/certificado-de-recebiveis",
    "Origin": "https://data.anbima.com.br",
}

BASE = "https://data-api.prd.anbima.com.br/web-bff/v1/certificado-recebiveis"

def tentar(params, rotulo):
    print(f"\n=== {rotulo} ===")
    print(f"params: {params}")
    try:
        resp = requests.get(BASE, headers=HEADERS, params=params, timeout=30)
        print(f"status: {resp.status_code}")
        if resp.status_code != 200:
            print(resp.text[:500])
            return
        data = resp.json()
        total = data.get("page", {}).get("total_elements") or data.get("totalElements") or "?"
        conteudo = data.get("content", [])
        print(f"total_elements (se vier): {total}")
        print(f"itens nesta página: {len(conteudo)}")
        if conteudo:
            print("chaves do 1º item:", sorted(conteudo[0].keys()))
            print("1º item completo:")
            print(json.dumps(conteudo[0], ensure_ascii=False, indent=2))
        print("estrutura 'page' (se vier):", json.dumps(data.get("page"), ensure_ascii=False)[:300])
    except Exception as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}")

tentar({"page": 0, "size": 3, "view": "precos"}, "view=precos, size=3")
tentar({"page": 0, "size": 100, "view": "caracteristicas"}, "view=caracteristicas, size=100 (ver paginação total)")
tentar({"page": 0, "size": 3, "view": "precos", "tipo_titulo": "CRA"}, "filtrando tipo_titulo=CRA")
tentar({"page": 0, "size": 3}, "sem view (default)")

print("\n\nFIM.")
