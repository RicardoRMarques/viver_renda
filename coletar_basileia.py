#!/usr/bin/env python3
"""
Coleta o Índice de Basileia dos bancos no IF.data do Banco Central e
atualiza `data/basileia.json`, que alimenta a tabela "Índice de Basileia
dos Bancos" do site.

POR QUE ESTA FONTE, E NÃO A API OFICIAL
----------------------------------------
A API OData do BC (olinda.bcb.gov.br) foi a primeira tentativa e não se
sustentou: em oito execuções reais, DUAS responderam e seis devolveram
HTTP 500 "Erro desconhecido" em tudo — a mesma consulta que trazia 14 mil
linhas falhava minutos depois. Ela MONTA a resposta a cada chamada, e é
isso que engasga.

A interface web do IF.data usa outro caminho: arquivos JSON estáticos,
já prontos no servidor. Servidor de arquivo não engasga como gerador de
consulta. O robô agora fala com ela, exatamente como o navegador fala:

    catálogo:  GET /ifdata/rest/relatorios2025a2030
    arquivo:   GET /ifdata/rest/arquivos?nomeArquivo=<caminho>

O nome do parâmetro ('nomeArquivo') e o método (GET) vieram do próprio
JavaScript da página. Ela concatena o caminho CRU, sem codificar — a
barra dupla de "ifdata_2025_2030//202603/..." vai literal, e aqui vai
igual, pra não inventar diferença onde o site não faz.

COMO OS ARQUIVOS SE ENCAIXAM
-----------------------------
    info<dt>.json       dicionário das colunas: id, nome e 'lid', que é a
                        chave usada nos dados. É AQUI que está a palavra
                        "Índice de Basileia" — os arquivos de dados só
                        carregam números e ids, nenhum texto.
    dados<dt>_N.json    {"id": N, "values": [{"e": <cod instituição>,
                                              "v": [{"i": <lid>, "v": <valor>}]}]}
    cadastro<dt>_<tipo>.json   lista de instituições com campos c0..c25;
                        c0 é o código que aparece como "e" nos dados e c2
                        é o nome curto ("BRADESCO", "ITAU").
    sel<dt>.json        tipos de instituição:
                          1009 = Conglomerados Prudenciais  <- o nosso
                          1005 = Conglomerados Financeiros
                          1006 = Instituições Individuais

O número que os bancos divulgam no release é o do conglomerado
PRUDENCIAL, por isso 1009.

COMO USAR
---------
    python3 coletar_basileia.py              # grava data/basileia.json
    python3 coletar_basileia.py --dry-run    # faz tudo e mostra, sem gravar
    python3 coletar_basileia.py --anomes 202603   # força uma data-base
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

BASE = os.environ.get("IFDATA_BASE", "https://www3.bcb.gov.br/ifdata/")
CATALOGO_URL = BASE + "rest/relatorios2025a2030"
ARQUIVO_URL = BASE + "rest/arquivos?nomeArquivo="
ARQUIVO = os.environ.get("ARQUIVO_BASILEIA", "data/basileia.json")

TIPO_PRUDENCIAL = int(os.environ.get("BASILEIA_TIPO", "1009"))
ROTULO_BASILEIA = os.environ.get("BASILEIA_ROTULO", "Índice de Basileia")

TEMPO_LIMITE = 120          # dados<dt>_1.json tem 16 MB
ESPERAS_ENTRE_TENTATIVAS = [3, 10]
PAUSA_ENTRE_CHAMADAS = float(os.environ.get("BASILEIA_PAUSA", "1"))
DATAS_PARA_TRAS = 4         # quantas data-bases tentar antes de desistir

CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE,
    "X-Requested-With": "XMLHttpRequest",
}

# Bancos da tabela do site. 'busca' são pedaços do nome como ele aparece
# no cadastro do IF.data — que usa nome CURTO ("BRADESCO", "ITAU"), não a
# razão social completa.
# Os bancos da tabela, em dois grupos.
#
# 'listado'  -> tem ação na B3: o site busca valor de mercado, P/L, P/VP,
#               ROE e DY na HG Brasil, além da Basileia.
# 'emissor'  -> não tem ação, mas emite CDB/LCI/LCA no varejo. São
#               justamente os que pagam mais (e onde o Basileia mais
#               importa); só a coluna de Basileia é preenchida.
#
# A chave dos que não têm ação é um apelido, não um ticker.
#
# NÃO ESTÁ AQUI, de propósito: Banco Master. Teve LIQUIDAÇÃO
# EXTRAJUDICIAL decretada pelo Banco Central. Era o nome mais citado em
# "CDB que paga mais" e, num site que orienta quem compra renda fixa,
# listá-lo como emissor seria pior do que não ter a tabela.
#
# 'busca' são pedaços do nome como o cadastro do IF.data escreve os
# CONGLOMERADOS: nome curto seguido de " - PRUDENCIAL" ("BRADESCO -
# PRUDENCIAL", "BB - PRUDENCIAL"). Vários fragmentos por banco porque a
# grafia varia; o robô avisa no log quando um não é encontrado.
BANCOS = [
    # --- com ação na B3 ---
    {"ticker": "ITUB4",  "nome": "Itaú Unibanco",        "grupo": "listado", "busca": ["ITAU", "ITAU UNIBANCO"]},
    {"ticker": "BBDC4",  "nome": "Bradesco",             "grupo": "listado", "busca": ["BRADESCO"]},
    {"ticker": "BBAS3",  "nome": "Banco do Brasil",      "grupo": "listado", "busca": ["BB", "BANCO DO BRASIL"]},
    {"ticker": "SANB11", "nome": "Santander Brasil",     "grupo": "listado", "busca": ["SANTANDER"]},
    {"ticker": "BPAC11", "nome": "BTG Pactual",          "grupo": "listado", "busca": ["BTG PACTUAL", "BTG"]},
    {"ticker": "BRSR6",  "nome": "Banrisul",             "grupo": "listado", "busca": ["BANRISUL"]},
    {"ticker": "ABCB4",  "nome": "Banco ABC Brasil",     "grupo": "listado", "busca": ["ABC-BRASIL", "ABC BRASIL"]},
    {"ticker": "BPAN4",  "nome": "Banco Pan",            "grupo": "listado", "busca": ["PAN", "BANCO PAN"]},
    {"ticker": "BRBI11", "nome": "BR Partners",          "grupo": "listado", "busca": ["BR PARTNERS", "BRPARTNERS"]},
    {"ticker": "BMGB4",  "nome": "Banco BMG",            "grupo": "listado", "busca": ["BMG"]},
    {"ticker": "SFSA4",  "nome": "Banco Sofisa",         "grupo": "listado", "busca": ["SOFISA"]},
    {"ticker": "PINE4",  "nome": "Banco Pine",           "grupo": "listado", "busca": ["PINE"]},
    {"ticker": "BMEB4",  "nome": "Mercantil do Brasil",  "grupo": "listado", "busca": ["MERCANTIL DO BRASIL", "MERCANTIL"]},
    {"ticker": "BSLI4",  "nome": "BRB — Banco de Brasília", "grupo": "listado", "busca": ["BRB"]},
    {"ticker": "BEES3",  "nome": "Banestes",             "grupo": "listado", "busca": ["BANESTES"]},
    {"ticker": "BAZA3",  "nome": "Banco da Amazônia",    "grupo": "listado", "busca": ["AMAZONIA", "BASA"]},

    # --- sem ação na B3, mas emitem no varejo ---
    {"ticker": "CAIXA",    "nome": "Caixa Econômica Federal", "grupo": "emissor", "busca": ["CAIXA ECONOMICA FEDERAL", "CAIXA", "CEF"]},
    {"ticker": "SAFRA",    "nome": "Banco Safra",         "grupo": "emissor", "busca": ["SAFRA"]},
    {"ticker": "DAYCOVAL", "nome": "Banco Daycoval",      "grupo": "emissor", "busca": ["DAYCOVAL"]},
    {"ticker": "BV",       "nome": "Banco BV",            "grupo": "emissor", "busca": ["BV", "VOTORANTIM"]},
    {"ticker": "INTER",    "nome": "Banco Inter",         "grupo": "emissor", "busca": ["INTER"]},
    {"ticker": "NUBANK",   "nome": "Nubank",              "grupo": "emissor", "busca": ["NU", "NUBANK", "NU PAGAMENTOS"]},
    {"ticker": "C6",       "nome": "C6 Bank",             "grupo": "emissor", "busca": ["C6"]},
    {"ticker": "ORIGINAL", "nome": "Banco Original",      "grupo": "emissor", "busca": ["ORIGINAL"]},
    {"ticker": "AGIBANK",  "nome": "Agibank",             "grupo": "emissor", "busca": ["AGIBANK"]},
    {"ticker": "PAGBANK",  "nome": "PagBank",             "grupo": "emissor", "busca": ["PAGSEGURO", "PAGBANK"]},
    {"ticker": "PICPAY",   "nome": "PicPay",              "grupo": "emissor", "busca": ["PICPAY"]},
    {"ticker": "NEON",     "nome": "Neon",                "grupo": "emissor", "busca": ["NEON"]},
    {"ticker": "FIBRA",    "nome": "Banco Fibra",         "grupo": "emissor", "busca": ["FIBRA"]},
    {"ticker": "XP",       "nome": "Banco XP",            "grupo": "emissor", "busca": ["XP"]},
]

# Empresas do mesmo grupo que NÃO são o banco. O índice de uma corretora
# não diz nada sobre a solidez do banco e costuma ser bem mais alto —
# publicar um no lugar do outro passaria despercebido.
TERMOS_NAO_BANCO = (
    "CORRETORA", "DISTRIBUIDORA", "CCTVM", "DTVM", "CVMC",
    "SEGURO", "SEGURADORA", "CAPITALIZACAO", "PREVIDENCIA",
    "LEASING", "ARRENDAMENTO", "CONSORCIO", "ADMINISTRADORA",
    "CARTOES", "FACTORING", "IMOBILIARIA", "ASSET", "GESTORA",
    # as cooperativas dominam o cadastro (mais de mil): sem elas na lista,
    # "PAN", "ABC" e "BB" casavam com SICREDI/SICOOB/UNICRED da vida.
    "COOPERATIVA", "SICREDI", "SICOOB", "UNICRED", "UNIPRIME", "CRESOL",
    "CREDITO MUTUO", "ECONOMIA E CREDITO",
)

DRY_RUN = "--dry-run" in sys.argv


def log(m):
    print(m, flush=True)


def normalizar(texto):
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).upper().strip()


def buscar(url, rotulo):
    """(dados, tamanho) — None quando falha. Erro vira log, não exceção."""
    if PAUSA_ENTRE_CHAMADAS:
        time.sleep(PAUSA_ENTRE_CHAMADAS)
    req = urllib.request.Request(url, headers=CABECALHOS)
    for tentativa, espera in enumerate(ESPERAS_ENTRE_TENTATIVAS, start=1):
        try:
            with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as r:
                bruto = r.read().decode("utf-8", "replace")
            return json.loads(bruto), len(bruto)
        except urllib.error.HTTPError as e:
            log(f"    [{e.code}] {rotulo}")
            if e.code < 500 or tentativa == len(ESPERAS_ENTRE_TENTATIVAS):
                return None, 0
        except Exception as e:  # noqa: BLE001
            log(f"    [erro] {rotulo}: {e}")
            if tentativa == len(ESPERAS_ENTRE_TENTATIVAS):
                return None, 0
        time.sleep(espera)
    return None, 0


def baixar_arquivo(caminho):
    nome = caminho.split("/")[-1]
    dados, tamanho = buscar(ARQUIVO_URL + caminho, nome)
    if dados is not None:
        log(f"    {nome}: {tamanho:,} bytes".replace(",", "."))
    return dados


def converter_numero(valor):
    """
    Devolve o número CRU, sem arredondar. Arredondar aqui foi um erro
    real: o IF.data entrega a Basileia como fração (0,1531 = 15,31%) e o
    round(2) transformava isso em 0,15 — perdendo os centésimos ANTES de
    a escala ser corrigida. O arredondamento acontece só no fim.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        limpo = valor.strip()
        if not limpo:
            return None
        if "," in limpo:
            limpo = limpo.replace(".", "").replace(",", ".")
        try:
            return float(limpo)
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------
# Descoberta dentro dos arquivos
# ----------------------------------------------------------------------

def achar_coluna_basileia(info):
    """
    Acha, no dicionário de colunas, a entrada do Índice de Basileia.

    Procura pelo NOME, não por id fixo: os ids são internos do BC e podem
    mudar entre trimestres. Se houver mais de uma coluna com "Basileia"
    (existe também "Basileia Ampliado" e variações de Nível I), fica com o
    nome mais curto, que é o índice puro.
    """
    if not isinstance(info, list):
        return None
    alvo = normalizar(ROTULO_BASILEIA)
    candidatas = []
    for entrada in info:
        if not isinstance(entrada, dict):
            continue
        for campo in ("n", "d"):
            nome = entrada.get(campo)
            if isinstance(nome, str) and "BASILEIA" in normalizar(nome):
                exato = 0 if normalizar(nome) == alvo else 1
                candidatas.append((exato, len(nome), nome, entrada))
                break
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: (c[0], c[1]))
    log(f"    colunas com 'Basileia' encontradas: {[c[2] for c in candidatas][:6]}")
    escolhida = candidatas[0][3]
    log(f"    usando: {candidatas[0][2]!r}  (lid={escolhida.get('lid')}, "
        f"id={escolhida.get('id')}, td={escolhida.get('td')})")
    return escolhida


def valores_por_instituicao(dados, lid):
    """
    Extrai {codigo_instituicao: valor} de um arquivo de dados.

    Formato: {"id": N, "values": [{"e": <cod>, "v": [{"i": <lid>, "v": <n>}]}]}
    """
    if not isinstance(dados, dict):
        return {}
    saida = {}
    for linha in dados.get("values", []):
        if not isinstance(linha, dict):
            continue
        codigo = linha.get("e")
        for celula in linha.get("v", []):
            if isinstance(celula, dict) and celula.get("i") == lid:
                numero = converter_numero(celula.get("v"))
                if numero is not None:
                    # normaliza zeros à esquerda: o mesmo código aparece
                    # como 10045 nos dados e "00010045" em alguns campos
                    # do cadastro.
                    saida[str(codigo).lstrip("0") or "0"] = numero
                break
    return saida


CAMPOS_NOME = ("c2", "c22", "c23")


def mapear_nomes(cadastro, codigos_dos_dados):
    """
    {codigo: nome} a partir do cadastro.

    Os campos vêm como c0..c25, SEM cabeçalho — o BC não diz o que é cada
    um. A investigação sugere c0 = código e c2 = nome curto ("BRADESCO",
    "ITAU"), mas isso é leitura de amostra, não contrato. Em vez de fixar
    o palpite, o robô testa cada campo como candidato a código e fica com
    o que mais casa com os códigos que vieram nos dados. Se um dia o BC
    reordenar as colunas, ele se ajusta sozinho em vez de devolver tabela
    vazia.
    """
    if not isinstance(cadastro, list) or not cadastro:
        return {}, None

    campos = [c for c in cadastro[0] if isinstance(cadastro[0].get(c), (str, int))]
    melhor = (0, None, {})
    for campo in campos:
        mapa = {}
        for item in cadastro:
            if not isinstance(item, dict):
                continue
            codigo = item.get(campo)
            nome = next((item.get(n) for n in CAMPOS_NOME if item.get(n)), None)
            if codigo not in (None, "") and nome:
                mapa[str(codigo).lstrip("0") or "0"] = str(nome)
        acertos = len(set(mapa) & codigos_dos_dados)
        if acertos > melhor[0]:
            melhor = (acertos, campo, mapa)

    if melhor[1]:
        log(f"    campo de código do cadastro: {melhor[1]} "
            f"({melhor[0]} códigos em comum com os dados)")
    return melhor[2], melhor[1]


# ----------------------------------------------------------------------
# Casamento com os tickers
# ----------------------------------------------------------------------

SUFIXO_CONGLOMERADO = "- PRUDENCIAL"


def parece_nao_banco(nome_normalizado):
    return any(t in nome_normalizado for t in TERMOS_NAO_BANCO)


def nome_base(nome):
    """
    ("BRADESCO", True) para "BRADESCO - PRUDENCIAL".

    O cadastro mistura razão social completa de mil e poucas instituições
    com os conglomerados, e SÓ os conglomerados levam esse sufixo. É a
    marca mais confiável do arquivo: o que a gente quer é sempre o
    "<NOME> - PRUDENCIAL".
    """
    n = normalizar(nome)
    if n.endswith(SUFIXO_CONGLOMERADO):
        return n[:-len(SUFIXO_CONGLOMERADO)].strip(), True
    return n, False


def contem_palavra(texto, fragmento):
    """
    Fragmento como PALAVRA inteira, não pedaço solto.

    Isto existe por causa de um erro que quase publicou número de
    cooperativa como se fosse do Banco Pan: "PAN" está DENTRO de
    "EXPANSÃO" (EXPANSAO), e a busca por substring casava.
    """
    return re.search(rf"(?<![A-Z0-9]){re.escape(fragmento)}(?![A-Z0-9])", texto) is not None


def pontuar_candidato(banco, nome):
    """
    Quão bem este nome corresponde a este banco. Menor é melhor; None se
    não corresponde.

    A ordem dos critérios importa e foi acertada apanhando:
      1. não parecer cooperativa/corretora/seguradora;
      2. TER o sufixo "- PRUDENCIAL" (é o conglomerado, o que queremos);
      3. bater EXATO com o nome curto, antes de bater como palavra solta;
      4. nome mais enxuto.

    A versão anterior premiava o fragmento mais LONGO, e por isso escolheu
    "...DO ESTADO DO RIO GRANDE DO SUL - SICREDI AJURIS RS" em vez de
    "BANRISUL - PRUDENCIAL", que estava ali na mesma lista.
    """
    base, prudencial = nome_base(nome)
    fora = 1 if parece_nao_banco(base) else 0
    melhor = None
    for fragmento in banco["busca"]:
        if base == fragmento:
            precisao = 0
        elif contem_palavra(base, fragmento):
            precisao = 1
        else:
            continue
        pontos = (fora, 0 if prudencial else 1, precisao, len(base))
        if melhor is None or pontos < melhor:
            melhor = pontos
    return melhor


def ajustar_escala(valores):
    """
    O IF.data entrega o Índice de Basileia como FRAÇÃO (0,1531), não como
    percentual (15,31). Descobrimos isso na primeira coleta real: a tabela
    saiu com "0,15%" no lugar de "15,31%".

    Em vez de multiplicar por 100 e torcer, o robô OLHA os números: se a
    mediana dos bancos escolhidos for menor que 1, é fração e vira
    percentual. Assim, se o BC um dia passar a entregar já em percentual,
    nada quebra — e nem precisa de alguém lembrar de mexer aqui.
    """
    if not valores:
        return valores, False
    ordenados = sorted(valores.values())
    mediana = ordenados[len(ordenados) // 2]
    if mediana >= 1:
        return {t: round(v, 2) for t, v in valores.items()}, False
    log(f"    valores vieram como fração (mediana {mediana:.4f}) — "
        f"convertendo para percentual")
    return {t: round(v * 100, 2) for t, v in valores.items()}, True


def casar(mapa_nomes, mapa_valores):
    candidatos = {}
    for codigo, valor in mapa_valores.items():
        nome = mapa_nomes.get(codigo)
        if not nome:
            continue
        for banco in BANCOS:
            pontos = pontuar_candidato(banco, nome)
            if pontos is not None:
                candidatos.setdefault(banco["ticker"], []).append((pontos, nome, valor))

    escolhidos = {}
    for ticker, lista in candidatos.items():
        lista.sort()
        _, nome, valor = lista[0]
        escolhidos[ticker] = (nome, valor)
        if len(lista) > 1:
            outros = [f"{n} ({v:g})" for _, n, v in lista[1:6]]
            log(f"    ATENÇÃO {ticker}: {len(lista)} instituições casaram. "
                f"Escolhida: {nome}. Descartadas: {outros}")
        if parece_nao_banco(nome_base(nome)[0]):
            log(f"    ATENÇÃO {ticker}: '{nome}' não parece ser o banco em si.")

    brutos = {t: v for t, (_, v) in escolhidos.items()}
    valores, _ = ajustar_escala(brutos)

    casados = [(t, escolhidos[t][0], valores[t]) for t in valores]
    for ticker, _, valor in casados:
        if not (5 < valor < 60):
            log(f"    ATENÇÃO {ticker}: {valor}% está fora da faixa plausível "
                f"para um banco (5% a 60%) — conferir a coluna.")
    return valores, casados


# ----------------------------------------------------------------------
# Gravação
# ----------------------------------------------------------------------

def rotulos(dt):
    ano, mes = divmod(int(dt), 100)
    trimestre = (mes + 2) // 3
    ultimo_dia = {3: "31/03", 6: "30/06", 9: "30/09", 12: "31/12"}.get(mes, f"--/{mes:02d}")
    return {"id": f"{ano}T{trimestre}", "rotulo": f"{trimestre}T{str(ano)[2:]}",
            "referencia": f"Base {ultimo_dia}/{ano} — IF.data/Banco Central "
                          f"(conglomerado prudencial)"}


def gravar(dt, valores):
    if os.path.exists(ARQUIVO):
        with open(ARQUIVO, encoding="utf-8") as f:
            dados = json.load(f)
    else:
        dados = {"bancos": [], "periodos": []}

    # a lista de bancos é sempre reescrita a partir daqui: assim, incluir
    # ou tirar um banco é mexer só neste arquivo, e o site acompanha.
    dados["bancos"] = [{"ticker": b["ticker"], "nome": b["nome"], "grupo": b["grupo"]}
                       for b in BANCOS]

    marcas = rotulos(dt)
    periodo = {"id": marcas["id"], "rotulo": marcas["rotulo"],
               "referencia": marcas["referencia"], "valores": valores}

    # Se nada mudou, não reescreve: rodando diariamente, o campo
    # 'atualizado_em' viraria um commit por dia só pra trocar uma data.
    atual = next((p for p in dados.get("periodos", [])
                  if str(p.get("id")) == periodo["id"]), None)
    if atual and atual.get("valores") == valores:
        log(f"\nNada mudou no período {periodo['rotulo']} — arquivo mantido como está.")
        return False

    periodos = [p for p in dados.get("periodos", []) if str(p.get("id")) != periodo["id"]]
    periodos.insert(0, periodo)
    dados["periodos"] = periodos
    dados["fonte"] = "IF.data — Banco Central do Brasil"
    dados["atualizado_em"] = date.today().isoformat()

    os.makedirs(os.path.dirname(ARQUIVO) or ".", exist_ok=True)
    temporario = ARQUIVO + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporario, ARQUIVO)   # troca atômica: o site nunca lê meio arquivo
    log(f"\nGravado {ARQUIVO} — período {periodo['rotulo']} ({len(valores)} bancos).")
    return True


# ----------------------------------------------------------------------

def processar(bloco):
    dt = bloco.get("dt")
    arquivos = [x.get("f") for x in bloco.get("files", []) if x.get("f")]
    log(f"\n=== data-base {dt} ===")

    def achar(trecho):
        return [a for a in arquivos if trecho in a.split("/")[-1]]

    caminhos_info = achar("info")
    if not caminhos_info:
        log("    sem arquivo 'info' — não dá pra saber o nome das colunas.")
        return None
    info = baixar_arquivo(caminhos_info[0])
    if info is None:
        return None

    coluna = achar_coluna_basileia(info)
    if coluna is None:
        log("    nenhuma coluna com 'Basileia' neste dicionário.")
        return None
    lid = coluna.get("lid")
    if lid is None:
        log("    a coluna encontrada não tem 'lid' — sem chave pra buscar nos dados.")
        return None

    # 'td' costuma apontar o arquivo de dados; se não bater, varre os outros.
    caminhos_dados = achar("dados")
    preferido = [a for a in caminhos_dados
                 if a.rstrip(".json").endswith(f"_{coluna.get('td')}")]
    ordem = preferido + [a for a in caminhos_dados if a not in preferido]

    mapa_valores = {}
    for caminho in ordem:
        dados = baixar_arquivo(caminho)
        if dados is None:
            continue
        mapa_valores = valores_por_instituicao(dados, lid)
        if mapa_valores:
            log(f"    lid {lid} encontrado em {caminho.split('/')[-1]}: "
                f"{len(mapa_valores)} instituições com valor")
            break
        log(f"    (lid {lid} não está em {caminho.split('/')[-1]})")

    if not mapa_valores:
        log("    o lid da Basileia não apareceu em nenhum arquivo de dados.")
        return None

    caminhos_cadastro = achar(f"cadastro{dt}_{TIPO_PRUDENCIAL}")
    if not caminhos_cadastro:
        log(f"    sem cadastro do tipo {TIPO_PRUDENCIAL} nesta data-base; "
            f"disponíveis: {[a.split('/')[-1] for a in achar('cadastro')]}")
        return None
    cadastro = baixar_arquivo(caminhos_cadastro[0])
    if cadastro is None:
        return None

    mapa_nomes, campo_codigo = mapear_nomes(cadastro, set(mapa_valores))
    log(f"    cadastro: {len(mapa_nomes)} instituições")
    if mapa_nomes:
        log(f"    exemplos: {list(mapa_nomes.items())[:3]}")

    if not mapa_nomes or campo_codigo is None:
        log("    ATENÇÃO: nenhum campo do cadastro casou com os códigos dos dados.")
        log(f"    campos disponíveis: {list(cadastro[0].keys()) if cadastro else []}")
        log(f"    exemplo de linha do cadastro: "
            f"{json.dumps(cadastro[0], ensure_ascii=False)[:400] if cadastro else ''}")
        log(f"    códigos vindos dos dados (amostra): {list(mapa_valores)[:8]}")
        return None

    valores, casados = casar(mapa_nomes, mapa_valores)
    log("\n  Casamento ticker <-> instituição:")
    for ticker, nome, valor in sorted(casados):
        log(f"    {ticker:7s} {valor:6.2f}%   {nome}")
    faltando = [b["ticker"] for b in BANCOS if b["ticker"] not in valores]
    if faltando:
        log(f"\n  NÃO ENCONTRADOS: {', '.join(faltando)}")
        investigar_faltantes(faltando, bloco, dt, arquivos, mapa_valores, mapa_nomes)

    return (dt, valores) if valores else None


def investigar_faltantes(faltando, bloco, dt, arquivos, mapa_valores, mapa_nomes):
    """
    Diz POR QUE um banco não apareceu, em vez de só constatar que faltou.

    A primeira versão desta dica listava qualquer instituição que
    contivesse alguma palavra do nome do banco — como "Banco Pan" tem
    "Banco", ela devolveu "BANCO AFINZ", "BANCO B3", "BANCO C6"...
    inútil. Agora procura só pelos fragmentos de busca, como palavra
    inteira, e olha TAMBÉM os outros tipos de instituição.

    Existe um motivo real e comum para um banco sumir da lista de
    conglomerados prudenciais: ele pode ser controlado por outro grupo e
    reportar dentro do conglomerado da controladora. Nesse caso o número
    existe, mas em outro escopo — e é isso que este diagnóstico revela.
    """
    def procurar(nomes_por_codigo, rotulo):
        for ticker in faltando:
            banco = next(b for b in BANCOS if b["ticker"] == ticker)
            achados = []
            for codigo, nome in nomes_por_codigo.items():
                base, _ = nome_base(nome)
                if any(base == f or contem_palavra(base, f) for f in banco["busca"]):
                    valor = mapa_valores.get(codigo)
                    achados.append(f"{nome}"
                                   + (f" [Basileia {valor:g}]" if valor is not None
                                      else " [sem valor de Basileia]"))
            log(f"    {ticker} em {rotulo}: {achados[:6] or 'nenhuma correspondência'}")

    log("    procurando nos conglomerados prudenciais (o que já foi lido):")
    procurar(mapa_nomes, "prudenciais")

    # os outros tipos: 1005 = Conglomerados Financeiros, 1006 = Individuais
    outros = [a for a in arquivos
              if "cadastro" in a.split("/")[-1]
              and f"_{TIPO_PRUDENCIAL}." not in a.split("/")[-1]]
    for caminho in outros:
        cadastro = baixar_arquivo(caminho)
        if not isinstance(cadastro, list) or not cadastro:
            continue
        mapa, _ = mapear_nomes(cadastro, set(mapa_valores))
        procurar(mapa, caminho.split("/")[-1])

    log("    (se o banco aparecer em outro tipo COM valor, dá pra usar esse número")
    log("     marcando o escopo; se não aparecer em lugar nenhum, a célula fica '—')")


def main():
    forcado = None
    for i, arg in enumerate(sys.argv):
        if arg == "--anomes" and i + 1 < len(sys.argv):
            forcado = int(sys.argv[i + 1])

    log(f"IF.data (interface web) — {CATALOGO_URL}")
    catalogo, _ = buscar(CATALOGO_URL, "catálogo")
    if not isinstance(catalogo, list) or not catalogo:
        log("Não consegui ler o catálogo. Nada foi gravado.")
        return 1

    catalogo.sort(key=lambda b: int(b.get("dt", 0)), reverse=True)
    log(f"data-bases publicadas: {[b.get('dt') for b in catalogo[:8]]}")

    blocos = ([b for b in catalogo if int(b.get("dt", 0)) == forcado] if forcado
              else catalogo[:DATAS_PARA_TRAS])
    if not blocos:
        log(f"data-base {forcado} não está no catálogo.")
        return 1

    for bloco in blocos:
        resultado = processar(bloco)
        if not resultado:
            log("    seguindo para a data-base anterior...")
            continue
        dt, valores = resultado
        if DRY_RUN:
            log("\n  (--dry-run: nada gravado)")
            return 0
        gravar(dt, valores)
        return 0

    log("\nNenhuma data-base rendeu o Índice de Basileia. Nada foi gravado.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
