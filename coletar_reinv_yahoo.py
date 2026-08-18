#!/usr/bin/env python3
"""
Coleta histórico de preço + dividendos via Yahoo Finance (endpoint não
oficial v8/finance/chart) para os ativos usados na calculadora de
"Reinvestimento de Dividendos" do site.

Pensado pra rodar 1x por dia via GitHub Actions, de madrugada (ver
.github/workflows/coleta-reinv-yahoo.yml). Gera:

  - data/reinv-historico/<TICKER>.json   → série completa por ativo
  - data/reinv-historico/_status.json    → resumo (profundidade real de
    cada ativo), pra acompanhar sem precisar abrir os arquivos grandes

IMPORTANTE (piloto):
  - O endpoint do Yahoo não é oficial/documentado — pode mudar de
    comportamento sem aviso a qualquer momento.
  - A base de dividendos do Yahoo para tickers .SA tem lacunas
    conhecidas em ativos menores (bibliotecas que usam esse mesmo
    endpoint alertam sobre isso). Pra blue chips costuma ser sólido.
  - Por isso está isolado nesse script/pasta, só pro Reinvestimento de
    Dividendos, sem tocar na fonte principal (HG Brasil) usada no resto
    do site.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------
# CONFIGURAÇÃO — edite esta lista pra incluir/tirar ativos do piloto.
# Comece pequeno, confira a profundidade em _status.json, vá expandindo
# aos poucos (cada ativo novo = mais 1 requisição por execução).
# ---------------------------------------------------------------------
TICKERS = [
    "PETR4", "VALE3", "ITUB4", "BBAS3", "WEGE3", "ABCB4", "BPAC11",
    "HGLG11", "KNCR11", "XPML11", "ALZR11", "GGRC11", "INFRA11", "KNIP11",
]

SAIDA_DIR = os.path.join("data", "reinv-historico")
STATUS_PATH = os.path.join(SAIDA_DIR, "_status.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PAUSA_ENTRE_REQUISICOES_SEG = 1.2


def buscar_yahoo(ticker_b3):
    """Busca histórico completo (range=max, diário) de preço e dividendos
    pro ticker, no formato bruto do endpoint v8/finance/chart do Yahoo."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_b3}.SA"
        f"?range=max&interval=1d&events=div,splits"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except Exception as e:  # noqa: BLE001 — piloto, queremos logar qualquer falha
        return None, str(e)


def processar(ticker, bruto):
    try:
        resultado = bruto["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None

    timestamps = resultado.get("timestamp") or []
    fechamentos = (
        resultado.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    )
    eventos = resultado.get("events", {}) or {}
    dividendos_brutos = eventos.get("dividends", {}) or {}
    splits_brutos = eventos.get("splits", {}) or {}

    def data_iso(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    precos = [
        {"data": data_iso(ts), "fechamento": round(fech, 6)}
        for ts, fech in zip(timestamps, fechamentos)
        if fech is not None
    ]
    if not precos:
        return None

    dividendos = sorted(
        (
            {"data": data_iso(ev["date"]), "valor": round(ev["amount"], 8)}
            for ev in dividendos_brutos.values()
        ),
        key=lambda d: d["data"],
    )

    # Splits ficam guardados pra uso futuro — a calculadora ainda NÃO
    # ajusta quantidade/preço em caso de desdobramento/grupamento.
    splits = sorted(
        (
            {
                "data": data_iso(ev["date"]),
                "numerador": ev.get("numerator"),
                "denominador": ev.get("denominator"),
            }
            for ev in splits_brutos.values()
        ),
        key=lambda d: d["data"],
    )

    return {
        "ticker": ticker,
        "fonte": "Yahoo Finance (endpoint não-oficial v8/finance/chart) — piloto",
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "preco_desde": precos[0]["data"],
        "preco_ate": precos[-1]["data"],
        "total_pregoes": len(precos),
        "dividendo_desde": dividendos[0]["data"] if dividendos else None,
        "total_dividendos": len(dividendos),
        "precos": precos,
        "dividendos": dividendos,
        "splits": splits,
    }


def main():
    os.makedirs(SAIDA_DIR, exist_ok=True)
    status = {"gerado_em": datetime.now(timezone.utc).isoformat(), "ativos": {}}

    print(f"Coletando histórico Yahoo Finance para {len(TICKERS)} ativos...\n")

    for i, ticker in enumerate(TICKERS, 1):
        print(f"[{i}/{len(TICKERS)}] {ticker}...")
        bruto, erro = buscar_yahoo(ticker)

        if erro:
            print(f"  falhou: {erro}")
            status["ativos"][ticker] = {"ok": False, "erro": erro}
            time.sleep(PAUSA_ENTRE_REQUISICOES_SEG)
            continue

        processado = processar(ticker, bruto)
        if processado is None:
            print("  sem dados utilizáveis no retorno")
            status["ativos"][ticker] = {"ok": False, "erro": "sem dados no retorno"}
            time.sleep(PAUSA_ENTRE_REQUISICOES_SEG)
            continue

        caminho = os.path.join(SAIDA_DIR, f"{ticker}.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(processado, f, ensure_ascii=False, separators=(",", ":"))

        status["ativos"][ticker] = {
            "ok": True,
            "preco_desde": processado["preco_desde"],
            "preco_ate": processado["preco_ate"],
            "total_pregoes": processado["total_pregoes"],
            "dividendo_desde": processado["dividendo_desde"],
            "total_dividendos": processado["total_dividendos"],
        }
        print(
            f"  preço desde {processado['preco_desde']} "
            f"({processado['total_pregoes']} pregões) · "
            f"{processado['total_dividendos']} dividendos"
            + (
                f" desde {processado['dividendo_desde']}"
                if processado["dividendo_desde"]
                else " (nenhum encontrado)"
            )
        )

        time.sleep(PAUSA_ENTRE_REQUISICOES_SEG)

    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    print(f"\nResumo salvo em {STATUS_PATH}")
    falhas = [t for t, s in status["ativos"].items() if not s.get("ok")]
    if falhas:
        print(f"Atenção: {len(falhas)} ativo(s) falharam: {', '.join(falhas)}")


if __name__ == "__main__":
    main()
