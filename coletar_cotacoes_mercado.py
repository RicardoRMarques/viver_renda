#!/usr/bin/env python3
"""Robô leve de cotações do mercado (preço + variação do dia).

Roda várias vezes durante o pregão pra alimentar o Mapa de Calor com dado
fresco, sem repetir o scraping pesado de fundamentos (B3/Fundamentus/CVM)
que o coletar_mercado.py já faz 1x por dia de madrugada — aquela cadência
continua certa pros FUNDAMENTOS (P/L, P/VP, DY, patrimônio), que não mudam
intradiário, mas a variação do dia muda o pregão inteiro e não podia ficar
presa na mesma foto de ontem (reportado em 27/11/2026: TRXF11 aparecendo
com a variação de ONTEM no Mapa de Calor, horas depois de já ter subido
mais de 5% no pregão de HOJE).

Lê o universo de tickers de mercado-acoes.json e mercado-fiis.json
(gerados pelo coletar_mercado.py) e busca só preço/variação via HG Brasil
/v2/finance/quotes, em lotes de 5 (limite do plano — ver
HGBRASIL_MAX_TICKERS_POR_REQUISICAO no coletar_hgbrasil.py, mesma
constante duplicada aqui). Publica em data/mercado-cotacoes.json, que o
index.html mescla por cima dos fundamentos diários na hora de colorir o
Mapa de Calor (ver mcCarregarDataset).

Uso:
  HGBRASIL_TOKEN=xxxxxxxx python coletar_cotacoes_mercado.py
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

TIMEOUT = 20
CHAVE_HG = os.environ.get("HGBRASIL_TOKEN", "").strip()
HGBRASIL_QUOTES_URL = "https://api.hgbrasil.com/v2/finance/quotes"
HGBRASIL_MAX_TICKERS_POR_REQUISICAO = 5
PAUSA_ENTRE_LOTES = 0.2  # segundos, só pra não martelar a API sem necessidade

MERCADO_ACOES_FILE = "mercado-acoes.json"
MERCADO_FIIS_FILE = "mercado-fiis.json"
OUTPUT_FILE = "data/mercado-cotacoes.json"


def log(msg):
    print(msg, file=sys.stderr)


def carregar_tickers(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"AVISO: não consegui ler {caminho}: {exc}")
        return []
    return [a["ticker"] for a in dados.get("ativos", []) if a.get("ticker")]


def tickers_b3(simbolos):
    return ",".join(f"B3:{s}" for s in simbolos)


def buscar_cotacoes_em_lotes(pool):
    resultados = []
    for i in range(0, len(pool), HGBRASIL_MAX_TICKERS_POR_REQUISICAO):
        lote = pool[i:i + HGBRASIL_MAX_TICKERS_POR_REQUISICAO]
        try:
            resp = requests.get(
                HGBRASIL_QUOTES_URL,
                params={"tickers": tickers_b3(lote), "key": CHAVE_HG},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            dados = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log(f"  lote {lote} falhou, pulando: {exc}")
            continue
        lote_resultados = dados.get("results") or []
        if not lote_resultados and dados.get("errors"):
            log(f"  lote {lote} retornou erro da HG Brasil: {dados.get('errors')}")
        resultados.extend(lote_resultados)
        time.sleep(PAUSA_ENTRE_LOTES)
    return resultados


def main():
    if not CHAVE_HG:
        log("ERRO: variável de ambiente HGBRASIL_TOKEN não definida.")
        sys.exit(1)

    tickers_acoes = carregar_tickers(MERCADO_ACOES_FILE)
    tickers_fiis = carregar_tickers(MERCADO_FIIS_FILE)
    pool = sorted(set(tickers_acoes + tickers_fiis))

    if not pool:
        log("ERRO: nenhum ticker encontrado em mercado-acoes.json/mercado-fiis.json — o robô diário já rodou alguma vez?")
        sys.exit(1)

    log(f"Buscando cotações de {len(pool)} tickers em lotes de {HGBRASIL_MAX_TICKERS_POR_REQUISICAO}...")
    resultados_brutos = buscar_cotacoes_em_lotes(pool)

    cotacoes = {}
    for ativo in resultados_brutos:
        symbol = ativo.get("symbol")
        if not symbol:
            continue
        quote = ativo.get("quote") or {}
        preco = quote.get("value")
        variacao = quote.get("change_percent")
        if preco is None or variacao is None:
            continue
        cotacoes[symbol] = {"preco": preco, "variacao_dia_pct": variacao}

    if not cotacoes:
        log("ERRO: nenhuma cotação válida retornada em nenhum lote — mantendo o arquivo anterior (nada a escrever).")
        sys.exit(1)

    saida = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cotacoes": cotacoes,
    }
    os.makedirs(os.path.dirname(OUTPUT_FILE) or ".", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    log(f"OK: {len(cotacoes)}/{len(pool)} cotações salvas em {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
