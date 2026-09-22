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

  - FI-Infra (fundos de debêntures incentivadas, Lei 12.431 — IFRA11,
    CDII11, KDIF11...): NÃO são FIIs, então não aparecem na página de FIIs
    do Fundamentus. Entram no mesmo mercado-fiis.json com segmento "Infra"
    e "tipo_fundo": "FI-Infra":
      1) B3 fundsListedProxy/Search/GetListFunds (typeFund FI-INFRA) -> lista
      2) B3 fundsProxy/fundsCall/GetListedSupplementFunds -> quantidade de
         cotas e rendimentos pagos (DY 12M = soma 12 meses ÷ preço)
      3) HG Brasil /v2/finance/quotes -> preço e variação do dia
      4) CVM dados abertos (registro_fundo_classe.zip + inf_diario_fi_AAAAMM.zip)
         -> valor da cota (VP/cota). O fundo da CVM é achado pelo nome (INFRA /
         INCENTIVADO) + quantidade de cotas (PL ÷ cota) igual à da B3.
         P/VP = preço ÷ VP/cota.
    Qualquer falha nessa etapa só deixa os FI-Infra de fora (com log) —
    nunca derruba o mercado-fiis.json.

Uso:
  HGBRASIL_TOKEN=xxxxxxxx python coletar_mercado.py
  python coletar_mercado.py --so-infra   # testa só a etapa de FI-Infra (não grava)

Nota: chave usada é a de servidor (a mesma do boletim diário via GitHub
Actions secret), não a de browser/CORS.
"""

import base64
import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone

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

# A HG Brasil devolve o setor no nível mais granular do "Setor de Atuação
# B3" (~70 categorias, tipo "Cervejas e Refrigerantes", "Bicicletas" etc.)
# — bom demais pro filtro da listagem virar uma parede de chips. Reduzido
# a pedido pros 4 setores que interessam pro perfil de dividendos do site
# (Financeiro, Energia Elétrica, Saúde, Materiais Básicos); tudo que não
# se encaixa nesses 4 cai em "Outros" (continua na tabela quando o filtro
# é "Todos", só não vira chip/submenu próprio).
MAPA_SETOR_MACRO = {
    # --- Financeiro ---
    "Bancos": "Financeiro",
    "Holdings Diversificadas": "Financeiro",
    "Incorporações": "Financeiro",
    "Intermediação Imobiliária": "Financeiro",
    "Intermediários Financeiros": "Financeiro",
    "Seguradoras": "Financeiro",
    "Serviços Financeiros Diversos": "Financeiro",
    "Exploração de Imóveis": "Financeiro",
    "Financeiro": "Financeiro",
    # --- Energia Elétrica ---
    "Energia": "Energia Elétrica",
    "Energia Elétrica": "Energia Elétrica",
    "Gás": "Energia Elétrica",
    "Utilidade Pública": "Energia Elétrica",
    "Água e Saneamento": "Energia Elétrica",
    # --- Saúde ---
    "Equipamentos de Saúde": "Saúde",
    "Medicamentos": "Saúde",
    "Saúde": "Saúde",
    # --- Materiais Básicos ---
    "Madeira": "Materiais Básicos",
    "Materiais Básicos": "Materiais Básicos",
    "Minerais Metálicos": "Materiais Básicos",
    "Mineração": "Materiais Básicos",
    "Papel e Celulose": "Materiais Básicos",
    "Químicos": "Materiais Básicos",
    "Siderurgia e Metalurgia": "Materiais Básicos",
}


def macro_setor(sub_setor):
    if not sub_setor:
        return "Outros"
    return MAPA_SETOR_MACRO.get(sub_setor.strip(), "Outros")


# O Fundamentus classifica FII num nível bem mais fino (Escritórios,
# Hospital, Hotel, Lajes Corporativas, Multicategoria, Residencial,
# Shopping Centers, Varejo etc.) do que os 4 grupos macro que fazem
# sentido pro filtro (igual o mockup original: Tijolo, Papel, Logístico,
# Híbrido). "Tijolo" agrupa tudo que é imóvel físico "puro" (exceto
# logística, que vira grupo próprio); "Papel" é recebíveis/CRI; "Híbrido"
# fica igual.
#
# As chaves aqui são comparadas de forma NORMALIZADA (sem acento, minúsculo,
# espaços colapsados — ver _normalizar_chave) porque o texto exato que o
# Fundamentus usa pode variar (ex: "Shopping Centers" vs "Shoppings") e
# não dá pra testar ao vivo daqui. Por segurança, qualquer segmento que não
# bata com nada aqui cai em "Outros" — NUNCA em "Tijolo" — pra não
# misturar Papel/Híbrido sem querer dentro do balde errado (foi
# exatamente esse bug que fez metade dos FIIs de papel aparecerem como
# "Tijolo").
MAPA_SEGMENTO_MACRO = {
    "escritorios": "Tijolo",
    "hospital": "Tijolo",
    "hoteis": "Tijolo",
    "hotel": "Tijolo",
    "lajes corporativas": "Tijolo",
    "residencial": "Tijolo",
    "shoppings": "Tijolo",
    "shopping centers": "Tijolo",
    "shopping": "Tijolo",
    "varejo": "Tijolo",
    "multicategoria": "Tijolo",
    "logistica": "Logístico",
    "logistico": "Logístico",
    "industrial e logistico": "Logístico",
    "galpoes logisticos": "Logístico",
    "titulos e val mob": "Papel",
    "titulos e valores mobiliarios": "Papel",
    "recebiveis": "Papel",
    "papel": "Papel",
    "papeis": "Papel",   # é assim que a HG Brasil chama (confirmado: KNCR11)
    "fiagro": "Papel",
    "hibrido": "Híbrido",
    "hibridos": "Híbrido",
    "fundo de fundos": "Híbrido",
    "fof": "Híbrido",
}


def _normalizar_chave(texto):
    """Remove acento, baixa a caixa e colapsa espaços/pontuação — pra
    comparar 'Shopping Centers' com 'shopping centers' e 'Títulos e Val.
    Mob.' com 'titulos e val mob' sem depender de bater a grafia exata."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def macro_segmento_fii(sub_segmento):
    chave = _normalizar_chave(sub_segmento)
    if not chave:
        return "Outros"
    resultado = MAPA_SEGMENTO_MACRO.get(chave)
    if resultado is None:
        # Fica registrado no log do Actions pra dar pra ver rapidinho
        # quais segmentos novos/diferentes apareceram e completar o mapa.
        log(f"  segmento de FII não mapeado (caiu em 'Outros'): {sub_segmento!r}")
        return "Outros"
    return resultado


# Camada de segurança por cima do texto raspado do Fundamentus: pros FIIs
# mais líquidos/conhecidos (justamente os que mais aparecem no topo das
# listas por patrimônio), fixa a classificação com base em fonte pública
# confirmada (ex: classificação ANBIMA/CVM), independente do que o texto
# scraped disser. Existe porque o texto do Fundamentus pra um fundo
# específico pode não bater com nenhuma chave do MAPA_SEGMENTO_MACRO (ou
# bater errado) sem eu conseguir testar ao vivo contra a página deles
# pra descobrir o motivo exato. Vale a pena crescer essa lista conforme
# forem aparecendo mais casos errados.
OVERRIDE_SEGMENTO_TICKER = {
    "KNCR11": "Papel",   # Kinea Rendimentos Imobiliários — CRI/papel
    "KNIP11": "Papel",   # Kinea Índices de Preços — CRI/papel
    "MXRF11": "Papel",   # Maxi Renda — papel
    "CPTS11": "Papel",   # Capitânia Securities — papel
    "RECR11": "Papel",   # REC Recebíveis Imobiliários — papel
    "IRDM11": "Papel",   # Iridium Recebíveis Imobiliários — papel
    "HCTR11": "Papel",   # Hectare CE — papel
    "VGIR11": "Papel",   # Valora RE III — papel
    "DEVA11": "Papel",   # Devant Recebíveis Imobiliários — papel
    "KNCA11": "Papel",   # Fiagro Kinea — papel/CRA
    # --- Logístico: mesmo problema do BRCO11 (relatado pelo Ricardo) pode
    # afetar qualquer um desses — nem o texto do Fundamentus nem o
    # classification.sector da HG são garantidos bater com as chaves de
    # MAPA_SEGMENTO_MACRO ("logistica", "galpoes logisticos" etc.), então
    # trava os principais/mais líquidos FIIs de logística da B3 direto
    # aqui (lista checada contra Investidor10/Funds Explorer/Suno em
    # 05/09/2026 — vale revisar se a carteira desses fundos mudar).
    "BRCO11": "Logístico",  # Bresco Logística
    "HGLG11": "Logístico",  # Pátria Logística (ex-CSHG Logística) — o maior/mais líquido do segmento
    "XPLG11": "Logístico",  # XP Log
    "BTLG11": "Logístico",  # BTG Pactual Logística
    "VILG11": "Logístico",  # Vinci Logística
    "LVBI11": "Logístico",  # VBI Logístico
    "GGRC11": "Logístico",  # GGR Covepi Renda
    "HSLG11": "Logístico",  # HSI Logística
    "RBRL11": "Logístico",  # RBR Log
    "TRBL11": "Logístico",  # SDI Logística Rio / Tellus Rio Bravo Renda Logística
}


def aplicar_override_segmento(ticker, segmento_calculado):
    return OVERRIDE_SEGMENTO_TICKER.get(ticker, segmento_calculado)


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
            # "logos" já vem nesse endpoint pra alguns ativos (não é
            # garantido pra todos) — mesmo campo (square_small/square_large)
            # que o site já usa ao vivo no navegador em outras telas (busca
            # rápida, comparador). Sem custo extra de requisição; se vier
            # vazio aqui, enriquecer_acoes_com_fundamentals tenta de novo, e
            # se nenhuma das duas trouxer nada o site cai pro "selo" com as
            # iniciais do ticker (fallback já existente no index.html).
            logos = item.get("logos") or {}
            ativos.append({
                "ticker": item["symbol"],
                "nome": item.get("name") or item.get("full_name") or item["symbol"],
                "setor": macro_setor(classificacao.get("sector")),
                "logo": logos.get("square_small") or logos.get("square_large"),
            })
        log(f"  página {pagina}: +{len(resultados)} itens (acumulado ações: {len(ativos)})")
        if len(resultados) < 20:  # heurística de "última página" — ajuste se
            break                  # o plano tiver um page_size diferente
        pagina += 1
        time.sleep(PAUSA_ENTRE_LOTES)
    return ativos


# ---------------------------------------------------------------------------
# 2) FUNDAMENTOS DAS AÇÕES — P/L, P/VP, VPA, LPA, P/Receita, EV/EBITDA,
#    Margens (bruta, líquida, EBITDA), ROE, ROA, ROIC, Dív.Líq/EBITDA,
#    Dívida/Patrimônio, Liquidez Corrente, DY e Valor de Mercado
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

            # Só sobrescreve o logo se /fundamentals trouxer um e o
            # /tickers não tiver trazido nada antes (ver comentário em
            # buscar_universo_acoes) — nunca apaga um logo que já veio ok.
            if not registro.get("logo"):
                logos_fund = resultado.get("logos") or {}
                registro["logo"] = logos_fund.get("square_small") or logos_fund.get("square_large")

            if ttm:
                valuation = ttm.get("valuation") or {}
                margens = ttm.get("margins") or {}
                dividendos = ttm.get("dividends") or {}
                rentabilidade = ttm.get("profitability") or {}
                alavancagem = ttm.get("leverage") or {}
                registro["pl"] = valuation.get("price_to_earnings_ratio")
                registro["pvp"] = valuation.get("price_to_book_ratio")
                registro["margem_liquida_pct"] = margens.get("net_profit_margin")
                registro["dy_pct"] = dividendos.get("yield_percent")
                registro["roe_pct"] = rentabilidade.get("return_on_equity")
                # Indicadores extras do botão "Customizar" da lista de ações
                # no site (o visitante liga/desliga cada coluna). Vêm todos
                # do mesmo TTM que já era lido acima — nenhuma requisição a
                # mais na HG Brasil.
                registro["vpa"] = valuation.get("book_value_per_share")
                registro["lpa"] = valuation.get("earnings_per_share")
                registro["p_receita"] = valuation.get("price_to_sales_ratio")
                registro["ev_ebitda"] = valuation.get("ev_to_ebitda")
                registro["margem_bruta_pct"] = margens.get("gross_profit_margin")
                registro["margem_ebitda_pct"] = margens.get("ebitda_margin")
                registro["roa_pct"] = rentabilidade.get("return_on_assets")
                registro["roic_pct"] = rentabilidade.get("return_on_invested_capital")
                registro["div_liq_ebitda"] = alavancagem.get("net_debt_to_ebitda_ratio")
                registro["divida_patrimonio"] = alavancagem.get("debt_to_equity_ratio")
                registro["liquidez_corrente"] = alavancagem.get("current_ratio")
            else:
                for campo_vazio in (
                    "pl", "pvp", "margem_liquida_pct", "dy_pct", "roe_pct",
                    "vpa", "lpa", "p_receita", "ev_ebitda", "margem_bruta_pct",
                    "margem_ebitda_pct", "roa_pct", "roic_pct", "div_liq_ebitda",
                    "divida_patrimonio", "liquidez_corrente",
                ):
                    registro[campo_vazio] = None

        log(f"  lote {i}-{i+len(lote)} ok ({len(dados.get('results', []))} retornados)")
        time.sleep(PAUSA_ENTRE_LOTES)

    # Descarta ações sem cotação (delistadas/suspensas) pra não poluir a lista
    return [a for a in universo if a.get("preco") is not None]


# ---------------------------------------------------------------------------
# 3) FIIs — cotação/variação via HG Brasil, fundamentos via Fundamentus
# ---------------------------------------------------------------------------
def _normalizar_num_br(valor):
    """Aceita tanto texto BR ('12,34', '1.234,56') quanto valores já
    convertidos em número pelo pandas (float/int/NaN) — o pd.read_html
    com decimal=","/thousands="." já converte a maioria das colunas
    sozinho, então aplicar essa função de novo em cima de um float pronto
    corrompe o valor (ex: 0.88 -> "0.88" -> remove o ponto -> 88.0)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        # NaN é float e "é diferente de si mesmo" — forma padrão de checar
        if valor != valor:
            return None
        return float(valor)
    t = str(valor).strip().replace("%", "")
    if t in ("", "-", "N/A", "nan", "None"):
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
    # O Fundamentus é um site antigo e serve as páginas em ISO-8859-1
    # (Latin-1), não em UTF-8 — forçar UTF-8 aqui é o que corrompia os
    # acentos ("Híbrido" virava "H�brido"). requests já detecta isso
    # sozinho via apparent_encoding; não sobrescrever.
    resp.encoding = resp.apparent_encoding or "ISO-8859-1"

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
        segmento_bruto = str(linha.get("Segmento", "")).strip()
        segmento_final = aplicar_override_segmento(ticker, macro_segmento_fii(segmento_bruto))
        if ticker in OVERRIDE_SEGMENTO_TICKER:
            log(f"  override aplicado em {ticker}: Fundamentus disse {segmento_bruto!r} -> forçado para {segmento_final!r}")
        fiis.append({
            "ticker": ticker,
            "nome": ticker,  # default — sobrescrito pela HG se o lote não falhar
            "segmento": segmento_final,
            "dy_pct": _normalizar_num_br(linha.get("Dividend Yield")),
            "pvp": _normalizar_num_br(linha.get("P/VP")),
            "patrimonio_liquido": _normalizar_num_br(linha.get("Valor de Mercado")),
            # Preço já sai do próprio Fundamentus — antes isso só vinha da
            # HG Brasil em enriquecer_fiis_com_quotes(), e um lote da HG
            # que falhasse (rate limit, timeout) derrubava o FII inteiro
            # da lista final (o filtro descarta quem não tem preço). Com
            # o Fundamentus como base, a HG vira só um complemento
            # (variação do dia) — se ela falhar, o FII continua na lista.
            "preco": _normalizar_num_br(linha.get("Cotação")),
            "logo": None,  # Fundamentus não tem logo — vem da HG em enriquecer_fiis_com_quotes()
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
            # Preço já veio do Fundamentus (mais fresco, é o request mais
            # recente) — só usa o da HG se por algum motivo o Fundamentus
            # não trouxe (nunca sobrescreve com None).
            if quote.get("value") is not None:
                registro["preco"] = quote.get("value")
            registro["variacao_dia_pct"] = quote.get("change_percent")
            # Só usa o valor de mercado da HG como fallback — o do
            # Fundamentus já é o "oficial" pra manter consistência com o
            # P/VP (os dois vêm da mesma fonte, mesma data-base).
            if registro.get("patrimonio_liquido") is None:
                registro["patrimonio_liquido"] = quote.get("market_cap")

            # Segmento: a HG Brasil devolve classification.sector também
            # pra FII (confirmado ao vivo: KNCR11 -> "Papéis") — é a MESMA
            # fonte que a "Consulta Ações, FIIs..." já usa e mostra
            # certinho, então tem prioridade sobre o texto raspado do
            # Fundamentus (que se mostrou inconsistente pra alguns
            # fundos). Só mantém o valor do Fundamentus se a HG não
            # devolver nada pra esse ticker nesse lote.
            setor_hg = (resultado.get("classification") or {}).get("sector")
            if setor_hg:
                registro["segmento"] = aplicar_override_segmento(simbolo, macro_segmento_fii(setor_hg))

            # Logo: mesmo campo (logos.square_small/square_large) que o
            # site já usa ao vivo pra ações e FIIs na "Consulta Ações,
            # FIIs..." e no comparador (confirmado funcionando ali) — vem
            # desse MESMO endpoint /v2/finance/quotes.
            logos = resultado.get("logos") or {}
            logo = logos.get("square_small") or logos.get("square_large")
            if logo:
                registro["logo"] = logo

        time.sleep(PAUSA_ENTRE_LOTES)

    return [f for f in fiis if f.get("preco") is not None]



# ---------------------------------------------------------------------------
# 4) FI-INFRA — B3 (lista, cotas, rendimentos) + HG (preço) + CVM (VP/cota)
# ---------------------------------------------------------------------------
B3_LISTA_FUNDOS = "https://sistemaswebb3-listados.b3.com.br/fundsListedProxy/Search/GetListFunds"
B3_SUPLEMENTO = "https://sistemaswebb3-listados.b3.com.br/fundsProxy/fundsCall/GetListedSupplementFunds"
B3_TIPO_FI_INFRA_TEXTO = "FI-INFRA"   # usado na lista (fundsListedProxy)
B3_TIPO_FI_INFRA_NUM = 27             # usado no suplemento (fundsProxy) — é o 27 das URLs funds-main/27/<SIGLA>/fi-infra
CVM_REGISTRO = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
CVM_INF_DIARIO = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ym}.zip"
# Casamento B3 x CVM pela quantidade de cotas: (PL ÷ valor da cota) na CVM
# tem de bater com a quantidade publicada pela B3. 2% de folga cobre
# emissão/amortização entre as datas das duas fontes.
TOLERANCIA_COTAS = 0.02
# P/VP fora disso é quase certamente casamento errado — descarta com log.
PVP_MIN, PVP_MAX = 0.5, 1.8
UA_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _b64(payload):
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _num_b3(texto):
    """'15.492.047' -> 15492047 ; '0,85000000000' -> 0.85"""
    if texto is None:
        return None
    t = str(texto).strip()
    if not t:
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _data_b3(texto):
    try:
        return datetime.strptime(str(texto).strip(), "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def _so_digitos(cnpj):
    return re.sub(r"\D", "", cnpj or "")


def buscar_lista_fi_infra():
    """Todos os FI-Infra listados na B3 (41 em 22/09/2026). A B3 recusa
    pageSize > 100, então pagina."""
    fundos, pagina = [], 1
    while True:
        url = f"{B3_LISTA_FUNDOS}/" + _b64({
            "language": "pt-br", "pageNumber": pagina, "pageSize": 100,
            "typeFund": B3_TIPO_FI_INFRA_TEXTO, "keyword": "",
        })
        resp = requests.get(url, headers=UA_NAVEGADOR, timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        for linha in dados.get("results") or []:
            sigla = (linha.get("acronym") or "").strip().upper()
            if sigla:
                fundos.append({"sigla": sigla, "ticker": sigla + "11",
                               "nome_b3": (linha.get("fundName") or "").strip()})
        total = int((dados.get("page") or {}).get("totalPages") or 1)
        if pagina >= total:
            break
        pagina += 1
    log(f"  B3: {len(fundos)} FI-Infra listados")
    return fundos


def buscar_suplemento_b3(sigla):
    """Quantidade de cotas e rendimentos pagos, direto da B3."""
    url = f"{B3_SUPLEMENTO}/" + _b64({"cnpj": "0", "identifierFund": sigla, "typeFund": B3_TIPO_FI_INFRA_NUM})
    resp = requests.get(url, headers=UA_NAVEGADOR, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json() if resp.text.strip() else {}


def rendimentos_12m(suplemento, hoje=None):
    """Soma dos rendimentos (R$/cota) com pagamento nos últimos 365 dias."""
    hoje = hoje or datetime.now(timezone.utc).date()
    inicio = hoje - timedelta(days=365)
    total, n = 0.0, 0
    for ev in suplemento.get("cashDividends") or []:
        data = _data_b3(ev.get("paymentDate")) or _data_b3(ev.get("lastDatePrior"))
        valor = _num_b3(ev.get("rate"))
        if data and valor and inicio < data <= hoje:
            total += valor
            n += 1
    return (total if n else None), n


def _ler_zip_csv(conteudo, nome_parcial=None):
    """Lê CSV(s) de um zip da CVM (ISO-8859-1, separador ';') linha a
    linha, sem carregar tudo em DataFrame (o informe diário tem ~600 mil
    linhas por mês)."""
    with zipfile.ZipFile(io.BytesIO(conteudo)) as zf:
        for nome in zf.namelist():
            if not nome.lower().endswith(".csv"):
                continue
            if nome_parcial and nome_parcial not in nome.lower():
                continue
            with zf.open(nome) as bruto:
                texto = io.TextIOWrapper(bruto, encoding="iso-8859-1", newline="")
                for linha in csv.DictReader(texto, delimiter=";"):
                    yield linha


def _baixar(url):
    resp = requests.get(url, headers=UA_NAVEGADOR, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def candidatos_cvm_infra():
    """CNPJs (de classe) cujo nome indica fundo incentivado de infraestrutura."""
    conteudo = _baixar(CVM_REGISTRO)
    if not conteudo:
        raise RuntimeError("CVM: registro_fundo_classe.zip não encontrado")
    cands = {}
    for linha in _ler_zip_csv(conteudo, "registro_classe"):
        nome = _normalizar_chave(linha.get("Denominacao_Social"))
        if ("infra" in nome) or ("incentivad" in nome):
            cnpj = _so_digitos(linha.get("CNPJ_Classe"))
            if cnpj:
                cands[cnpj] = linha.get("Denominacao_Social") or ""
    log(f"  CVM: {len(cands)} classes com INFRA/INCENTIVADO no nome")
    return cands


def cotas_cvm(candidatos, hoje=None):
    """Última posição de cada candidato no informe diário: valor da cota,
    PL e cotas (PL ÷ cota). Lê o mês atual e o anterior (no começo do mês o
    arquivo novo ainda está vazio)."""
    hoje = hoje or datetime.now(timezone.utc).date()
    anterior = (hoje.replace(day=1) - timedelta(days=1))
    ultimo = {}
    for ym in (f"{anterior:%Y%m}", f"{hoje:%Y%m}"):
        conteudo = _baixar(CVM_INF_DIARIO.format(ym=ym))
        if not conteudo:
            log(f"  CVM: inf_diario_fi_{ym}.zip ainda não publicado")
            continue
        for linha in _ler_zip_csv(conteudo):
            cnpj = _so_digitos(linha.get("CNPJ_FUNDO_CLASSE") or linha.get("CNPJ_FUNDO"))
            if cnpj not in candidatos:
                continue
            try:
                cota = float(linha.get("VL_QUOTA") or 0)
                pl = float(linha.get("VL_PATRIM_LIQ") or 0)
            except ValueError:
                continue
            if cota <= 0 or pl <= 0:
                continue
            chave = (cnpj, (linha.get("ID_SUBCLASSE") or "").strip())
            data = linha.get("DT_COMPTC") or ""
            if chave not in ultimo or data >= ultimo[chave]["data"]:
                ultimo[chave] = {"cnpj": cnpj, "data": data, "cota": cota, "pl": pl, "cotas": pl / cota,
                                 "nome": candidatos[cnpj]}
    log(f"  CVM: posição de {len(ultimo)} classes/subclasses de infra")
    return list(ultimo.values())


def casar_vp(fundo, posicoes):
    """Acha na CVM o fundo com a mesma quantidade de cotas da B3."""
    q = fundo.get("cotas_b3")
    if not q:
        return None, "sem quantidade de cotas na B3"
    perto = [p for p in posicoes if abs(p["cotas"] / q - 1) <= TOLERANCIA_COTAS]
    if not perto:
        return None, f"nenhuma classe da CVM com ~{q:,.0f} cotas"
    preco = fundo.get("preco")
    if len(perto) > 1 and preco:
        perto.sort(key=lambda p: abs(p["cota"] / preco - 1))
    escolhido = perto[0]
    if preco:
        pvp = preco / escolhido["cota"]
        if not (PVP_MIN <= pvp <= PVP_MAX):
            return None, f"P/VP {pvp:.2f} fora de [{PVP_MIN}, {PVP_MAX}] — casamento descartado ({escolhido['nome']})"
    obs = f"{len(perto)} candidatos" if len(perto) > 1 else "casamento único"
    return escolhido, obs


def coletar_fi_infra():
    log("=== FI-Infra ===")
    fundos = buscar_lista_fi_infra()
    for f in fundos:
        try:
            sup = buscar_suplemento_b3(f["sigla"])
        except Exception as e:  # noqa: BLE001
            log(f"  {f['ticker']}: suplemento da B3 falhou ({e})")
            sup = {}
        f["cotas_b3"] = _num_b3(sup.get("quantity"))
        f["rend_12m"], f["n_rend"] = rendimentos_12m(sup)
        f["nome_pregao"] = (sup.get("fund") or "").strip()
        time.sleep(PAUSA_ENTRE_LOTES)

    # Preço e variação: HG (mesma fonte e mesmo limite de 5 por chamada dos FIIs)
    por_ticker = {f["ticker"]: f for f in fundos}
    tickers = list(por_ticker)
    for i in range(0, len(tickers), LOTE_QUOTES):
        lote = tickers[i:i + LOTE_QUOTES]
        try:
            dados = requisitar(f"{BASE_HG}/quotes", {
                "format": "json-cors", "tickers": ",".join(f"B3:{t}" for t in lote), "key": CHAVE_HG,
            })
        except Exception as e:  # noqa: BLE001
            log(f"  HG: lote {lote} falhou ({e})")
            continue
        for r in dados.get("results", []):
            reg = por_ticker.get(r.get("symbol"))
            if not reg:
                continue
            q = r.get("quote") or {}
            reg["preco"] = q.get("value")
            reg["variacao_dia_pct"] = q.get("change_percent")
            reg["nome"] = r.get("name") or reg["ticker"]
            logos = r.get("logos") or {}
            reg["logo"] = logos.get("square_small") or logos.get("square_large")
        time.sleep(PAUSA_ENTRE_LOTES)

    posicoes = []
    try:
        posicoes = cotas_cvm(candidatos_cvm_infra())
    except Exception as e:  # noqa: BLE001
        log(f"  CVM indisponível — FI-Infra saem sem P/VP nesta rodada: {e}")

    saida = []
    for f in fundos:
        if f.get("preco") is None:
            log(f"  {f['ticker']}: sem cotação na HG — fica de fora")
            continue
        vp, obs = casar_vp(f, posicoes) if posicoes else (None, "sem dados da CVM")
        pvp = round(f["preco"] / vp["cota"], 2) if vp else None
        dy = round(f["rend_12m"] / f["preco"] * 100, 2) if f.get("rend_12m") else None
        log(f"  {f['ticker']}: preço {f['preco']} | cotas B3 {f.get('cotas_b3') or 0:,.0f} | "
            f"VP/cota {vp['cota'] if vp else '-'} ({obs}{', CNPJ ' + vp['cnpj'] if vp else ''}) | "
            f"P/VP {pvp if pvp is not None else '-'} | DY {dy if dy is not None else '-'}% ({f['n_rend']} pagtos)")
        saida.append({
            "ticker": f["ticker"],
            "nome": f.get("nome") or f["nome_b3"] or f["ticker"],
            "segmento": "Infra",
            "tipo_fundo": "FI-Infra",
            "dy_pct": dy,
            "pvp": pvp,
            "vp_cota": round(vp["cota"], 4) if vp else None,
            "patrimonio_liquido": round(vp["pl"], 2) if vp else None,
            "preco": f["preco"],
            "variacao_dia_pct": f.get("variacao_dia_pct"),
            "logo": f.get("logo"),
            "cnpj": vp["cnpj"] if vp else None,
        })
    com_pvp = sum(1 for x in saida if x["pvp"] is not None)
    log(f"  FI-Infra: {len(saida)} com preço, {com_pvp} com P/VP")
    return saida


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    agora = datetime.now(timezone.utc).isoformat()

    if "--so-infra" in sys.argv:
        # Diagnóstico: roda só a etapa de FI-Infra e imprime, sem gravar nada.
        for x in coletar_fi_infra():
            print(json.dumps(x, ensure_ascii=False))
        return

    log("=== Ações ===")
    universo_acoes = buscar_universo_acoes()
    acoes = enriquecer_acoes_com_fundamentals(universo_acoes)
    with open(SAIDA_ACOES, "w", encoding="utf-8") as f:
        json.dump({"atualizado_em": agora, "ativos": acoes}, f, ensure_ascii=False, indent=2)
    log(f"Gravado {SAIDA_ACOES} com {len(acoes)} ações")

    log("=== FIIs ===")
    fiis_fundamentus = buscar_fiis_fundamentus()
    fiis = enriquecer_fiis_com_quotes(fiis_fundamentus)
    # FI-Infra entram no mesmo arquivo (segmento "Infra"). Etapa isolada:
    # se falhar, os FIIs são gravados do mesmo jeito.
    try:
        ja = {f["ticker"] for f in fiis}
        infra = [x for x in coletar_fi_infra() if x["ticker"] not in ja]
        fiis.extend(infra)
    except Exception as e:  # noqa: BLE001
        log(f"FI-Infra: etapa falhou, gravando só os FIIs: {e}")
    with open(SAIDA_FIIS, "w", encoding="utf-8") as f:
        json.dump({"atualizado_em": agora, "ativos": fiis}, f, ensure_ascii=False, indent=2)
    log(f"Gravado {SAIDA_FIIS} com {len(fiis)} FIIs")


if __name__ == "__main__":
    main()
