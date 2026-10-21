#!/usr/bin/env python3
"""Descartável — confirma se a HG Brasil tem dados do ABTC11 (ETF de
Bitcoin listado na B3), que não aparece na Consulta Rápida do site."""
import json
import requests

CHAVE_HG = "850310af"

url = f"https://api.hgbrasil.com/v2/finance/quotes?format=json-cors&tickers=B3:ABTC11&key={CHAVE_HG}"
resp = requests.get(url, timeout=20)
print(f"status: {resp.status_code}")
print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
