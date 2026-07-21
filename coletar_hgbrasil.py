#!/usr/bin/env python3
"""
coletar_hgbrasil.py
----------------------
Robô que roda no GitHub Actions e coleta os dados exibidos no site (nunca
diretamente no navegador do visitante), publicando os seguintes arquivos
na raiz do repositório:

- noticias.json : últimas notícias do mercado (feed RSS, sem token)
- indices.json  : Ibovespa, IFIX, Dólar, Euro, Bitcoin, Selic (HG Brasil) +
                   IPCA mensal/acumulado no ano (Banco Central) + CPI EUA (BLS)
- ranking.json  : 6 melhores ações e 6 melhores FIIs do momento (HG Brasil)

Todas as cotações (índices e ranking) agora usam o MESMO token da HG Brasil
(variável de ambiente HGBRASIL_TOKEN, configurada como secret no GitHub
Actions) — o mesmo provedor já usado no widget de busca do index.html.
IPCA/CPI continuam vindo de fontes públicas sem token (BCB/BLS), pois a HG
Brasil não cobre esses indicadores. O token nunca fica no HTML nem é
exposto ao navegador do visitante.

Uso local (opcional, para testar):
    export HGBRASIL_TOKEN="seu_token_aqui"   # afeta índices e ranking
    python coletar_hgbrasil.py
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
    ("InfoMoney (geral)", "https://www.infomoney.com.br/feed/"),
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

# ---------------------------------------------------------------------------
# HG Brasil — mesma chave/token usado em todo o projeto (site e robô)
# ---------------------------------------------------------------------------

# Endpoint "default": em UM único request devolve câmbio (USD/EUR), Bitcoin
# (nas principais corretoras, já em USD), Ibovespa, IFIX e Selic/CDI.
# Substitui as antigas chamadas ao Yahoo Finance + Selic no Banco Central.
HGBRASIL_INDICES_URL = "https://api.hgbrasil.com/finance"

# Endpoint v2 de cotações (ações/FIIs), aceita múltiplos tickers separados
# por vírgula no formato "B3:PETR4,B3:VALE3" e parâmetro `sort` (volume,
# value ou change_percent). Não existe "listar o mercado todo": por isso
# usamos um pool fixo de tickers líquidos como universo de busca.
HGBRASIL_QUOTES_URL = "https://api.hgbrasil.com/v2/finance/quotes"

# Endpoint v2 de DRE (Beta) — usado só para "receita" (TTM) no ranking de
# ações. Requer plano compatível com endpoints Beta; se a chave não tiver
# acesso, o ranking de receita fica vazio sem quebrar o resto do boletim.
HGBRASIL_INCOME_URL = "https://api.hgbrasil.com/v2/finance/income-statements"

# IPCA: variação mensal, via API pública do Banco Central (série SGS 433)
BCB_IPCA_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/1?formato=json"

# IPCA acumulado no ano: não existe uma série pronta pra isso no BCB, então
# compomos os valores mensais (série 433) desde janeiro do ano corrente.
BCB_IPCA_ANO_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"

# CPI (EUA): índice de preços ao consumidor, via API pública do BLS
BLS_CPI_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0"

# Pool fixo de ações e FIIs líquidos, usado como universo para os rankings
# (Dividend Yield, Valor de Mercado, Receita e "mais negociados"). Ajuste
# essas listas à vontade para incluir/trocar ativos específicos.
POOL_ACOES = [
    "PETR4", "VALE3", "ITUB4", "BBDC4", "B3SA3", "ABEV3", "WEGE3", "BBAS3",
    "RENT3", "SUZB3", "GGBR4", "RADL3", "EQTL3", "PRIO3", "RAIL3", "CSNA3",
    "ELET3", "CPLE6", "SBSP3", "CMIG4", "BBSE3", "VIVT3", "TAEE11", "AXIA3",
    "ALOS3",
]
POOL_FIIS = [
    "KNCR11", "CPTS11", "RECR11", "HGLG11", "VILG11", "VISC11", "MXRF11",
    "XPML11", "BTLG11", "HFOF11", "KNSC11", "VGIR11", "GARE11", "TRXF11",
    "IRIM11", "ALZR11", "XPCA11", "BTHF11", "MCCI11", "XPLG11",
]
RANKING_FIIS_QTD = 6
RANKING_ACOES_QTD = 6




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


def _data_bcb(data_iso):
    """Converte 'AAAA-MM-DD' (formato da HG Brasil) para 'DD/MM/AAAA' (mesmo
    padrão usado pelas séries do Banco Central no restante do boletim)."""
    try:
        ano, mes, dia = data_iso.split("-")
        return f"{dia}/{mes}/{ano}"
    except (AttributeError, ValueError):
        return data_iso


def coletar_indices_hgbrasil(token):
    """Busca, num único request à HG Brasil, Ibovespa, IFIX, Dólar, Euro,
    Bitcoin (USD) e Selic — mesma fonte/token usados no widget do site."""
    resp = requests.get(HGBRASIL_INDICES_URL, params={"key": token}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    # A HG Brasil pode responder HTTP 200 mesmo com chave recusada (ex: chave
    # restrita a um domínio, sendo usada aqui no servidor/GitHub Actions, que
    # não tem domínio/referer de navegador). Nesse caso "results" pode vir
    # vazio silenciosamente. Detectamos isso explicitamente para não mascarar
    # o problema.
    valida = data.get("valid_key")
    if valida is False:
        raise ValueError(f"Chave HG Brasil recusada no endpoint 'finance' (server-side). Resposta: {json.dumps(data, ensure_ascii=False)[:500]}")

    resultados = data.get("results")
    if not isinstance(resultados, dict) or not resultados:
        print(
            "AVISO: resposta da HG Brasil ('finance') veio sem 'results' utilizável. "
            f"Resposta bruta: {json.dumps(data, ensure_ascii=False)[:500]}",
            file=sys.stderr,
        )
        resultados = {}

    indices = []

    moedas = resultados.get("currencies") or {}
    usd = moedas.get("USD") or {}
    eur = moedas.get("EUR") or {}
    if usd.get("buy") is not None:
        indices.append({"label": "Dólar", "prefixo": "R$ ", "valor": usd.get("buy"),
                         "variacao_pct": usd.get("variation")})
    if eur.get("buy") is not None:
        indices.append({"label": "Euro", "prefixo": "R$ ", "valor": eur.get("buy"),
                         "variacao_pct": eur.get("variation")})

    mercados = resultados.get("stocks") or {}
    ibov = mercados.get("IBOVESPA") or {}
    ifix = mercados.get("IFIX") or {}
    nasdaq = mercados.get("NASDAQ") or {}
    dow = mercados.get("DOWJONES") or {}
    if ibov.get("points") is not None:
        indices.insert(0, {"label": "Ibovespa", "prefixo": "pontos", "valor": ibov.get("points"),
                            "variacao_pct": ibov.get("variation")})
    if ifix.get("points") is not None:
        indices.append({"label": "IFIX", "prefixo": "pontos", "valor": ifix.get("points"),
                         "variacao_pct": ifix.get("variation")})
    if dow.get("points") is not None:
        indices.append({"label": "Dow Jones", "prefixo": "pontos", "valor": dow.get("points"),
                         "variacao_pct": dow.get("variation")})
    if nasdaq.get("points") is not None:
        indices.append({"label": "Nasdaq", "prefixo": "pontos", "valor": nasdaq.get("points"),
                         "variacao_pct": nasdaq.get("variation")})

    # Bitcoin: usamos uma corretora cotada em USD (a HG cota BTC/BRL por
    # padrão em "currencies", mas o boletim exibe em dólar).
    bitcoin = resultados.get("bitcoin") or {}
    btc_usd = bitcoin.get("bitstamp") or bitcoin.get("blockchain_info") or {}
    if btc_usd.get("last") is not None:
        indices.append({"label": "Bitcoin (USD)", "prefixo": "US$ ", "valor": btc_usd.get("last"),
                         "variacao_pct": btc_usd.get("variation")})

    taxas = resultados.get("taxes") or []
    if taxas:
        ultima_taxa = taxas[-1]
        if ultima_taxa.get("selic") is not None:
            indices.append({
                "label": "Selic (meta)",
                "valor_pct": ultima_taxa.get("selic"),
                "referencia": _data_bcb(ultima_taxa.get("date")),
            })

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


def coletar_indices(token):
    """Monta a lista completa de índices do boletim: mercado + Selic (HG
    Brasil, num único request) + macro (BCB/BLS). A Selic é reposicionada
    logo após o Ibovespa, na posição tradicional do boletim."""
    if not token:
        print("AVISO: HGBRASIL_TOKEN não configurado — pulando índices de mercado (Ibovespa, IFIX, Dólar, Euro, Bitcoin, Selic).", file=sys.stderr)
        indices = []
    else:
        try:
            indices = coletar_indices_hgbrasil(token)
            if not indices:
                print(
                    "AVISO: nenhum índice de mercado (Ibovespa/Dólar/Euro/Bitcoin/Selic) "
                    "veio da HG Brasil — provavelmente a chave não tem acesso ao endpoint "
                    "'finance' nesse contexto (server-side). Confira em console.hgbrasil.com "
                    "se a chave usada em HGBRASIL_TOKEN é do tipo servidor/sem restrição de "
                    "domínio (diferente da chave 'uso exposto' embutida no index.html).",
                    file=sys.stderr,
                )
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao buscar índices na HG Brasil: {exc}", file=sys.stderr)
            indices = []

    # Reordena a Selic para logo depois do Ibovespa, se ambos existirem.
    selic = next((item for item in indices if item.get("label") == "Selic (meta)"), None)
    if selic:
        indices.remove(selic)
        posicao_ibovespa = next(
            (i for i, item in enumerate(indices) if item.get("label") == "Ibovespa"), None
        )
        indices.insert((posicao_ibovespa + 1) if posicao_ibovespa is not None else 0, selic)

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


def obter_token_hgbrasil():
    """Token da HG Brasil, lido da variável de ambiente HGBRASIL_TOKEN (secret
    do GitHub Actions). Mesmo provedor usado no widget de busca do site — só
    usado aqui no servidor, nunca fica no HTML."""
    return os.environ.get("HGBRASIL_TOKEN", "").strip()


def _tickers_b3(simbolos):
    """Formata uma lista de símbolos ('PETR4') no padrão exigido pela HG
    Brasil ('B3:PETR4'), separados por vírgula."""
    return ",".join(f"B3:{s}" for s in simbolos)


def _extrair_patrimonio(ativo):
    """Tenta localizar o campo de patrimônio líquido/valor patrimonial do
    fundo na resposta da HG Brasil, testando os nomes mais prováveis. Se
    nenhum bater, cai para market_cap (valor de mercado) como aproximação.
    DEBUG: veja no log do Actions o 'DEBUG: campos disponíveis...' abaixo
    para conferir/ajustar o nome exato do campo, se necessário."""
    quote = ativo.get("quote") or {}
    fund = ativo.get("fund") or ativo.get("fii") or {}
    for fonte in (fund, ativo, quote):
        for chave in ("net_worth", "patrimonio_liquido", "patrimony", "equity", "book_value"):
            valor = fonte.get(chave)
            if isinstance(valor, (int, float)):
                return valor
    return quote.get("market_cap")


def _buscar_fundamentos_fiis(pool, token):
    """Busca, num único request, preço, variação, Dividend Yield (12m) e
    valor patrimonial de todo o pool de FIIs via /v2/finance/quotes."""
    params = {"tickers": _tickers_b3(pool), "key": token}
    resp = requests.get(HGBRASIL_QUOTES_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    ativos = data.get("results") or []

    if ativos:
        print(
            f"DEBUG: campos disponíveis num FII de exemplo ({ativos[0].get('symbol')}): "
            f"{json.dumps(ativos[0], ensure_ascii=False)[:800]}",
            file=sys.stderr,
        )
    else:
        print(
            "DEBUG: /v2/finance/quotes (FIIs) voltou sem 'results'. Resposta bruta: "
            f"{json.dumps(data, ensure_ascii=False)[:800]}",
            file=sys.stderr,
        )

    fundamentos = {}
    for ativo in ativos:
        symbol = ativo.get("symbol")
        if not symbol:
            continue
        quote = ativo.get("quote") or {}
        dividendos = ativo.get("dividends") or {}
        fundamentos[symbol] = {
            "ticker": symbol,
            "nome": ativo.get("name") or "",
            "preco": quote.get("value"),
            "variacao_pct": quote.get("change_percent"),
            "volume": quote.get("volume"),
            "dividend_yield_pct": dividendos.get("yield_12m_percent"),
            "patrimonio": _extrair_patrimonio(ativo),
        }
    return fundamentos


def coletar_rankings_fiis(token, pool=None, qtd=RANKING_FIIS_QTD):
    """Monta os 3 rankings de FIIs exibidos lado a lado no site: Maiores
    Valor Patrimonial, Maiores Dividend Yield e Mais Negociados (volume do
    dia) — equivalente ao 'Mais Buscados' do Investidor10, mas usando um
    dado de mercado real (volume) em vez de popularidade de site, que não
    dá para obter via API de forma automática e confiável."""
    pool = pool or POOL_FIIS
    fundamentos = _buscar_fundamentos_fiis(pool, token)
    candidatos = list(fundamentos.values())

    def _top(campo):
        ordenados = [f for f in candidatos if isinstance(f.get(campo), (int, float))]
        ordenados.sort(key=lambda f: f[campo], reverse=True)
        return [{
            "ticker": f["ticker"],
            "nome": f["nome"],
            "preco": f["preco"],
            "variacao_pct": f["variacao_pct"],
            "valor": f[campo],
        } for f in ordenados[:qtd]]

    return {
        "valor_patrimonial": _top("patrimonio"),
        "dividend_yield": _top("dividend_yield_pct"),
        "mais_negociados": _top("volume"),
    }


def _buscar_fundamentos_acoes(pool, token):
    """Busca, num único request, preço, Dividend Yield (12m) e valor de
    mercado de todo o pool de ações via /v2/finance/quotes."""
    params = {"tickers": _tickers_b3(pool), "key": token}
    resp = requests.get(HGBRASIL_QUOTES_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    resultados_brutos = data.get("results") or []

    if not resultados_brutos:
        print(
            "DEBUG: /v2/finance/quotes (ações) voltou sem 'results'. Resposta bruta: "
            f"{json.dumps(data, ensure_ascii=False)[:800]}",
            file=sys.stderr,
        )

    fundamentos = {}
    for ativo in resultados_brutos:
        symbol = ativo.get("symbol")
        if not symbol:
            continue
        quote = ativo.get("quote") or {}
        dividendos = ativo.get("dividends") or {}
        fundamentos[symbol] = {
            "ticker": symbol,
            "nome": ativo.get("name") or "",
            "preco": quote.get("value"),
            "dividend_yield_pct": dividendos.get("yield_12m_percent"),
            "market_cap": quote.get("market_cap"),
            "receita_total": None,
        }
    return fundamentos


def _buscar_receita_acoes(pool, token):
    """Busca a receita TTM de cada ação do pool via /v2/finance/income-statements
    (endpoint Beta — requer plano compatível). Retorna {ticker: receita}."""
    params = {"tickers": _tickers_b3(pool), "period": "annual", "key": token}
    resp = requests.get(HGBRASIL_INCOME_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    receitas = {}
    for ativo in data.get("results") or []:
        symbol = ativo.get("symbol")
        statements = ativo.get("statements") or []
        if symbol and statements:
            receitas[symbol] = statements[0].get("revenue")  # TTM (ou mais recente)
    return receitas


def coletar_rankings_acoes(token, pool=None, qtd=RANKING_ACOES_QTD):
    """Monta os 3 rankings de ações: Maiores Dividend Yield, Maiores Valor de
    Mercado e Maiores Receita, com base no pool fixo de ações líquidas."""
    pool = pool or POOL_ACOES
    fundamentos = _buscar_fundamentos_acoes(pool, token)

    try:
        receitas = _buscar_receita_acoes(pool, token)
        for symbol, receita in receitas.items():
            if symbol in fundamentos:
                fundamentos[symbol]["receita_total"] = receita
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"AVISO: falha ao buscar receita (income-statements, endpoint Beta) — "
              f"ranking de receita ficará vazio: {exc}", file=sys.stderr)

    candidatos = list(fundamentos.values())

    def _top(campo):
        ordenados = [a for a in candidatos if isinstance(a.get(campo), (int, float))]
        ordenados.sort(key=lambda a: a[campo], reverse=True)
        return [{
            "ticker": a["ticker"],
            "nome": a["nome"],
            "preco": a["preco"],
            "valor": a[campo],
        } for a in ordenados[:qtd]]

    return {
        "dividend_yield": _top("dividend_yield_pct"),
        "valor_mercado": _top("market_cap"),
        "receita": _top("receita_total"),
    }


def coletar_ranking(token):
    """Monta o ranking completo do boletim: 3 rankings de ações (DY, valor de
    mercado, receita) e 3 rankings de FIIs (valor patrimonial, dividend
    yield, mais negociados) — 6 itens cada.
    Retorna vazio se o token não estiver configurado ou a API falhar — nesse
    caso o front-end mantém o ranking anterior."""
    vazio_fiis = {"valor_patrimonial": [], "dividend_yield": [], "mais_negociados": []}
    if not token:
        print("AVISO: HGBRASIL_TOKEN não configurado — pulando rankings de ações/FIIs.", file=sys.stderr)
        return {"acoes": {"dividend_yield": [], "valor_mercado": [], "receita": []}, "fiis": vazio_fiis}

    resultado = {"acoes": {"dividend_yield": [], "valor_mercado": [], "receita": []}, "fiis": vazio_fiis}

    try:
        resultado["acoes"] = coletar_rankings_acoes(token)
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"AVISO: falha ao montar rankings de ações: {exc}", file=sys.stderr)

    try:
        resultado["fiis"] = coletar_rankings_fiis(token)
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"AVISO: falha ao montar rankings de FIIs: {exc}", file=sys.stderr)

    return resultado


def main():
    token = obter_token_hgbrasil()
    noticias = coletar_noticias()

    indices = coletar_indices(token)
    if indices:
        with open(INDICES_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(indices, f, ensure_ascii=False, indent=2)
        print(f"OK: {len(indices)} índices salvos em {INDICES_OUTPUT_FILE}.")
    else:
        print("AVISO: nenhum índice coletado. Mantendo arquivo anterior, se existir.", file=sys.stderr)

    ranking = coletar_ranking(token)
    acoes_r = ranking.get("acoes", {})
    total_acoes = len(acoes_r.get("dividend_yield", [])) + len(acoes_r.get("valor_mercado", [])) + len(acoes_r.get("receita", []))
    fiis_r = ranking.get("fiis", {})
    total_fiis = len(fiis_r.get("valor_patrimonial", [])) + len(fiis_r.get("dividend_yield", [])) + len(fiis_r.get("mais_negociados", []))

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
