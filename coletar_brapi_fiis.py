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
NOTICIAS_FEED_URL = "https://www.infomoney.com.br/mercados/feed/"
NOTICIAS_QTD = 3
TIMEOUT = 20


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


def coletar_noticias(quantidade=NOTICIAS_QTD):
    """Busca as últimas notícias do feed RSS de mercado financeiro configurado
    e retorna uma lista de dicts prontos para exibição no boletim."""
    try:
        resp = requests.get(NOTICIAS_FEED_URL, timeout=TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ViverDeRendaBot/1.0)"
        })
        resp.raise_for_status()
        raiz = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"AVISO: falha ao buscar notícias: {exc}", file=sys.stderr)
        return []

    itens = raiz.findall("./channel/item")[:quantidade]
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
            "fonte": "InfoMoney",
            "publicado_em": data_pub,
        })

    return noticias


def main():
    noticias = coletar_noticias()

    if not noticias:
        print("ERRO: nenhuma notícia coletada. Mantendo arquivo anterior, se existir.", file=sys.stderr)
        sys.exit(1)

    with open(NOTICIAS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(noticias, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(noticias)} notícias salvas em {NOTICIAS_OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
