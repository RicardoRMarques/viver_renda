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

import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

NOTICIAS_OUTPUT_FILE = "noticias.json"

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


def main():
    noticias = coletar_noticias()

    if not noticias:
        print("ERRO: nenhum dos feeds configurados retornou notícias. Mantendo arquivo anterior, se existir.", file=sys.stderr)
        sys.exit(1)

    with open(NOTICIAS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(noticias, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(noticias)} notícias salvas em {NOTICIAS_OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
