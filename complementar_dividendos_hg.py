#!/usr/bin/env python3
"""
Complementa o histórico de dividendos coletado do Yahoo Finance com os
proventos antigos da HG Brasil.

POR QUE ISSO EXISTE
-------------------
O `coletar_reinv_yahoo.py` monta `data/reinv-historico/<TICKER>.json` com
preços + dividendos do Yahoo. Só que, para vários ativos da B3, a série de
DIVIDENDOS do Yahoo começa muito depois da série de PREÇOS — em geral porque
o ativo mudou de ticker em algum momento (o Yahoo costuma manter os preços
do código antigo, mas perde os proventos).

Caso real que motivou este script: BTLG11 (que era TRXL11 até 12/2019).
O simulador de reinvestimento do site achava preço desde 2016, mas só 55
pagamentos de dividendo, quando existiram 119 no período — o histórico do
Yahoo começa em 02/2022. Resultado: R$ 1.000 investidos em 09/2016
apareciam como +183% quando o resultado real foi +397%. Não era erro de
cálculo, era buraco de dado — e o simulador não tinha como saber disso.

O QUE ELE FAZ
-------------
Para cada arquivo já gerado pelo robô do Yahoo:
  1. Compara a data do primeiro PREÇO com a data do primeiro DIVIDENDO.
  2. Se houver buraco (dividendo começando depois do preço), busca na HG
     Brasil só a janela que falta.
  3. Junta as duas séries, remove duplicatas e regrava o arquivo.
  4. Registra no próprio JSON de onde veio cada coisa e desde quando os
     dividendos são confiáveis (`dividendos_desde`), pro site poder avisar
     o visitante quando AINDA sobrar buraco (nem toda API tem tudo).

O formato do arquivo não muda — só ganha mais itens em "dividendos" e
alguns campos de metadado, que o site ignora se não souber ler.

COMO USAR
---------
    export HGBRASIL_TOKEN=xxxxx
    python3 complementar_dividendos_hg.py                 # roda em todos
    python3 complementar_dividendos_hg.py BTLG11 HGLG11   # só nesses
    python3 complementar_dividendos_hg.py --dry-run       # só mostra o que faria

No GitHub Actions, rodar SEMPRE depois do coletar_reinv_yahoo.py, no mesmo
workflow (ele lê e regrava os arquivos que o outro acabou de gerar).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

PASTA = os.environ.get("PASTA_REINV", "data/reinv-historico")
CHAVE_HG = os.environ.get("HGBRASIL_TOKEN", "")
BASE_HG = "https://api.hgbrasil.com/v2/finance"

PAUSA_ENTRE_CHAMADAS = 1.2   # segundos — a HG limita requisições por minuto
ANOS_POR_CHAMADA = 2         # janelas menores falham menos que um range de 10 anos
TOLERANCIA_DUPLICATA_DIAS = 5
FOLGA_ACEITAVEL_DIAS = 45    # buraco menor que isso não vale uma chamada

DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(msg, flush=True)


def iso(d):
    return d.strftime("%Y-%m-%d")


def parse(d):
    return datetime.strptime(d, "%Y-%m-%d").date()


def requisitar(url, tentativas=3):
    for tentativa in range(1, tentativas + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "viverderenda-bot/1.0"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if tentativa == tentativas:
                raise
            espera = 3 * tentativa
            log(f"      tentativa {tentativa} falhou ({e}); repetindo em {espera}s")
            time.sleep(espera)


def buscar_dividendos_hg(ticker, inicio, fim):
    """Proventos em dinheiro já pagos, no formato [{'data':..,'valor':..}].

    A HG devolve results[0].series[] com category/status/payment_date/amount —
    mesmos campos que o site já usa no caminho ao vivo (reinvBuscarDividendos
    no index.html). Só entram 'cash' + 'paid': provento anunciado e ainda não
    pago não pode virar cota no simulador.
    """
    encontrados = []
    janela_inicio = inicio
    while janela_inicio <= fim:
        janela_fim = min(fim, date(janela_inicio.year + ANOS_POR_CHAMADA, janela_inicio.month, 1) - timedelta(days=1))
        params = urllib.parse.urlencode({
            "format": "json-cors",
            "key": CHAVE_HG,
            "tickers": f"B3:{ticker}",
            "start_date": iso(janela_inicio),
            "end_date": iso(janela_fim),
        })
        dados = requisitar(f"{BASE_HG}/dividends?{params}")

        erro = None
        if isinstance(dados.get("errors"), list) and dados["errors"]:
            erro = dados["errors"][0].get("message")
        elif isinstance(dados.get("error"), dict):
            erro = dados["error"].get("message")
        elif isinstance(dados.get("error"), str):
            erro = dados["error"]
        if erro:
            raise RuntimeError(f"HG Brasil recusou: {erro}")

        resultados = dados.get("results")
        ativo = resultados[0] if isinstance(resultados, list) and resultados else (resultados or {})
        for ev in (ativo.get("series") or []):
            if ev.get("category") != "cash" or ev.get("status") != "paid":
                continue
            valor, quando = ev.get("amount"), ev.get("payment_date")
            if not isinstance(valor, (int, float)) or valor <= 0 or not quando:
                continue
            encontrados.append({"data": str(quando)[:10], "valor": float(valor)})

        janela_inicio = janela_fim + timedelta(days=1)
        time.sleep(PAUSA_ENTRE_CHAMADAS)

    return encontrados


def mesclar(existentes, novos):
    """Junta as duas séries sem duplicar o mesmo pagamento.

    Duas fontes raramente concordam no dia exato (uma usa data-com, outra
    data de pagamento), então considera duplicata o provento de valor
    parecido dentro de uma janela de poucos dias — mesma lógica que o site
    já usa pra deduplicar splits repetidos do Yahoo.
    """
    juntos = list(existentes)
    adicionados = 0
    for novo in sorted(novos, key=lambda x: x["data"]):
        d_novo = parse(novo["data"])
        duplicado = False
        for atual in juntos:
            if abs((parse(atual["data"]) - d_novo).days) > TOLERANCIA_DUPLICATA_DIAS:
                continue
            maior = max(abs(atual["valor"]), abs(novo["valor"]), 1e-9)
            if abs(atual["valor"] - novo["valor"]) / maior < 0.02:  # até 2% de diferença
                duplicado = True
                break
        if not duplicado:
            juntos.append(novo)
            adicionados += 1
    juntos.sort(key=lambda x: x["data"])
    return juntos, adicionados


def processar(caminho):
    ticker = os.path.splitext(os.path.basename(caminho))[0]
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)

    precos = dados.get("precos") or []
    dividendos = dados.get("dividendos") or []
    if not precos:
        log(f"  {ticker}: sem série de preços, pulando")
        return "sem_precos"

    primeiro_preco = parse(min(p["data"] for p in precos))
    primeiro_div = parse(min(d["data"] for d in dividendos)) if dividendos else None
    hoje = date.today()

    if primeiro_div and (primeiro_div - primeiro_preco).days <= FOLGA_ACEITAVEL_DIAS:
        log(f"  {ticker}: sem buraco (preços desde {primeiro_preco}, dividendos desde {primeiro_div})")
        return "ok"

    # Fecha a janela no ÚLTIMO DIA DO MÊS ANTERIOR ao primeiro dividendo que
    # o Yahoo já tem, em vez de na véspera dele. Motivo: as duas fontes usam
    # convenções de data diferentes pro MESMO pagamento — o Yahoo costuma
    # marcar a data-com e a HG devolve payment_date, uns 10 a 15 dias depois.
    # Buscando até a véspera, o pagamento do mês da emenda voltaria pela HG
    # com data suficientemente distante pra escapar da checagem de duplicata
    # e ser contado duas vezes. Preenchendo só MESES INTEIROS que o Yahoo não
    # cobre, essa sobreposição deixa de existir.
    if primeiro_div:
        fim_busca = primeiro_div.replace(day=1) - timedelta(days=1)
        if fim_busca < primeiro_preco:
            log(f"  {ticker}: buraco menor que um mês, nada a complementar")
            return "ok"
    else:
        fim_busca = hoje
    buraco_dias = (fim_busca - primeiro_preco).days
    log(f"  {ticker}: buraco de {buraco_dias} dias "
        f"(preços desde {primeiro_preco}, dividendos desde {primeiro_div or '—'}) — buscando na HG Brasil")

    if DRY_RUN:
        return "dry_run"

    try:
        antigos = buscar_dividendos_hg(ticker, primeiro_preco, fim_busca)
    except Exception as e:  # noqa: BLE001
        log(f"     falhou: {e} — arquivo mantido como está")
        return "falhou"

    if not antigos:
        log("     a HG Brasil também não tem proventos nessa janela")
        # Ainda assim registra a cobertura real: é isso que permite o site
        # avisar o visitante em vez de mostrar um número subestimado calado.
        dados["dividendos_desde"] = iso(primeiro_div) if primeiro_div else None
        dados["dividendos_fontes"] = ["yahoo"]
        dados["dividendos_complementados_em"] = iso(hoje)
        gravar(caminho, dados)
        return "sem_dados_extras"

    dividendos, adicionados = mesclar(dividendos, antigos)
    dados["dividendos"] = dividendos
    dados["dividendos_desde"] = min(d["data"] for d in dividendos)
    dados["dividendos_fontes"] = ["yahoo", "hgbrasil"] if adicionados else ["yahoo"]
    dados["dividendos_complementados_em"] = iso(hoje)
    gravar(caminho, dados)
    log(f"     +{adicionados} proventos adicionados "
        f"(total {len(dividendos)}, agora desde {dados['dividendos_desde']})")
    return "complementado"


def gravar(caminho, dados):
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporario, caminho)  # troca atômica: nunca deixa arquivo pela metade


def main():
    if not CHAVE_HG and not DRY_RUN:
        log("HGBRASIL_TOKEN não definido — nada a fazer.")
        return 1
    if not os.path.isdir(PASTA):
        log(f"Pasta {PASTA} não encontrada.")
        return 1

    pedidos = [a.upper() for a in sys.argv[1:] if not a.startswith("--")]
    arquivos = sorted(
        os.path.join(PASTA, nome)
        for nome in os.listdir(PASTA)
        if nome.endswith(".json") and not nome.startswith("_")
        and (not pedidos or os.path.splitext(nome)[0].upper() in pedidos)
    )
    if not arquivos:
        log("Nenhum arquivo de histórico encontrado.")
        return 0

    log(f"Verificando {len(arquivos)} ativo(s) em {PASTA}{' (dry-run)' if DRY_RUN else ''}...")
    resumo = {}
    for caminho in arquivos:
        try:
            resultado = processar(caminho)
        except Exception as e:  # noqa: BLE001
            log(f"  {os.path.basename(caminho)}: erro inesperado ({e})")
            resultado = "erro"
        resumo[resultado] = resumo.get(resultado, 0) + 1

    log("\nResumo: " + ", ".join(f"{k}={v}" for k, v in sorted(resumo.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
