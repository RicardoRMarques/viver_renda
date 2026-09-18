#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletar_debentures.py
---------------------------------------------------------------------
Baixa as TAXAS INDICATIVAS do mercado secundário de debêntures que a
ANBIMA publica todo dia útil e grava em data/debentures.json.

Por que este robô existe
------------------------
Taxa de CDB, LCI e LCA não é dado público — não existe API gratuita que
publique isso, e é por isso que o site não tenta ser um buscador de
ofertas. Debênture é a exceção: a ANBIMA publica, aberto e sem login, a
taxa a que cada papel está sendo negociado no secundário. É o único
pedaço do crédito privado brasileiro sobre o qual dá para dizer algo
concreto usando só fonte pública.

O que o arquivo É e o que ele NÃO é
-----------------------------------
É uma taxa INDICATIVA de mercado secundário: a referência de onde o
papel está sendo negociado hoje entre instituições. NÃO é uma oferta
que o usuário consegue comprar, e não é preço de emissão nova. O site
precisa dizer isso na tela — é a diferença entre informar e enganar.

Formato do arquivo (descoberto testando, não estava documentado)
----------------------------------------------------------------
URL:      https://www.anbima.com.br/informacoes/merc-sec-debentures/arqs/db{AAMMDD}.txt
Encoding: latin-1 (ISO-8859-1) — decodificar como UTF-8 corrompe os
          acentos dos nomes de emissor ("IMOBILIÁRIOS" vira lixo).
Separador: @ (arroba), não vírgula nem ponto-e-vírgula.
Decimais: vírgula.
Ausente:  "--" (e "N/D" na coluna de duration).

Estrutura:
  linha 1  título institucional da ANBIMA
  linha 2  cabeçalho das colunas
  demais   um papel por linha, 15 campos:

    0 Código                        8  Intervalo Indicativo Mínimo
    1 Nome                          9  Intervalo Indicativo Máximo
    2 Repac./Venc. (dd/mm/aaaa)     10 PU
    3 Índice/Correção               11 % PU Par / % VNE
    4 Taxa de Compra                12 Duration (em DIAS ÚTEIS)
    5 Taxa de Venda                 13 % Reune
    6 Taxa Indicativa               14 Referência NTN-B
    7 Desvio Padrão

Duas armadilhas de leitura que custaram tempo:

1. Os marcadores "(*)" e "(**)" depois do nome do emissor marcam
   CLÁUSULA DE RESGATE/RECOMPRA — e não, como parecia óbvio, debênture
   incentivada da Lei 12.431. Confirmado na documentação da API da
   ANBIMA. Rotular isenção de IR com base neles colocaria no ar uma
   informação tributária errada, que é o pior tipo de erro que um site
   de educação financeira pode cometer. Este robô NÃO marca incentivada:
   o arquivo simplesmente não traz essa informação.

2. A coluna "Índice/Correção" traz a taxa CONTRATADA na emissão
   ("DI + 1,6%"), enquanto "Taxa Indicativa" traz onde o papel está
   sendo negociado HOJE (0,7181 = DI + 0,72%). São coisas diferentes, e
   a segunda é a que interessa. Num papel de crédito deteriorado a
   distância entre as duas é gritante: o HAPV15 foi emitido a DI + 1,75%
   e negocia a DI + 9,87%, com PU em 82% do par.

Saída: data/debentures.json
---------------------------------------------------------------------
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

URL_BASE = "https://www.anbima.com.br/informacoes/merc-sec-debentures/arqs/db{data}.txt"

# Quantos dias voltar procurando o último arquivo publicado.
#
# O arquivo do dia sai no fim da tarde, então rodar de manhã sempre pega
# o dia anterior. Somando feriado prolongado (Carnaval, Natal/Ano Novo)
# com um eventual atraso da ANBIMA, 10 dias corridos cobrem com folga
# sem o robô ficar martelando o servidor deles à toa.
DIAS_PARA_TRAS = 10

TEMPO_LIMITE = 45  # segundos por tentativa

# Um User-Agent de navegador de verdade. Sem isso, servidor de instituição
# financeira costuma devolver 403 para cliente sem identificação.
CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/plain,*/*",
}

CAMINHO_SAIDA = os.path.join("data", "debentures.json")

# Quantos campos a linha precisa ter para ser considerada dado. O arquivo
# às vezes termina a linha sem preencher as últimas colunas (o "% Reune" e
# a "Referência NTN-B" vêm vazios em boa parte dos papéis), então o corte
# é pelo que realmente importa, não pelos 15 exatos.
MINIMO_CAMPOS = 13

# Índice por dia útil, para converter duration de dias úteis em anos.
DIAS_UTEIS_NO_ANO = 252


# ---------------------------------------------------------------------
# Conversores
# ---------------------------------------------------------------------

def numero(bruto):
    """Converte "1089,197466" em 1089.197466.

    Devolve None para os marcadores de ausência do arquivo ("--", "N/D")
    e para qualquer coisa que não seja número. Devolver None e não 0.0 é
    deliberado: zero é um valor legítimo de taxa, e confundir "não
    negociou" com "negociou a zero" faria o site mostrar um papel
    fantasma no topo do ranking de menor taxa.
    """
    texto = (bruto or "").strip()
    if not texto or texto in {"--", "-", "N/D", "ND", "n/d"}:
        return None
    try:
        return float(texto.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def data_iso(bruto):
    """dd/mm/aaaa -> aaaa-mm-dd. Devolve None se não for data."""
    texto = (bruto or "").strip()
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def limpar_emissor(bruto):
    """Separa o nome do emissor dos marcadores de resgate antecipado.

    Devolve (nome, tem_clausula). Os marcadores "(*)" e "(**)" indicam
    cláusula de resgate/recompra — ou seja, o emissor pode chamar o papel
    de volta antes do vencimento. Isso é informação útil para quem compra
    (o prazo pode não ser o prazo), então vira um campo próprio em vez de
    ser jogado fora junto com a limpeza do nome.
    """
    texto = (bruto or "").strip()
    tem_clausula = "(*)" in texto or "(**)" in texto
    nome = texto.replace("(**)", "").replace("(*)", "")
    # Espaços duplos sobram quando os dois marcadores saem juntos.
    return " ".join(nome.split()), tem_clausula


# Os indexadores que já vimos no arquivo, e como reconhecê-los. A ordem
# importa: "IGP-M" precisa ser testado antes de qualquer regra mais
# frouxa que pudesse casar com "IGP".
def classificar_indexador(bruto):
    """"DI + 1,6%" -> ("di", 1.6). "IPCA + 7,43%" -> ("ipca", 7.43).

    Devolve (chave, taxa_contratada). Papel cujo formato não conhecemos
    volta como ("outro", None) e é CONTADO no aviso do fim da execução —
    mesma política do robô do Tesouro. O site mostra esses papéis com o
    rótulo cru do arquivo, em vez de escondê-los: sumir com papel por não
    saber classificar seria pior do que mostrá-lo sem categoria.
    """
    texto = (bruto or "").strip()
    normal = texto.upper().replace(" ", "")

    if normal.startswith("IGP-M") or normal.startswith("IGPM"):
        chave = "igpm"
    elif normal.startswith("IPCA"):
        chave = "ipca"
    elif normal.startswith("DI") or normal.startswith("%DI") or normal.endswith("DI"):
        chave = "di"
    elif normal.startswith("PRE") or normal.startswith("PRÉ"):
        chave = "prefixado"
    else:
        return "outro", None

    # A taxa contratada é o que vem depois do "+". Papel sem "+"
    # (ex: "% do DI") não tem spread contratado — fica None, e é correto.
    taxa = None
    if "+" in texto:
        taxa = numero(texto.split("+", 1)[1].replace("%", ""))
    return chave, taxa


# ---------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------

def baixar_do_dia(dia):
    """Tenta baixar o arquivo de um dia. Devolve o texto ou None."""
    url = URL_BASE.format(data=dia.strftime("%y%m%d"))
    try:
        resposta = requests.get(url, headers=CABECALHOS, timeout=TEMPO_LIMITE)
    except requests.RequestException as erro:
        print(f"  {dia.strftime('%d/%m/%Y')}: falha de rede ({erro})")
        return None

    if resposta.status_code == 404:
        return None
    if resposta.status_code != 200:
        print(f"  {dia.strftime('%d/%m/%Y')}: HTTP {resposta.status_code}")
        return None

    # O arquivo é latin-1. O requests adivinha errado com frequência
    # quando o servidor não manda charset, então forçamos.
    resposta.encoding = "latin-1"
    texto = resposta.text

    # Um 200 com página de erro HTML no corpo é mais comum do que parece
    # em servidor .asp antigo — checar o conteúdo, não só o status.
    if "@" not in texto or "ANBIMA" not in texto[:200]:
        print(f"  {dia.strftime('%d/%m/%Y')}: respondeu 200 mas não parece o arquivo")
        return None

    return texto


def baixar_mais_recente(hoje=None):
    """Volta no calendário até achar o último arquivo publicado.

    Pula sábado e domingo sem nem tentar a requisição — não existe
    arquivo de fim de semana, e bater no servidor para receber 404 duas
    vezes por semana é desperdício.
    """
    hoje = hoje or date.today()
    print("Procurando o arquivo mais recente da ANBIMA...")
    for recuo in range(DIAS_PARA_TRAS + 1):
        dia = hoje - timedelta(days=recuo)
        if dia.weekday() >= 5:  # 5 = sábado, 6 = domingo
            continue
        texto = baixar_do_dia(dia)
        if texto:
            print(f"  encontrado: {dia.strftime('%d/%m/%Y')}")
            return dia, texto
    return None, None


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------

def interpretar(texto):
    """Transforma o texto do arquivo numa lista de papéis."""
    papeis = []
    desconhecidos = {}
    ignoradas = 0

    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or "@" not in linha:
            continue
        campos = linha.split("@")
        if len(campos) < MINIMO_CAMPOS:
            ignoradas += 1
            continue

        codigo = campos[0].strip()
        # A linha de cabeçalho também tem 15 campos e arrobas — o que a
        # separa do dado é o primeiro campo ser o rótulo da coluna.
        if not codigo or codigo.upper().startswith("CÓDIGO") or codigo.upper().startswith("CODIGO"):
            continue

        vencimento = data_iso(campos[2])
        if not vencimento:
            # Sem vencimento não dá para calcular prazo nenhum, e prazo é
            # o eixo de toda comparação que o site faz.
            ignoradas += 1
            continue

        emissor, tem_clausula = limpar_emissor(campos[1])
        rotulo = campos[3].strip()
        indexador, taxa_contratada = classificar_indexador(rotulo)
        if indexador == "outro" and rotulo:
            desconhecidos[rotulo] = desconhecidos.get(rotulo, 0) + 1

        duration_du = numero(campos[12]) if len(campos) > 12 else None

        papeis.append({
            "codigo": codigo,
            "emissor": emissor,
            "vencimento": vencimento,
            "indexador": indexador,
            "rotulo_indexador": rotulo,
            "taxa_contratada": taxa_contratada,
            "taxa_indicativa": numero(campos[6]),
            "taxa_compra": numero(campos[4]),
            "taxa_venda": numero(campos[5]),
            "desvio_padrao": numero(campos[7]),
            "pu": numero(campos[10]),
            "pct_par": numero(campos[11]),
            "duration_du": duration_du,
            "duration_anos": round(duration_du / DIAS_UTEIS_NO_ANO, 2) if duration_du else None,
            "resgate_antecipado": tem_clausula,
        })

    return papeis, desconhecidos, ignoradas


def avisar_indexadores_desconhecidos(desconhecidos):
    """Grita no log quando aparece um formato de indexador novo.

    Mesma política do robô do Tesouro: o site continua funcionando (o
    papel entra como "outro" e é exibido com o rótulo cru), mas o log
    deixa registrado para alguém olhar. Formato novo aparecendo em
    silêncio é como uma categoria inteira some de uma tabela sem ninguém
    perceber por meses.
    """
    if not desconhecidos:
        return
    print("\n  ATENÇÃO: indexador em formato não reconhecido:")
    for rotulo, quantos in sorted(desconhecidos.items(), key=lambda x: -x[1]):
        print(f"    {quantos:4d}x  {rotulo!r}")
    print("    (os papéis entraram como 'outro' — ninguém foi descartado)")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    dia, texto = baixar_mais_recente()
    if not texto:
        print(f"\nERRO: nenhum arquivo encontrado nos últimos {DIAS_PARA_TRAS} dias.")
        print("Confira se o padrão da URL da ANBIMA mudou:")
        print(f"  {URL_BASE.format(data='AAMMDD')}")
        return 1

    papeis, desconhecidos, ignoradas = interpretar(texto)

    if not papeis:
        print("\nERRO: o arquivo foi baixado mas nenhuma linha foi interpretada.")
        print("Provável mudança de layout — confira o separador e a ordem das colunas.")
        return 1

    por_indexador = {}
    for p in papeis:
        por_indexador[p["indexador"]] = por_indexador.get(p["indexador"], 0) + 1
    com_taxa = sum(1 for p in papeis if p["taxa_indicativa"] is not None)

    print(f"\n  {len(papeis)} papéis lidos ({ignoradas} linhas ignoradas)")
    for chave, quantos in sorted(por_indexador.items(), key=lambda x: -x[1]):
        print(f"    {chave:10s} {quantos:5d}")
    print(f"  com taxa indicativa do dia: {com_taxa}")
    avisar_indexadores_desconhecidos(desconhecidos)

    # Mais novo primeiro no vencimento deixa o arquivo legível para quem
    # abrir no olho; a tela reordena como quiser.
    papeis.sort(key=lambda p: (p["indexador"], p["vencimento"]))

    saida = {
        "_leia_me": (
            "Taxas INDICATIVAS do mercado secundário de debêntures, publicadas pela ANBIMA. "
            "É a referência de onde o papel está sendo negociado entre instituições — NÃO é uma "
            "oferta disponível para compra nem preço de emissão nova. "
            "Para papéis atrelados ao DI, 'taxa_indicativa' é o spread sobre o DI (0,72 = DI + 0,72%); "
            "para IPCA e IGP-M, é a taxa real acima do índice. "
            "'taxa_contratada' é o spread combinado na emissão, que pode ser bem diferente do de hoje. "
            "'resgate_antecipado' vem dos marcadores (*) e (**) do arquivo, que indicam cláusula de "
            "resgate/recompra — e NÃO isenção de IR: o arquivo da ANBIMA não informa quais papéis são "
            "debêntures incentivadas da Lei 12.431."
        ),
        "fonte": "ANBIMA — Mercado Secundário de Debêntures",
        "fonte_url": URL_BASE.format(data=dia.strftime("%y%m%d")),
        "data_base": dia.strftime("%d/%m/%Y"),
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(papeis),
        "por_indexador": por_indexador,
        "debentures": papeis,
    }

    os.makedirs(os.path.dirname(CAMINHO_SAIDA), exist_ok=True)
    with open(CAMINHO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    tamanho = os.path.getsize(CAMINHO_SAIDA) / 1024
    print(f"\n  {CAMINHO_SAIDA} gravado ({tamanho:.0f} KB) — data-base {saida['data_base']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
