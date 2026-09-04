#!/usr/bin/env python3
"""
Robô "Mercado" — coleta em massa o universo de Ações e FIIs pra alimentar
as páginas de listagem (menu "Mercado" > Lista de Ações / Lista de FIIs).

Gera dois arquivos na raiz do repo:
  - mercado-acoes.json
  - mercado-fiis.json

FONTES:
  - Ações: 100% HG Brasil.
      1) /v2/finance/tickers?sources=B3   -> universo + setor/segmento (paginado)
      2) /v2/finance/fundamentals         -> P/L, P/VP, Margem Líquida, DY,
                                              Valor de Mercado (em lotes, aceita
                                              vários tickers separados por vírgula)
  - FIIs: HG Brasil não tem fundamentos de FII (testado em produção — o
    endpoint /v2/finance/fundamentals devolve "statements": [] pra kind=="fii").
      1) /v2/finance/quotes  -> cotação, variação do dia, valor de mercado
      2) Fundamentus (fii_resultado.php) -> P/VP, Dividend Yield, Valor de
         Mercado (cross-check) e Segmento, tudo numa página só, sem precisar
         bater ticker por ticker.

Uso:
  HGBRASIL_TOKEN=xxxxxxxx python coletar_mercado.py

Nota: chave usada é a de servidor (a mesma do boletim diário via GitHub
Actions secret), não a de browser/CORS.
"""

import io
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests

CHAVE_HG = os.environ.get("HGBRASIL_TOKEN", "").strip()
if not CHAVE_HG:
    print("ERRO: variável de ambiente HGBRASIL_TOKEN não definida.", file=sys.stderr)
    sys.exit(1)

BASE_HG = "https://api.hgbrasil.com/v2/finance"
TIMEOUT = 30
LOTE_FUNDAMENTALS = 10   # quantos tickers pedir por chamada de /fundamentals
LOTE_QUOTES = 5          # a HG limita cotações a 5 tickers por requisição (já
                          # descoberto e documentado no robô do boletim)
PAUSA_ENTRE_LOTES = 0.3  # segundos, só pra não martelar a API sem necessidade

SAIDA_ACOES = "mercado-acoes.json"
SAIDA_FIIS = "mercado-fiis.json"


def log(msg):
    print(f"[coletar_mercado] {msg}", flush=True)


def requisitar(url, params, tentativas=3):
    """GET com retry simples — a HG Brasil já é chamada assim no resto do
    projeto (várias vezes por dia), então um retry curto evita que uma
    falha de rede pontual derrube a coleta inteira."""
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            dados = resp.json()
            if dados.get("metadata", {}).get("key_status") not in (None, "valid"):
                raise RuntimeError(f"key_status inválido: {dados['metadata']}")
            return dados
        except Exception as e:  # noqa: BLE001 — robô de coleta, log e segue
            ultimo_erro = e
            log(f"  falha ({tentativa}/{tentativas}) em {url}: {e}")
            time.sleep(1.5 * tentativa)
    raise RuntimeError(f"desisti após {tentativas} tentativas: {ultimo_erro}")


# ---------------------------------------------------------------------------
# 1) UNIVERSO DE AÇÕES — lista de tickers + setor/segmento
# ---------------------------------------------------------------------------
def buscar_universo_acoes():
    """Pagina /v2/finance/tickers?sources=B3 e filtra kind == 'stock'.
    Não usamos 'query' (busca textual) — sem esse parâmetro o endpoint
    devolve o catálogo completo, paginado."""
    log("Buscando universo de ações na HG Brasil (/v2/finance/tickers)...")
    ativos = []
    pagina = 1
    while True:
        dados = requisitar(f"{BASE_HG}/tickers", {
            "format": "json-cors",
            "sources": "B3",
            "sort": "symbol",
            "order": "asc",
            "page": pagina,
            "key": CHAVE_HG,
        })
        resultados = dados.get("results", [])
        if not resultados:
            break
        for item in resultados:
            if item.get("kind") != "stock":
                continue
            classificacao = item.get("classification") or {}
            ativos.append({
                "ticker": item["symbol"],
                "nome": item.get("name") or item.get("full_name") or item["symbol"],
                "setor": classificacao.get("sector") or "Outros",
            })
        log(f"  página {pagina}: +{len(resultados)} itens (acumulado ações: {len(ativos)})")
        if len(resultados) < 20:  # heurística de "última página" — ajuste se
            break                  # o plano tiver um page_size diferente
        pagina += 1
        time.sleep(PAUSA_ENTRE_LOTES)
    return ativos


# ---------------------------------------------------------------------------
# 2) FUNDAMENTOS DAS AÇÕES — P/L, P/VP, Margem Líquida, DY, Valor de Mercado
# ---------------------------------------------------------------------------
def enriquecer_acoes_com_fundamentals(universo):
    log(f"Buscando fundamentos de {len(universo)} ações em lotes de {LOTE_FUNDAMENTALS}...")
    por_ticker = {a["ticker"]: a for a in universo}
    tickers = list(por_ticker.keys())

    for i in range(0, len(tickers), LOTE_FUNDAMENTALS):
        lote = tickers[i:i + LOTE_FUNDAMENTALS]
        tickers_hg = ",".join(f"B3:{t}" for t in lote)
        try:
            dados = requisitar(f"{BASE_HG}/fundamentals", {
                "format": "json-cors",
                "tickers": tickers_hg,
                "period": "annual",  # traz o TTM automaticamente em statements[0]
                "key": CHAVE_HG,
            })
        except Exception as e:  # noqa: BLE001
            log(f"  lote {i}-{i+len(lote)} falhou de vez, pulando: {e}")
            continue

        for resultado in dados.get("results", []):
            simbolo = resultado.get("symbol")
            registro = por_ticker.get(simbolo)
            if not registro:
                continue

            statements = resultado.get("statements") or []
            ttm = next((s for s in statements if s.get("period_type") == "ttm"), None) \
                or (statements[0] if statements else None)

            registro["preco"] = (resultado.get("quote") or {}).get("value")
            registro["valor_mercado"] = (resultado.get("quote") or {}).get("market_cap")
            registro["variacao_dia_pct"] = (resultado.get("quote") or {}).get("change_percent")

            if ttm:
                valuation = ttm.get("valuation") or {}
                margens = ttm.get("margins") or {}
                dividendos = ttm.get("dividends") or {}
                registro["pl"] = valuation.get("price_to_earnings_ratio")
                registro["pvp"] = valuation.get("price_to_book_ratio")
                registro["margem_liquida_pct"] = margens.get("net_profit_margin")
                registro["dy_pct"] = dividendos.get("yield_percent")
            else:
                registro["pl"] = registro["pvp"] = registro["margem_liquida_pct"] = registro["dy_pct"] = None

        log(f"  lote {i}-{i+len(lote)} ok ({len(dados.get('results', []))} retornados)")
        time.sleep(PAUSA_ENTRE_LOTES)

    # Descarta ações sem cotação (delistadas/suspensas) pra não poluir a lista
    return [a for a in universo if a.get("preco") is not None]


# ---------------------------------------------------------------------------
# 3) FIIs — cotação/variação via HG Brasil, fundamentos via Fundamentus
# ---------------------------------------------------------------------------
def _normalizar_num_br(texto):
    """'12,34' -> 12.34 | '1.234,56' -> 1234.56 | '-' ou vazio -> None"""
    if texto is None:
        return None
    t = str(texto).strip().replace("%", "")
    if t in ("", "-", "N/A", "nan"):
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def buscar_fiis_fundamentus():
    """A página fii_resultado.php do Fundamentus devolve TODOS os FIIs
    negociados numa tabela HTML só — muito mais simples que bater ticker
    por ticker, e é a mesma fonte que o projeto já usa hoje pro P/VP."""
    log("Buscando fundamentos de FIIs no Fundamentus...")
    url = "https://www.fundamentus.com.br/fii_resultado.php"
    headers = {
        # Sem um User-Agent de navegador de verdade o Fundamentus costuma
        # bloquear a requisição — já é uma pegadinha conhecida desse site.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    try:
        import pandas as pd
        tabelas = pd.read_html(io.StringIO(resp.text), decimal=",", thousands=".")
    except ImportError:
        raise RuntimeError(
            "pandas + lxml são necessários pra ler a tabela do Fundamentus "
            "(pip install pandas lxml)"
        )
    if not tabelas:
        raise RuntimeError("Fundamentus não devolveu nenhuma tabela — layout deve ter mudado")

    tabela = tabelas[0]
    # Colunas conhecidas da página (podem mudar se o Fundamentus alterar o
    # layout — se isso quebrar, a primeira coisa a checar é esse mapeamento):
    # Papel | Segmento | Cotação | FFO Yield | Dividend Yield | P/VP |
    # Valor de Mercado | Liquidez | Qtd de imóveis | Preço do m2 |
    # Aluguel por m2 | Cap Rate | Vacância Média
    fiis = []
    for _, linha in tabela.iterrows():
        ticker = str(linha.get("Papel", "")).strip().upper()
        if not ticker:
            continue
        fiis.append({
            "ticker": ticker,
            "segmento": str(linha.get("Segmento", "Outros")).strip() or "Outros",
            "dy_pct": _normalizar_num_br(linha.get("Dividend Yield")),
            "pvp": _normalizar_num_br(linha.get("P/VP")),
            "patrimonio_liquido": _normalizar_num_br(linha.get("Valor de Mercado")),
        })
    log(f"  {len(fiis)} FIIs lidos do Fundamentus")
    return fiis


def enriquecer_fiis_com_quotes(fiis):
    """Completa cotação, variação e nome de cada FII via HG Brasil (em
    lotes de 5 — mesmo limite já usado no restante do projeto)."""
    log(f"Buscando cotações de {len(fiis)} FIIs na HG Brasil (lotes de {LOTE_QUOTES})...")
    por_ticker = {f["ticker"]: f for f in fiis}
    tickers = list(por_ticker.keys())

    for i in range(0, len(tickers), LOTE_QUOTES):
        lote = tickers[i:i + LOTE_QUOTES]
        tickers_hg = ",".join(f"B3:{t}" for t in lote)
        try:
            dados = requisitar(f"{BASE_HG}/quotes", {
                "format": "json-cors",
                "tickers": tickers_hg,
                "key": CHAVE_HG,
            })
        except Exception as e:  # noqa: BLE001
            log(f"  lote {i}-{i+len(lote)} falhou, pulando: {e}")
            continue

        for resultado in dados.get("results", []):
            simbolo = resultado.get("symbol")
            registro = por_ticker.get(simbolo)
            if not registro:
                continue
            registro["nome"] = resultado.get("name") or simbolo
            quote = resultado.get("quote") or {}
            registro["preco"] = quote.get("value")
            registro["variacao_dia_pct"] = quote.get("change_percent")
            # Só usa o valor de mercado da HG como fallback — o do
            # Fundamentus já é o "oficial" pra manter consistência com o
            # P/VP (os dois vêm da mesma fonte, mesma data-base).
            if registro.get("patrimonio_liquido") is None:
                registro["patrimonio_liquido"] = quote.get("market_cap")

        time.sleep(PAUSA_ENTRE_LOTES)

    return [f for f in fiis if f.get("preco") is not None]


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    agora = datetime.now(timezone.utc).isoformat()

    log("=== Ações ===")
    universo_acoes = buscar_universo_acoes()
    acoes = enriquecer_acoes_com_fundamentals(universo_acoes)
    with open(SAIDA_ACOES, "w", encoding="utf-8") as f:
        json.dump({"atualizado_em": agora, "ativos": acoes}, f, ensure_ascii=False, indent=2)
    log(f"Gravado {SAIDA_ACOES} com {len(acoes)} ações")

    log("=== FIIs ===")
    fiis_fundamentus = buscar_fiis_fundamentus()
    fiis = enriquecer_fiis_com_quotes(fiis_fundamentus)
    with open(SAIDA_FIIS, "w", encoding="utf-8") as f:
        json.dump({"atualizado_em": agora, "ativos": fiis}, f, ensure_ascii=False, indent=2)
    log(f"Gravado {SAIDA_FIIS} com {len(fiis)} FIIs")


if __name__ == "__main__":
    main()
