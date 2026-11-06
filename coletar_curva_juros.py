#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletar_curva_juros.py
---------------------------------------------------------------------
Baixa a Estrutura a Termo da Taxa de Juros (ETTJ) — a "curva de juros
futuros" — publicada pela ANBIMA todo dia útil, e grava a curva
PREFIXADA (a que o mercado associa a juros/DI futuro) em
data/curva-juros.json.

Por que este robô existe
-------------------------
A seção "Juros Futuros x Fundos Imobiliários" do site (index.html,
#juros-x-fiis) existe pra mostrar visualmente por que FIIs reagem à
curva de juros: quando o mercado projeta juros mais altos pra frente,
FIIs tendem a cair, e vice-versa. A fase 1 dessa seção só mostrava o
histórico do IFIX (reaproveitando a Consulta Rápida); esta é a fase 2,
que traz a curva de juros em si.

A B3 negocia contratos futuros de DI (DI1), mas não publica um arquivo
simples de "vertice x taxa" pronto — o arquivo real (TaxaSwap) vem
dentro de um ZIP aninhado num .exe autoextraível, em texto de largura
fixa, bem mais frágil de manter. A ANBIMA já faz esse trabalho pra
todo mundo: ela AJUSTA um modelo (Svensson/Nelson-Siegel) às taxas
negociadas no dia e publica a curva resultante já pronta, por vértice
— é essa curva ajustada que a imprensa financeira chama de "curva de
juros", "curva do DI futuro" ou "curva pré".

Como foi descoberto (não documentado publicamente pela ANBIMA)
-----------------------------------------------------------------
A página institucional https://www.anbima.com.br/informacoes/est-termo/
tem um formulário de download (XLS/CSV/TXT/XML) que posta pra um
endpoint clássico, usado por vários projetos open-source dedicados a
isso (ex: github.com/rafa-rod/pyettj) — não testei esse endpoint ao
vivo (sem acesso à internet no ambiente onde este robô foi escrito),
então o formato exato da resposta XML (nomes de atributo dentro de
cada elemento) é a parte menos certa deste robô. Por isso a função
`interpretar_xml` é DEFENSIVA: não assume nomes de atributo fixos,
procura por substrings prováveis ("vertice"/"dias"/"prazo" pro prazo
em dias, "taxa" pra taxa), e LOGA a estrutura bruta encontrada se não
conseguir casar nada — isso dá visibilidade real no primeiro run do
GitHub Actions (que tem internet) pra corrigir rápido se o palpite
estiver errado, em vez de gravar silenciosamente um arquivo vazio ou
errado.

URL:    https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp
Método: POST (application/x-www-form-urlencoded)
Corpo:  Idioma=PT, Dt_Ref=DD/MM/AAAA, saida=xml
Resposta esperada: XML com elementos <VERTICES> (um por par
   vértice/taxa, agrupados por "Grupo" — PREFIXADOS, SELIC, IPCA
   etc.), <PARAMETRO> (parâmetros do modelo ajustado, não usados
   aqui — já usamos a curva pronta em VERTICES) e <ERRO>.

A ANBIMA só mantém os últimos ~5 dias úteis disponíveis por essa via —
por isso tenta a data de hoje e, se vier vazio (fim de semana, feriado,
ou o dia ainda não foi publicado), volta alguns dias até achar.

Saída: data/curva-juros.json
---------------------------------------------------------------------
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

URL_DOWNLOAD = "https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp"
URL_FONTE = "https://www.anbima.com.br/informacoes/est-termo/"

CAMINHO_SAIDA = os.path.join("data", "curva-juros.json")

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/xml,application/xml,text/html,*/*",
    "Referer": URL_FONTE,
}

TEMPO_LIMITE = 30  # segundos

# Quantos dias corridos pra trás tentar, se a data mais recente ainda
# não tiver curva publicada (fim de semana, feriado, antes do fechamento).
MAX_DIAS_PARA_TRAS = 7

# Candidatos de nome de atributo dentro de <VERTICES>, em ordem de
# preferência — case-insensitive, por SUBSTRING (não precisa bater
# exato: "Vertice" e "vertice_dias" batem com "vertice", por exemplo).
# Ver cabeçalho do arquivo sobre por que isso é defensivo.
CANDIDATOS_PRAZO = ("vertice", "dias", "prazo")
CANDIDATOS_TAXA = ("taxa",)
CANDIDATOS_GRUPO = ("grupo", "tipo", "curva")

# Como a ANBIMA rotula a curva prefixada (a que o mercado chama de
# "curva de juros"/"curva do DI futuro") — tentados em ordem, por
# substring, case-insensitive.
GRUPO_PREFIXADA_CANDIDATOS = ("prefixado", "prefixada", "pre")


def _primeiro_valor_por_substring(attrib_lower, candidatos):
    """Primeira chave de `attrib_lower` que CONTÉM alguma substring de
    `candidatos` (já em minúsculo). Devolve (chave, valor) ou (None, None)."""
    for candidato in candidatos:
        for chave, valor in attrib_lower.items():
            if candidato in chave:
                return chave, valor
    return None, None


def _numero(bruto):
    """"13,45" ou "13.45" -> 13.45. None se não for numérico."""
    texto = (bruto or "").strip().replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def baixar_xml(data_ref):
    """POST no endpoint clássico da ANBIMA pedindo a ETTJ de `data_ref`
    (objeto date). Devolve o texto da resposta (pode vir vazio/sem dado
    se a ANBIMA não tiver publicado essa data)."""
    payload = {
        "Idioma": "PT",
        "Dt_Ref": data_ref.strftime("%d/%m/%Y"),
        "saida": "xml",
    }
    resp = requests.post(
        URL_DOWNLOAD, data=payload, headers=CABECALHOS, timeout=TEMPO_LIMITE
    )
    resp.raise_for_status()
    return resp.text


def interpretar_xml(texto_xml):
    """Extrai os pontos (dias, taxa) da curva PREFIXADA a partir do XML
    da ANBIMA. Devolve (pontos, grupo_usado, grupos_vistos) — pontos
    vazio quer dizer "não achei nada reconhecível" (data sem curva
    publicada, ou mudança de formato — `grupos_vistos` ajuda a
    diagnosticar qual dos dois)."""
    try:
        raiz = ET.fromstring(texto_xml)
    except ET.ParseError:
        return [], None, []

    grupos_vistos = set()
    pontos_por_grupo = {}

    for elemento in raiz.iter():
        if elemento.tag.upper() != "VERTICES":
            continue
        attrib_lower = {k.lower(): v for k, v in elemento.attrib.items()}

        _, grupo = _primeiro_valor_por_substring(attrib_lower, CANDIDATOS_GRUPO)
        grupo = (grupo or "").strip()
        if grupo:
            grupos_vistos.add(grupo)

        _, prazo_bruto = _primeiro_valor_por_substring(attrib_lower, CANDIDATOS_PRAZO)
        _, taxa_bruta = _primeiro_valor_por_substring(attrib_lower, CANDIDATOS_TAXA)
        dias = _numero(prazo_bruto)
        taxa = _numero(taxa_bruta)
        if dias is None or taxa is None:
            continue

        pontos_por_grupo.setdefault(grupo, []).append({"dias": int(dias), "taxa": taxa})

    # Acha o grupo prefixado entre os vistos (case-insensitive, por
    # substring) — não assume o rótulo exato.
    grupo_usado = None
    for candidato in GRUPO_PREFIXADA_CANDIDATOS:
        for grupo in grupos_vistos:
            if candidato in grupo.lower():
                grupo_usado = grupo
                break
        if grupo_usado:
            break

    if not grupo_usado:
        return [], None, sorted(grupos_vistos)

    pontos = sorted(pontos_por_grupo.get(grupo_usado, []), key=lambda p: p["dias"])
    return pontos, grupo_usado, sorted(grupos_vistos)


def coletar(data_mais_recente=None):
    """Tenta baixar a curva da data mais recente possível, voltando dia
    a dia até MAX_DIAS_PARA_TRAS se vier vazia. Devolve (pontos,
    data_usada, grupo_usado) ou (None, None, None) se esgotar as tentativas."""
    data_alvo = data_mais_recente or datetime.now(timezone.utc).date()
    ultimos_grupos_vistos = []

    for i in range(MAX_DIAS_PARA_TRAS):
        data_tentativa = data_alvo - timedelta(days=i)
        try:
            texto = baixar_xml(data_tentativa)
        except requests.RequestException as exc:
            print(f"  {data_tentativa}: falha de rede ({exc})", file=sys.stderr)
            continue

        pontos, grupo_usado, grupos_vistos = interpretar_xml(texto)
        if grupos_vistos:
            ultimos_grupos_vistos = grupos_vistos
        if pontos:
            return pontos, data_tentativa, grupo_usado

        if not grupos_vistos:
            # Nem um <VERTICES> foi achado — provavelmente a resposta não é
            # o XML esperado (página de erro, HTML, formato mudou). Loga um
            # pedaço bruto da resposta pra dar visibilidade real no log do
            # GitHub Actions (que tem internet pra validar, diferente de
            # onde este robô foi escrito — ver cabeçalho do arquivo).
            amostra = texto.strip().replace("\n", " ")[:600]
            print(f"  {data_tentativa}: resposta não reconhecida (tamanho={len(texto)} chars). "
                  f"Primeiros 600 caracteres: {amostra!r}", file=sys.stderr)
        print(f"  {data_tentativa}: sem curva prefixada publicada, tentando o dia anterior...", file=sys.stderr)

    if ultimos_grupos_vistos:
        print(
            f"AVISO: a ANBIMA respondeu com dados, mas nenhum grupo pareceu ser a curva "
            f"prefixada. Grupos vistos: {ultimos_grupos_vistos} — "
            f"confira GRUPO_PREFIXADA_CANDIDATOS neste arquivo.",
            file=sys.stderr,
        )
    return None, None, None


def main():
    print("Baixando a curva de juros (ETTJ prefixada) da ANBIMA...")
    pontos, data_usada, grupo_usado = coletar()

    if not pontos:
        print("\nERRO: não consegui obter a curva prefixada dos últimos "
              f"{MAX_DIAS_PARA_TRAS} dias corridos.", file=sys.stderr)
        return 1

    print(f"\n  data de referência: {data_usada} (grupo ANBIMA: \"{grupo_usado}\")")
    print(f"  {len(pontos)} vértice(s): {', '.join(str(p['dias']) for p in pontos)} dias")

    saida = {
        "_leia_me": (
            "Curva de juros futuros (ETTJ prefixada), publicada diariamente pela ANBIMA — "
            "o mercado se refere a ela como \"curva de juros\", \"curva do DI futuro\" ou "
            "\"curva pré\". Cada ponto em 'pontos' é um vértice (prazo em dias corridos) "
            "com a taxa anual prefixada (%) que o mercado precifica para esse prazo, "
            "ajustada pelo modelo da própria ANBIMA (Svensson/Nelson-Siegel) a partir dos "
            "títulos públicos negociados no dia. Quando essa curva sobe (sobretudo nos "
            "vértices mais longos), FIIs tendem a cair — a renda fixa fica mais atraente "
            "na comparação; quando recua, o movimento costuma ser o oposto. Ver seção "
            "'Juros Futuros x Fundos Imobiliários' em viverderenda.dev.br."
        ),
        "fonte": "ANBIMA — Estrutura a Termo da Taxa de Juros (ETTJ), curva prefixada",
        "fonte_url": URL_FONTE,
        "grupo_anbima": grupo_usado,
        "data_referencia": data_usada.isoformat(),
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pontos": pontos,
    }

    os.makedirs(os.path.dirname(CAMINHO_SAIDA), exist_ok=True)
    with open(CAMINHO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    tamanho = os.path.getsize(CAMINHO_SAIDA) / 1024
    print(f"\n  {CAMINHO_SAIDA} gravado ({tamanho:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
