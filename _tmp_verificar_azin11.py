#!/usr/bin/env python3
"""Descartável — confirma como a HG Brasil classifica o "kind" do AZIN11
(FI-Infra), que não conseguiu criar alerta de preço no site."""
import json
import requests

CHAVE_HG = "850310af"

url = f"https://api.hgbrasil.com/v2/finance/quotes?format=json-cors&tickers=B3:AZIN11&key={CHAVE_HG}"
resp = requests.get(url, timeout=20)
print(f"status: {resp.status_code}")
print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
