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
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------
# CONFIGURAÇÃO — lista de ativos.
#
# Combina DUAS fontes:
#
# 1) POOL_ACOES + POOL_FIIS, importados direto do coletar_hgbrasil.py —
#    a MESMA lista que já alimenta os rankings do site. Editar a lista
#    lá também muda o que este script coleta, sem editar dois lugares.
#
# 2) IBOV_SNAPSHOT — a carteira teórica oficial do Ibovespa (índice de
#    ações mais negociadas da B3), pra ampliar bastante a cobertura de
#    ações além do pool curado do site. NÃO é importada dinamicamente
#    porque não existe hoje uma fonte gratuita simples pra isso — é um
#    RETRATO estático, coletado manualmente em 18/08/2026 (carteira
#    vigente maio–agosto/2026, 79 ativos, conferida contra o anúncio
#    oficial da B3 e cruzada em duas fontes jornalísticas
#    independentes). A B3 rebalanceia essa carteira a cada 4 meses
#    (jan/mai/set) — então esse retrato vai ficar levemente desatualizado
#    com o tempo (ativo novo que entrar não aparece; ativo que sair só
#    vai dar erro inofensivo na coleta, sem quebrar nada). Vale revisar
#    esse bloco a cada poucos meses.
#
# NÃO incluímos hoje a lista completa do IFIX (índice de FIIs) — a única
# fonte que achei pra composição atual dele veio com fortes sinais de
# ter sido gerada por IA sem revisão (texto com resquício de prompt e um
# ticker duplicado com dois valores diferentes), então preferimos não
# arriscar publicar tickers possivelmente inventados.
# ---------------------------------------------------------------------
IBOV_SNAPSHOT = [
    "ALOS3", "ABEV3", "ASAI3", "AURE3", "AXIA3", "AXIA6", "AZZA3", "B3SA3",
    "BBSE3", "BBDC3", "BBDC4", "BRAP4", "BBAS3", "BRKM5", "BRAV3", "BPAC11",
    "CXSE3", "CEAB3", "CMIG4", "COGN3", "CSMG3", "CPLE3", "CSAN3", "CPFE3",
    "CMIN3", "CURY3", "CYRE3", "DIRR3", "EMBJ3", "ENGI11", "ENEV3", "EGIE3",
    "EQTL3", "FLRY3", "GGBR4", "GOAU4", "HAPV3", "HYPE3", "IGTI11", "ISAE4",
    "ITSA4", "ITUB4", "KLBN11", "RENT3", "LREN3", "MGLU3", "POMO4", "MBRF3",
    "BEEF3", "MOTV3", "MRVE3", "MULT3", "NATU3", "PETR3", "PETR4", "RECV3",
    "PSSA3", "PRIO3", "RADL3", "RDOR3", "RAIL3", "SBSP3", "SANB11", "CSNA3",
    "SLCE3", "SMFT3", "SUZB3", "TAEE11", "VIVT3", "TIMS3", "TOTS3", "UGPA3",
    "USIM5", "VALE3", "VAMO3", "VBBR3", "VIVA3", "WEGE3", "YDUQ3",
]

try:
    from coletar_hgbrasil import POOL_ACOES, POOL_FIIS, obter_token_brapi
except ImportError:
    print(
        "AVISO: não consegui importar de coletar_hgbrasil.py — usando "
        "retrato fixo de POOL_ACOES/POOL_FIIS (pode estar desatualizado) "
        "e lendo o token da brapi direto da variável de ambiente. Rode "
        "este script a partir da raiz do repositório.",
        file=sys.stderr,
    )
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

    def obter_token_brapi():
        return os.environ.get("VIVERDERENDA_BRAPI", "").strip()

# Base "sempre disponível" (sem depender de rede): ranking do site +
# carteira do Ibovespa. A lista completa de FIIs via brapi.dev é
# adicionada em main(), se o token VIVERDERENDA_BRAPI tiver acesso ao
# endpoint /api/quote/list — ver buscar_fiis_brapi().
TICKERS_BASE = sorted(set(POOL_ACOES) | set(POOL_FIIS) | set(IBOV_SNAPSHOT))

BRAPI_LIST_URL = "https://brapi.dev/api/quote/list"


def buscar_fiis_brapi(token):
    """Busca a lista completa de FIIs (type=fund) via brapi.dev
    (/api/quote/list), paginando até o fim. Retorna um set de tickers, ou
    um set vazio se o endpoint não estiver disponível nesse plano/token
    (ex: 401/403) — nesse caso o robô simplesmente não expande a lista
    além do que já tinha, sem quebrar a execução.
    """
    tickers = set()
    pagina = 1
    paginas_max = 15  # trava de segurança — não deve existir plano com >1500 FIIs
    while pagina <= paginas_max:
        url = f"{BRAPI_LIST_URL}?type=fund&limit=100&page={pagina}&token={token}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if pagina == 1:
                print(
                    f"AVISO: brapi.dev /quote/list respondeu HTTP {e.code} "
                    f"({e.reason}) — provavelmente esse endpoint não está "
                    "disponível no plano do seu token. Mantendo só a lista "
                    "de FIIs já conhecida (POOL_FIIS).",
                    file=sys.stderr,
                )
            break
        except Exception as e:  # noqa: BLE001
            print(f"AVISO: falha ao consultar brapi.dev /quote/list: {e}", file=sys.stderr)
            break

        stocks = dados.get("stocks") or []
        if not stocks:
            break
        for item in stocks:
            symbol = item.get("stock") or item.get("symbol")
            if symbol:
                tickers.add(symbol.strip().upper())

        if not dados.get("hasNextPage"):
            break
        pagina += 1
        time.sleep(0.5)

    return tickers


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


def deduplicar_splits(splits_ordenados):
    """O Yahoo Finance às vezes registra o MESMO desdobramento/grupamento
    duas vezes, com datas próximas (1-2 dias de diferença) — nenhum
    fundo/empresa faz o mesmo split duas vezes em poucos dias. Junta como
    um evento só quando a proporção é igual e as datas estão próximas
    (até 5 dias), mantendo o primeiro."""
    JANELA_DIAS_DUPLICATA = 5
    resultado = []
    for s in splits_ordenados:
        data_s = datetime.strptime(s["data"], "%Y-%m-%d")
        duplicata = any(
            s["numerador"] == r["numerador"]
            and s["denominador"] == r["denominador"]
            and abs((data_s - datetime.strptime(r["data"], "%Y-%m-%d")).days) <= JANELA_DIAS_DUPLICATA
            for r in resultado
        )
        if not duplicata:
            resultado.append(s)
    return resultado


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

    # A calculadora de reinvestimento (index.html) já usa isso pra
    # ajustar quantidade/preço em caso de desdobramento/grupamento —
    # deduplicar_splits() abaixo evita que o mesmo evento real apareça
    # duas vezes na base do Yahoo (visto de verdade no ALZR11: um único
    # desdobramento efetivado em 05/05/2025 apareceu como dois registros,
    # 06/05 e 07/05, o que inflava o resultado em 10x indevidos).
    splits_brutos_lista = sorted(
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
    splits = deduplicar_splits(splits_brutos_lista)

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


# ---------------------------------------------------------------------
# Filtro de qualidade — aplicado SÓ aos tickers que vieram da brapi.dev
# (o pool curado do site + a carteira do Ibovespa nunca são filtrados,
# são confiáveis por definição). O endpoint type=fund da brapi traz
# TODO tipo de fundo negociado em bolsa — inclusive muitos recém-
# listados, com pouquíssimo histórico e zero dividendo pago ainda (ex:
# "03BK11", 20 pregões, 0 dividendos, listado há 12 dias). Um fundo
# assim não serve pra nada na calculadora de Reinvestimento — só ocupa
# espaço no repositório. Em vez de tentar adivinhar liquidez pelos
# campos da brapi (não testei ao vivo o que eles realmente trazem),
# usamos os dados REAIS que o próprio Yahoo devolveu pra decidir.
# ---------------------------------------------------------------------
MINIMO_PREGOES_UTEIS = 60      # ~3 meses de negociação
MINIMO_DIVIDENDOS_UTEIS = 1    # já pagou pelo menos 1 provento alguma vez


def main():
    os.makedirs(SAIDA_DIR, exist_ok=True)
    status = {"gerado_em": datetime.now(timezone.utc).isoformat(), "ativos": {}}

    # Tenta expandir a lista de FIIs via brapi.dev antes de começar a
    # coleta de verdade. Se o token não tiver acesso a esse endpoint
    # (plano gratuito pode ou não incluir — ver comentário em
    # buscar_fiis_brapi), simplesmente segue só com TICKERS_BASE.
    token_brapi = obter_token_brapi()
    tickers_confiaveis = set(TICKERS_BASE)
    tickers_brapi = set()
    if token_brapi:
        print("Consultando brapi.dev por uma lista completa de FIIs (type=fund)...")
        fiis_brapi = buscar_fiis_brapi(token_brapi)
        tickers_brapi = fiis_brapi - tickers_confiaveis
        if tickers_brapi:
            print(
                f"  +{len(tickers_brapi)} FIIs novos encontrados via brapi.dev "
                f"(serão filtrados depois: só entram os com pelo menos "
                f"{MINIMO_PREGOES_UTEIS} pregões e {MINIMO_DIVIDENDOS_UTEIS}+ "
                f"dividendo pago).\n"
            )
        elif fiis_brapi:
            print("  nenhum FII novo além dos já conhecidos.\n")
        # se fiis_brapi veio vazio, buscar_fiis_brapi já explicou o motivo
    else:
        print(
            "INFO: VIVERDERENDA_BRAPI não configurado neste ambiente — "
            "pulando a expansão de FIIs via brapi.dev, usando só a lista "
            "já conhecida (ranking do site + Ibovespa).\n"
        )

    # Ordem: primeiro os confiáveis (sempre entram), depois os da brapi
    # (passam pelo filtro de qualidade abaixo).
    todos_tickers = sorted(tickers_confiaveis) + sorted(tickers_brapi)

    print(f"Coletando histórico Yahoo Finance para {len(todos_tickers)} ativos...\n")

    filtrados_qualidade = 0

    for i, ticker in enumerate(todos_tickers, 1):
        eh_confiavel = ticker in tickers_confiaveis
        print(f"[{i}/{len(todos_tickers)}] {ticker}...")
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

        # Filtro de qualidade — só pros tickers vindos da brapi. Se não
        # passar, NÃO grava o arquivo (evita inchar o repositório com
        # fundo sem histórico útil), mas registra no status o motivo,
        # pra ficar visível por que ele não entrou.
        if not eh_confiavel and (
            processado["total_pregoes"] < MINIMO_PREGOES_UTEIS
            or processado["total_dividendos"] < MINIMO_DIVIDENDOS_UTEIS
        ):
            filtrados_qualidade += 1
            status["ativos"][ticker] = {
                "ok": False,
                "filtrado_qualidade": True,
                "motivo": (
                    f"{processado['total_pregoes']} pregões / "
                    f"{processado['total_dividendos']} dividendos "
                    f"(mínimo: {MINIMO_PREGOES_UTEIS} pregões, "
                    f"{MINIMO_DIVIDENDOS_UTEIS} dividendo)"
                ),
            }
            print(f"  filtrado (histórico insuficiente): {status['ativos'][ticker]['motivo']}")
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
    if filtrados_qualidade:
        print(f"{filtrados_qualidade} ativo(s) da brapi filtrado(s) por histórico insuficiente (não gravaram arquivo).")
    falhas = [
        t for t, s in status["ativos"].items()
        if not s.get("ok") and not s.get("filtrado_qualidade")
    ]
    if falhas:
        print(f"Atenção: {len(falhas)} ativo(s) falharam de verdade: {', '.join(falhas)}")


if __name__ == "__main__":
    main()
