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
isso (ex: github.com/rafa-rod/pyettj). A primeira versão deste robô
tinha sido escrita sem conseguir testar isso ao vivo (sem acesso à
internet no ambiente onde foi escrito) e tinha suposições erradas
sobre o formato da resposta — foi rodado de verdade via GitHub Actions
em 06/11/2026 e corrigido com a estrutura REAL confirmada:

URL:    https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp
Método: POST (application/x-www-form-urlencoded)
Corpo:  Idioma=PT, Dt_Ref=DD/MM/AAAA, saida=xml
Resposta real (exemplo, 05/11/2026):
   <CURVAZERO>
     <DATA_REFERENCIA>05/11/2026</DATA_REFERENCIA>
     <PARAMETROS>
       <PARAMETRO Grupo='PREFIXADOS' B1='...' B2='...' L1='...' L2='.../>
       <PARAMETRO Grupo='IPCA' .../>
     </PARAMETROS>
     <ETTJ>
       <VERTICES Vertice='252' IPCA='6,4273' Prefixados='14,2002' Inflacao='7,3034' />
       <VERTICES Vertice='378' IPCA='7,2327' Prefixados='14,4879' Inflacao='...' />
       ...
     </ETTJ>
   </CURVAZERO>
Ou seja: <VERTICES> NÃO tem um atributo "Grupo" (esse só existe em
<PARAMETRO>, que não usamos — já usamos a curva pronta, não os
parâmetros do modelo) — cada <VERTICES> é uma linha com TODAS as
curvas (Prefixados/IPCA/Inflacao) na mesma tag, uma coluna por curva.
"Vertice" é o prazo — 252 ≈ 1 ano, o que indica DIAS ÚTEIS (252
dias úteis/ano é a convenção padrão brasileira), não dias corridos,
mas a ANBIMA não documenta isso explicitamente. Decimais vêm com
vírgula. Quando não há curva publicada pra uma data (fim de semana,
feriado, ou o dia ainda não fechou), a resposta inteira é só
"<CURVAZERO>\r </CURVAZERO>" — sem nenhum <VERTICES>.

A ANBIMA só mantém os últimos ~5 dias úteis disponíveis por essa via —
por isso tenta a data de hoje e, se vier vazio, volta alguns dias até
achar.

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

# Série histórica de um ponto representativo da curva — usada pra
# comparar visualmente, ao longo do tempo, com o IFIX (ver
# carregarComparacaoJurosIfix no index.html). A ANBIMA só devolve a
# foto do dia (ou, no máximo, os últimos ~5 dias úteis) — não uma
# série longa pronta — então este robô vai acumulando 1 ponto por dia
# útil a cada execução, igual o padrão já usado em
# noticias-historico.json.
CAMINHO_HISTORICO = os.path.join("data", "curva-juros-historico.json")
HISTORICO_MAX_PONTOS = 756  # ~3 anos de dias úteis — limite generoso

# Vértice FIXO (não "o mais curto disponível"!) — descoberto em
# 12/11/2026: a ANBIMA passou a publicar também um vértice de 126 dias
# (~6 meses), mais curto que o de 252 (~1 ano) que vinha sendo usado
# até então. Como atualizar_historico() pegava `pontos[0]` (o vértice
# mais curto da lista, não um prazo fixo), a série de "taxa do vértice
# de 1 ano" trocou de prazo silenciosamente num único dia, misturando
# uma taxa de 6 meses no meio de uma série de taxas de 1 ano — queda
# "real" de 14,04% pra 13,79% que na verdade era, em boa parte, só o
# efeito de comparar prazos diferentes (o vértice de 252 dias nesse
# mesmo dia estava em 14,14%, bem mais parecido com o dia anterior).
# Fixar o vértice evita que isso se repita se a ANBIMA publicar um
# vértice ainda mais curto no futuro.
VERTICE_ALVO_DIAS = 252

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

# Estrutura REAL confirmada rodando contra o endpoint de verdade em
# 06/11/2026 (ver histórico do commit anterior pra o que foi tentado
# antes, com suposições erradas): a resposta não é <ETTJ> na raiz, é
# <CURVAZERO><DATA_REFERENCIA>.../ <PARAMETROS>...</ PARAMETROS>
# <ETTJ><VERTICES .../></ETTJ></CURVAZERO>. Cada <VERTICES> NÃO tem um
# atributo "Grupo" — tem um atributo "Vertice" (prazo em dias) e UMA
# COLUNA POR CURVA na mesma linha: Prefixados, IPCA, Inflacao. Exemplo
# real: <VERTICES Vertice='252' IPCA='6,4273' Prefixados='14,2002'
# Inflacao='7,3034' />. Decimais vêm com vírgula. Quando a ANBIMA não
# tem curva publicada pra uma data (fim de semana, feriado, ou o dia
# ainda não fechou), a resposta inteira é só
# "<CURVAZERO>\r </CURVAZERO>" (27 bytes, sem <VERTICES> nenhum).
ATRIBUTO_VERTICE = "Vertice"
ATRIBUTO_TAXA_PREFIXADA = "Prefixados"


def _numero(bruto):
    """Número no formato brasileiro: "." é separador de MILHAR, "," é
    decimal — "1.008" é mil e oito, não um vírgula zero-zero-oito.
    Remove os pontos ANTES de trocar a vírgula por ponto decimal; sem
    essa ordem, "Vertice='1.008'" virava 1.008 e `int()` truncava pra
    1 — foi exatamente esse bug que gerou vértices fantasmas de "1" e
    "2" dias a partir de 1008/1134/.../2520 na primeira versão deste
    robô (confirmado no log do GitHub Actions em 06/11/2026). None se
    vazio ou não numérico (a ANBIMA deixa esses campos em branco pros
    vértices além do alcance da curva prefixada, por exemplo)."""
    texto = (bruto or "").strip()
    if not texto:
        return None
    texto = texto.replace(".", "").replace(",", ".")
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
    """Extrai os pontos (dias, taxa prefixada) a partir do XML da
    ANBIMA. Devolve (pontos, atributos_vistos) — pontos vazio quer
    dizer "sem curva publicada nessa data" (comum em fim de
    semana/feriado/antes do fechamento) OU "mudança de formato";
    `atributos_vistos` (os nomes de atributo do primeiro <VERTICES>
    encontrado, se houver) ajuda a diferenciar os dois casos no log."""
    try:
        raiz = ET.fromstring(texto_xml)
    except ET.ParseError:
        return [], []

    # Busca <VERTICES> só DENTRO de <ETTJ> especificamente (não em
    # qualquer lugar do documento) — defensivo, caso outra seção da
    # resposta um dia reuse a mesma tag com outro significado.
    #
    # NOTA pra quem for debugar isto de novo: os vértices "fantasma" de
    # 1/2 dias vistos na primeira versão deste robô (06/11/2026) NÃO
    # eram de outra seção — eram os vértices longos de verdade (1008,
    # 1134 ... 2520) truncados por um bug em `_numero()`, que não
    # removia o "." de milhar brasileiro antes de converter: "1.008"
    # virava 1.008 (não mil e oito) e `int()` cortava pra 1. O fix foi
    # em `_numero()`, não aqui.
    ettj_el = raiz.find("ETTJ")
    if ettj_el is None:
        return [], []

    pontos = []
    atributos_vistos = []

    for elemento in ettj_el.findall("VERTICES"):
        if not atributos_vistos:
            atributos_vistos = sorted(elemento.attrib.keys())

        dias = _numero(elemento.get(ATRIBUTO_VERTICE))
        taxa = _numero(elemento.get(ATRIBUTO_TAXA_PREFIXADA))
        if dias is None or taxa is None:
            continue
        pontos.append({"dias": int(dias), "taxa": taxa})

    pontos.sort(key=lambda p: p["dias"])
    return pontos, atributos_vistos


def coletar(data_mais_recente=None):
    """Tenta baixar a curva da data mais recente possível, voltando dia
    a dia até MAX_DIAS_PARA_TRAS se vier vazia. Devolve (pontos,
    data_usada) ou (None, None) se esgotar as tentativas."""
    data_alvo = data_mais_recente or datetime.now(timezone.utc).date()

    for i in range(MAX_DIAS_PARA_TRAS):
        data_tentativa = data_alvo - timedelta(days=i)
        try:
            texto = baixar_xml(data_tentativa)
        except requests.RequestException as exc:
            print(f"  {data_tentativa}: falha de rede ({exc})", file=sys.stderr)
            continue

        pontos, atributos_vistos = interpretar_xml(texto)
        if pontos:
            return pontos, data_tentativa

        if not atributos_vistos:
            # Nem um <VERTICES> foi achado — pode ser só "sem curva nessa
            # data" (normal em fim de semana/feriado/antes do fechamento,
            # a resposta vem como "<CURVAZERO></CURVAZERO>" vazio) ou a
            # resposta não é o XML esperado de verdade. Loga um pedaço
            # bruto pra diferenciar os dois casos no log do GitHub Actions
            # (que tem internet pra validar, diferente de onde este robô
            # foi escrito — ver cabeçalho do arquivo).
            amostra = texto.strip().replace("\n", " ")[:600]
            print(f"  {data_tentativa}: resposta sem <VERTICES> (tamanho={len(texto)} chars). "
                  f"Primeiros 600 caracteres: {amostra!r}", file=sys.stderr)
        else:
            print(f"  {data_tentativa}: <VERTICES> achado, mas sem os atributos esperados "
                  f"({ATRIBUTO_VERTICE!r}/{ATRIBUTO_TAXA_PREFIXADA!r}). "
                  f"Atributos vistos: {atributos_vistos}", file=sys.stderr)
        print(f"  {data_tentativa}: sem curva prefixada publicada, tentando o dia anterior...", file=sys.stderr)

    return None, None


def atualizar_historico(data_referencia, pontos):
    """Acrescenta 1 ponto (data, taxa do vértice FIXO de VERTICE_ALVO_DIAS
    dias — não o mais curto disponível, ver comentário na constante) ao
    arquivo de série histórica, sem duplicar se já houver um ponto pra
    essa mesma data (ex: robô rodado mais de uma vez no mesmo dia útil —
    nesse caso substitui pelo mais recente). Se a curva do dia não tiver
    esse vértice específico, NÃO mexe no histórico (evita misturar
    prazos diferentes na mesma série) e devolve o histórico como estava.
    """
    ponto_alvo = next((p for p in pontos if p["dias"] == VERTICE_ALVO_DIAS), None)

    historico = []
    if os.path.exists(CAMINHO_HISTORICO):
        try:
            with open(CAMINHO_HISTORICO, "r", encoding="utf-8") as arquivo:
                historico = (json.load(arquivo) or {}).get("pontos", [])
        except (json.JSONDecodeError, OSError) as exc:
            print(f"AVISO: não consegui ler o histórico anterior ({exc}); começando do zero.", file=sys.stderr)
            historico = []

    if ponto_alvo is None:
        disponiveis = [p["dias"] for p in pontos]
        print(f"AVISO: vértice de {VERTICE_ALVO_DIAS} dias não veio na curva de "
              f"{data_referencia} (vértices disponíveis: {disponiveis}) — não atualizando "
              "o histórico hoje, pra não misturar prazos diferentes na mesma série.",
              file=sys.stderr)
        return historico

    data_iso = data_referencia.isoformat()
    historico = [p for p in historico if p.get("data") != data_iso]
    historico.append({"data": data_iso, "taxa": ponto_alvo["taxa"]})
    historico.sort(key=lambda p: p["data"])
    historico = historico[-HISTORICO_MAX_PONTOS:]

    saida = {
        "_leia_me": (
            "Série histórica de um ponto representativo da curva de juros futuros "
            f"(ETTJ prefixada, vértice fixo de {VERTICE_ALVO_DIAS} dias úteis, ~1 ano) — "
            "1 valor por dia útil, acumulado ao longo do tempo por este robô (a ANBIMA só "
            "disponibiliza a foto do dia, não uma série longa pronta). Serve pra comparar "
            "visualmente com o histórico do IFIX e ver se os dois se movem de forma "
            "inversa — ver seção 'Juros Futuros x Fundos Imobiliários' em "
            "viverderenda.dev.br."
        ),
        "vertice_dias": VERTICE_ALVO_DIAS,
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pontos": historico,
    }
    os.makedirs(os.path.dirname(CAMINHO_HISTORICO), exist_ok=True)
    with open(CAMINHO_HISTORICO, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    return historico


def main():
    print("Baixando a curva de juros (ETTJ prefixada) da ANBIMA...")
    pontos, data_usada = coletar()

    if not pontos:
        print("\nERRO: não consegui obter a curva prefixada dos últimos "
              f"{MAX_DIAS_PARA_TRAS} dias corridos.", file=sys.stderr)
        return 1

    print(f"\n  data de referência: {data_usada}")
    print(f"  {len(pontos)} vértice(s): {', '.join(str(p['dias']) for p in pontos)} dias")

    saida = {
        "_leia_me": (
            "Curva de juros futuros (ETTJ prefixada), publicada diariamente pela ANBIMA — "
            "o mercado se refere a ela como \"curva de juros\", \"curva do DI futuro\" ou "
            "\"curva pré\". Cada ponto em 'pontos' é um vértice (prazo em dias úteis — "
            "252 é aproximadamente 1 ano) com a taxa anual prefixada (%) que o mercado "
            "precifica para esse prazo, ajustada pelo modelo da própria ANBIMA "
            "(Svensson/Nelson-Siegel) a partir dos títulos públicos negociados no dia. "
            "Quando essa curva sobe (sobretudo nos vértices mais longos), FIIs tendem a "
            "cair — a renda fixa fica mais atraente na comparação; quando recua, o "
            "movimento costuma ser o oposto. Ver seção 'Juros Futuros x Fundos "
            "Imobiliários' em viverderenda.dev.br."
        ),
        "fonte": "ANBIMA — Estrutura a Termo da Taxa de Juros (ETTJ), curva prefixada",
        "fonte_url": URL_FONTE,
        "data_referencia": data_usada.isoformat(),
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pontos": pontos,
    }

    os.makedirs(os.path.dirname(CAMINHO_SAIDA), exist_ok=True)
    with open(CAMINHO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    tamanho = os.path.getsize(CAMINHO_SAIDA) / 1024
    print(f"\n  {CAMINHO_SAIDA} gravado ({tamanho:.1f} KB)")

    historico = atualizar_historico(data_usada, pontos)
    print(f"  {CAMINHO_HISTORICO} atualizado ({len(historico)} dia(s) de histórico acumulado)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
