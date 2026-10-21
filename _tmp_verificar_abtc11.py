#!/usr/bin/env python3
"""Descartável — confirma se a HG Brasil tem dados do ABTC11 (ETF de
Bitcoin listado na B3), que não aparece na Consulta Rápida do site."""
import json
import requests

CHAVE_HG = "850310af"
CHAVE_TWELVEDATA = "034d1589162e413d9a1e9608860cb06a"

url = f"https://api.hgbrasil.com/v2/finance/quotes?format=json-cors&tickers=B3:ABTC11&key={CHAVE_HG}"
resp = requests.get(url, timeout=20)
print(f"HG Brasil status: {resp.status_code}")
print(json.dumps(resp.json(), ensure_ascii=False, indent=2))

for simbolo in ("ABTC11", "ABTC11:BVMF", "ABTC11.SA"):
    url_td = f"https://api.twelvedata.com/quote?symbol={simbolo}&apikey={CHAVE_TWELVEDATA}"
    resp_td = requests.get(url_td, timeout=20)
    print(f"\nTwelveData ({simbolo}) status: {resp_td.status_code}")
    print(json.dumps(resp_td.json(), ensure_ascii=False, indent=2))
