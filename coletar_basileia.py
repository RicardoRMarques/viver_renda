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
  2. Baixa o cadastro de instituições e os valores do relatório.
  3. Acha a coluna de Basileia PELO NOME (qualquer chave cujo nome
     normalizado contenha "basileia"), em vez de depender da grafia
     exata — assim uma renomeação no lado do BC não quebra tudo.
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
Rode com --explorar e mande a saída. Ela imprime os nomes de relatório,
as chaves que vieram e uma amostra das instituições — que é exatamente o
que falta para acertar os detalhes (o ambiente onde este script foi
escrito não tem saída de rede para olinda.bcb.gov.br, então os nomes de
parâmetro abaixo são a forma documentada, mas não foram exercitados
contra o servidor real).
"""

import json
import os
import re
import sys
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

# Relatório do IF.data que traz o Índice de Basileia. "Resumo" é onde ele
# aparece; o código é confirmado em tempo de execução pela lista de
# relatórios (ver escolher_relatorio()).
RELATORIO_PADRAO = os.environ.get("BASILEIA_RELATORIO", "1")

TEMPO_LIMITE = 60
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
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "viverderenda-basileia/1.0 (+https://viverderenda.dev.br)",
    })
    with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def pedir(recurso, parametros=None, top=None, silencioso=False):
    """Devolve a lista em 'value', ou None se a chamada falhar."""
    url = montar_url(recurso, parametros, top)
    try:
        dados = buscar_json(url)
    except urllib.error.HTTPError as e:
        if not silencioso:
            log(f"  HTTP {e.code} em {recurso} — {url}")
        return None
    except Exception as e:  # noqa: BLE001
        if not silencioso:
            log(f"  falhou {recurso}: {e}")
        return None
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


def escolher_relatorio(anomes):
    """
    Procura, na lista de relatórios do período, aquele cujo nome sugere
    Basileia/Capital. Se não achar, volta o padrão.
    """
    lista = pedir("ListaDeRelatorios", {"AnoMes": anomes, "TipoInstituicao": TIPO_INSTITUICAO},
                  silencioso=True)
    if not lista:
        lista = pedir("ListaDeRelatorios", {"AnoMes": anomes}, silencioso=True)
    if not lista:
        return RELATORIO_PADRAO, []

    preferidos = ("BASILEIA", "CAPITAL", "RESUMO")
    melhor = None
    for item in lista:
        nome = normalizar(" ".join(str(v) for v in item.values()))
        for peso, alvo in enumerate(preferidos):
            if alvo in nome and (melhor is None or peso < melhor[0]):
                numero = (item.get("numeroRelatorio") or item.get("NumeroRelatorio")
                          or item.get("relatorio") or item.get("Relatorio"))
                if numero is not None:
                    melhor = (peso, str(numero), item)
    if melhor:
        return melhor[1], lista
    return RELATORIO_PADRAO, lista


def achar_campo_basileia(linhas):
    """
    Acha a chave da Basileia pelo NOME, não por grafia fixa. Prefere a que
    for exatamente o índice, evitando 'basileiaAmpliado'/'nivel1' quando
    houver mais de uma candidata.
    """
    if not linhas:
        return None
    candidatas = [k for k in linhas[0] if "BASILEIA" in normalizar(k)]
    if not candidatas:
        return None
    candidatas.sort(key=lambda k: (len(normalizar(k)), normalizar(k)))
    return candidatas[0]


def achar_campo_nome(linhas):
    if not linhas:
        return None
    for chave in linhas[0]:
        n = normalizar(chave)
        if "NOMEINSTITUICAO" in n or "INSTITUICAO" in n or n == "NOME":
            return chave
    return None


def casar_banco(nome_normalizado):
    for banco in BANCOS:
        for fragmento in banco["busca"]:
            if fragmento in nome_normalizado:
                return banco
    return None


def converter_numero(valor):
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    if isinstance(valor, str):
        limpo = valor.strip().replace(".", "").replace(",", ".")
        try:
            return round(float(limpo), 2)
        except ValueError:
            return None
    return None


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
    log(f"IF.data — tentando os períodos: {', '.join(str(p) for p in periodos)}")

    for anomes in periodos:
        log(f"\n=== {anomes} ===")
        relatorio, lista_relatorios = escolher_relatorio(anomes)
        if EXPLORAR and lista_relatorios:
            log(f"  relatórios disponíveis ({len(lista_relatorios)}):")
            for item in lista_relatorios[:40]:
                log(f"    {json.dumps(item, ensure_ascii=False)}")
        log(f"  relatório escolhido: {relatorio}")

        linhas = pedir("IfDataValores", {
            "AnoMes": anomes,
            "TipoInstituicao": TIPO_INSTITUICAO,
            "Relatorio": str(relatorio),
        })
        if not linhas:
            log("  sem dados nesse período, tentando o anterior...")
            continue

        log(f"  {len(linhas)} linhas recebidas")
        if EXPLORAR:
            log(f"  chaves da primeira linha:\n    {json.dumps(list(linhas[0].keys()), ensure_ascii=False)}")
            log(f"  amostra:\n    {json.dumps(linhas[0], ensure_ascii=False)[:900]}")

        campo_basileia = achar_campo_basileia(linhas)
        campo_nome = achar_campo_nome(linhas)
        log(f"  campo de Basileia: {campo_basileia or 'NÃO ENCONTRADO'}")
        log(f"  campo de nome:     {campo_nome or 'NÃO ENCONTRADO'}")
        if not campo_basileia or not campo_nome:
            log("  >> rode com --explorar e me mande a saída: as chaves acima é que faltam.")
            return 1

        valores, casados = {}, []
        for linha in linhas:
            nome = normalizar(linha.get(campo_nome))
            banco = casar_banco(nome)
            if not banco or banco["ticker"] in valores:
                continue
            numero = converter_numero(linha.get(campo_basileia))
            if numero is None:
                continue
            valores[banco["ticker"]] = numero
            casados.append((banco["ticker"], linha.get(campo_nome), numero))

        log("\n  Casamento ticker <-> instituição:")
        for ticker, nome_bc, numero in sorted(casados):
            log(f"    {ticker:7s} {numero:6.2f}%   {nome_bc}")
        faltando = [b["ticker"] for b in BANCOS if b["ticker"] not in valores]
        if faltando:
            log(f"\n  NÃO ENCONTRADOS: {', '.join(faltando)}")
            log("  (aparecem como '—' no site; ajustar 'busca' desses bancos aqui no script)")

        if not valores:
            log("\n  Nenhum banco casou — não vou gravar um período vazio.")
            log("  Rode com --explorar pra ver como as razões sociais vêm escritas.")
            return 1

        if EXPLORAR or DRY_RUN:
            log("\n  (sem gravar)")
            return 0
        gravar(anomes, valores)
        return 0

    log("\nNenhum período retornou dados. Rode com --explorar.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
