#!/usr/bin/env python3
"""
coletar_brapi_fiis.py
----------------------
Robô que roda no GitHub Actions e coleta os dados exibidos no site (nunca
diretamente no navegador do visitante), publicando os seguintes arquivos
na raiz do repositório:

- noticias.json : últimas notícias do mercado (feed RSS)
- indices.json  : Ibovespa, IFIX, Dólar, Euro, Bitcoin (Yahoo Finance) +
                   IPCA mensal/acumulado no ano (Banco Central) + CPI EUA (BLS)
- ranking.json  : 6 melhores ações e 6 melhores FIIs do momento (brapi)

Apenas o ranking.json depende de um token da brapi (variável de ambiente
BRAPI_TOKEN, configurada como secret no GitHub Actions) — os demais usam
fontes públicas sem autenticação. O token nunca fica no HTML nem é exposto
ao navegador do visitante.

Uso local (opcional, para testar):
    export BRAPI_TOKEN="seu_token_aqui"   # opcional, só afeta o ranking
    python coletar_brapi_fiis.py
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date

import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

NOTICIAS_OUTPUT_FILE = "noticias.json"
INDICES_OUTPUT_FILE = "indices.json"
RANKING_OUTPUT_FILE = "ranking.json"

# Tenta cada feed nesta ordem até conseguir pelo menos 1 notícia.
# Alguns provedores (ex: InfoMoney) às vezes bloqueiam requisições vindas
# de servidores/datacenters (como o do GitHub Actions), então mantemos
# alternativas para não deixar o boletim sem notícias.
NOTICIAS_FEEDS = [
    ("InfoMoney", "https://www.infomoney.com.br/mercados/feed/"),
    ("G1 Economia", "https://g1.globo.com/dynamo/economia/rss2.xml"),
    ("Money Times", "https://www.moneytimes.com.br/feed/"),
]
NOTICIAS_QTD = 3
TIMEOUT = 20

HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.9",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

# Tickers do Yahoo Finance para os índices/câmbio/cripto do boletim.
# O Yahoo não libera CORS para chamadas direto do navegador, então
# buscamos aqui no robô (servidor) e publicamos em indices.json.
YAHOO_TICKERS = [
    ("Ibovespa", "^BVSP", "pontos"),
    ("IFIX", "IFIX.SA", "pontos"),
    ("Dólar", "BRL=X", "R$ "),
    ("Euro", "EURBRL=X", "R$ "),
    ("Bitcoin (USD)", "BTC-USD", "US$ "),
]
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# IPCA: variação mensal, via API pública do Banco Central (série SGS 433)
BCB_IPCA_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/1?formato=json"

# IPCA acumulado no ano: não existe uma série pronta pra isso no BCB, então
# compomos os valores mensais (série 433) desde janeiro do ano corrente.
BCB_IPCA_ANO_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"

# CPI (EUA): índice de preços ao consumidor, via API pública do BLS
BLS_CPI_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0"

# Rankings de ativos, via brapi:
# - Ações: Maiores Dividend Yield, Maiores Valor de Mercado, Maiores Receita (5 cada)
# - FIIs: 6 melhores do momento (maior variação % do dia)
# O endpoint /api/quote/list só ordena por close/change/volume/market_cap_basic —
# não tem "ordenar por Dividend Yield" nem "por Receita" prontos. Por isso
# buscamos um pool das ações mais líquidas e calculamos os rankings de DY e
# Receita aqui, buscando os fundamentos de cada uma individualmente.
BRAPI_LIST_URL = "https://brapi.dev/api/quote/list"
BRAPI_QUOTE_URL = "https://brapi.dev/api/quote"
RANKING_QTD = 6
RANKING_ACOES_QTD = 5
POOL_ACOES_TAMANHO = 25  # quantas ações líquidas usamos como base p/ DY e Receita


def _extrair_imagem_do_item(item):
    """Tenta achar uma imagem para a notícia em diferentes formatos de RSS:
    <enclosure>, <media:content>/<media:thumbnail> ou <img> dentro da descrição."""
    ns_media = "{http://search.yahoo.com/mrss/}"

    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        return enclosure.get("url")

    media_content = item.find(f"{ns_media}content")
    if media_content is not None and media_content.get("url"):
        return media_content.get("url")

    media_thumb = item.find(f"{ns_media}thumbnail")
    if media_thumb is not None and media_thumb.get("url"):
        return media_thumb.get("url")

    descricao = item.findtext("description") or ""
    match = re.search(r'<img[^>]+src="([^"]+)"', descricao)
    if match:
        return match.group(1)

    return None


def _buscar_feed(nome_fonte, url):
    """Busca e faz parse de um feed RSS específico. Retorna lista de notícias
    (pode ser vazia) ou lança exceção em caso de falha de rede/parse."""
    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS_NAVEGADOR)
    resp.raise_for_status()
    raiz = ET.fromstring(resp.content)

    itens = raiz.findall("./channel/item")[:NOTICIAS_QTD]
    noticias = []

    for item in itens:
        titulo = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        data_pub = (item.findtext("pubDate") or "").strip()
        imagem = _extrair_imagem_do_item(item)

        if not titulo or not link:
            continue

        noticias.append({
            "titulo": titulo,
            "link": link,
            "imagem": imagem,
            "fonte": nome_fonte,
            "publicado_em": data_pub,
        })

    return noticias


def coletar_noticias():
    """Tenta cada feed configurado em NOTICIAS_FEEDS, na ordem, até conseguir
    pelo menos uma notícia. Retorna lista de dicts prontos para o boletim."""
    for nome_fonte, url in NOTICIAS_FEEDS:
        try:
            noticias = _buscar_feed(nome_fonte, url)
            if noticias:
                print(f"OK: {len(noticias)} notícias obtidas de {nome_fonte}.")
                return noticias
            print(f"AVISO: feed de {nome_fonte} respondeu, mas sem itens úteis.", file=sys.stderr)
        except (requests.RequestException, ET.ParseError) as exc:
            print(f"AVISO: falha ao buscar notícias de {nome_fonte} ({url}): {exc}", file=sys.stderr)

    return []


def _buscar_yahoo(nome, ticker, prefixo):
    """Busca preço atual e variação % do dia de um ticker no Yahoo Finance."""
    url = YAHOO_CHART_URL.format(ticker=ticker)
    resp = requests.get(url, params={"interval": "1d", "range": "5d"},
                         headers=HEADERS_NAVEGADOR, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    resultado = data.get("chart", {}).get("result")
    if not resultado:
        raise ValueError(f"Yahoo Finance não retornou dados para {ticker}")

    meta = resultado[0].get("meta", {})
    preco = meta.get("regularMarketPrice")
    fechamento_anterior = meta.get("previousClose") or meta.get("chartPreviousClose")

    variacao = None
    if isinstance(preco, (int, float)) and isinstance(fechamento_anterior, (int, float)) and fechamento_anterior:
        variacao = (preco - fechamento_anterior) / fechamento_anterior * 100

    return {
        "label": nome,
        "prefixo": prefixo,
        "valor": preco,
        "variacao_pct": variacao,
    }


def coletar_indices_mercado():
    """Busca Ibovespa, Dólar, Euro e Bitcoin (USD) no Yahoo Finance."""
    indices = []
    for nome, ticker, prefixo in YAHOO_TICKERS:
        try:
            indices.append(_buscar_yahoo(nome, ticker, prefixo))
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao buscar {nome} ({ticker}) no Yahoo Finance: {exc}", file=sys.stderr)
    return indices


def coletar_ipca():
    """Variação mensal do IPCA, via API pública do Banco Central (série SGS 433)."""
    try:
        resp = requests.get(BCB_IPCA_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        if not dados:
            return None
        item = dados[-1]
        valor = float(item["valor"].replace(",", "."))
        return {"label": "IPCA (mensal)", "valor_pct": valor, "referencia": item.get("data")}
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print(f"AVISO: falha ao buscar IPCA no Banco Central: {exc}", file=sys.stderr)
        return None


def coletar_ipca_acumulado_ano():
    """IPCA acumulado no ano corrente, compondo os valores mensais (série SGS 433)
    de janeiro até o mês mais recente disponível. Não existe uma série pronta
    para o acumulado do ano no BCB, então o cálculo é feito aqui."""
    try:
        hoje = date.today()
        params = {
            "formato": "json",
            "dataInicial": f"01/01/{hoje.year}",
            "dataFinal": hoje.strftime("%d/%m/%Y"),
        }
        resp = requests.get(BCB_IPCA_ANO_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        if not dados:
            return None

        fator_acumulado = 1.0
        for item in dados:
            valor_mes = float(item["valor"].replace(",", ".")) / 100
            fator_acumulado *= (1 + valor_mes)

        acumulado_pct = (fator_acumulado - 1) * 100
        ultimo_mes = dados[-1].get("data")
        return {
            "label": "IPCA (acum. ano)",
            "valor_pct": acumulado_pct,
            "referencia": ultimo_mes,
        }
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print(f"AVISO: falha ao calcular IPCA acumulado no ano: {exc}", file=sys.stderr)
        return None


def coletar_cpi_eua():
    """Variação mensal do CPI (EUA), via API pública do BLS (Bureau of Labor Statistics)."""
    try:
        ano_atual = date.today().year
        params = {"startyear": str(ano_atual - 1), "endyear": str(ano_atual)}
        resp = requests.get(BLS_CPI_URL, params=params, timeout=TIMEOUT,
                             headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        dados = resp.json()
        serie = dados.get("Results", {}).get("series", [])
        pontos = serie[0].get("data", []) if serie else []
        if len(pontos) < 2:
            return None

        # A API retorna do mais recente para o mais antigo
        atual, anterior = float(pontos[0]["value"]), float(pontos[1]["value"])
        variacao = (atual - anterior) / anterior * 100
        return {"label": "CPI (EUA, mensal)", "valor_pct": variacao, "referencia": pontos[0].get("periodName", "")}
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print(f"AVISO: falha ao buscar CPI dos EUA no BLS: {exc}", file=sys.stderr)
        return None


def coletar_indices():
    """Monta a lista completa de índices do boletim: mercado (Yahoo) + macro (BCB/BLS)."""
    indices = coletar_indices_mercado()

    ipca = coletar_ipca()
    if ipca:
        indices.append(ipca)

    ipca_ano = coletar_ipca_acumulado_ano()
    if ipca_ano:
        indices.append(ipca_ano)

    cpi = coletar_cpi_eua()
    if cpi:
        indices.append(cpi)

    return indices


def obter_token_brapi():
    """Token da brapi, lido da variável de ambiente BRAPI_TOKEN (secret do
    GitHub Actions). Só usado aqui no servidor — nunca fica no HTML."""
    return os.environ.get("BRAPI_TOKEN", "").strip()


def _buscar_ranking_brapi(tipo, token, quantidade=RANKING_QTD):
    """Busca os ativos com maior variação % do dia via /api/quote/list."""
    params = {
        "type": tipo,
        "sortBy": "change",
        "sortOrder": "desc",
        "limit": quantidade,
        "page": 1,
        "token": token,
    }
    resp = requests.get(BRAPI_LIST_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    ativos = data.get("stocks") or []

    ranking = []
    for ativo in ativos[:quantidade]:
        ranking.append({
            "ticker": ativo.get("stock"),
            "nome": ativo.get("name") or "",
            "preco": ativo.get("close"),
            "variacao_pct": ativo.get("change"),
        })
    return ranking


def _obter_pool_acoes_liquidas(token, tamanho=POOL_ACOES_TAMANHO):
    """Lista as ações mais líquidas (maior volume) para servir de base aos
    rankings de Dividend Yield e Receita, que a brapi não deixa ordenar direto."""
    params = {
        "type": "stock",
        "sortBy": "volume",
        "sortOrder": "desc",
        "limit": tamanho,
        "page": 1,
        "token": token,
    }
    resp = requests.get(BRAPI_LIST_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return [item.get("stock") for item in (data.get("stocks") or []) if item.get("stock")]


def _buscar_fundamentos_acao(ticker, token):
    """Busca preço, Dividend Yield, valor de mercado e receita total de uma ação."""
    url = f"{BRAPI_QUOTE_URL}/{ticker}"
    params = {"modules": "defaultKeyStatistics,financialData", "token": token}
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    ativo = (data.get("results") or [None])[0]
    if not ativo:
        return None

    stats = ativo.get("defaultKeyStatistics") or {}
    financeiro = ativo.get("financialData") or {}
    dy = ativo.get("dividendYield") or stats.get("dividendYield")

    return {
        "ticker": ativo.get("symbol"),
        "nome": ativo.get("longName") or ativo.get("shortName") or "",
        "preco": ativo.get("regularMarketPrice"),
        "dividend_yield_pct": dy,
        "market_cap": ativo.get("marketCap"),
        "receita_total": financeiro.get("totalRevenue"),
    }


def coletar_rankings_acoes(token, tamanho_pool=POOL_ACOES_TAMANHO, qtd=RANKING_ACOES_QTD):
    """Monta os 3 rankings de ações: Maiores Dividend Yield, Maiores Valor de
    Mercado e Maiores Receita, com base num pool das ações mais líquidas."""
    tickers = _obter_pool_acoes_liquidas(token, tamanho_pool)
    pool = []

    for ticker in tickers:
        try:
            fundamentos = _buscar_fundamentos_acao(ticker, token)
            if fundamentos:
                pool.append(fundamentos)
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao buscar fundamentos de {ticker}: {exc}", file=sys.stderr)

    def _top(campo):
        candidatos = [a for a in pool if isinstance(a.get(campo), (int, float))]
        candidatos.sort(key=lambda a: a[campo], reverse=True)
        return [{
            "ticker": a["ticker"],
            "nome": a["nome"],
            "preco": a["preco"],
            "valor": a[campo],
        } for a in candidatos[:qtd]]

    return {
        "dividend_yield": _top("dividend_yield_pct"),
        "valor_mercado": _top("market_cap"),
        "receita": _top("receita_total"),
    }


def coletar_ranking():
    """Monta o ranking completo do boletim: 3 rankings de ações (DY, valor de
    mercado, receita — 5 cada) e o ranking de FIIs (6 melhores do momento).
    Retorna vazio se o token não estiver configurado ou a API falhar — nesse
    caso o front-end mantém o ranking anterior."""
    token = obter_token_brapi()
    if not token:
        print("AVISO: BRAPI_TOKEN não configurado — pulando rankings de ações/FIIs.", file=sys.stderr)
        return {"acoes": {"dividend_yield": [], "valor_mercado": [], "receita": []}, "fiis": []}

    resultado = {"acoes": {"dividend_yield": [], "valor_mercado": [], "receita": []}, "fiis": []}

    try:
        resultado["acoes"] = coletar_rankings_acoes(token)
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"AVISO: falha ao montar rankings de ações: {exc}", file=sys.stderr)

    try:
        resultado["fiis"] = _buscar_ranking_brapi("fund", token)
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"AVISO: falha ao buscar ranking de FIIs: {exc}", file=sys.stderr)

    return resultado


def main():
    noticias = coletar_noticias()

    indices = coletar_indices()
    if indices:
        with open(INDICES_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(indices, f, ensure_ascii=False, indent=2)
        print(f"OK: {len(indices)} índices salvos em {INDICES_OUTPUT_FILE}.")
    else:
        print("AVISO: nenhum índice coletado. Mantendo arquivo anterior, se existir.", file=sys.stderr)

    ranking = coletar_ranking()
    acoes_r = ranking.get("acoes", {})
    total_acoes = len(acoes_r.get("dividend_yield", [])) + len(acoes_r.get("valor_mercado", [])) + len(acoes_r.get("receita", []))
    total_fiis = len(ranking.get("fiis", []))

    if total_acoes or total_fiis:
        with open(RANKING_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(ranking, f, ensure_ascii=False, indent=2)
        print(f"OK: ranking salvo em {RANKING_OUTPUT_FILE} "
              f"({total_acoes} entradas de ações, {total_fiis} FIIs).")
    else:
        print("AVISO: ranking vazio. Mantendo arquivo anterior, se existir.", file=sys.stderr)

    if not noticias:
        print("ERRO: nenhum dos feeds configurados retornou notícias. Mantendo arquivo anterior, se existir.", file=sys.stderr)
        sys.exit(1)

    with open(NOTICIAS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(noticias, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(noticias)} notícias salvas em {NOTICIAS_OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
