#!/usr/bin/env python3
"""Descartável — confirma o que a HG Brasil está devolvendo de verdade
pro P/VP (price_to_book_ratio) do TRXF11, reportado errado no site
(mostrando 1,14 em vez de ~0,74-0,75)."""
import json
import requests

CHAVE_HG = "850310af"

for simbolo in ["TRXF11", "B3:TRXF11"]:
    print(f"\n=== /finance/stock_price?symbol={simbolo} ===")
    url = f"https://api.hgbrasil.com/finance/stock_price?format=json-cors&key={CHAVE_HG}&symbol={simbolo}"
    resp = requests.get(url, timeout=20)
    print(f"status: {resp.status_code}")
    data = resp.json()
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])

print("\n=== /v2/finance/quotes?tickers=B3:TRXF11 (pra comparar preço/patrimônio) ===")
url2 = f"https://api.hgbrasil.com/v2/finance/quotes?format=json-cors&tickers=B3:TRXF11&key={CHAVE_HG}"
resp2 = requests.get(url2, timeout=20)
print(f"status: {resp2.status_code}")
print(json.dumps(resp2.json(), ensure_ascii=False, indent=2)[:3000])
