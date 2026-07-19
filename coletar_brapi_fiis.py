#!/usr/bin/env python3
"""
coletar_brapi_fiis.py
----------------------
Busca as últimas notícias do mercado financeiro (feed RSS) e salva o
resultado em noticias.json, na raiz do repositório. É o arquivo que o
Boletim de Mercado Viver de Renda (index.html) exibe no lado direito.

Nada aqui depende de token da brapi — o nome do arquivo ficou por
compatibilidade histórica com uma versão anterior que também coletava FIIs
via API, mas essa parte foi removida porque:
1) o boletim de FIIs não é mais exibido no site;
2) fundamentos de FIIs via /api/quote (fundamental=true/dividends=true)
   exigem o plano Pro da brapi — no Free/Startup a API retorna 403.

Uso local (opcional, para testar):
    python coletar_brapi_fiis.py
"""

import json
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


def main():
    noticias = coletar_noticias()

    indices = coletar_indices()
    if indices:
        with open(INDICES_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(indices, f, ensure_ascii=False, indent=2)
        print(f"OK: {len(indices)} índices salvos em {INDICES_OUTPUT_FILE}.")
    else:
        print("AVISO: nenhum índice coletado. Mantendo arquivo anterior, se existir.", file=sys.stderr)

    if not noticias:
        print("ERRO: nenhum dos feeds configurados retornou notícias. Mantendo arquivo anterior, se existir.", file=sys.stderr)
        sys.exit(1)

    with open(NOTICIAS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(noticias, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(noticias)} notícias salvas em {NOTICIAS_OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
