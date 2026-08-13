#!/usr/bin/env python3
"""
coletar_hgbrasil.py
----------------------
Robô que roda no GitHub Actions e coleta os dados exibidos no site (nunca
diretamente no navegador do visitante), publicando os seguintes arquivos
na raiz do repositório:

- noticias.json : últimas notícias do mercado (feed RSS, sem token)
- indices.json  : Ibovespa, IFIX (brapi), Dólar/Euro (BCB), Bitcoin (CoinGecko) +
                   IPCA mensal/acumulado no ano (Banco Central) + CPI EUA (BLS)
- ranking.json  : 6 melhores ações e 6 melhores FIIs do momento (HG Brasil)

Todas as cotações (índices e ranking) agora usam o MESMO token da HG Brasil
(variável de ambiente HGBRASIL_TOKEN, configurada como secret no GitHub
Actions) — o mesmo provedor já usado no widget de busca do index.html.
IPCA/CPI continuam vindo de fontes públicas sem token (BCB/BLS), pois a HG
Brasil não cobre esses indicadores. O token nunca fica no HTML nem é
exposto ao navegador do visitante.

A coleta de rankings (Ações/FIIs) é mais pesada em requisições do que
índices/notícias (vários lotes de tickers + endpoint Beta de receita), por
isso é opcional nesta execução: só roda com a flag --com-ranking (ou
variável de ambiente COLETAR_RANKING=1). Isso permite agendar índices e
notícias a cada 15 min, e os rankings só 1x por dia.

Uso local (opcional, para testar):
    export HGBRASIL_TOKEN="seu_token_aqui"   # afeta índices e ranking
    python coletar_hgbrasil.py                 # só índices + notícias
    python coletar_hgbrasil.py --com-ranking    # também atualiza os rankings
"""

import html
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

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
]

# Feeds RSS OFICIAIS do Investing.com Brasil (documentados em
# br.investing.com/webmaster-tools/rss), usados pra montar um "Top 3" mais
# variado — em vez de 3 manchetes da mesma fonte, tentamos 1 de cada
# categoria abaixo. O feed de FIIs é um diferencial: não é comum achar uma
# fonte só de fundos imobiliários com RSS aberto, e encaixa direto no foco
# do site (Viver de Renda).
NOTICIAS_FONTES_MISTAS = [
    ("Investing.com (FIIs)", "https://br.investing.com/rss/news_450.rss"),
    ("Investing.com (Ações)", "https://br.investing.com/rss/news_25.rss"),
    ("Investing.com (Economia)", "https://br.investing.com/rss/news_14.rss"),
]
NOTICIAS_QTD = 3
NOTICIAS_QTD_TOP3 = 5  # InfoMoney/Money Times — o site mostra até 5 dessas, só 1 do Investing.com
TIMEOUT = 20

# fiis.com.br não tem feed RSS público — a única forma de puxar notícia de
# lá é fazendo scraping da própria página de notícias. Pega as
# FIIS_NOTICIAS_QTD_TOPO primeiras da listagem e sorteia uma a cada coleta
# (em vez de sempre a 1ª), pra não ficar "travado" mostrando o mesmo
# destaque por dias quando o site demora a publicar algo novo. Fica fora do
# esquema de fallback dos outros feeds porque é fonte única: se falhar, o
# boletim segue sem esse item (nunca quebra o resto da coleta).
FIIS_NOTICIAS_URL = "https://fiis.com.br/noticias/"
FIIS_NOTICIAS_QTD_TOPO = 4

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
# ------------------------------------------------------------------
# bolsai (usebolsai.com) — usada nos rankings de FIIs. Fonte B3/CVM, com
# endpoint dedicado /fiis/{ticker} que traz P/VP, DY 12m e o patrimônio
# líquido REAL do fundo (net_asset_value). Isso é melhor que a HG Brasil
# nesse ponto específico: lá o patrimônio não vinha de forma confiável e
# o código caía para market_cap (valor de mercado) como aproximação.
# Diferente do resto do site, aqui a chave fica no servidor (secret do
# GitHub Actions), então não precisa passar pelo Cloudflare Worker.
# ------------------------------------------------------------------
BOLSAI_BASE_URL = "https://api.usebolsai.com/api/v1"
# Volume não vem no /fiis/{ticker}; é lido do histórico de preços, que
# também permite calcular a variação % do dia (últimos 2 fechamentos).
BOLSAI_PAUSA_ENTRE_CHAMADAS = 0.15  # segundos, para não saturar a API

# ------------------------------------------------------------------
# brapi (brapi.dev) — usada só para os índices da B3 (Ibovespa e IFIX).
# São 2 requisições por execução (o plano gratuito limita a 1 ticker por
# chamada em /stocks/quote), ~5,8 mil/mês — folgado no limite de 15 mil.
#
# Câmbio e cripto NÃO vêm da brapi: os endpoints /v2/currency e
# /v2/crypto respondem 403 FEATURE_NOT_AVAILABLE no plano gratuito.
# Ficam no Banco Central e na CoinGecko (abaixo).
#
# Dow Jones e Nasdaq foram removidos do boletim: a brapi cobre só o
# mercado brasileiro. CPI (EUA) vem do BLS, Selic/IPCA do Banco Central.
# ------------------------------------------------------------------
BRAPI_BASE_URL = "https://brapi.dev/api"
BRAPI_INDICES = [("^BVSP", "Ibovespa"), ("IFIX", "IFIX")]

# ------------------------------------------------------------------
# Câmbio: Banco Central (SGS), mesma fonte já usada para Selic e IPCA.
# Oficial, gratuita, sem chave e sem limite de requisições — resolve os
# 429 que a AwesomeAPI dava por cota compartilhada de IP no Actions.
#
# Série 1     = Dólar americano (venda, PTAX)
# Série 21619 = Euro (venda, PTAX)
#
# Buscamos as 2 últimas observações de cada série: a mais recente é o
# valor exibido, e a anterior serve para calcular a variação %. É PTAX
# (fechamento do dia), então durante o pregão o valor fica no fechamento
# anterior — e em fins de semana/feriados o BCB não publica, caso em que
# a última cotação disponível é reaproveitada.
# ------------------------------------------------------------------
BCB_CAMBIO_SERIES = [
    # (número da série SGS, rótulo no boletim)
    (1, "Dólar"),
    (21619, "Euro"),
]

# ------------------------------------------------------------------
# Cripto: CoinGecko — gratuita, sem chave. Devolve preço em BRL e a
# variação de 24h. Vários ativos vêm na MESMA requisição, então incluir
# o Ethereum ao lado do Bitcoin não custa chamada extra.
# ------------------------------------------------------------------
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MOEDAS = [
    # (id na CoinGecko, rótulo no boletim)
    ("bitcoin", "Bitcoin"),
    ("ethereum", "Ethereum"),
]

# ------------------------------------------------------------------
# Memória de notícias já publicadas. Sem isso, o robô sempre pegava o
# item do TOPO de cada feed — e feeds de baixo volume (como o de FIIs do
# Investing.com) ficam dias com a mesma manchete no topo, fazendo o
# boletim repetir a notícia. Guardamos os links usados nos últimos dias
# e descemos no feed até achar algo inédito.
# ------------------------------------------------------------------
NOTICIAS_HISTORICO_ARQUIVO = "noticias-historico.json"
NOTICIAS_HISTORICO_DIAS = 10      # por quanto tempo um link é considerado "já usado"
NOTICIAS_POOL_POR_FONTE = 12      # quantos itens olhar em cada feed procurando algo novo

# ------------------------------------------------------------------
# Tesouro Direto — CSV oficial do Tesouro Transparente (portal CKAN do
# Tesouro Nacional), dataset "Taxas dos Títulos Ofertados pelo Tesouro
# Direto". Gratuito, sem chave, atualizado todo dia útil.
#
# O endpoint JSON antigo (tesourodireto.com.br/.../treasurybondsinfo.json)
# foi DESCONTINUADO — responde 410 Gone. Este CSV é a fonte oficial que
# o restava, e é de onde os agregadores de mercado tiram os dados.
#
# O arquivo traz o histórico desde 2004, então é grande (centenas de MB).
# Por isso: (a) lemos em streaming, linha a linha, guardando só as da
# data mais recente; (b) a coleta roda 1x/dia junto com os rankings —
# baixar isso a cada 15 min seria desperdício, e o Tesouro publica as
# taxas uma vez por dia útil de qualquer forma.
#
# Formato (separador ";", decimais com vírgula):
#   Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;
#   Taxa Venda Manha;PU Compra Manha;PU Venda Manha;PU Base Manha
# ------------------------------------------------------------------
TESOURO_CSV_URL = ("https://www.tesourotransparente.gov.br/ckan/dataset/"
                   "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
                   "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/"
                   "precotaxatesourodireto.csv")
TESOURO_ARQUIVO = "tesouro.json"
TESOURO_TIMEOUT = 180          # o arquivo é grande; o timeout padrão não basta
# O Tesouro vende frações de 1% do título, então o mínimo é 1% do PU.
TESOURO_FRACAO_MINIMA = 0.01

BCB_IPCA_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/1?formato=json"

# IPCA acumulado em 12 meses (janela móvel, não calendário): compomos os
# últimos 12 valores mensais da série 433. É a métrica de inflação anual
# "de verdade" usada pelo mercado (bem diferente do acumulado desde
# janeiro, que amplifica demais quando anualizado no meio do ano).
BCB_IPCA_12M_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/12?formato=json"

# CPI (EUA): índice de preços ao consumidor, via API pública do BLS
BLS_CPI_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0"

# Selic (meta definida pelo Copom): via API pública do Banco Central (série
# SGS 432) em vez do campo "taxes" da HG Brasil — a HG Brasil demora demais
# para atualizar esse dado específico depois de reuniões do Copom (dias,
# às vezes mais), enquanto o BCB publica no mesmo dia da decisão.
BCB_SELIC_META_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/5?formato=json"

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
    <enclosure>, <media:content>/<media:thumbnail>, <img> dentro de
    <content:encoded> (comum em feeds WordPress, como o da Money Times) ou
    dentro de <description> — inclusive quando o HTML vem "escapado"
    (&lt;img ...&gt;) em vez de tags de verdade."""
    ns_media = "{http://search.yahoo.com/mrss/}"
    ns_content = "{http://purl.org/rss/1.0/modules/content/}"

    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        return enclosure.get("url")

    media_content = item.find(f"{ns_media}content")
    if media_content is not None and media_content.get("url"):
        return media_content.get("url")

    media_thumb = item.find(f"{ns_media}thumbnail")
    if media_thumb is not None and media_thumb.get("url"):
        return media_thumb.get("url")

    # content:encoded costuma trazer o HTML completo do post (incluindo a
    # imagem de destaque) em feeds WordPress — a Money Times é um caso
    # onde a <description> sozinha não tem a imagem, mas essa tag tem.
    conteudo_completo = item.findtext(f"{ns_content}encoded") or ""
    match = re.search(r'<img[^>]+src="([^"]+)"', conteudo_completo)
    if match:
        return match.group(1)

    descricao = item.findtext("description") or ""
    match = re.search(r'<img[^>]+src="([^"]+)"', descricao)
    if match:
        return match.group(1)

    # Alguns feeds "escapam" o HTML dentro da description (vira texto puro
    # com &lt;img ...&gt; em vez de tag de verdade) — tenta de novo depois
    # de desescapar as entidades HTML.
    descricao_desescapada = html.unescape(descricao)
    match = re.search(r'<img[^>]+src="([^"]+)"', descricao_desescapada)
    if match:
        return match.group(1)

    return None


def _buscar_imagem_og(link):
    """Fallback pra quando o RSS não traz imagem nenhuma (é o caso da
    Money Times): busca a própria página da notícia e extrai a tag
    og:image do HTML — toda página feita pra ser compartilhada em
    WhatsApp/redes sociais tem essa tag, então é uma fonte confiável.
    Timeout curto de propósito (não pode atrasar a coleta toda por causa
    de 1 imagem) e nunca lança exceção — se falhar, só fica sem imagem."""
    try:
        resp = requests.get(link, timeout=8, headers=HEADERS_NAVEGADOR)
        resp.raise_for_status()
        match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', resp.text)
        if not match:
            # A ordem dos atributos pode vir invertida (content antes de property)
            match = re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', resp.text)
        return match.group(1) if match else None
    except requests.RequestException:
        return None


# Trocado de "lista de bloqueio" pra "lista de permissão": em vez de tentar
# adivinhar toda palavra fora de tema possível (esporte, geopolítica,
# acidente, saúde... a lista nunca teria fim), só deixa passar notícia do
# feed "geral" do InfoMoney se o título bater com alguma palavra de
# mercado/finanças/tecnologia/IA. Mais confiável — não depende de prever
# cada categoria nova que possa aparecer misturada no feed.
PALAVRAS_MERCADO_TECH = [
    # Mercado / bolsa / ativos
    "ação", "ações", "bolsa", "ibovespa", "ibov", "b3", "fii", "fiis", "fiagro",
    "dividendo", "mercado", "investidor", "investimento", "renda fixa",
    "renda variável", "tesouro direto", "cdb", "lci", "lca", "debênture",
    "ipo", "follow-on", "ação da", "ações da", "papel da", "papéis da",
    # Macroeconomia
    "juros", "selic", "copom", "inflação", "ipca", "pib", "dólar", "dolar",
    "câmbio", "cambio", "commodities", "petróleo", "petroleo", "banco central",
    "fed ", "federal reserve", "economia", "fiscal", "déficit", "superávit",
    "tarifa", "tarifaço", "exportação", "importação",
    # Mercados internacionais
    "wall street", "nasdaq", "nyse", "s&p", "dow jones", "bitcoin",
    "criptomoeda", "cripto",
    # Empresas / resultados
    "balanço", "lucro líquido", "receita líquida", "faturamento", "fusão",
    "aquisição", "resultado do", "resultado da", "trimestre",
    # Tecnologia / IA
    "tecnologia", "inteligência artificial", " ia ", "startup", "chip",
    "semicondutor", "big tech", "nvidia", "openai", "google", "microsoft",
    "apple", "amazon", "tesla",
]


def _e_noticia_de_mercado(titulo):
    """True se o título bater com alguma palavra de PALAVRAS_MERCADO_TECH."""
    titulo_lower = f" {titulo.lower()} "
    return any(palavra in titulo_lower for palavra in PALAVRAS_MERCADO_TECH)



def carregar_historico_noticias():
    """Lê os links já publicados, descartando os mais antigos que
    NOTICIAS_HISTORICO_DIAS. Retorna (set_de_links, dict_link_para_data).

    Se o arquivo não existir (primeira execução) ou estiver corrompido,
    devolve vazio — o robô segue normalmente e cria o arquivo no fim."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), NOTICIAS_HISTORICO_ARQUIVO)
    try:
        with open(caminho, "r", encoding="utf-8") as arq:
            dados = json.load(arq)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set(), {}

    limite = datetime.now(timezone.utc) - timedelta(days=NOTICIAS_HISTORICO_DIAS)
    recentes = {}
    for link, quando in (dados.get("links") or {}).items():
        try:
            if datetime.fromisoformat(quando) >= limite:
                recentes[link] = quando
        except (ValueError, TypeError):
            continue
    return set(recentes), recentes


def salvar_historico_noticias(historico, links_novos):
    """Grava o histórico com os links usados nesta execução carimbados
    com a data de hoje. Falha de escrita não derruba o robô — no pior
    caso a próxima execução pode repetir uma notícia."""
    agora = datetime.now(timezone.utc).isoformat()
    for link in links_novos:
        historico[link] = agora

    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), NOTICIAS_HISTORICO_ARQUIVO)
    try:
        with open(caminho, "w", encoding="utf-8") as arq:
            json.dump({"links": historico}, arq, ensure_ascii=False, indent=2)
        print(f"OK: histórico de notícias salvo ({len(historico)} link(s) dos últimos {NOTICIAS_HISTORICO_DIAS} dias).")
    except OSError as exc:
        print(f"AVISO: não foi possível salvar o histórico de notícias: {exc}", file=sys.stderr)


def _buscar_feed(nome_fonte, url, qtd_maxima=NOTICIAS_QTD, filtrar_tema=False):
    """Busca e faz parse de um feed RSS específico. Retorna lista de notícias
    (pode ser vazia) ou lança exceção em caso de falha de rede/parse.

    filtrar_tema=True busca um "pool" maior de itens brutos (não só
    qtd_maxima) e descarta os fora do tema mercado/finanças ANTES de
    cortar pra qtd_maxima — senão, se as primeiras posições do feed
    fossem esporte/lifestyle, a gente perderia vaga à toa."""
    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS_NAVEGADOR)
    resp.raise_for_status()
    raiz = ET.fromstring(resp.content)

    pool = qtd_maxima * 8 if filtrar_tema else qtd_maxima
    itens_brutos = raiz.findall("./channel/item")[:pool]
    noticias = []

    for item in itens_brutos:
        if len(noticias) >= qtd_maxima:
            break

        titulo = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        data_pub = (item.findtext("pubDate") or "").strip()

        if not titulo or not link:
            continue
        if filtrar_tema and not _e_noticia_de_mercado(titulo):
            continue

        imagem = _extrair_imagem_do_item(item)
        if not imagem and link:
            imagem = _buscar_imagem_og(link)

        noticias.append({
            "titulo": titulo,
            "link": link,
            "imagem": imagem,
            "fonte": nome_fonte,
            "publicado_em": data_pub,
        })

    return noticias


def _buscar_noticia_fii():
    """Faz scraping das notícias em destaque de https://fiis.com.br/noticias/
    (o site não tem feed RSS público). Estrutura esperada (tema WordPress):
    um link de miniatura envolvendo <img alt="TÍTULO" src="IMAGEM">, seguido
    de um link igual dentro de um heading (<h3>/<h2>) com o texto do título.

    Pega as até FIIS_NOTICIAS_QTD_TOPO primeiras notícias da listagem e
    sorteia uma delas — em vez de sempre devolver a 1ª. Isso evita ficar
    "travado" mostrando a mesma notícia por dias quando o site fonte demora
    a publicar algo novo (a home dele muda de ordem/destaque bem devagar).

    Retorna um dict no mesmo formato dos outros itens de notícia, ou None se
    não conseguir extrair nada (nunca lança exceção pra fora — scraping de
    HTML de terceiros é frágil e não pode derrubar o resto da coleta)."""
    try:
        resp = requests.get(FIIS_NOTICIAS_URL, timeout=TIMEOUT, headers=HEADERS_NAVEGADOR)
        resp.raise_for_status()
        html_pagina = resp.text

        # 1) Links de miniatura: <a href="https://fiis.com.br/noticias/SLUG/">
        #    ... <img ... src="IMG" ... alt="TÍTULO" ...> ... </a>
        padrao_thumb = re.compile(
            r'<a[^>]+href="(https://fiis\.com\.br/noticias/[^"?#]+/)"[^>]*>\s*'
            r'<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"',
            re.IGNORECASE | re.DOTALL,
        )
        candidatos = []
        links_vistos = set()
        for match in padrao_thumb.finditer(html_pagina):
            link, imagem, titulo = match.group(1), match.group(2), match.group(3).strip()
            if link in links_vistos:
                continue  # a mesma notícia pode aparecer 2x na página (destaque + lista)
            links_vistos.add(link)

            # O "alt" da miniatura às vezes vem vazio ou genérico — nesse caso,
            # busca o título de verdade no heading que repete o mesmo link logo
            # depois (padrão: <h3><a href="MESMO_LINK">TÍTULO REAL</a></h3>).
            if not titulo:
                padrao_titulo = re.compile(
                    r'<a[^>]+href="' + re.escape(link) + r'"[^>]*>([^<]+)</a>',
                    re.IGNORECASE,
                )
                achou_titulo = padrao_titulo.search(html_pagina, match.end())
                if achou_titulo:
                    titulo = achou_titulo.group(1).strip()

            if not titulo or not link:
                continue

            candidatos.append({"titulo": titulo, "link": link, "imagem": imagem})
            if len(candidatos) >= FIIS_NOTICIAS_QTD_TOPO:
                break

        if not candidatos:
            print("AVISO: scraping do fiis.com.br não achou o padrão esperado (site pode ter mudado o layout).", file=sys.stderr)
            return None

        escolhida = random.choice(candidatos)

        # A listagem só mostra hora relativa ("Hoje às 12:50"), sem data
        # em formato utilizável — como não sabemos a hora real de publicação
        # de cada uma das candidatas, usamos o horário de coleta como
        # aproximação razoável.
        publicado_em = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "titulo": escolhida["titulo"],
            "link": escolhida["link"],
            "imagem": escolhida["imagem"],
            "fonte": "FIIs.com.br",
            "publicado_em": publicado_em,
        }
    except requests.RequestException as exc:
        print(f"AVISO: falha ao buscar notícia de FIIs em {FIIS_NOTICIAS_URL}: {exc}", file=sys.stderr)
        return None



def coletar_noticias():
    """Monta DOIS grupos de notícias separados, pra não repetir a mesma
    manchete em 'Destaques da Bolsa' e em 'Top 3 Notícias' no boletim:

    - "destaques": 1 notícia de cada categoria do Investing.com (FIIs,
      Ações, Economia) — até 3 no total.
    - "top3": até 3 notícias do InfoMoney/Money Times (os feeds antigos),
      sem repetir nenhum link que já tenha entrado em "destaques".

    Se alguma fonte falhar, cada grupo tenta se completar sozinho com as
    fontes do OUTRO grupo como reforço, na ordem em que aparecem — assim
    nunca fica faltando notícia por causa de 1 fonte fora do ar."""
    # Começa já sabendo o que saiu nos últimos dias, para não repetir.
    historico_links, historico = carregar_historico_noticias()
    links_ja_usados = set(historico_links)
    if historico_links:
        print(f"INFO: {len(historico_links)} link(s) dos últimos {NOTICIAS_HISTORICO_DIAS} dias serão evitados.")

    def _coletar_grupo(fontes, qtd_alvo, qtd_por_fonte=None):
        """qtd_por_fonte limita quantos itens ACEITAR de cada fonte, numa
        única passada. Isso é essencial pro grupo "misto" (Investing.com):
        sem esse limite, a primeira fonte que respondesse bem já preenchia
        sozinha as 3 vagas, e as outras 2 categorias nunca chegavam a ser
        consultadas.
        Quando qtd_por_fonte é None, cada fonte pode preencher a cota
        inteira sozinha (comportamento de "cadeia de fallback", usado nos
        feeds antigos do InfoMoney/Money Times — onde só queremos VARIAR
        de fonte se a anterior falhar, não misturar por mistura).

        Importante: o quanto LEMOS de cada feed (NOTICIAS_POOL_POR_FONTE)
        é maior que o quanto aceitamos. É isso que permite pular manchetes
        já publicadas em dias anteriores e descer até achar uma inédita —
        antes só olhávamos o topo, que em feeds de baixo volume fica dias
        sem mudar e fazia o boletim repetir a mesma notícia."""
        grupo = []
        for nome_fonte, url in fontes:
            if len(grupo) >= qtd_alvo:
                break
            aceitar_desta_fonte = qtd_por_fonte if qtd_por_fonte is not None else (qtd_alvo - len(grupo))
            try:
                itens = _buscar_feed(
                    nome_fonte, url, qtd_maxima=NOTICIAS_POOL_POR_FONTE,
                    filtrar_tema="(geral)" in nome_fonte,
                )
                aceitos = 0
                for item in itens:
                    if len(grupo) >= qtd_alvo or aceitos >= aceitar_desta_fonte:
                        break
                    if item["link"] not in links_ja_usados:
                        grupo.append(item)
                        links_ja_usados.add(item["link"])
                        aceitos += 1
                if aceitos:
                    print(f"OK: {aceitos} notícia(s) inédita(s) de {nome_fonte} (de {len(itens)} lidas).")
                elif itens:
                    print(f"AVISO: {nome_fonte} respondeu, mas todas as {len(itens)} notícias já foram publicadas antes.", file=sys.stderr)
                else:
                    print(f"AVISO: feed de {nome_fonte} respondeu, mas sem itens úteis.", file=sys.stderr)
            except (requests.RequestException, ET.ParseError) as exc:
                print(f"AVISO: falha ao buscar notícias de {nome_fonte} ({url}): {exc}", file=sys.stderr)
        return grupo

    # "destaques": 1 item de cada categoria (força diversidade de fonte).
    # "top3": voltou a ser cadeia de fallback normal (cada fonte pode
    # preencher a cota inteira sozinha) — a trava de "1 por fonte" só fazia
    # sentido enquanto tinha Money Times misturado aqui, forçando espaço
    # pra ela; sem ela, só sobrou InfoMoney, e a trava estava limitando à
    # toa quantas notícias conseguíamos puxar de lá.
    destaques = _coletar_grupo(NOTICIAS_FONTES_MISTAS, NOTICIAS_QTD, qtd_por_fonte=1)
    top3 = _coletar_grupo(NOTICIAS_FEEDS, NOTICIAS_QTD_TOP3)

    # Reforço cruzado: se um grupo ficou incompleto, tenta completar com as
    # fontes do outro grupo (ainda respeitando os links já usados).
    if len(destaques) < NOTICIAS_QTD:
        faltam = NOTICIAS_QTD - len(destaques)
        print(f"INFO: 'destaques' incompleto ({len(destaques)}/{NOTICIAS_QTD}) — reforçando com feeds do outro grupo.")
        destaques.extend(_coletar_grupo(NOTICIAS_FEEDS, faltam))

    if len(top3) < NOTICIAS_QTD_TOP3:
        faltam = NOTICIAS_QTD_TOP3 - len(top3)
        print(f"INFO: 'top3' incompleto ({len(top3)}/{NOTICIAS_QTD_TOP3}) — tentando mais 1 das fontes que já funcionaram antes de recorrer ao outro grupo.")
        # 1ª tentativa: pede mais itens das MESMAS fontes (até 3 de cada,
        # não só 1) — se buscasse só 1, ia sempre cair na notícia mais
        # recente de cada fonte, que já foi usada na 1ª passada e seria
        # descartada por duplicidade sem nunca chegar na 2ª notícia de
        # verdade. Com até 3, sobra margem pra achar algo novo.
        top3.extend(_coletar_grupo(NOTICIAS_FEEDS, faltam, qtd_por_fonte=3))
        # Se AINDA faltar (ex: todas as fontes empataram em 1 item cada),
        # aí sim recorre ao grupo do Investing.com como último recurso.
        if len(top3) < NOTICIAS_QTD_TOP3:
            faltam = NOTICIAS_QTD_TOP3 - len(top3)
            top3.extend(_coletar_grupo(NOTICIAS_FONTES_MISTAS, faltam, qtd_por_fonte=1))

    # Último recurso: se mesmo assim faltou notícia, é porque tudo que os
    # feeds têm hoje já foi publicado antes. Nesse caso, repetir é melhor
    # que mandar um boletim vazio — então liberamos o histórico e
    # completamos com o que houver (preferindo o mais antigo, que é o que
    # o leitor tem menos chance de lembrar).
    def _completar_repetindo(grupo, fontes, qtd_alvo, rotulo):
        if len(grupo) >= qtd_alvo:
            return grupo
        print(f"AVISO: '{rotulo}' segue incompleto ({len(grupo)}/{qtd_alvo}) — nenhuma notícia inédita "
              f"nos feeds. Reaproveitando publicadas anteriormente para o boletim não sair vazio.",
              file=sys.stderr)
        usados_agora = {n["link"] for n in grupo}
        candidatos = []
        for nome_fonte, url in fontes:
            try:
                for item in _buscar_feed(nome_fonte, url, qtd_maxima=NOTICIAS_POOL_POR_FONTE,
                                         filtrar_tema="(geral)" in nome_fonte):
                    if item["link"] not in usados_agora:
                        candidatos.append(item)
            except (requests.RequestException, ET.ParseError):
                continue
        # Mais antigo primeiro: quem saiu há mais tempo volta antes.
        candidatos.sort(key=lambda i: historico.get(i["link"], ""))
        for item in candidatos:
            if len(grupo) >= qtd_alvo:
                break
            grupo.append(item)
            usados_agora.add(item["link"])
        return grupo

    destaques = _completar_repetindo(destaques, NOTICIAS_FONTES_MISTAS, NOTICIAS_QTD, "destaques")
    top3 = _completar_repetindo(top3, NOTICIAS_FEEDS, NOTICIAS_QTD_TOP3, "top3")

    resultado = {
        "destaques": destaques[:NOTICIAS_QTD],
        "top3": top3[:NOTICIAS_QTD_TOP3],
    }

    # Só marcamos como "já publicado" o que de fato entrou no resultado.
    # Itens que foram coletados mas cortados pelo [:N] continuam livres
    # para aparecer numa próxima execução.
    publicados = {n["link"] for n in resultado["destaques"] + resultado["top3"] if n.get("link")}

    fii = _buscar_noticia_fii()
    if fii:
        resultado["fii"] = fii
        if fii.get("link"):
            publicados.add(fii["link"])
        print(f"OK: notícia de FIIs obtida de FIIs.com.br.")
    else:
        print("AVISO: sem notícia de FIIs nesta execução (fica sem o campo 'fii' — front-end já trata isso).", file=sys.stderr)

    salvar_historico_noticias(historico, publicados)
    return resultado


def _tipo_do_titulo(nome, indexador):
    """Classifica o título em Selic / Prefixado / IPCA+ a partir do nome e
    do indexador, para o site conseguir agrupar."""
    texto = f"{nome or ''} {indexador or ''}".upper()
    if "SELIC" in texto:
        return "Tesouro Selic"
    if "IPCA" in texto:
        return "Tesouro IPCA+"
    if "PREFIXADO" in texto or "PRE" == (indexador or "").upper():
        return "Tesouro Prefixado"
    if "RENDA+" in texto or "EDUCA" in texto:
        return "Tesouro IPCA+"
    return "Outros"


def _num_br(texto):
    """Converte número em formato brasileiro ('13,42' / '1.234,56') para
    float. O CSV do Tesouro usa vírgula decimal e ponto de milhar."""
    if texto is None:
        return None
    limpo = str(texto).strip().replace(".", "").replace(",", ".")
    if not limpo:
        return None
    try:
        return float(limpo)
    except ValueError:
        return None


def _data_br_para_iso(texto):
    """'01/03/2029' -> '2029-03-01'. Devolve None se não bater o formato."""
    partes = str(texto or "").strip().split("/")
    if len(partes) != 3:
        return None
    dia, mes, ano = partes
    if len(ano) != 4:
        return None
    return f"{ano}-{mes.zfill(2)}-{dia.zfill(2)}"


def coletar_tesouro_direto():
    """Lê o CSV oficial do Tesouro Transparente em streaming e devolve os
    títulos da data-base mais recente.

    O arquivo tem o histórico inteiro desde 2004, então nunca é carregado
    de uma vez: percorremos linha a linha guardando apenas as da maior
    data-base vista. Retorna None se falhar — o chamador preserva o
    tesouro.json anterior.
    """
    campos_por_data = {}
    maior_data = ""
    linhas_lidas = 0

    try:
        with requests.get(
            TESOURO_CSV_URL,
            headers={"User-Agent": HEADERS_NAVEGADOR["User-Agent"]},
            timeout=TESOURO_TIMEOUT,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            resp.encoding = resp.encoding or "latin-1"

            cabecalho = None
            for linha in resp.iter_lines(decode_unicode=True):
                if not linha:
                    continue
                colunas = linha.split(";")
                if cabecalho is None:
                    cabecalho = [c.strip().lower() for c in colunas]
                    continue

                linhas_lidas += 1
                if len(colunas) < 6:
                    continue

                data_base = _data_br_para_iso(colunas[2])
                if not data_base:
                    continue

                # Só guardamos a data mais recente; ao encontrar uma data
                # maior, descartamos o que havia acumulado antes.
                if data_base > maior_data:
                    maior_data = data_base
                    campos_por_data = {}
                if data_base != maior_data:
                    continue

                nome = (colunas[0] or "").strip()
                vencimento = _data_br_para_iso(colunas[1])
                taxa_compra = _num_br(colunas[3])
                if not nome or not vencimento or taxa_compra is None:
                    continue

                pu_compra = _num_br(colunas[5]) if len(colunas) > 5 else None
                # Chave por título+vencimento evita duplicata na mesma data.
                campos_por_data[f"{nome}|{vencimento}"] = {
                    "nome": f"{nome} {vencimento[:4]}",
                    "tipo": _tipo_do_titulo(nome, nome),
                    "indexador": nome,
                    "vencimento": vencimento,
                    "taxa_compra": round(taxa_compra, 4),
                    "taxa_resgate": (lambda t: round(t, 4) if t is not None else None)(
                        _num_br(colunas[4]) if len(colunas) > 4 else None),
                    "preco_unitario": pu_compra,
                    "investimento_minimo": round(pu_compra * TESOURO_FRACAO_MINIMA, 2)
                        if pu_compra is not None else None,
                }
    except (requests.RequestException, ValueError) as exc:
        print(f"AVISO: falha ao ler o CSV do Tesouro Transparente ({exc}). "
              "O tesouro.json anterior será mantido.", file=sys.stderr)
        return None

    titulos = list(campos_por_data.values())
    if not titulos:
        print(f"AVISO: CSV do Tesouro lido ({linhas_lidas} linhas), mas nenhum título válido "
              "na data mais recente. O formato do arquivo pode ter mudado.", file=sys.stderr)
        return None

    titulos.sort(key=lambda t: (t["tipo"], t["vencimento"]))
    print(f"OK: {len(titulos)} título(s) do Tesouro Direto na data-base {maior_data} "
          f"({linhas_lidas} linhas lidas).")

    return {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "data_base": maior_data,
        "titulos": titulos,
    }


def salvar_tesouro(dados):
    """Grava o tesouro.json. Se a coleta falhou (dados=None), NÃO mexe no
    arquivo — o site continua mostrando as taxas da última coleta boa,
    com a data de atualização visível para o leitor perceber."""
    if dados is None:
        print("INFO: tesouro.json mantido como estava (coleta falhou nesta execução).", file=sys.stderr)
        return
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), TESOURO_ARQUIVO)
    try:
        with open(caminho, "w", encoding="utf-8") as arq:
            json.dump(dados, arq, ensure_ascii=False, indent=2)
        print(f"OK: {len(dados['titulos'])} título(s) salvos em {TESOURO_ARQUIVO}.")
    except OSError as exc:
        print(f"AVISO: não foi possível salvar {TESOURO_ARQUIVO}: {exc}", file=sys.stderr)


def _brapi_get(caminho, token, params=None):
    """GET autenticado na brapi. Retorna o JSON, ou None se falhar — assim
    um endpoint fora do ar não derruba os outros índices."""
    try:
        resp = requests.get(
            f"{BRAPI_BASE_URL}{caminho}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        data = resp.json()
        # A brapi sinaliza erro no corpo (error/message/code), inclusive em
        # alguns casos com HTTP 200 — checamos os dois.
        if resp.status_code != 200 or data.get("error"):
            print(
                f"AVISO: brapi {caminho} respondeu {resp.status_code}: "
                f"{data.get('code') or ''} {data.get('message') or str(data)[:200]}",
                file=sys.stderr,
            )
            return None
        return data
    except (requests.RequestException, ValueError) as exc:
        print(f"AVISO: falha ao chamar brapi {caminho}: {exc}", file=sys.stderr)
        return None


def _para_float(valor):
    """A brapi devolve os campos de câmbio como string ('5.2159'); os de
    índices e cripto vêm como número. Normaliza os dois casos."""
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return None


def coletar_cambio_bcb():
    """Dólar e Euro pelas séries SGS do Banco Central (PTAX de venda).

    Pede as 2 últimas observações de cada série: a mais recente vira o
    valor exibido e a anterior serve para calcular a variação %. Se só
    houver uma observação disponível, o índice entra sem variação em vez
    de ficar de fora.

    Cada moeda é independente — se uma série falhar, a outra ainda entra.
    """
    indices = []
    for serie, rotulo in BCB_CAMBIO_SERIES:
        url = (
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}"
            "/dados/ultimos/2?formato=json"
        )
        try:
            dados = _requisitar_bcb_com_retry(url)
        except (requests.RequestException, ValueError) as exc:
            print(f"AVISO: falha ao buscar {rotulo} no Banco Central (série {serie}): {exc}", file=sys.stderr)
            continue

        if not isinstance(dados, list) or not dados:
            print(f"AVISO: Banco Central não retornou dados para {rotulo} (série {serie}).", file=sys.stderr)
            continue

        # O SGS devolve em ordem cronológica: o último item é o mais recente.
        atual = _para_float((dados[-1] or {}).get("valor"))
        if atual is None:
            print(f"AVISO: valor inválido para {rotulo} na série {serie} do BCB.", file=sys.stderr)
            continue

        variacao = None
        if len(dados) >= 2:
            anterior = _para_float((dados[-2] or {}).get("valor"))
            if anterior:
                variacao = ((atual - anterior) / anterior) * 100

        indices.append({
            "label": rotulo,
            "prefixo": "R$ ",
            "valor": atual,
            "variacao_pct": variacao,
            "referencia": (dados[-1] or {}).get("data"),
        })

    return indices


def coletar_cripto_coingecko():
    """Bitcoin e Ethereum em reais pela CoinGecko (gratuita, sem chave),
    com a variação das últimas 24h. Os dois vêm na mesma requisição.

    Retorna lista vazia se falhar — o boletim sai sem cripto em vez de
    quebrar. Se só um dos ativos vier, o outro ainda entra."""
    ids = ",".join(m[0] for m in COINGECKO_MOEDAS)
    try:
        resp = requests.get(
            COINGECKO_URL,
            params={
                "ids": ids,
                "vs_currencies": "brl",
                "include_24hr_change": "true",
            },
            headers={"User-Agent": HEADERS_NAVEGADOR["User-Agent"], "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        dados = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"AVISO: falha ao buscar cripto na CoinGecko: {exc}", file=sys.stderr)
        return []

    indices = []
    for cripto_id, rotulo in COINGECKO_MOEDAS:
        item = (dados or {}).get(cripto_id) or {}
        valor = _para_float(item.get("brl"))
        if valor is None:
            print(f"AVISO: CoinGecko não retornou o preço de {rotulo} ({cripto_id}).", file=sys.stderr)
            continue
        indices.append({
            "label": rotulo,
            "prefixo": "R$ ",
            "valor": valor,
            "variacao_pct": _para_float(item.get("brl_24h_change")),
        })
    return indices


def coletar_indices_brapi(token):
    """Monta os índices de mercado do boletim.

    Ibovespa e IFIX vêm da brapi (2 requisições — o plano gratuito aceita
    1 ticker por chamada). Dólar e Euro vêm do Banco Central (séries SGS,
    oficiais e sem limite) e o Bitcoin da CoinGecko (gratuita, sem chave)
    — os endpoints de câmbio e cripto da brapi são pagos.

    Cada bloco é independente: se uma das fontes falhar, os índices da
    outra ainda entram no boletim."""
    indices = []

    # --- Índices da B3 (1 requisição cada: o plano gratuito limita a 1
    # ticker por chamada em /stocks/quote) ---
    for ticker, rotulo in BRAPI_INDICES:
        data = _brapi_get("/v2/stocks/quote", token, {"symbols": ticker})
        resultados = (data or {}).get("results") or []
        if not resultados:
            continue
        # v2 aninha os campos em "data"; aceitamos o formato plano também,
        # para não quebrar caso a resposta mude.
        item = resultados[0]
        dados = item.get("data") or item
        valor = _para_float(dados.get("regularMarketPrice"))
        if valor is None:
            continue
        indices.append({
            "label": rotulo,
            "prefixo": "pontos",
            "valor": valor,
            "variacao_pct": _para_float(dados.get("regularMarketChangePercent")),
        })

    # --- Câmbio (Banco Central) e cripto (CoinGecko) ---
    indices.extend(coletar_cambio_bcb())
    indices.extend(coletar_cripto_coingecko())

    return indices


def _requisitar_bcb_com_retry(url, tentativas=3):
    """GET genérico com retry pras APIs do Banco Central (SGS) — usado por
    Selic, IPCA e IPCA 12m. O BCB às vezes cai/retorna 502 por alguns
    minutos (instabilidade deles, não do nosso lado); múltiplas tentativas
    com uma pequena pausa entre elas cobrem bem esse tipo de falha
    passageira sem exigir mudar cada função.

    Manda um User-Agent de navegador (igual já fazemos com os feeds RSS) —
    é possível que requisições sem esse cabeçalho, vindas de um datacenter
    como o do GitHub Actions, sejam tratadas como tráfego suspeito por
    algum proxy/CDN na frente da API do BCB e recebam 502 por causa disso."""
    headers_bcb = {
        "User-Agent": HEADERS_NAVEGADOR["User-Agent"],
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(url, timeout=TIMEOUT, headers=headers_bcb)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            ultimo_erro = exc
            if tentativa < tentativas:
                print(f"AVISO: tentativa {tentativa} de acessar o Banco Central falhou ({exc}); tentando de novo em 2s...", file=sys.stderr)
                time.sleep(2)
    raise ultimo_erro


def coletar_selic_bcb():
    """Meta da Selic definida pelo Copom, via API pública do Banco Central
    (série SGS 432) — atualiza no mesmo dia da decisão, diferente do campo
    'taxes' da HG Brasil, que pode demorar bem mais para refletir um corte
    ou alta recém-anunciados. Pega os últimos 5 valores e usa o mais
    recente (a Selic só muda em dias de reunião do Copom, então o
    'último' costuma ficar vários dias/semanas parado, é esperado)."""
    try:
        dados = _requisitar_bcb_com_retry(BCB_SELIC_META_URL)
        if not dados:
            return None
        item = dados[-1]
        valor = float(item["valor"].replace(",", "."))
        print(f"OK: Selic obtida do Banco Central (SGS 432): {valor}% (referência: {item.get('data')}).")
        return {"label": "Selic (meta)", "valor_pct": valor, "referencia": item.get("data")}
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print(f"AVISO: falha ao buscar Selic no Banco Central após tentativas: {exc}", file=sys.stderr)
        return None


def coletar_ipca():
    """Variação mensal do IPCA, via API pública do Banco Central (série SGS 433)."""
    try:
        dados = _requisitar_bcb_com_retry(BCB_IPCA_URL)
        if not dados:
            return None
        item = dados[-1]
        valor = float(item["valor"].replace(",", "."))
        return {"label": "IPCA (mensal)", "valor_pct": valor, "referencia": item.get("data")}
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        print(f"AVISO: falha ao buscar IPCA no Banco Central após tentativas: {exc}", file=sys.stderr)
        return None


def coletar_ipca_12_meses():
    """IPCA acumulado nos últimos 12 meses (janela móvel), compondo os
    valores mensais mais recentes da série SGS 433. Essa é a métrica de
    inflação anual usada pelo mercado (Focus, corretoras etc.) — diferente
    do acumulado desde janeiro do ano corrente, que só reflete o ano
    calendário e distorce muito se anualizado no meio do ano."""
    try:
        dados = _requisitar_bcb_com_retry(BCB_IPCA_12M_URL)
        if not dados:
            return None

        fator_acumulado = 1.0
        for item in dados:
            valor_mes = float(item["valor"].replace(",", ".")) / 100
            fator_acumulado *= (1 + valor_mes)

        acumulado_pct = (fator_acumulado - 1) * 100
        ultimo_mes = dados[-1].get("data")
        return {
            "label": "IPCA (12 meses)",
            "valor_pct": acumulado_pct,
            "referencia": ultimo_mes,
            # Sempre 12 (ou o que a API tiver retornado, se faltar histórico).
            # Mantido por compatibilidade com o cálculo de anualização no site
            # — com 12 meses reais, o valor já é o anual, sem distorção.
            "meses": len(dados),
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


def coletar_indices(token_brapi):
    """Monta a lista completa de índices do boletim: mercado (brapi) +
    Selic/IPCA (Banco Central) + CPI (BLS). A Selic é posicionada logo
    após o Ibovespa, na posição tradicional do boletim."""
    if not token_brapi:
        print("AVISO: VIVERDERENDA_BRAPI não configurado — pulando índices de mercado (Ibovespa, IFIX, Dólar, Euro, Bitcoin).", file=sys.stderr)
        indices = []
    else:
        try:
            indices = coletar_indices_brapi(token_brapi)
            if not indices:
                print(
                    "AVISO: nenhum índice de mercado veio da brapi. Confira se o token "
                    "em VIVERDERENDA_BRAPI é válido e se a cota mensal do plano "
                    "(15 mil requisições no gratuito) não foi esgotada.",
                    file=sys.stderr,
                )
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao buscar índices na brapi: {exc}", file=sys.stderr)
            indices = []

    # Selic: fonte oficial é o Banco Central (série SGS 432), que atualiza
    # no mesmo dia da decisão do Copom. Não há mais fallback pela HG Brasil
    # — ela chegou a ser usada aqui, mas mostrava valor desatualizado após
    # as reuniões, então o BCB é a única fonte.
    selic = coletar_selic_bcb()
    if not selic:
        print("AVISO: Selic do Banco Central falhou — o boletim sai sem ela nesta execução.", file=sys.stderr)

    # Reordena a Selic para logo depois do Ibovespa, se ambos existirem.
    if selic:
        posicao_ibovespa = next(
            (i for i, item in enumerate(indices) if item.get("label") == "Ibovespa"), None
        )
        indices.insert((posicao_ibovespa + 1) if posicao_ibovespa is not None else 0, selic)

    ipca = coletar_ipca()
    if ipca:
        indices.append(ipca)

    ipca_12m = coletar_ipca_12_meses()
    if ipca_12m:
        indices.append(ipca_12m)

    cpi = coletar_cpi_eua()
    if cpi:
        indices.append(cpi)

    return indices


def obter_token_hgbrasil():
    """Token da HG Brasil, lido da variável de ambiente HGBRASIL_TOKEN (secret
    do GitHub Actions). Mesmo provedor usado no widget de busca do site — só
    usado aqui no servidor, nunca fica no HTML."""
    return os.environ.get("HGBRASIL_TOKEN", "").strip()


def obter_token_brapi():
    """Token da brapi, lido da variável de ambiente VIVERDERENDA_BRAPI
    (secret do GitHub Actions). Usado nos índices de mercado do boletim."""
    return os.environ.get("VIVERDERENDA_BRAPI", "").strip()


def obter_token_bolsai():
    """Chave da bolsai, lida da variável de ambiente VIVERDERENDA_BOLSAI_RANKING
    (secret do GitHub Actions). Usada só nos rankings de FIIs.

    Aceita também o nome antigo VIVERDERENDA_BOLSAI como fallback, para não
    quebrar caso o secret volte a ser criado com aquele nome. Essa chave é
    independente da que está no Cloudflare Worker (BOLSAI_API_KEY): lá ela
    serve o navegador, aqui roda no servidor do GitHub Actions."""
    return (
        os.environ.get("VIVERDERENDA_BOLSAI_RANKING", "").strip()
        or os.environ.get("VIVERDERENDA_BOLSAI", "").strip()
    )


def _tickers_b3(simbolos):
    """Formata uma lista de símbolos ('PETR4') no padrão exigido pela HG
    Brasil ('B3:PETR4'), separados por vírgula."""
    return ",".join(f"B3:{s}" for s in simbolos)


# O plano da chave usada aqui (server-side) limita a 5 tickers por
# requisição no endpoint /v2/finance/quotes (erro "MAX_PER_REQUEST" quando
# excedido). Por isso, toda busca de fundamentos é feita em lotes.
HGBRASIL_MAX_TICKERS_POR_REQUISICAO = 5


def _buscar_quotes_em_lotes(pool, token, tamanho_lote=HGBRASIL_MAX_TICKERS_POR_REQUISICAO):
    """Busca cotações/fundamentos de um pool de tickers via /v2/finance/quotes,
    dividindo em lotes de `tamanho_lote` para respeitar o limite do plano
    (erro MAX_PER_REQUEST). Retorna a lista combinada de 'results' de todos
    os lotes."""
    resultados = []
    for i in range(0, len(pool), tamanho_lote):
        lote = pool[i:i + tamanho_lote]
        params = {"tickers": _tickers_b3(lote), "key": token}
        resp = requests.get(HGBRASIL_QUOTES_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        lote_resultados = data.get("results") or []
        if not lote_resultados and data.get("errors"):
            print(
                f"AVISO: lote {lote} retornou erro da HG Brasil: {data.get('errors')}",
                file=sys.stderr,
            )
        resultados.extend(lote_resultados)
    return resultados


def _bolsai_get(caminho, chave, params=None):
    """GET autenticado na bolsai. Retorna o JSON, ou None se falhar (para
    que um FII problemático não derrube o ranking inteiro)."""
    url = f"{BOLSAI_BASE_URL}{caminho}"
    try:
        resp = requests.get(
            url,
            params=params or {},
            headers={"X-API-Key": chave},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            print(
                f"AVISO: bolsai {caminho} respondeu {resp.status_code}: {resp.text[:200]}",
                file=sys.stderr,
            )
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"AVISO: falha ao chamar bolsai {caminho}: {exc}", file=sys.stderr)
        return None


def _buscar_fundamentos_fiis_bolsai(pool, chave):
    """Busca, um ticker por vez, os dados de cada FII do pool na bolsai.

    Faz 2 chamadas por FII:
      1. /fiis/{ticker}          -> nome, cotação, P/VP, DY 12m e o
                                    patrimônio líquido real (net_asset_value)
      2. /stocks/{ticker}/history -> volume do último pregão e variação %
                                    do dia (calculada dos 2 últimos closes)

    Com 20 FIIs no pool são ~40 requisições por execução. Como o ranking
    roda 1x por dia (--com-ranking), isso é irrelevante frente ao limite
    de 10.000/dia do plano Pro.
    """
    fundamentos = {}

    for ticker in pool:
        dados = _bolsai_get(f"/fiis/{ticker}", chave)
        if not dados:
            continue

        # Volume e variação vêm do histórico de preços (2 últimos pregões).
        historico = _bolsai_get(
            f"/stocks/{ticker}/history", chave, params={"limit": 2}
        )
        volume = None
        variacao_pct = None
        precos = (historico or {}).get("prices") or []
        if precos:
            # A bolsai devolve do mais recente para o mais antigo.
            volume = precos[0].get("volume")
            if len(precos) >= 2:
                atual = precos[0].get("close")
                anterior = precos[1].get("close")
                if isinstance(atual, (int, float)) and isinstance(anterior, (int, float)) and anterior:
                    variacao_pct = ((atual - anterior) / anterior) * 100

        fundamentos[ticker] = {
            "ticker": dados.get("ticker") or ticker,
            "nome": dados.get("name") or "",
            "preco": dados.get("close_price"),
            "variacao_pct": variacao_pct,
            "volume": volume,
            "dividend_yield_pct": dados.get("dividend_yield_ttm"),
            # net_asset_value é o patrimônio líquido de fato, apurado por
            # laudo e informado à CVM — não uma aproximação por market_cap.
            "patrimonio": dados.get("net_asset_value"),
        }

        time.sleep(BOLSAI_PAUSA_ENTRE_CHAMADAS)

    if fundamentos:
        exemplo = next(iter(fundamentos.values()))
        print(
            f"DEBUG: exemplo de FII coletado da bolsai: "
            f"{json.dumps(exemplo, ensure_ascii=False)[:400]}",
            file=sys.stderr,
        )
    else:
        print("DEBUG: bolsai não retornou nenhum FII do pool.", file=sys.stderr)

    return fundamentos


def coletar_rankings_fiis(chave_bolsai, pool=None, qtd=RANKING_FIIS_QTD):
    """Monta os 3 rankings de FIIs exibidos lado a lado no site: Maiores
    Valor Patrimonial, Maiores Dividend Yield e Mais Negociados (volume do
    dia) — equivalente ao 'Mais Buscados' do Investidor10, mas usando um
    dado de mercado real (volume) em vez de popularidade de site, que não
    dá para obter via API de forma automática e confiável.

    Migrado da HG Brasil para a bolsai: o patrimônio agora é o valor
    patrimonial real do fundo (net_asset_value), e não mais uma
    aproximação por valor de mercado."""
    pool = pool or POOL_FIIS
    fundamentos = _buscar_fundamentos_fiis_bolsai(pool, chave_bolsai)
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
    """Busca, em lotes de 5 tickers, preço, Dividend Yield (12m) e valor de
    mercado de todo o pool de ações via /v2/finance/quotes."""
    resultados_brutos = _buscar_quotes_em_lotes(pool, token)

    if not resultados_brutos:
        print("DEBUG: /v2/finance/quotes (ações) não retornou nenhum resultado em nenhum lote.", file=sys.stderr)

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
    (endpoint Beta — requer plano compatível). Retorna {ticker: receita}.
    Também respeita o limite de tickers por requisição do plano."""
    receitas = {}
    for i in range(0, len(pool), HGBRASIL_MAX_TICKERS_POR_REQUISICAO):
        lote = pool[i:i + HGBRASIL_MAX_TICKERS_POR_REQUISICAO]
        params = {"tickers": _tickers_b3(lote), "period": "annual", "key": token}
        resp = requests.get(HGBRASIL_INCOME_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
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


def coletar_ranking(token, chave_bolsai=None):
    """Monta o ranking completo do boletim: 3 rankings de ações (DY, valor de
    mercado, receita) e 3 rankings de FIIs (valor patrimonial, dividend
    yield, mais negociados) — 6 itens cada.

    Ações ainda usam a HG Brasil; FIIs usam a bolsai. As duas metades são
    independentes: se uma chave faltar ou uma API falhar, a outra continua
    funcionando e o front-end mantém a parte anterior do ranking."""
    vazio_acoes = {"dividend_yield": [], "valor_mercado": [], "receita": []}
    vazio_fiis = {"valor_patrimonial": [], "dividend_yield": [], "mais_negociados": []}
    resultado = {"acoes": dict(vazio_acoes), "fiis": dict(vazio_fiis)}

    if token:
        try:
            resultado["acoes"] = coletar_rankings_acoes(token)
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao montar rankings de ações: {exc}", file=sys.stderr)
    else:
        print("AVISO: HGBRASIL_TOKEN não configurado — pulando rankings de ações.", file=sys.stderr)

    if chave_bolsai:
        try:
            resultado["fiis"] = coletar_rankings_fiis(chave_bolsai)
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao montar rankings de FIIs: {exc}", file=sys.stderr)
    else:
        print("AVISO: VIVERDERENDA_BOLSAI_RANKING não configurado — pulando rankings de FIIs.", file=sys.stderr)

    return resultado


# ============================================================
# ALERTAS DE PREÇO — checa a cada execução do robô (a cada 15 min) se
# algum alerta cadastrado no site bateu a condição, e manda e-mail via
# Resend. Usa a service_role key do Supabase (NUNCA a publishable key —
# essa aqui ignora RLS de propósito, pra conseguir ler os alertas de
# todo mundo, não só de um usuário). Tudo opcional: se as credenciais não
# estiverem configuradas nos Secrets do GitHub, essa etapa é pulada sem
# quebrar o resto da coleta.
# ============================================================
SUPABASE_URL = "https://mzknjnupizprfatfmxqg.supabase.co"

# Mesma chave "uso exposto" (plano free) já usada no fallback de Stocks/ETFs
# internacionais do index.html — não é segredo, só limitada por domínio/plano.
CHAVE_TWELVEDATA = "034d1589162e413d9a1e9608860cb06a"

# Kinds gravados pelo front-end quando o alerta é de ação/FII/Fiagro da B3
# (ver botão "Criar alerta" no index.html). Qualquer outro kind (ex:
# "internacional") é tratado aqui como Stock/ETF americano via TwelveData.
KINDS_B3 = ("stock", "fii", "fiagro")


def _obter_alertas_pendentes(service_role_key):
    """Busca todos os alertas cadastrados, já com o e-mail do dono (via a
    view alertas_com_email — ver sql/criar-tabela-alertas-preco.sql)."""
    url = f"{SUPABASE_URL}/rest/v1/alertas_com_email?select=*"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
    }
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _remover_alerta(service_role_key, alerta_id):
    url = f"{SUPABASE_URL}/rest/v1/alertas_preco?id=eq.{alerta_id}"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
    }
    requests.delete(url, headers=headers, timeout=TIMEOUT)


def _enviar_email_alerta(resend_key, destinatario, ticker, condicao, valor_alvo, valor_atual):
    direcao = "caiu abaixo de" if condicao == "abaixo" else "subiu acima de"
    assunto = f"{ticker} {direcao} R$ {valor_alvo:.2f} — Dividendos | Viver de Renda"
    corpo_html = (
        f"<h2>Seu alerta de {ticker} foi disparado!</h2>"
        f"<p><strong>{ticker}</strong> {direcao} <strong>R$ {valor_alvo:.2f}</strong> "
        f"que você configurou.</p>"
        f"<p>Valor atual: <strong>R$ {valor_atual:.2f}</strong></p>"
        f"<p>Esse alerta já foi removido automaticamente — se quiser continuar "
        f"acompanhando, crie um novo em "
        f"<a href='https://viverderenda.dev.br/'>viverderenda.dev.br</a>.</p>"
    )
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
        json={
            "from": "Dividendos | Viver de Renda <alertas@mail.viverderenda.dev.br>",
            "to": [destinatario],
            "subject": assunto,
            "html": corpo_html,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()


def _obter_precos_twelvedata(tickers):
    """Cotação atual de tickers fora da B3 (Stocks/ETFs americanos, ex:
    KBWD, AAPL), usados pelos alertas de preço com kind != stock/fii/fiagro."""
    if not tickers:
        return {}
    try:
        url = f"https://api.twelvedata.com/price?symbol={','.join(tickers)}&apikey={CHAVE_TWELVEDATA}"
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        # Com 1 símbolo só, a API responde {"price": "..."} direto (sem
        # aninhar pelo símbolo); com vários, responde {"TICKER": {"price": ...}, ...}.
        if len(tickers) == 1:
            dados = {tickers[0]: dados}
        precos = {}
        for ticker, info in dados.items():
            preco = info.get("price") if isinstance(info, dict) else None
            if preco is None:
                continue
            try:
                precos[ticker] = float(preco)
            except (TypeError, ValueError):
                continue
        return precos
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"AVISO: falha ao buscar cotações internacionais (TwelveData) para os alertas: {exc}", file=sys.stderr)
        return {}


def verificar_e_disparar_alertas(token):
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()

    if not service_role_key or not resend_key:
        print("INFO: Alertas de preço pulados (SUPABASE_SERVICE_ROLE_KEY e/ou RESEND_API_KEY não configurados nos Secrets).")
        return

    try:
        alertas = _obter_alertas_pendentes(service_role_key)
    except requests.RequestException as exc:
        print(f"AVISO: falha ao buscar alertas de preço: {exc}", file=sys.stderr)
        return

    if not alertas:
        return

    tickers_b3 = sorted({a["ticker"] for a in alertas if a.get("kind") in KINDS_B3})
    tickers_intl = sorted({a["ticker"] for a in alertas if a.get("kind") not in KINDS_B3})

    precos = {}
    if tickers_b3:
        tickers_query = ",".join(f"B3:{t}" for t in tickers_b3)
        try:
            url = f"https://api.hgbrasil.com/v2/finance/quotes?format=json-cors&tickers={tickers_query}&key={token}"
            resp = requests.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            resultados = resp.json().get("results", [])
            precos.update({r["symbol"].replace("B3:", ""): r.get("quote", {}).get("value") for r in resultados})
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"AVISO: falha ao buscar cotações da B3 para os alertas: {exc}", file=sys.stderr)

    if tickers_intl:
        precos.update(_obter_precos_twelvedata(tickers_intl))

    disparados = 0
    for alerta in alertas:
        preco_atual = precos.get(alerta["ticker"])
        if preco_atual is None:
            continue

        valor_alvo = float(alerta["valor_alvo"])
        bateu = (
            (alerta["condicao"] == "abaixo" and preco_atual <= valor_alvo)
            or (alerta["condicao"] == "acima" and preco_atual >= valor_alvo)
        )
        if not bateu:
            continue

        try:
            _enviar_email_alerta(
                resend_key, alerta["email"], alerta["ticker"], alerta["condicao"], valor_alvo, preco_atual
            )
            _remover_alerta(service_role_key, alerta["id"])
            disparados += 1
        except requests.RequestException as exc:
            print(f"AVISO: falha ao enviar e-mail de alerta para {alerta.get('email')}: {exc}", file=sys.stderr)

    print(f"OK: {disparados} alerta(s) de preço disparado(s) e removido(s) (de {len(alertas)} verificado(s)).")


def main():
    # Coleta de rankings (Ações/FIIs) é mais pesada em requisições à API
    # (vários lotes de tickers, mais o endpoint Beta de receita) do que
    # índices/notícias. Por isso ela é opcional: só roda quando o workflow
    # passar --com-ranking (ou a variável de ambiente COLETAR_RANKING=1),
    # permitindo agendar índices/notícias a cada 15 min e os rankings só
    # 1x por dia, sem gerar chamadas desnecessárias à HG Brasil no resto
    # do dia.
    coletar_rankings_agora = (
        "--com-ranking" in sys.argv
        or os.environ.get("COLETAR_RANKING", "").strip() == "1"
    )

    token = obter_token_hgbrasil()
    noticias = coletar_noticias()

    indices = coletar_indices(obter_token_brapi())
    if indices:
        with open(INDICES_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(indices, f, ensure_ascii=False, indent=2)
        print(f"OK: {len(indices)} índices salvos em {INDICES_OUTPUT_FILE}.")
    else:
        print("AVISO: nenhum índice coletado. Mantendo arquivo anterior, se existir.", file=sys.stderr)

    if coletar_rankings_agora:
        # O CSV do Tesouro tem o histórico desde 2004 (centenas de MB), e
        # as taxas mudam só uma vez por dia útil. Por isso a coleta anda
        # junto com os rankings, 1x/dia — a cada 15 min seria desperdício
        # de banda e tempo de runner, sem nenhum dado novo em troca.
        salvar_tesouro(coletar_tesouro_direto())

        ranking = coletar_ranking(token, obter_token_bolsai())
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
    else:
        print("INFO: coleta de rankings (Ações/FIIs) pulada nesta execução "
              "(roda só 1x/dia — use --com-ranking para forçar).")

    if not noticias.get("destaques") and not noticias.get("top3"):
        print("ERRO: nenhum dos feeds configurados retornou notícias. Mantendo arquivo anterior, se existir.", file=sys.stderr)
        sys.exit(1)

    with open(NOTICIAS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(noticias, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(noticias.get('destaques', []))} notícia(s) em 'destaques', "
          f"{len(noticias.get('top3', []))} notícia(s) em 'top3' e "
          f"{'1' if noticias.get('fii') else '0'} notícia de FIIs salvas em {NOTICIAS_OUTPUT_FILE}.")

    verificar_e_disparar_alertas(token)


if __name__ == "__main__":
    main()
