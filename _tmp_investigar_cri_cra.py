#!/usr/bin/env python3
"""Script descartável — só para descobrir se existe alguma fonte pública
(sem login) de preços/taxas de CRI/CRA, nos mesmos moldes do arquivo
.txt de debêntures da ANBIMA. Roda uma vez via GitHub Actions (rede
liberada lá, bloqueada no sandbox do Claude) e depois é apagado."""
import sys
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
}

TENTATIVAS = [
    # API oficial "ANBIMA Feed" — provável exigir client_id/secret (OAuth2)
    ("https://api.anbima.com.br/feed/precos-indices/v1/cri-cra/mercado-secundario", {}),
    # Variações de nome de arquivo .txt no mesmo padrão dos debêntures
    ("https://www.anbima.com.br/informacoes/merc-sec-cri/arqs/cri261019.txt", {}),
    ("https://www.anbima.com.br/informacoes/merc-sec-cra/arqs/cra261019.txt", {}),
    ("https://www.anbima.com.br/informacoes/merc-sec-cricra/arqs/cricra261019.txt", {}),
    # Página do ANBIMA Data mencionada nas notícias — ver se devolve algo
    # (HTML normal, ou talvez um JSON de uma chamada de API por trás)
    ("https://data.anbima.com.br/cri-cra", {}),
    ("https://www.anbima.com.br/pt_br/informar/taxas-de-fundos-de-investimento.htm", {}),
]

for url, params in TENTATIVAS:
    print(f"\n=== {url} ===")
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20, allow_redirects=True)
        print(f"status: {resp.status_code}  (final url: {resp.url})")
        print(f"content-type: {resp.headers.get('content-type')}")
        corpo = resp.text[:500]
        print(f"primeiros 500 chars:\n{corpo}")
    except requests.RequestException as exc:
        print(f"ERRO: {exc}")

print("\n\nFIM — revise os resultados acima.")
sys.exit(0)
