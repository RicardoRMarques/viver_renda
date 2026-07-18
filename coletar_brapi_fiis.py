"""
Coletor de FIIs — API brapi.dev
----------------------------------------------------------------------
Substitui o scraping por uma API oficial em JSON.

IMPORTANTE — dois níveis de acesso na brapi:
  1) Cotação básica (preço, variação, volume) -> /api/quote/
     Disponível em TODOS os planos, inclusive o gratuito. Sem custo.
  2) Indicadores completos (P/VP, DY, vacância, patrimônio, cotistas,
     dados do administrador) -> /api/v2/fii/indicators
     Exclusivo do plano PRO (pago). No sandbox sem token, só funciona
     para os tickers de teste MXRF11 e HGLG11.

Este script tenta os indicadores completos primeiro; se não tiver
token Pro (ou o ticker não for MXRF11/HGLG11), cai automaticamente
para a cotação básica gratuita.

Dependências:
    pip install requests

Uso:
    # sem token — funciona com cotação básica para qualquer ticker
    python coletar_brapi_fiis.py

    # com token Pro — libera indicadores completos para todos os tickers
    export BRAPI_TOKEN="seu_token_aqui"
    python coletar_brapi_fiis.py

Gera: fiis_investidor10.json (mesmo nome usado pelo pesquisa-fiis.html,
assim não precisa mudar nada no site)
"""

import json
import os
import time
import requests

BASE_URL = "https://brapi.dev/api"
TICKERS = ["BTLG11", "IRIM11", "ALZR11", "TRXF11", "GARE11"]

TOKEN = os.getenv("BRAPI_TOKEN")  # opcional — necessário para indicadores completos
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

# Tickers liberados no sandbox gratuito para indicadores completos
TICKERS_SANDBOX_LIVRE = {"MXRF11", "HGLG11"}


def buscar_indicadores_completos(ticker: str) -> dict | None:
    """
    Tenta /api/v2/fii/indicators. Só funciona sem token para MXRF11/HGLG11.
    Com BRAPI_TOKEN de plano Pro, funciona para qualquer ticker.
    """
    if not TOKEN and ticker not in TICKERS_SANDBOX_LIVRE:
        return None  # nem tenta, pra não gastar request à toa

    try:
        resp = requests.get(
            f"{BASE_URL}/v2/fii/indicators",
            params={"symbols": ticker},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        dados = resp.json().get("fiis", [])
        return dados[0] if dados else None
    except Exception:
        return None


def buscar_cotacao_basica(ticker: str) -> dict | None:
    """/api/quote/{ticker} — disponível em todos os planos, inclusive gratuito."""
    try:
        resp = requests.get(f"{BASE_URL}/quote/{ticker}", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        dados = resp.json().get("results", [])
        return dados[0] if dados else None
    except Exception:
        return None


def montar_registro(ticker: str) -> dict:
    completo = buscar_indicadores_completos(ticker)

    if completo:
        return {
            "ticker": ticker,
            "fonte": "brapi.dev (indicadores completos)",
            "cotacao": f"R$ {completo.get('price', '—')}",
            "dy_12m": f"{round(completo.get('dividendYield12m', 0) * 100, 2)}%" if completo.get("dividendYield12m") else "—",
            "p_vp": completo.get("priceToNav", "—"),
            "segmento": completo.get("segmentoAtuacao", "—"),
            "tipo_fundo": completo.get("segmentType", "—"),
            "tipo_gestao": completo.get("tipoGestao", "—"),
            "numero_cotistas": completo.get("totalInvestors", "—"),
            "valor_patrimonial": completo.get("equity", "—"),
            "razao_social": completo.get("name", ticker),
        }

    # fallback: cotação básica gratuita
    basica = buscar_cotacao_basica(ticker)
    if basica:
        return {
            "ticker": ticker,
            "fonte": "brapi.dev (cotação básica — plano gratuito)",
            "cotacao": f"R$ {basica.get('regularMarketPrice', '—')}",
            "dy_12m": "— (requer plano Pro)",
            "p_vp": "— (requer plano Pro)",
            "segmento": "— (requer plano Pro)",
            "tipo_fundo": "—",
            "tipo_gestao": "—",
            "numero_cotistas": "—",
            "valor_patrimonial": "—",
            "razao_social": basica.get("shortName", ticker),
        }

    return {"ticker": ticker, "fonte": "brapi.dev", "erro": "não encontrado"}


def main():
    if not TOKEN:
        print("Aviso: sem BRAPI_TOKEN definido — usando apenas cotação básica gratuita.")
        print("Indicadores completos (P/VP, DY, vacância) exigem plano Pro.\n")

    resultado = []
    for ticker in TICKERS:
        print(f"Coletando {ticker}...")
        resultado.append(montar_registro(ticker))
        time.sleep(0.5)

    with open("fiis_investidor10.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(f"\nConcluído: {len(resultado)} fundos salvos em fiis_investidor10.json")


if __name__ == "__main__":
    main()
