#!/usr/bin/env python3
"""
Relatórios gerenciais dos FIIs — link direto para o último PDF publicado.

Grava data/relatorios-gerenciais.json. O site lê esse arquivo e, na Consulta
Rápida, troca o botão que abria a lista INTEIRA de documentos do fundo no
Fundos.NET (fato relevante, oferta pública, informe mensal, edital...) por um
botão que abre SÓ o relatório gerencial mais recente.

Fonte: Fundos.NET (B3), o mesmo sistema onde a CVM recebe os documentos.
    https://fnet.bmfbovespa.com.br/fnet/publico/abrirGerenciadorDocumentosCVM

--------------------------------------------------------------------------
Por que um fundo de cada vez (pelo CNPJ)
--------------------------------------------------------------------------
A busca geral do Fundos.NET devolve o campo `cnpjFundo` sempre em branco —
só o nome do fundo e o nome de pregão, que não casam de forma confiável com o
ticker. Pedindo pelo CNPJ não há casamento a adivinhar: o documento é daquele
fundo e pronto. O CNPJ de cada FII vem do mercado-fiis.json, gravado pelo
coletar_mercado.py a partir do campo `tax_id` da HG Brasil (a mesma resposta
que ele já pedia — nenhuma chamada a mais).

--------------------------------------------------------------------------
Só relatório gerencial — nada de fato relevante ou oferta
--------------------------------------------------------------------------
Dois filtros, de propósito:
  1. no servidor: idCategoriaDocumento=7 ("Relatórios") e idTipoDocumento=9
     ("Relatório Gerencial");
  2. aqui, linha a linha: o tipo devolvido tem de ser exatamente "Relatório
     Gerencial" e a categoria "Relatórios". Se um dia o Fundos.NET ignorar
     o filtro (ou renumerar os ids), o que vier de outro tipo é descartado e
     contado no diagnóstico, em vez de aparecer no site.
Documento cancelado ou inativo também fica de fora. Se o fundo reapresentou o
relatório, vale a versão mais alta da mesma data de referência.

Gestora que publica o relatório gerencial com outro tipo (como "Fato
Relevante" ou "Comunicado ao Mercado") fica SEM link — foi o combinado: o
botão só aponta para o que o próprio fundo classificou como relatório
gerencial.

--------------------------------------------------------------------------
Protocolo do Fundos.NET
--------------------------------------------------------------------------
Endpoint JSON (o mesmo que a tela pública usa):
    GET pesquisarGerenciadorDocumentosDados
Precisa: cabeçalho X-Requested-With: XMLHttpRequest, o token CSRF da página
principal no cabeçalho CSRFToken (quando a página publica um), e os
parâmetros d (contador), s (pular), l (quantidade), _ (timestamp).
Ids de categoria/tipo e o formato das datas conferidos na biblioteca aberta
`mercados` (PythonicCafe/mercados, fundosnet.py e choices.py).
Link do PDF: downloadDocumento?id=<id>.

--------------------------------------------------------------------------
Duas fontes: Fundos.NET e, se ele não responder, a API da B3
--------------------------------------------------------------------------
Na primeira execução no GitHub Actions (23/09/2026) o Fundos.NET não
respondeu nem a página inicial (read timeout de 25 s) — o mesmo que acontece
a partir dos servidores das sessões do Claude. Tudo indica que ele não
atende bem máquinas de nuvem fora do Brasil. Por isso:
  1. o robô testa o Fundos.NET UMA vez (até ~30 s). Respondeu → usa ele.
  2. não respondeu → usa a API de documentos da própria B3,
     sistemaswebb3-listados.b3.com.br/fundsListedProxy/Search/GetListedDocuments,
     o mesmo host que o coletar_mercado.py já usa para os FI-Infra. Pedido:
     {cnpj, identifierFund (sigla), typeFund (7 FII, 27 FI-Infra, 34 Fiagro),
     dateInitial, dateFinal, category: 7 (Relatórios), pageNumber, pageSize}
     — formato visto na biblioteca `mercados` (b3.py).
     O FORMATO DA RESPOSTA desse endpoint não está documentado em lugar
     nenhum que eu tenha conseguido ler; a biblioteca devolve a linha crua.
     Então a leitura é tolerante (procura o valor "Relatório Gerencial", o
     id do documento e as datas pelos nomes dos campos) e a PRIMEIRA linha
     de cada execução vai inteira para o log — se algo não casar, é ali que
     se descobre o formato real.
Em nenhum dos dois caminhos o link muda: é sempre o PDF no Fundos.NET
(downloadDocumento?id=), que abre normalmente no navegador de quem está no
Brasil — o problema é só com os servidores do Actions.

O certificado TLS do Fundos.NET já deu problema de cadeia com o requests.
O robô tenta com verificação; se falhar por SSL, repete sem verificar e
avisa no log (é dado público, só leitura).

Uso:
    python coletar_relatorios_gerenciais.py              # rodada normal
    python coletar_relatorios_gerenciais.py --dry-run    # não grava nada
    python coletar_relatorios_gerenciais.py --tudo       # ignora a folga de dias
    python coletar_relatorios_gerenciais.py --tickers HGLG11,KNCR11 --diagnostico
"""

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

BASE_FNET = "https://fnet.bmfbovespa.com.br/fnet/publico/"
URL_DOWNLOAD = BASE_FNET + "downloadDocumento?id={id}"
ID_CATEGORIA_RELATORIOS = 7
ID_TIPO_RELATORIO_GERENCIAL = 9

ARQ_FIIS = "mercado-fiis.json"
ARQ_SAIDA = os.path.join("data", "relatorios-gerenciais.json")

# Relatório gerencial é mensal (alguns fundos, trimestral). Fundo cujo último
# relatório chegou há menos que isto não é consultado de novo hoje — corta a
# rodada diária de ~500 pedidos para só os fundos "em dia de publicar".
DIAS_SEM_CONSULTAR = 20
# Relatório mais velho que isto sai do arquivo: link de um ano atrás passa a
# impressão de que é o atual. O site mostra a data de referência, mas um
# fundo que parou de publicar não deve continuar com botão.
DIAS_MAXIMO_RELATORIO = 400
PAUSA_ENTRE_PEDIDOS = 0.4
TIMEOUT = (10, 40)            # (conectar, ler)
TIMEOUT_TESTE_FNET = (10, 30)  # teste único do Fundos.NET no começo
# Se os primeiros fundos falharem TODOS, a fonte está fora do ar: para aqui
# em vez de esperar o timeout de cada um dos ~500 fundos.
DISJUNTOR_FALHAS_SEGUIDAS = 5
B3_DOCUMENTOS = "https://sistemaswebb3-listados.b3.com.br/fundsListedProxy/Search/GetListedDocuments/"
B3_TIPO_FII, B3_TIPO_FI_INFRA, B3_TIPO_FIAGRO = 7, 27, 34
DIAS_JANELA_B3 = 200
TENTATIVAS = 3

BRT = timezone(timedelta(hours=-3))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_RE_CSRF_META = re.compile(r"""<meta[^>]+name=["']_csrf["'][^>]+content=["']([^"']+)["']""", re.I)
_RE_CSRF_JS = re.compile(r"""csrf_token ?= ?["']([^"']+)["']""")


def log(msg):
    print(msg, flush=True)


def normalizar(texto):
    """Minúsculo, sem acento e sem espaço repetido — para comparar rótulos."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def eh_relatorio_gerencial(linha):
    """Segundo filtro, feito aqui: só passa o que o Fundos.NET classifica
    como Relatórios > Relatório Gerencial."""
    return (normalizar(linha.get("tipoDocumento")) == "relatorio gerencial"
            and normalizar(linha.get("categoriaDocumento")) == "relatorios")


def eh_ativo(linha):
    """Descarta documento cancelado/inativo. O Fundos.NET usa dois campos
    para a mesma coisa: situacaoDocumento (A/C/I) e descricaoStatus."""
    sit = str(linha.get("situacaoDocumento") or "").strip().upper()
    if sit:
        return sit == "A"
    status = normalizar(linha.get("descricaoStatus"))
    return status in ("", "ativo", "ativa")


def data_referencia(linha):
    """Data de referência do documento. O formato vem no próprio registro
    (formatoDataReferencia): 1 = AAAA, 2 = MM/AAAA, 3 = DD/MM/AAAA,
    4 = DD/MM/AAAA HH:MM. Devolve (date, texto_mm/aaaa) ou (None, None)."""
    valor = str(linha.get("dataReferencia") or "").strip()
    fmt = str(linha.get("formatoDataReferencia") or "").strip()
    if not valor:
        return None, None
    try:
        if fmt == "1":
            d = datetime.strptime(valor, "%Y").date()
        elif fmt == "2":
            d = datetime.strptime("01/" + valor, "%d/%m/%Y").date()
        elif fmt == "4":
            d = datetime.strptime(valor, "%d/%m/%Y %H:%M").date()
        else:
            d = datetime.strptime(valor[:10], "%d/%m/%Y").date()
    except ValueError:
        return None, None
    return d, d.strftime("%m/%Y")


def data_entrega(linha):
    valor = str(linha.get("dataEntrega") or "").strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor, fmt).replace(tzinfo=BRT)
        except ValueError:
            continue
    return None


def escolher_mais_recente(linhas):
    """Entre as linhas já filtradas, a de data de referência mais nova; no
    empate, a entregue por último e de versão mais alta (reapresentação)."""
    candidatas = []
    for ln in linhas:
        ref, _ = data_referencia(ln)
        ent = data_entrega(ln)
        if ref is None and ent is None:
            continue
        candidatas.append(((ref or ent.date()), ent or datetime.min.replace(tzinfo=BRT),
                           int(ln.get("versao") or 0), ln))
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    return candidatas[0][3]


class FundosNet:
    def __init__(self):
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = UA
        self.sessao.headers["Accept"] = "application/json,text/html,*/*"
        self.verificar_ssl = True
        self.contador = 0
        self._iniciado = False

    nome = "Fundos.NET"

    def _get(self, caminho, timeout=TIMEOUT, **kw):
        url = BASE_FNET + caminho
        try:
            return self.sessao.get(url, timeout=timeout, verify=self.verificar_ssl, **kw)
        except requests.exceptions.SSLError as e:
            if not self.verificar_ssl:
                raise
            log(f"  AVISO: certificado do Fundos.NET recusado ({e.__class__.__name__}); "
                "repetindo sem verificar o TLS (dado público, só leitura).")
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.verificar_ssl = False
            return self.sessao.get(url, timeout=timeout, verify=False, **kw)

    def iniciar(self):
        """Abre a página pública para pegar cookie de sessão e token CSRF."""
        if self._iniciado:
            return
        resp = self._get("abrirGerenciadorDocumentosCVM", timeout=TIMEOUT_TESTE_FNET)
        resp.raise_for_status()
        m = _RE_CSRF_META.search(resp.text) or _RE_CSRF_JS.search(resp.text)
        if m and m.group(1).strip():
            self.sessao.headers["CSRFToken"] = m.group(1).strip()
            log("  Fundos.NET: sessão aberta (com token CSRF).")
        else:
            log("  Fundos.NET: sessão aberta (a página não publicou token CSRF).")
        self._iniciado = True

    def relatorios_do_fundo(self, cnpj, ticker=None, fundo=None, quantos=10):
        """Últimos documentos Relatórios > Relatório Gerencial do fundo."""
        self.iniciar()
        self.contador += 1
        params = {
            "d": self.contador,
            "s": 0,
            "l": quantos,
            "o[0][dataEntrega]": "desc",
            "idCategoriaDocumento": ID_CATEGORIA_RELATORIOS,
            "idTipoDocumento": ID_TIPO_RELATORIO_GERENCIAL,
            "idEspecieDocumento": 0,
            "tipoFundo": "",
            "cnpj": cnpj,
            "cnpjFundo": cnpj,
            "dataInicial": "",
            "dataFinal": "",
            "_": int(time.time() * 1000),
        }
        ultimo_erro = None
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                resp = self._get("pesquisarGerenciadorDocumentosDados", params=params,
                                 headers={"X-Requested-With": "XMLHttpRequest"})
                if resp.status_code in (429,) or resp.status_code >= 500:
                    ultimo_erro = f"HTTP {resp.status_code}"
                else:
                    resp.raise_for_status()
                    dados = resp.json()
                    if not isinstance(dados, dict) or "data" not in dados:
                        ultimo_erro = f"resposta inesperada: {str(dados)[:200]}"
                    else:
                        return dados.get("data") or []
            except (requests.RequestException, ValueError) as e:
                ultimo_erro = e
            time.sleep(1.5 * tentativa)
        raise RuntimeError(f"Fundos.NET falhou para o CNPJ {cnpj}: {ultimo_erro}")


# ---------------------------------------------------------------------------
# Fonte alternativa: API de documentos da B3
# ---------------------------------------------------------------------------
_CHAVES_ID = ("idfnet", "iddocumento", "iddocument", "documentid", "id")
_TIPOS_PROIBIDOS = ("fato relevante", "comunicado ao mercado", "aviso aos cotistas",
                    "ata de assembleia", "edital de convocacao")
# Outros tipos da mesma categoria "Relatórios" no Fundos.NET.
_OUTROS_RELATORIOS = ("relatorio anual", "outros relatorios", "relatorio de agencia de rating",
                      "relatorio de agencia classificadora de risco", "relatorio do representante de cotistas",
                      "relatorio de agente fiduciario")


def _parse_data_b3(valor):
    """Aceita os formatos vistos nas APIs da B3 e do Fundos.NET. Devolve
    (datetime, precisao) — precisao 'mes' quando só veio MM/AAAA."""
    v = str(valor or "").strip()
    if not v or v.startswith("0001-01-01"):
        return None, None
    for fmt, prec in (("%Y-%m-%dT%H:%M:%S.%f", "dia"), ("%Y-%m-%dT%H:%M:%S", "dia"),
                      ("%Y-%m-%d %H:%M:%S", "dia"), ("%Y-%m-%d", "dia"),
                      ("%d/%m/%Y %H:%M:%S", "dia"), ("%d/%m/%Y %H:%M", "dia"),
                      ("%d/%m/%Y", "dia"), ("%m/%Y", "mes")):
        try:
            return datetime.strptime(v[:26] if "T" in v else v, fmt), prec
        except ValueError:
            continue
    return None, None


def linha_b3_para_fnet(bruta):
    """Traduz uma linha da API da B3 para o formato do Fundos.NET usado no
    resto do robô. Leitura tolerante: o formato da resposta não é
    documentado. Linha que não der para identificar com segurança como
    Relatório Gerencial sai com tipo vazio — e é descartada adiante."""
    if not isinstance(bruta, dict):
        return None
    chaves = {normalizar(k).replace("_", ""): k for k in bruta}
    textos = [normalizar(v) for v in bruta.values() if isinstance(v, str)]

    # Aceita o valor "Relatório Gerencial" (ou um título que comece assim,
    # como "Relatório Gerencial - Agosto 2026"), desde que NENHUM outro
    # campo da linha diga que é outro tipo de documento.
    tipo = ""
    parece_gerencial = any(t == "relatorio gerencial" or t.startswith("relatorio gerencial ")
                           or t.startswith("relatorio gerencial-") for t in textos)
    outro_tipo = any(t in _TIPOS_PROIBIDOS or t in _OUTROS_RELATORIOS or t.startswith("oferta publica")
                     for t in textos)
    if parece_gerencial and not outro_tipo:
        tipo = "Relatório Gerencial"

    doc_id = None
    for chave in _CHAVES_ID:
        k = chaves.get(chave)
        if k is not None and str(bruta[k]).strip().isdigit() and int(bruta[k]) > 0:
            doc_id = int(bruta[k])
            break

    ref = ent = None
    ref_prec = None
    for kn, k in chaves.items():
        dt, prec = _parse_data_b3(bruta[k])
        if not dt:
            continue
        if "refer" in kn and ref is None:
            ref, ref_prec = dt, prec
        elif any(p in kn for p in ("deliver", "entrega", "delivery", "sent", "publica")) and ent is None:
            ent = dt
        elif kn in ("date", "data", "datedocument", "documentdate") and ent is None:
            ent = dt

    status = ""
    for kn, k in chaves.items():
        if "status" in kn or "situa" in kn:
            status = normalizar(bruta[k])
            break
    situacao = "C" if status.startswith("cancel") or status == "c" else "I" if status.startswith("inativ") or status == "i" else "A"

    versao = 1
    for kn in ("version", "versao"):
        k = chaves.get(kn)
        if k is not None and str(bruta[k]).strip().isdigit():
            versao = int(bruta[k])

    if doc_id is None:
        return None
    return {
        "id": doc_id,
        "categoriaDocumento": "Relatórios" if tipo else "",
        "tipoDocumento": tipo,
        "dataReferencia": (ref.strftime("%m/%Y") if ref_prec == "mes" else ref.strftime("%d/%m/%Y")) if ref else "",
        "formatoDataReferencia": "2" if ref_prec == "mes" else "3",
        "dataEntrega": ent.strftime("%d/%m/%Y %H:%M") if ent else "",
        "versao": versao,
        "situacaoDocumento": situacao,
        "descricaoFundo": next((str(bruta[k]).strip() for kn, k in chaves.items()
                                if kn in ("companyname", "fundname", "nomefundo")), ""),
    }


class FonteB3:
    """GetListedDocuments da B3, categoria 7 (Relatórios)."""
    nome = "API da B3"

    def __init__(self):
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = UA
        self.sessao.headers["Accept"] = "application/json,*/*"
        self.primeira_linha_mostrada = False

    @staticmethod
    def _b64(payload):
        return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")

    def _pedir(self, payload):
        resp = self.sessao.get(B3_DOCUMENTOS + self._b64(payload), timeout=TIMEOUT)
        if resp.status_code >= 500 or resp.status_code == 429:
            raise RuntimeError(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        texto = resp.text.strip()
        if not texto:
            return []
        dados = json.loads(texto)
        if isinstance(dados, str):          # a B3 às vezes devolve o JSON como string
            dados = json.loads(dados) if dados.strip() else []
        if isinstance(dados, dict):
            return dados.get("results") or dados.get("data") or []
        return dados if isinstance(dados, list) else []

    def relatorios_do_fundo(self, cnpj, ticker=None, fundo=None, quantos=50):
        hoje = datetime.now(BRT).date()
        sigla = (ticker or "")[:4]
        tipo_fundo = str((fundo or {}).get("tipo_fundo") or "").lower()
        tipos = [B3_TIPO_FI_INFRA] if "infra" in tipo_fundo else [B3_TIPO_FII, B3_TIPO_FIAGRO]
        ultimo_erro = None
        for tipo in tipos:
            payload = {"pageNumber": 1, "pageSize": quantos, "cnpj": cnpj, "identifierFund": sigla,
                       "typeFund": tipo, "dateInitial": (hoje - timedelta(days=DIAS_JANELA_B3)).isoformat(),
                       "dateFinal": hoje.isoformat(), "category": ID_CATEGORIA_RELATORIOS}
            brutas = None
            for tentativa in range(1, TENTATIVAS + 1):
                try:
                    brutas = self._pedir(payload)
                    break
                except (requests.RequestException, ValueError, RuntimeError) as e:
                    ultimo_erro = e
                    time.sleep(1.5 * tentativa)
            if brutas is None:
                continue
            if brutas and not self.primeira_linha_mostrada:
                self.primeira_linha_mostrada = True
                log(f"  [B3] formato da resposta (1ª linha, {ticker}, typeFund {tipo}): "
                    f"{json.dumps(brutas[0], ensure_ascii=False)[:1200]}")
            if brutas:
                return [ln for ln in (linha_b3_para_fnet(b) for b in brutas) if ln]
        if ultimo_erro is not None:
            raise RuntimeError(f"API da B3 falhou para {ticker} ({cnpj}): {ultimo_erro}")
        return []


class FonteComReserva:
    """Fundos.NET na frente, API da B3 de reserva — fundo a fundo.
    Se o Fundos.NET falhar para um fundo, o MESMO fundo é pedido à B3 na
    hora. Se ele falhar nos primeiros DISJUNTOR_FALHAS_SEGUIDAS fundos sem
    nenhum acerto, deixa de ser tentado no resto da rodada (a página inicial
    pode abrir e a busca não responder — foi preciso cobrir os dois casos)."""

    def __init__(self, principal, reserva):
        self.principal, self.reserva = principal, reserva
        self.falhas_principal = 0
        self.acertos_principal = 0
        self.desistiu = False

    @property
    def nome(self):
        return self.reserva.nome if self.desistiu else f"{self.principal.nome} (reserva: {self.reserva.nome})"

    def relatorios_do_fundo(self, cnpj, ticker=None, fundo=None):
        if not self.desistiu:
            try:
                linhas = self.principal.relatorios_do_fundo(cnpj, ticker=ticker, fundo=fundo)
                self.acertos_principal += 1
                return linhas
            except Exception as e:  # noqa: BLE001
                self.falhas_principal += 1
                if not self.acertos_principal and self.falhas_principal >= DISJUNTOR_FALHAS_SEGUIDAS:
                    self.desistiu = True
                    log(f"  {self.principal.nome} falhou nos {self.falhas_principal} primeiros fundos "
                        f"({str(e)[:120]}). Seguindo só com a {self.reserva.nome}.")
        return self.reserva.relatorios_do_fundo(cnpj, ticker=ticker, fundo=fundo)


def escolher_fonte():
    """Fundos.NET se ele responder; senão, a API da B3."""
    fnet = FundosNet()
    try:
        fnet.iniciar()
        return FonteComReserva(fnet, FonteB3())
    except Exception as e:  # noqa: BLE001
        log(f"  Fundos.NET não respondeu daqui ({e.__class__.__name__}: {str(e)[:160]}). "
            "Usando a API de documentos da B3.")
        return FonteB3()


def ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_atomico(caminho, dados):
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(caminho) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, caminho)


def montar_registro(linha, cnpj):
    ref, ref_txt = data_referencia(linha)
    ent = data_entrega(linha)
    return {
        "id": int(linha["id"]),
        "url": URL_DOWNLOAD.format(id=int(linha["id"])),
        "referencia": ref.isoformat() if ref else None,
        "referencia_texto": ref_txt,
        "entregue_em": ent.date().isoformat() if ent else None,
        "versao": int(linha.get("versao") or 1),
        "fundo": str(linha.get("descricaoFundo") or "").strip() or None,
        "cnpj": cnpj,
    }


def coletar(fnet, fiis, anterior, agora, tudo=False, diagnostico=False):
    relatorios = dict(anterior.get("relatorios") or {})
    diag = {"fonte": getattr(fnet, "nome", "?"), "fundos_no_arquivo": len(fiis), "sem_cnpj": 0, "consultados": 0,
            "pulados_em_dia": 0, "com_relatorio": 0, "sem_relatorio": 0,
            "falhas": 0, "linhas_lidas": 0, "descartadas_outro_tipo": 0,
            "descartadas_inativas": 0, "exemplos_descartados": []}
    sem_cnpj = []

    for f in fiis:
        ticker = str(f.get("ticker") or "").strip().upper()
        cnpj = re.sub(r"\D", "", str(f.get("cnpj") or ""))
        if not ticker:
            continue
        if len(cnpj) != 14:
            diag["sem_cnpj"] += 1
            sem_cnpj.append(ticker)
            continue

        atual = relatorios.get(ticker)
        if atual and not tudo and atual.get("cnpj") == cnpj and atual.get("entregue_em"):
            try:
                entregue = datetime.fromisoformat(atual["entregue_em"]).date()
                if (agora.date() - entregue).days < DIAS_SEM_CONSULTAR:
                    diag["pulados_em_dia"] += 1
                    continue
            except ValueError:
                pass

        diag["consultados"] += 1
        try:
            linhas = fnet.relatorios_do_fundo(cnpj, ticker=ticker, fundo=f)
        except Exception as e:  # noqa: BLE001 — um fundo não derruba os outros
            diag["falhas"] += 1
            log(f"  {ticker}: falhou ({e}) — mantém o link anterior, se houver.")
            if diag["falhas"] == diag["consultados"] >= DISJUNTOR_FALHAS_SEGUIDAS:
                log(f"  Os {DISJUNTOR_FALHAS_SEGUIDAS} primeiros fundos falharam: {fnet.nome} fora do ar. Parando.")
                diag["interrompido"] = True
                break
            time.sleep(PAUSA_ENTRE_PEDIDOS)
            continue

        diag["linhas_lidas"] += len(linhas)
        if diagnostico and linhas:
            log(f"  [diagnóstico] {ticker} primeira linha crua: {json.dumps(linhas[0], ensure_ascii=False)[:900]}")

        validas = []
        for ln in linhas:
            if not eh_relatorio_gerencial(ln):
                diag["descartadas_outro_tipo"] += 1
                if len(diag["exemplos_descartados"]) < 5:
                    diag["exemplos_descartados"].append(
                        f"{ticker}: {ln.get('categoriaDocumento')} / {ln.get('tipoDocumento')}")
                continue
            if not eh_ativo(ln):
                diag["descartadas_inativas"] += 1
                continue
            validas.append(ln)

        escolhido = escolher_mais_recente(validas)
        if escolhido:
            relatorios[ticker] = montar_registro(escolhido, cnpj)
            diag["com_relatorio"] += 1
            r = relatorios[ticker]
            log(f"  {ticker}: ref. {r['referencia_texto']} · entregue {r['entregue_em']} · id {r['id']}")
        else:
            diag["sem_relatorio"] += 1
            # Não apaga o link anterior só porque a consulta de hoje veio
            # vazia: pode ser instabilidade. A idade máxima (abaixo) é que
            # tira do ar o que ficou velho.
        time.sleep(PAUSA_ENTRE_PEDIDOS)

    if diag.get("interrompido"):
        return dict(sorted(relatorios.items())), diag

    # Fundo que saiu do mercado-fiis.json ou relatório velho demais saem.
    tickers_vivos = {str(f.get("ticker") or "").upper() for f in fiis}
    limite = agora.date() - timedelta(days=DIAS_MAXIMO_RELATORIO)
    removidos = []
    for t in list(relatorios):
        r = relatorios[t]
        ref = r.get("referencia") or r.get("entregue_em")
        velho = False
        try:
            velho = bool(ref) and datetime.fromisoformat(ref).date() < limite
        except ValueError:
            pass
        if t not in tickers_vivos or velho:
            removidos.append(t)
            del relatorios[t]
    if removidos:
        log(f"  Removidos (saíram da lista ou relatório com mais de {DIAS_MAXIMO_RELATORIO} dias): {', '.join(sorted(removidos))}")

    if sem_cnpj:
        log(f"  Sem CNPJ no {ARQ_FIIS} ({len(sem_cnpj)}): {', '.join(sem_cnpj[:15])}"
            f"{' ...' if len(sem_cnpj) > 15 else ''}")
        if len(sem_cnpj) == len(fiis):
            log("  ATENÇÃO: nenhum fundo tem CNPJ — rode primeiro o coletar_mercado.py "
                "atualizado (ele passou a gravar o campo cnpj).")
    return dict(sorted(relatorios.items())), diag


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="não grava o arquivo")
    ap.add_argument("--tudo", action="store_true", help=f"consulta mesmo quem publicou há menos de {DIAS_SEM_CONSULTAR} dias")
    ap.add_argument("--tickers", default="", help="só estes tickers, separados por vírgula")
    ap.add_argument("--diagnostico", action="store_true", help="mostra a primeira linha crua de cada fundo")
    args = ap.parse_args(argv)

    agora = datetime.now(BRT)
    base = ler_json(ARQ_FIIS, {})
    fiis = base.get("ativos") or []
    if args.tickers:
        quero = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        fiis = [f for f in fiis if str(f.get("ticker") or "").upper() in quero]
    log(f"Relatórios gerenciais: {len(fiis)} fundos em {ARQ_FIIS}.")
    if not fiis:
        log("Nada a fazer.")
        return 0

    anterior = ler_json(ARQ_SAIDA, {})
    fnet = escolher_fonte()
    log(f"  Fonte: {fnet.nome}.")

    relatorios, diag = coletar(fnet, fiis, anterior, agora, tudo=args.tudo, diagnostico=args.diagnostico)

    log("Resumo: " + ", ".join(f"{k}={v}" for k, v in diag.items() if k != "exemplos_descartados"))
    if diag["exemplos_descartados"]:
        log("  Exemplos descartados por não serem relatório gerencial: "
            + " | ".join(diag["exemplos_descartados"]))

    # Se TODAS as consultas falharam, não sobrescreve o arquivo bom com um
    # arquivo que só reflete a queda do Fundos.NET.
    if diag["consultados"] and diag["falhas"] == diag["consultados"]:
        log(f"ERRO: todas as consultas falharam ({fnet.nome}). Arquivo mantido como estava.")
        return 1

    saida = {
        "_leia_me": ("Link para o último RELATÓRIO GERENCIAL de cada FII, publicado no Fundos.NET "
                     "(categoria Relatórios, tipo Relatório Gerencial). Fato relevante, oferta pública, "
                     "informes e comunicados ficam de fora de propósito. Gerado pelo robô "
                     "coletar_relatorios_gerenciais.py; o CNPJ de cada fundo vem do mercado-fiis.json."),
        "fonte": "Fundos.NET — B3",
        "atualizado_em": agora.date().isoformat(),
        "relatorios": relatorios,
    }
    if args.dry_run:
        log(f"--dry-run: {len(relatorios)} relatórios; nada gravado.")
        return 0

    # Não mexe no arquivo quando só a data de atualização mudaria — evita
    # commit diário vazio.
    if (anterior.get("relatorios") or {}) == relatorios and os.path.exists(ARQ_SAIDA):
        log("Nenhum relatório novo — arquivo não alterado.")
        return 0
    gravar_atomico(ARQ_SAIDA, saida)
    log(f"Gravado {ARQ_SAIDA}: {len(relatorios)} fundos com relatório gerencial.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
