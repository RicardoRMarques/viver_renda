#!/usr/bin/env python3
"""
Coleta o Índice de Basileia dos bancos no IF.data do Banco Central e
atualiza `data/basileia.json`, que alimenta a tabela "Índice de Basileia
dos Bancos" do site.

POR QUE ISSO EXISTE
-------------------
O Índice de Basileia não existe em API de mercado. Foi conferido o
inventário completo de campos do /v2/finance/fundamentals da HG Brasil
(que alimenta o resto do site): há valuation, leverage, margins,
profitability e dividends — nenhum campo de capital regulatório, para
nenhum ticker. É esperado: a HG é API de mercado; Basileia é dado
prudencial de instituição financeira.

Antes deste robô, os números ficavam digitados à mão dentro do
index.html. A fonte primária é pública e gratuita: o IF.data do Banco
Central, o mesmo lugar de onde saem os números que os bancos publicam.

O QUE ELE FAZ
-------------
  1. Descobre o trimestre mais recente publicado (anda pra trás a partir
     do trimestre atual até achar dado).
  2. SONDA os relatórios um a um, puxando poucas linhas de cada com $top,
     até achar aquele que contém a Basileia. Não dá pra fixar o número:
     na primeira execução real o relatório 1 devolveu 14 mil linhas sem
     nenhuma coluna de Basileia.
  3. Aceita os dois feitios em que o IF.data devolve o dado — coluna
     própria ("largo") ou linha rotulada ("longo") — e acha o campo pelo
     NOME/RÓTULO, não por grafia fixa, pra sobreviver a renomeação.
  4. Casa cada instituição com o ticker da B3 pela razão social.
  5. Insere o período novo no topo de `data/basileia.json`, preservando
     os períodos anteriores. O site monta o seletor a partir desse
     arquivo, então nada muda no index.html.

COMO USAR
---------
    python3 coletar_basileia.py --explorar     # só investiga a API e mostra o que achou
    python3 coletar_basileia.py --dry-run      # faz tudo, mostra o resultado, NÃO grava
    python3 coletar_basileia.py                # grava data/basileia.json
    python3 coletar_basileia.py --anomes 202606   # força um trimestre específico

Sem chave de API: o IF.data é aberto.

SE ALGO NÃO BATER
-----------------
Rode com --explorar: ele imprime as chaves de CADA relatório sondado, e
o diagnóstico com linhas de amostra. É isso que permite acertar um
fragmento de razão social ou um campo novo sem adivinhação.
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

BASE = os.environ.get("BASILEIA_BASE", "https://olinda.bcb.gov.br/olinda/servico/IFDATA/versao/v1/odata")
ARQUIVO = os.environ.get("ARQUIVO_BASILEIA", "data/basileia.json")

# TipoInstituicao no IF.data:
#   1 = Conglomerados Prudenciais e Instituições Independentes  <- o que os bancos divulgam
#   2 = Conglomerados Financeiros e Instituições Independentes
#   3 = Instituições Individuais
# O número que aparece no release de resultados é o do conglomerado
# PRUDENCIAL, por isso o padrão é 1.
TIPO_INSTITUICAO = int(os.environ.get("BASILEIA_TIPO", "1"))

# Relatórios sondados em busca da Basileia. Em vez de fixar um número, o
# robô espia cada um com $top e vê qual traz o dado (ver procurar_relatorio).
# Motivo: na primeira execução real o relatório 1 devolveu 14 mil linhas
# SEM coluna de Basileia — chutar o número não funciona.
# O relatório 1 vem primeiro porque a execução real de 08/09/2026 provou
# que é ele quem traz a Basileia (formato longo, NomeColuna/Saldo). Os
# outros só são sondados se o 1 vier VAZIO — nunca se ele falhar.
RELATORIOS_PARA_SONDAR = [int(n) for n in
                          os.environ.get("BASILEIA_RELATORIOS", "1,2,3,4,5,6,7,8").split(",")]
LINHAS_PARA_ESPIAR = 400   # amostra por relatório na sondagem

TEMPO_LIMITE = 90
ESPERAS_ENTRE_TENTATIVAS = [2, 5]       # segundos entre as tentativas
# Freio: se o servidor está fora do ar, insistir em 12 relatórios x 6
# trimestres x 3 tentativas faz o job rodar meia hora pra nada. Depois
# desta quantidade de falhas SEGUIDAS, a execução para e avisa.
FALHAS_SEGUIDAS_PARA_DESISTIR = 10

# O Olinda limita requisições: numa execução real, depois de ~13 chamadas
# em sequência ele passou a devolver 500 em TUDO, inclusive numa consulta
# que tinha funcionado segundos antes. Uma pausa entre chamadas custa
# alguns segundos e evita queimar a cota logo no primeiro trimestre.
PAUSA_ENTRE_CHAMADAS = float(os.environ.get("BASILEIA_PAUSA", "1.5"))
TRIMESTRES_PARA_TRAS = 6   # ~1,5 ano de tentativas antes de desistir

# Bancos da tabela do site. 'busca' são pedaços da razão social como ela
# aparece no IF.data (já normalizada: sem acento, maiúscula). Vários
# fragmentos por banco porque o BC muda a grafia de tempos em tempos.
BANCOS = [
    {"ticker": "ITUB4",  "nome": "Itaú Unibanco",     "busca": ["ITAU UNIBANCO", "ITAU"]},
    {"ticker": "BBDC4",  "nome": "Bradesco",          "busca": ["BRADESCO"]},
    {"ticker": "BBAS3",  "nome": "Banco do Brasil",   "busca": ["BANCO DO BRASIL", "BB "]},
    {"ticker": "SANB11", "nome": "Santander Brasil",  "busca": ["SANTANDER"]},
    {"ticker": "BPAC11", "nome": "BTG Pactual",       "busca": ["BTG PACTUAL", "BTG"]},
    {"ticker": "BRSR6",  "nome": "Banco Banrisul",    "busca": ["BANRISUL", "ESTADO DO RIO GRANDE DO SUL"]},
    {"ticker": "ABCB4",  "nome": "Banco ABC Brasil",  "busca": ["ABC BRASIL", "ABC-BRASIL"]},
    {"ticker": "BPAN4",  "nome": "Banco Pan",         "busca": ["BANCO PAN", "PAN "]},
    {"ticker": "BRBI11", "nome": "BR Partners",       "busca": ["BR PARTNERS", "BRPARTNERS"]},
]

EXPLORAR = "--explorar" in sys.argv
DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(msg, flush=True)


def normalizar(texto):
    """Sem acento, maiúscula, espaços colapsados — pra casar razão social."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).upper().strip()


def buscar_json(url):
    """
    O Olinda devolve HTTP 500 "Erro desconhecido" de forma intermitente —
    a MESMA consulta falha e, segundos depois, responde. Sem repetição o
    robô desistia de um trimestre que existe. Espera crescente entre as
    tentativas pra não insistir em cima de um servidor que está sofrendo.
    """
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "viverderenda-basileia/1.0 (+https://viverderenda.dev.br)",
    })
    ultimo_erro = None
    for tentativa, espera in enumerate(ESPERAS_ENTRE_TENTATIVAS, start=1):
        try:
            with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            ultimo_erro = e
            if e.code < 500 or tentativa == len(ESPERAS_ENTRE_TENTATIVAS):
                raise            # 4xx é erro nosso: repetir não adianta
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            if tentativa == len(ESPERAS_ENTRE_TENTATIVAS):
                raise
        time.sleep(espera)
    if ultimo_erro:
        raise ultimo_erro


def montar_url(recurso, parametros=None, top=None):
    """
    Monta a URL no formato de function import do OData, que é como o
    Olinda expõe os recursos do IF.data:

      .../IfDataValores(AnoMes=@AnoMes,...)?@AnoMes=202606&$format=json
    """
    query = {"$format": "json"}
    if parametros:
        assinatura = ",".join(f"{k}=@{k}" for k in parametros)
        recurso = f"{recurso}({assinatura})"
        for chave, valor in parametros.items():
            # texto vai entre aspas simples no OData; número vai cru
            query[f"@{chave}"] = f"'{valor}'" if isinstance(valor, str) else str(valor)
    if top:
        query["$top"] = str(top)
    return f"{BASE}/{recurso}?" + urllib.parse.urlencode(query, safe="'@$")


class ServidorInstavel(Exception):
    """O IF.data está fora do ar agora — não adianta continuar."""


falhas_seguidas = 0


def conferir_freio():
    """
    Só conta falha de consulta de verdade. A sonda do $top é silenciosa e
    fica de fora: o servidor rejeitá-la é resposta esperada, e contá-la
    disparava o freio num servidor saudável que apenas não aceita $top.
    """
    if falhas_seguidas >= FALHAS_SEGUIDAS_PARA_DESISTIR:
        raise ServidorInstavel(
            f"{falhas_seguidas} consultas seguidas falharam — o IF.data parece "
            "fora do ar. Nada foi gravado; a proxima execucao tenta de novo.")


def pedir(recurso, parametros=None, top=None, silencioso=False):
    """
    Devolve a lista de 'value'. A distinção abaixo importa:

        None  -> a CHAMADA falhou (HTTP, rede, JSON inválido)
        []    -> a chamada deu certo e não veio nada

    Misturar os dois foi o que escondeu um erro real: o $top derrubava a
    consulta, o erro era engolido e o robô concluía "trimestre ainda não
    publicado" para todos os períodos.
    """
    global falhas_seguidas
    url = montar_url(recurso, parametros, top)
    if PAUSA_ENTRE_CHAMADAS:
        time.sleep(PAUSA_ENTRE_CHAMADAS)
    try:
        dados = buscar_json(url)
    except urllib.error.HTTPError as e:
        corpo = ""
        try:
            corpo = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        if not silencioso:
            log(f"  HTTP {e.code} em {recurso}")
            log(f"    URL: {url}")
            if corpo:
                log(f"    resposta: {corpo}")
        if not silencioso:
            falhas_seguidas += 1
            conferir_freio()
        return None
    except Exception as e:  # noqa: BLE001
        if not silencioso:
            log(f"  falhou {recurso}: {e}")
            log(f"    URL: {url}")
        if not silencioso:
            falhas_seguidas += 1
            conferir_freio()
        return None
    falhas_seguidas = 0
    valor = dados.get("value")
    return valor if isinstance(valor, list) else None


# ----------------------------------------------------------------------
# Descoberta
# ----------------------------------------------------------------------

def trimestres_recentes(quantidade):
    """Fins de trimestre (AAAAMM) do mais recente pro mais antigo."""
    hoje = date.today()
    mes = ((hoje.month - 1) // 3) * 3    # 0, 3, 6 ou 9 = último fim de trimestre já completo
    ano = hoje.year
    if mes == 0:                          # jan/fev/mar -> o trimestre fechado é dez do ano passado
        mes, ano = 12, ano - 1
    saida = []
    for _ in range(quantidade):
        saida.append(ano * 100 + mes)
        mes -= 3
        if mes <= 0:
            mes += 12
            ano -= 1
    return saida


# O Olinda pode não aceitar $top nesses recursos. Quando isso aparece, o
# robô desliga a otimização pro resto da execução e passa a baixar o
# relatório inteiro (recortando as primeiras linhas aqui mesmo).
USAR_TOP = True


def parametros_valores(anomes, relatorio):
    return {"AnoMes": anomes, "TipoInstituicao": TIPO_INSTITUICAO, "Relatorio": str(relatorio)}


def espiar(anomes, relatorio, quantas=LINHAS_PARA_ESPIAR):
    """
    Puxa linhas de um relatório pra ver o formato. Tenta com $top (barato);
    se o servidor não aceitar, baixa inteiro.

    Devolve (linhas, veio_inteiro). O segundo item importa: quando o
    relatório já veio completo aqui, não faz sentido baixá-lo de novo
    depois — além do desperdício, era exatamente aí que o 500 intermitente
    derrubava a execução DEPOIS de já ter encontrado o dado.
    """
    global USAR_TOP
    parametros = parametros_valores(anomes, relatorio)

    if USAR_TOP:
        linhas = pedir("IfDataValores", parametros, top=quantas, silencioso=True)
        if linhas:
            return linhas, False
        # Pode ser relatório vazio OU $top rejeitado — só dá pra saber
        # repetindo sem ele.

    linhas = pedir("IfDataValores", parametros)
    if linhas and USAR_TOP:
        USAR_TOP = False
        log("  ($top não funcionou neste servidor — seguindo sem ele)")
    if linhas is None:
        return None, False
    return linhas, True


def campos_numericos(linha):
    return [k for k, v in linha.items() if converter_numero(v) is not None]


def detectar_basileia(linhas):
    """
    Descobre ONDE está o Índice de Basileia. O IF.data pode devolver em
    dois feitios, e o robô aceita os dois:

      LARGO  — uma linha por instituição, uma COLUNA chamada algo como
               'indiceDeBasileia'.
      LONGO  — uma linha por instituição × indicador, com uma coluna de
               rótulo (cujo VALOR é "Índice de Basileia") e outra com o
               número. É o feitio que explica um relatório com 14 mil
               linhas para pouco mais de mil instituições.

    Devolve um dicionário descrevendo o achado, ou None.
    """
    if not linhas:
        return None

    # --- LARGO: o nome da coluna entrega ---
    candidatas = [k for k in linhas[0] if "BASILEIA" in normalizar(k)]
    if candidatas:
        # a mais curta é o índice em si; as maiores são variações
        # ('...Ampliado', '...Nivel1'), que não é o que a tabela mostra.
        candidatas.sort(key=lambda k: (len(normalizar(k)), normalizar(k)))
        return {"formato": "largo", "campo_valor": candidatas[0]}

    # --- LONGO: o VALOR de alguma coluna é que diz "Basileia" ---
    for chave in linhas[0]:
        rotulos_vistos = set()
        for linha in linhas:
            valor = linha.get(chave)
            if isinstance(valor, str) and "BASILEIA" in normalizar(valor):
                rotulos_vistos.add(valor)
        if rotulos_vistos:
            # mesmo critério: o rótulo mais curto é o índice puro
            alvo = sorted(rotulos_vistos, key=lambda r: (len(r), r))[0]
            exemplo = next(l for l in linhas
                           if isinstance(l.get(chave), str) and normalizar(l[chave]) == normalizar(alvo))
            numericos = [k for k in campos_numericos(exemplo)
                         if not any(t in normalizar(k) for t in ("CODIGO", "COD", "ANO", "TIPO", "CNPJ", "ORDEM"))]
            if not numericos:
                continue
            numericos.sort(key=lambda k: (0 if "VALOR" in normalizar(k) or "SALDO" in normalizar(k) else 1, len(k)))
            return {"formato": "longo", "campo_rotulo": chave,
                    "rotulo": alvo, "outros_rotulos": sorted(rotulos_vistos),
                    "campo_valor": numericos[0]}
    return None


def achar_campo_nome(linhas):
    """
    A coluna da razão social. Precisa excluir explicitamente 'TipoInstituicao'
    e afins: a primeira versão casava com ela por conter 'Instituicao', e o
    robô saía procurando banco dentro de um campo que só diz o tipo.
    """
    if not linhas:
        return None
    # 'TIPO' porque TipoInstituicao contém "Instituicao" e casava por engano.
    # 'COLUNA'/'RELATORIO'/'GRUPO' porque no formato longo existe uma
    # NomeColuna, que é o rótulo do indicador ("Índice de Basileia") e não
    # a razão social — foi o que o robô pegou na execução anterior.
    proibidos = ("TIPO", "COD", "CNPJ", "ANO", "MES", "SEGMENTO", "UF", "CIDADE",
                 "ORDEM", "COLUNA", "RELATORIO", "GRUPO", "SALDO", "MOEDA", "DOCUMENTO")
    candidatas = []
    for chave in linhas[0]:
        n = normalizar(chave)
        if any(t in n for t in proibidos):
            continue
        if not isinstance(linhas[0].get(chave), str):
            continue
        if "INSTITUICAO" in n or "CONGLOMERADO" in n:
            peso = 0
        elif n.startswith("NOME") or n == "RAZAOSOCIAL":
            peso = 1
        else:
            continue
        candidatas.append((peso, len(n), chave))
    candidatas.sort()
    return candidatas[0][2] if candidatas else None


def achar_campo_codigo(linhas):
    """A chave do código da instituição (pra juntar com o cadastro)."""
    if not linhas:
        return None
    for preferido in ("CODINST", "CODIGOINSTITUICAO", "CODCONGLOMERADO", "CODIGO"):
        for chave in linhas[0]:
            if normalizar(chave) == preferido:
                return chave
    for chave in linhas[0]:
        if normalizar(chave).startswith("COD"):
            return chave
    return None


def carregar_cadastro(anomes):
    """
    IfDataCadastro traz a razão social de cada instituição. É necessário
    quando o relatório de valores identifica a instituição só por código —
    que é justamente o desenho do formato longo.
    """
    for parametros in ({"AnoMes": anomes, "TipoInstituicao": TIPO_INSTITUICAO},
                       {"AnoMes": anomes}):
        linhas = pedir("IfDataCadastro", parametros, silencioso=True)
        if linhas:
            return linhas
    return None


def diagnosticar(linhas, quantas=3):
    """O que eu preciso ver quando algo não bate. Vai pro log do Actions."""
    log("  --- DIAGNÓSTICO (mande isto se pedir ajuda) ---")
    log(f"  chaves: {json.dumps(list(linhas[0].keys()), ensure_ascii=False)}")
    for linha in linhas[:quantas]:
        log(f"  linha: {json.dumps(linha, ensure_ascii=False)[:700]}")
    log("  --- fim do diagnóstico ---")


def procurar_relatorio(anomes):
    """
    Sonda os relatórios um por um até achar o que traz a Basileia. Em vez
    de confiar num número fixo: na primeira execução real o relatório 1
    devolveu 14 mil linhas e a lista de relatórios do BC veio vazia.
    """
    vazios_seguidos = 0
    vistos = []          # (numero, chaves, linha de exemplo) — pro diagnóstico
    for indice, numero in enumerate(RELATORIOS_PARA_SONDAR):
        linhas, inteiro = espiar(anomes, numero)
        if linhas is None:
            log(f"  relatório {numero}: a consulta FALHOU (veja o erro acima)")
            # O IF.data devolve 500 (não lista vazia) para trimestre que
            # ainda não existe: 202606 falhou nos 12 relatórios enquanto
            # 202603 respondia. Insistir nos outros relatórios do mesmo
            # trimestre só queima cota — e foi assim que a execução real
            # esgotou o limite antes de chegar num período publicado.
            if indice == 0:
                log("  (o primeiro relatório falhou — tratando como trimestre indisponível)")
                return None
            continue
        if not linhas:
            log(f"  relatório {numero}: sem dados")
            vazios_seguidos += 1
            # Trimestre ainda não publicado: os primeiros relatórios vêm
            # todos vazios. Desiste cedo em vez de gastar 12 requisições
            # por período pra descobrir a mesma coisa.
            if indice + 1 == vazios_seguidos and vazios_seguidos >= 2:
                log("  (período parece não publicado — parando a sondagem)")
                return None
            continue
        vazios_seguidos = 0

        amostra = linhas[:LINHAS_PARA_ESPIAR]
        achado = detectar_basileia(amostra)
        campo_nome = achar_campo_nome(amostra)
        marca = "<-- TEM BASILEIA" if achado else ""
        log(f"  relatório {numero}: {len(linhas)} linhas, nome={campo_nome or 'nenhum'} {marca}")
        # As chaves saem SEMPRE, não só no --explorar: numa rotina agendada,
        # pedir pra rerodar custa um dia.
        log(f"    chaves: {json.dumps(list(amostra[0].keys()), ensure_ascii=False)}")

        vistos.append((numero, list(amostra[0].keys()), amostra[0]))
        if achado:
            achado["relatorio"] = numero
            achado["campo_nome"] = campo_nome
            achado["campo_codigo"] = achar_campo_codigo(amostra)
            achado["amostra"] = amostra
            achado["completo"] = linhas if inteiro else None
            return achado

    # Nada encontrado: imprime o que veio de cada relatório SEM precisar
    # rerodar com --explorar.
    if vistos:
        log("\n  --- DIAGNÓSTICO: relatórios que responderam ---")
        for numero, chaves, exemplo in vistos:
            log(f"  relatório {numero}: {json.dumps(chaves, ensure_ascii=False)}")
            log(f"    exemplo: {json.dumps(exemplo, ensure_ascii=False)[:500]}")
        log("  --- fim do diagnóstico ---")
    return None


def casar_banco(nome_normalizado):
    for banco in BANCOS:
        for fragmento in banco["busca"]:
            if fragmento in nome_normalizado:
                return banco
    return None


def converter_numero(valor):
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    if isinstance(valor, str):
        limpo = valor.strip()
        if not limpo:
            return None
        # "15,31" e "1.234,56" vêm assim do BC; "15.31" também aparece
        if "," in limpo:
            limpo = limpo.replace(".", "").replace(",", ".")
        try:
            return round(float(limpo), 2)
        except ValueError:
            return None
    return None


def resolver_nomes(anomes, achado):
    """
    Define COMO descobrir a razão social de cada linha do relatório.

    No formato longo o relatório costuma identificar a instituição só pelo
    código; a razão social mora no IfDataCadastro. Devolve uma função que
    recebe a linha e devolve o nome (ou None).
    """
    campo_nome = achado.get("campo_nome")
    if campo_nome:
        log(f"    nomes: direto da coluna {campo_nome}")
        return lambda linha: linha.get(campo_nome)

    campo_codigo = achado.get("campo_codigo")
    if not campo_codigo:
        log("    nomes: SEM coluna de nome e SEM coluna de código — impossível identificar")
        return None

    cadastro = carregar_cadastro(anomes)
    if not cadastro:
        log("    nomes: IfDataCadastro não respondeu — impossível identificar")
        return None

    cad_codigo = achar_campo_codigo(cadastro)
    cad_nome = achar_campo_nome(cadastro)
    log(f"    cadastro: {len(cadastro)} instituições "
        f"(código={cad_codigo}, nome={cad_nome})")
    log(f"    chaves do cadastro: {json.dumps(list(cadastro[0].keys()), ensure_ascii=False)}")
    if not cad_codigo or not cad_nome:
        return None

    mapa = {str(linha.get(cad_codigo)): linha.get(cad_nome) for linha in cadastro}
    log(f"    nomes: juntando {campo_codigo} (valores) com {cad_codigo} (cadastro)")
    return lambda linha: mapa.get(str(linha.get(campo_codigo)))


def extrair_valores(linhas, achado, obter_nome):
    """Percorre o relatório inteiro e devolve {ticker: indice}."""
    valores, casados = {}, []
    for linha in linhas:
        if achado["formato"] == "longo":
            rotulo = linha.get(achado["campo_rotulo"])
            if not isinstance(rotulo, str) or normalizar(rotulo) != normalizar(achado["rotulo"]):
                continue
        nome_bc = obter_nome(linha)
        if not nome_bc:
            continue
        banco = casar_banco(normalizar(nome_bc))
        if not banco or banco["ticker"] in valores:
            continue
        numero = converter_numero(linha.get(achado["campo_valor"]))
        if numero is None:
            continue
        valores[banco["ticker"]] = numero
        casados.append((banco["ticker"], nome_bc, numero))
    return valores, casados


# ----------------------------------------------------------------------
# Gravação
# ----------------------------------------------------------------------

def rotulos(anomes):
    ano, mes = divmod(int(anomes), 100)
    trimestre = (mes + 2) // 3
    ultimo_dia = {3: "31/03", 6: "30/06", 9: "30/09", 12: "31/12"}.get(mes, f"--/{mes:02d}")
    return {
        "id": f"{ano}T{trimestre}",
        "rotulo": f"{trimestre}T{str(ano)[2:]}",
        "referencia": f"Base {ultimo_dia}/{ano} — IF.data/Banco Central (conglomerado prudencial)",
    }


def gravar(anomes, valores):
    if os.path.exists(ARQUIVO):
        with open(ARQUIVO, encoding="utf-8") as f:
            dados = json.load(f)
    else:
        dados = {"bancos": [{"ticker": b["ticker"], "nome": b["nome"]} for b in BANCOS],
                 "periodos": []}

    marcas = rotulos(anomes)
    periodo = {
        "id": marcas["id"],
        "rotulo": marcas["rotulo"],
        "referencia": marcas["referencia"],
        "valores": valores,
    }

    # Se nada mudou, não reescreve o arquivo. Sem isso, rodando semanalmente,
    # o campo 'atualizado_em' viraria um commit novo toda semana só pra
    # trocar uma data — sujando o histórico do repositório à toa.
    atual = next((p for p in dados.get("periodos", []) if str(p.get("id")) == periodo["id"]), None)
    if atual and atual.get("valores") == valores:
        log(f"\nNada mudou no período {periodo['rotulo']} — arquivo mantido como está.")
        return False

    # Substitui o período se já existir; senão entra no topo (o site usa a
    # ordem da lista, e o primeiro é o que abre selecionado).
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
    os.replace(temporario, ARQUIVO)   # troca atômica: nunca deixa o site ler meio arquivo
    log(f"\nGravado {ARQUIVO} — período {periodo['rotulo']} ({len(valores)} bancos).")
    return True


# ----------------------------------------------------------------------

def main():
    forcado = None
    for i, arg in enumerate(sys.argv):
        if arg == "--anomes" and i + 1 < len(sys.argv):
            forcado = int(sys.argv[i + 1])

    periodos = [forcado] if forcado else trimestres_recentes(TRIMESTRES_PARA_TRAS)
    log(f"IF.data — períodos a tentar: {', '.join(str(p) for p in periodos)}")

    for anomes in periodos:
        log(f"\n=== {anomes} ===")
        achado = procurar_relatorio(anomes)
        if not achado:
            log("  nenhum relatório desse período tem Basileia — tentando o período anterior...")
            continue

        log(f"\n  ACHADO no relatório {achado['relatorio']} (formato {achado['formato']})")
        log(f"    coluna do valor: {achado['campo_valor']}")
        log(f"    coluna do nome:  {achado['campo_nome'] or 'nenhuma (vai juntar com o cadastro)'}")
        log(f"    coluna do código: {achado['campo_codigo'] or 'nenhuma'}")
        if achado["formato"] == "longo":
            log(f"    rótulo usado:    {achado['rotulo']!r}")
            outros = [r for r in achado.get("outros_rotulos", []) if r != achado["rotulo"]]
            if outros:
                log(f"    (outros rótulos com 'basileia', ignorados: {outros})")

        # Se a sondagem já baixou o relatório inteiro, reaproveita. Baixar
        # de novo desperdiçava a maior requisição da execução e dava outra
        # chance pro 500 intermitente derrubar tudo depois de já ter achado.
        linhas = achado.get("completo")
        if linhas:
            log(f"    reaproveitando as {len(linhas)} linhas já baixadas")
        else:
            linhas = pedir("IfDataValores", parametros_valores(anomes, achado["relatorio"]))
            if not linhas:
                log("  o relatório inteiro não veio; tentando o período anterior...")
                continue
            log(f"    {len(linhas)} linhas no relatório completo")

        obter_nome = resolver_nomes(anomes, achado)
        if obter_nome is None:
            log("\n  Não consegui identificar as instituições.")
            diagnosticar(achado["amostra"])
            return 1

        valores, casados = extrair_valores(linhas, achado, obter_nome)

        log("\n  Casamento ticker <-> instituição:")
        for ticker, nome_bc, numero in sorted(casados):
            log(f"    {ticker:7s} {numero:6.2f}%   {nome_bc}")
        faltando = [b["ticker"] for b in BANCOS if b["ticker"] not in valores]
        if faltando:
            log(f"\n  NÃO ENCONTRADOS: {', '.join(faltando)}")
            log("  (ficam como '—' no site; ajustar a lista 'busca' desses bancos no topo do script)")
            # ajuda a acertar o fragmento: mostra nomes parecidos que existem
            amostra = sorted({str(obter_nome(l)) for l in linhas[:6000] if obter_nome(l)})
            for ticker in faltando:
                banco = next(b for b in BANCOS if b["ticker"] == ticker)
                # tenta cada palavra do nome e dos fragmentos de busca: é o
                # bastante pra revelar como o BC escreve aquela instituição
                termos = {palavra for texto in [banco["nome"]] + banco["busca"]
                          for palavra in normalizar(texto).split() if len(palavra) >= 4}
                parecidos = sorted({n for n in amostra
                                    for t in termos if t in normalizar(n)})[:5]
                log(f"    {ticker}: parecidos no IF.data -> {parecidos or 'nada parecido'}")

        if not valores:
            log("\n  Nenhum banco casou — não vou gravar um período vazio.")
            diagnosticar(achado["amostra"])
            return 1

        if EXPLORAR or DRY_RUN:
            log("\n  (sem gravar)")
            return 0
        gravar(anomes, valores)
        return 0

    log("\nNenhum período retornou Basileia.")
    log("Rode com --explorar: ele imprime as chaves de cada relatório sondado.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ServidorInstavel as e:
        log(f"\nPARANDO: {e}")
        sys.exit(1)
