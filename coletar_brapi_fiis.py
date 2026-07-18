#!/usr/bin/env python3
"""
coletar_brapi_fiis.py
----------------------
Busca cotação + indicadores fundamentalistas de uma lista de FIIs na API da
brapi.dev e salva o resultado em fiis_investidor10.json, na raiz do repositório.

O token da API é lido de uma variável de ambiente (BRAPI_TOKEN), nunca fica
escrito no código nem é exposto ao navegador do visitante do site. Este
script roda apenas no GitHub Actions (servidor), então é seguro usar o token
aqui.

Uso local (opcional, para testar):
    export BRAPI_TOKEN="seu_token_aqui"
    python coletar_brapi_fiis.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# Lista de FIIs acompanhados no boletim. Ajuste livremente.
TICKERS_FII = [
    "BTLG11",
    "IRIM11",
    "ALZR11",
    "TRXF11",
    "GARE11",
    "MXRF11",
    "HGLG11",
    "KNRI11",
    "VISC11",
    "XPML11",
]

BRAPI_BASE_URL = "https://brapi.dev/api/quote"
OUTPUT_FILE = "fiis_investidor10.json"
LOTE = 8  # brapi aceita vários tickers por chamada; dividimos em lotes por segurança
TIMEOUT = 20


def obter_token() -> str:
    token = os.environ.get("BRAPI_TOKEN", "").strip()
    if not token:
        print("ERRO: variável de ambiente BRAPI_TOKEN não definida.", file=sys.stderr)
        sys.exit(1)
    return token


def dividir_em_lotes(lista, tamanho):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def formatar_percentual(valor):
    if valor is None:
        return None
    try:
        return f"{float(valor):.2f}%"
    except (TypeError, ValueError):
        return None


def formatar_moeda(valor):
    if valor is None:
        return None
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return None


def formatar_numero(valor, casas=2):
    if valor is None:
        return None
    try:
        return f"{float(valor):.{casas}f}"
    except (TypeError, ValueError):
        return None


def buscar_lote(tickers, token):
    url = f"{BRAPI_BASE_URL}/{','.join(tickers)}"
    params = {"fundamental": "true", "dividends": "true"}
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def montar_registro(ativo):
    dy = (
        ativo.get("dividendYield")
        or (ativo.get("defaultKeyStatistics") or {}).get("dividendYield")
    )
    pvp = (ativo.get("defaultKeyStatistics") or {}).get("priceToBook")

    return {
        "ticker": ativo.get("symbol"),
        "razao_social": ativo.get("longName") or ativo.get("shortName") or "",
        "cotacao": formatar_moeda(ativo.get("regularMarketPrice")),
        "variacao_dia": formatar_percentual(ativo.get("regularMarketChangePercent")),
        "dy_12m": formatar_percentual(dy),
        "p_vp": formatar_numero(pvp),
        "segmento": (ativo.get("industry") or ativo.get("sector") or "—"),
        "fonte": "brapi.dev",
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }


def main():
    token = obter_token()
    resultados = []
    erros = []

    for lote in dividir_em_lotes(TICKERS_FII, LOTE):
        try:
            ativos = buscar_lote(lote, token)
            for ativo in ativos:
                resultados.append(montar_registro(ativo))
        except requests.RequestException as exc:
            print(f"AVISO: falha ao buscar lote {lote}: {exc}", file=sys.stderr)
            erros.extend(lote)
        time.sleep(1)  # respeita rate limit da API

    if not resultados:
        print("ERRO: nenhum FII foi coletado com sucesso. Mantendo arquivo anterior.", file=sys.stderr)
        sys.exit(1)

    # Ordena pelo maior Dividend Yield (quando disponível) para destacar no boletim
    def chave_ordenacao(item):
        try:
            return -float((item.get("dy_12m") or "0%").replace("%", ""))
        except ValueError:
            return 0

    resultados.sort(key=chave_ordenacao)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(resultados)} FIIs salvos em {OUTPUT_FILE}.")
    if erros:
        print(f"AVISO: falha ao coletar os tickers: {', '.join(erros)}", file=sys.stderr)


if __name__ == "__main__":
    main()
