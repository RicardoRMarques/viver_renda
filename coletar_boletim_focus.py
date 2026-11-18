#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletar_boletim_focus.py
---------------------------------------------------------------------
Baixa a mediana das expectativas de mercado (Boletim Focus) pra PIB,
IPCA, Selic e Câmbio, direto da API oficial do Banco Central, e grava
em data/boletim-focus.json.

Por que este robô existe
-------------------------
A seção "O que o mercado espera" do site (index.html, #boletim-focus)
mostra a mesma informação do PDF semanal do Focus
(https://www.bcb.gov.br/publicacoes/focus) — a mediana das respostas
de centenas de instituições sobre pra onde PIB, inflação, juros e
câmbio devem ir nos próximos anos. Em vez de baixar e interpretar o
PDF (frágil: muda de layout, e o conteúdo real já está em formato
aberto em outro lugar), este robô consulta a API "Expectativas de
Mercado" do BCB (portal Olinda/Dados Abertos), que publica a MESMA
pesquisa em JSON, com histórico completo pronto — nenhum PDF
envolvido.

API usada
---------
https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais
Protocolo OData, GET com querystring. Campos relevantes:
  Indicador        — nome exato do indicador (ex: "PIB Total", "IPCA",
                      "Selic", "Câmbio" — strings confirmadas contra a
                      API em 17/11/2026, batendo com o PDF do Focus de
                      13/11/2026: IPCA/2027 mediana 5,00 e Selic/2027
                      mediana 12,75 e Câmbio/2027 mediana 5,45 e
                      PIB Total/2027 mediana 1,02, todos conferidos).
  DataReferencia   — o ANO ao qual a projeção se refere (ex: "2027"),
                      não a data da pesquisa.
  Data             — a data em que a pesquisa foi respondida/apurada.
  Mediana          — a mediana das respostas nessa data, pra esse
                      indicador e ano de referência — é o número que o
                      PDF do Focus mostra em destaque na coluna "Hoje".
  baseCalculo      — 0 = todos os respondentes; 1 = só quem respondeu
                      nos últimos 30 dias. Usamos SEMPRE 0, que é o
                      valor que o PDF do Focus destaca em negrito na
                      coluna "Hoje" (conferido: numeroRespondentes bate
                      com a coluna "Resp.**" do PDF, não "Resp.***").
Sem necessidade de chave/autenticação; é um serviço público.

O que é salvo
--------------
Pra cada indicador, a mediana "de hoje" pros próximos 4 anos de
referência (ano atual até ano atual + 3 — mesma janela do PDF do
Focus), e a série histórica diária dos últimos ~365 dias SÓ pros 2
anos de referência mais próximos (ano atual e o seguinte) — são os
que o mercado mais atualiza e os mais relevantes pros gráficos de
"evolução da mediana". Diferente da curva de juros (ANBIMA, sem API
de histórico — robô próprio tem que ir acumulando 1 ponto por dia),
aqui a própria API do BCB já tem o histórico completo, então cada
execução deste robô busca a janela de novo do zero — não precisa
acumular estado entre execuções.

Saída: data/boletim-focus.json
---------------------------------------------------------------------
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import requests

# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

URL_API = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/"
    "odata/ExpectativasMercadoAnuais"
)
URL_FONTE = "https://www.bcb.gov.br/publicacoes/focus"

CAMINHO_SAIDA = os.path.join("data", "boletim-focus.json")

# Nomes exatos como a API espera em "Indicador" — ver nota no cabeçalho
# sobre como foram conferidos contra o PDF do Focus de 13/11/2026.
INDICADORES = ["PIB Total", "IPCA", "Selic", "Câmbio"]

QUANTIDADE_ANOS_SNAPSHOT = 4  # ano atual + 3 seguintes, igual ao PDF do Focus
QUANTIDADE_ANOS_HISTORICO = 2  # só os 2 anos mais próximos, pros gráficos

DIAS_HISTORICO = 365
LIMITE_PONTOS_HISTORICO = 400  # folga sobre ~252 dias úteis/ano

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

TEMPO_LIMITE = 30  # segundos
TENTATIVAS = 3


def log(msg):
    print(f"[coletar_boletim_focus] {msg}", flush=True)


def requisitar(params, tentativas=TENTATIVAS):
    """GET na API Olinda com retry simples — a mesma consulta roda 4
    indicadores x 6 anos (4 snapshot + 2 histórico) = 24 chamadas numa
    única execução; um retry curto evita que uma falha de rede pontual
    derrube a coleta inteira por causa de 1 chamada."""
    # requests.get(params=...) codifica espaço como "+" (application/
    # x-www-form-urlencoded); esse endpoint OData só aceita "%20" — com
    # "+" devolve 400 Bad Request. urlencode com quote_via=quote resolve.
    url = f"{URL_API}?{urlencode(params, quote_via=quote)}"

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(url, headers=CABECALHOS, timeout=TEMPO_LIMITE)
            resp.raise_for_status()
            dados = resp.json()
            return dados.get("value", [])
        except Exception as exc:  # noqa: BLE001 — robô de coleta, loga e tenta de novo
            ultimo_erro = exc
            log(f"  falha ({tentativa}/{tentativas}): {exc}")
            time.sleep(1.5 * tentativa)
    raise RuntimeError(f"desisti após {tentativas} tentativas: {ultimo_erro}")


def buscar_snapshot(indicador, ano):
    """Mediana mais recente (baseCalculo=0) de `indicador` pro ano de
    referência `ano`. Devolve o item da API (dict) ou None se não
    houver nenhum ponto pra essa combinação ainda (ex: ano distante
    demais no futuro, que o mercado ainda não começou a projetar)."""
    params = {
        "$filter": f"Indicador eq '{indicador}' and DataReferencia eq '{ano}' and baseCalculo eq 0",
        "$orderby": "Data desc",
        "$top": 1,
        "$select": "Data,Mediana,numeroRespondentes",
        "$format": "json",
    }
    pontos = requisitar(params)
    return pontos[0] if pontos else None


def buscar_historico(indicador, ano, desde_iso):
    """Série diária (baseCalculo=0) de `indicador` pro ano de
    referência `ano`, desde `desde_iso` (YYYY-MM-DD) até hoje, em
    ordem cronológica. Pode vir vazia se o mercado ainda não tiver
    começado a projetar esse ano de referência na janela pedida."""
    params = {
        "$filter": (
            f"Indicador eq '{indicador}' and DataReferencia eq '{ano}' "
            f"and baseCalculo eq 0 and Data ge '{desde_iso}'"
        ),
        "$orderby": "Data asc",
        "$top": LIMITE_PONTOS_HISTORICO,
        "$select": "Data,Mediana",
        "$format": "json",
    }
    return requisitar(params)


def main():
    agora_utc = datetime.now(timezone.utc)
    hoje_brasilia = (agora_utc - timedelta(hours=3)).date()
    ano_atual = hoje_brasilia.year
    anos_snapshot = [str(ano_atual + i) for i in range(QUANTIDADE_ANOS_SNAPSHOT)]
    anos_historico = anos_snapshot[:QUANTIDADE_ANOS_HISTORICO]
    desde_iso = (hoje_brasilia - timedelta(days=DIAS_HISTORICO)).isoformat()

    log(f"Coletando Boletim Focus — anos {anos_snapshot[0]} a {anos_snapshot[-1]}...")

    indicadores_saida = {}
    data_pesquisa_mais_recente = None

    for indicador in INDICADORES:
        log(f"  {indicador}...")
        anos_dados = {}

        for ano in anos_snapshot:
            item = buscar_snapshot(indicador, ano)
            if item is None:
                log(f"    {ano}: sem dado ainda (ok pra anos distantes)")
                continue
            anos_dados[ano] = {
                "mediana": item["Mediana"],
                "numeroRespondentes": item.get("numeroRespondentes"),
            }
            if data_pesquisa_mais_recente is None or item["Data"] > data_pesquisa_mais_recente:
                data_pesquisa_mais_recente = item["Data"]

        if not anos_dados:
            log(f"    AVISO: nenhum ano retornou dado pra {indicador!r}, pulando indicador.")
            continue

        historico = {}
        for ano in anos_historico:
            pontos = buscar_historico(indicador, ano, desde_iso)
            historico[ano] = [
                {"data": p["Data"], "mediana": p["Mediana"]} for p in pontos
            ]
            log(f"    histórico {ano}: {len(pontos)} ponto(s)")

        indicadores_saida[indicador] = {"anos": anos_dados, "historico": historico}

    if not indicadores_saida:
        print("\nERRO: nenhum indicador foi coletado com sucesso.", file=sys.stderr)
        return 1

    saida = {
        "_leia_me": (
            "Mediana das expectativas de mercado (Boletim Focus) do Banco Central — "
            "pesquisa semanal com centenas de bancos, gestoras e corretoras sobre pra "
            "onde PIB, inflação, juros e câmbio devem ir. Mesmos números do PDF publicado "
            "em bcb.gov.br/publicacoes/focus, obtidos pela API aberta 'Expectativas de "
            "Mercado' (Olinda/Dados Abertos), não por leitura de PDF. Em 'indicadores', "
            "cada chave é um indicador (PIB Total, IPCA, Selic, Câmbio); 'anos' traz a "
            "mediana mais recente por ano de referência (ano atual + 3 seguintes); "
            "'historico' traz a série diária da mediana dos últimos ~365 dias, só pros "
            "2 anos de referência mais próximos (os mais acompanhados). Ver seção "
            "'O que o mercado espera' em viverderenda.dev.br."
        ),
        "fonte": "Banco Central do Brasil — Boletim Focus (Expectativas de Mercado)",
        "fonte_url": URL_FONTE,
        "data_pesquisa": data_pesquisa_mais_recente,
        "atualizado_em": agora_utc.isoformat(timespec="seconds"),
        "indicadores": indicadores_saida,
    }

    os.makedirs(os.path.dirname(CAMINHO_SAIDA), exist_ok=True)
    with open(CAMINHO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    tamanho = os.path.getsize(CAMINHO_SAIDA) / 1024
    log(f"\n  {CAMINHO_SAIDA} gravado ({tamanho:.1f} KB), "
        f"{len(indicadores_saida)}/{len(INDICADORES)} indicador(es), "
        f"pesquisa mais recente: {data_pesquisa_mais_recente}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
