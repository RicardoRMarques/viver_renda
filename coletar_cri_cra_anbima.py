#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletar_cri_cra_anbima.py
---------------------------------------------------------------------
Baixa as TAXAS do mercado secundário de CRI e CRA que a ANBIMA publica
todo dia útil e grava em data/cri_cra_precos.json.

Por que este robô existe
-------------------------
coletar_cri.py já traz o cadastro de EMISSÃO dos CRIs listados na B3,
mas a B3 não publica uma taxa de mercado secundário atualizada para
esses papéis. A ANBIMA publica — só que não num arquivo .txt de nome
fixo como o das debêntures (ver coletar_debentures.py): é um endpoint
de exportação da página institucional de "Taxas de CRI e CRA".

Esse endpoint também cobre CRA (Certificado de Recebíveis do
Agronegócio), que a B3 não tem fonte alguma — então este robô é, hoje,
a ÚNICA fonte de dado de CRA do site.

O que o arquivo É e o que ele NÃO é
------------------------------------
É uma taxa INDICATIVA de mercado secundário — a referência de onde o
papel está sendo negociado hoje entre instituições — não uma oferta
disponível para compra, nem preço de emissão nova. Mesma natureza do
dado de Debêntures, mesma ressalva.

A ANBIMA não precifica todo papel todo dia: dos CRIs cadastrados na B3
(~4400 ainda não vencidos), só uma fração pequena (na faixa de 300)
aparece aqui com preço. O arquivo também lista, às vezes, papéis sem
nenhuma taxa preenchida naquele dia (negociação não aconteceu) — este
robô mantém essas linhas (com os campos de taxa em None) em vez de
descartar, porque "existe mas não negociou hoje" é informação diferente
de "não existe".

Como foi descoberto (não documentado publicamente)
-----------------------------------------------------
A página https://data.anbima.com.br/busca/certificado-de-recebiveis
(a SPA nova do "ANBIMA Data") tem um botão "CSV" que, ao ser clicado,
dispara uma chamada para www.anbima.com.br/pt_br/anbima/
TaxasCriCraExport/downloadExterno — mas essa chamada depende de estado
de sessão da SPA e devolve HTTP 500 fora dela.

A página INSTITUCIONAL clássica — www.anbima.com.br/pt_br/informar/
precos-e-indices/precos/taxas-de-cri-e-cra/taxas-de-cri-e-cra.htm — tem
o MESMO botão "CSV", só que ele chama um endpoint diferente e mais
simples: TaxasCriCraExport/exportarCSV. Testado e confirmado (sessão de
23/10/2026): esse endpoint é público e SEM ESTADO — nem cookie, nem
sessão, nem token. Um requests.get() direto, do zero, devolve o mesmo
arquivo que o navegador baixa.

URL:       https://www.anbima.com.br/pt_br/anbima/TaxasCriCraExport/exportarCSV
Parâmetros: filtroTermo="" e filtroData="" (deixar os dois em branco não
            exige adivinhar data, diferente do arquivo de debêntures —
            mas também NÃO devolve só um dia: vem uma SÉRIE dos últimos
            ~5 dias úteis, com cada papel repetido uma vez por dia. O
            robô fica só com a data mais recente — ver interpretar()).
Encoding:  latin-1 (ISO-8859-1). Linhas terminam em \n (não \r\n, ao
           contrário do arquivo de CRI da B3 — ver coletar_cri.py).
Separador: ; (ponto e vírgula). Decimais: vírgula. Ausente: campo vazio.

Estrutura (17 colunas, confirmadas rodando contra o arquivo real):
   0 Data de Referência             9  Taxa Venda
   1 Risco de Crédito               10 Taxa Indicativa
   2 Emissor                        11 Desvio Padrão
   3 Série                          12 PU
   4 Emissão                        13 % PU Par / % VNE
   5 Código                         14 Duration
   6 Vencimento                     15 Referência NTNB
   7 Índice / Correção              16 % Reune
   8 Taxa Compra

"Risco de Crédito" é o originador/devedor dos recebíveis que garantem o
papel (quem paga, no fim) — diferente de "Emissor", que é a
securitizadora que estruturou e vende o papel. Pode vir em branco: nem
todo papel tem essa informação divulgada.

Como separar CRI de CRA
-------------------------
O arquivo não tem uma coluna "tipo". Todo código de CRA visto contém a
substring "CRA" (ex: "CRA019003V2"); todo código de CRI visto é
alfanumérico sem essa substring (ex: "25B0013406" — mesmo padrão dos
códigos do cadastro da B3 em coletar_cri.py). Não é uma regra garantida
pela ANBIMA por documentação, só um padrão observado — fica registrado
aqui para quem revisar este robô no futuro.

A coluna "Índice/Correção" usa o MESMO tipo de texto que o arquivo de
debêntures ("100% do DI", "IPCA + 6,80%", "PREFIXADO"...) — a função de
classificação abaixo é uma versão estendida da de coletar_debentures.py.

Saída: data/cri_cra_precos.json
---------------------------------------------------------------------
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

URL_EXPORT = "https://www.anbima.com.br/pt_br/anbima/TaxasCriCraExport/exportarCSV"
URL_FONTE = (
    "https://www.anbima.com.br/pt_br/informar/precos-e-indices/precos/"
    "taxas-de-cri-e-cra/taxas-de-cri-e-cra.htm"
)

CAMINHO_SAIDA = os.path.join("data", "cri_cra_precos.json")

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": URL_FONTE,
}

TEMPO_LIMITE = 30  # segundos

# Nomes das colunas, na ordem esperada — serve de DETECTOR DE MUDANÇA
# (mesma ideia do coletar_cri.py e coletar_debentures.py): se a ANBIMA
# reordenar ou renomear, o robô avisa no log em vez de gravar errado
# calado. O mapeamento de posição é feito pelo NOME, não por índice fixo
# — então uma reordenação não quebra a coleta, só o aviso.
COLUNAS_ESPERADAS = (
    "Data de Referência", "Risco de Crédito", "Emissor", "Série", "Emissão",
    "Código", "Vencimento", "Índice / Correção", "Taxa Compra", "Taxa Venda",
    "Taxa Indicativa", "Desvio Padrão", "PU", "% PU Par / % VNE", "Duration",
    "Referência NTNB", "% Reune",
)

# Mesma lista de indexadores conhecidos do coletar_cri.py (a B3 e a
# ANBIMA usam os mesmos nomes de indexador de renda fixa brasileira).
INDEXADORES_CONHECIDOS = {
    "DI", "IGP-DI", "IGP-M", "INCC-DI", "INCC-M", "INPC", "IPCA",
    "IPC-FIPE", "PREFIXADO", "SELIC", "TR",
}


# ---------------------------------------------------------------------
# Conversores
# ---------------------------------------------------------------------

def numero(bruto):
    """"466,74430235" -> 466.74430235. Vazio/"--"/não numérico -> None."""
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


def _chave(indexador_anbima):
    """"IGP-M" -> "igp_m". Mesma convenção de chave usada em coletar_cri.py."""
    return indexador_anbima.lower().replace("-", "_")


def classificar_indexador(bruto):
    """Interpreta o texto da coluna "Índice/Correção".

    Formatos vistos ou esperados (mesma família do arquivo de
    debêntures, ver coletar_debentures.py):
      "100% do DI"              -> fração do indexador, sem spread
      "100% do DI + 1,50%"      -> fração do indexador + spread
      "IPCA + 6,80%"            -> indexador puro + spread
      "PREFIXADO"               -> só o nome, sem taxa publicada
      ""                        -> sem informação

    Devolve (indexador, pct_indexador, spread, rotulo_cru).
    """
    texto = (bruto or "").strip()
    if not texto:
        return "outro", None, None, texto

    # "100% do DI" -> "100% DI": remove a palavra "do" (preposição, não faz
    # parte do nome de nenhum indexador) antes de compactar os espaços.
    sem_preposicao = re.sub(r"\bDO\b", "", texto.upper())
    compacto = re.sub(r"\s+", "", sem_preposicao)

    # "100%DI+1,50%" — fração do indexador + spread.
    m = re.match(r"^([\d.,]+)%([A-Z\-]+)\+([\d.,]+)%$", compacto)
    if m:
        pct_idx, idx, spread = m.group(1), m.group(2), m.group(3)
        if idx in INDEXADORES_CONHECIDOS:
            return _chave(idx), numero(pct_idx), numero(spread), texto
        return "outro", None, None, texto

    # "100%DI" — só a fração do indexador, sem spread.
    m = re.match(r"^([\d.,]+)%([A-Z\-]+)$", compacto)
    if m:
        pct_idx, idx = m.group(1), m.group(2)
        if idx in INDEXADORES_CONHECIDOS:
            return _chave(idx), numero(pct_idx), None, texto
        return "outro", None, None, texto

    # "IPCA+6,80%" — indexador puro + spread.
    m = re.match(r"^([A-Z\-]+)\+([\d.,]+)%$", compacto)
    if m:
        idx, spread = m.group(1), m.group(2)
        if idx in INDEXADORES_CONHECIDOS:
            return _chave(idx), None, numero(spread), texto
        return "outro", None, None, texto

    # Só o nome do indexador, sem taxa (ex: "PREFIXADO").
    if compacto in INDEXADORES_CONHECIDOS:
        return _chave(compacto), None, None, texto

    return "outro", None, None, texto


def classificar_tipo(codigo):
    """CRA tem "CRA" no código (ex: CRA019003V2); CRI não (ex: 25B0013406).

    Padrão observado, não documentado pela ANBIMA — ver docstring do
    módulo."""
    return "CRA" if "CRA" in (codigo or "").upper() else "CRI"


# ---------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------

def baixar():
    """Chama o endpoint de exportação e devolve o texto do CSV (latin-1).

    filtroTermo e filtroData em branco fazem o servidor devolver o
    último dia disponível automaticamente — confirmado rodando contra o
    arquivo real em 23/10/2026. É público e sem sessão: nenhum cookie é
    necessário (testado também sem eles, resultado idêntico).
    """
    resposta = requests.get(
        URL_EXPORT,
        params={"filtroTermo": "", "filtroData": ""},
        headers=CABECALHOS,
        timeout=TEMPO_LIMITE,
    )
    if resposta.status_code != 200:
        raise RuntimeError(f"HTTP {resposta.status_code} ao chamar {URL_EXPORT}")
    resposta.encoding = "latin-1"
    texto = resposta.text
    if not texto or "Código" not in texto.splitlines()[0]:
        raise RuntimeError("corpo vazio ou sem a linha de cabeçalho esperada")
    return texto


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------

def interpretar(texto):
    """Transforma o texto do CSV numa lista de papéis. Devolve (papeis, data_base, ignoradas)."""
    # splitlines() em vez de split("\r\n"): o arquivo da B3 (coletar_cri.py)
    # usa \r\n, mas este aqui veio só com \n — descoberto rodando contra o
    # arquivo real (sem isso, o arquivo inteiro cai como "uma linha só" e
    # o parser não acha nada). splitlines() cobre os dois sem apostar.
    linhas = [l for l in texto.splitlines() if l.strip()]
    if not linhas:
        return [], None, 0

    cabecalho = [c.strip() for c in linhas[0].split(";")]
    if tuple(cabecalho) != COLUNAS_ESPERADAS:
        print("  AVISO: o cabeçalho do arquivo da ANBIMA mudou do que o robô esperava.")
        print(f"    esperado: {COLUNAS_ESPERADAS}")
        print(f"    veio:     {tuple(cabecalho)}")
        print("    seguindo pelo NOME de cada coluna, deve funcionar mesmo assim.")

    def indice(nome, usar_fallback=None):
        if nome in cabecalho:
            return cabecalho.index(nome)
        return usar_fallback

    pos = {
        "data_ref": indice("Data de Referência", 0),
        "risco": indice("Risco de Crédito", 1),
        "emissor": indice("Emissor", 2),
        "serie": indice("Série", 3),
        "emissao": indice("Emissão", 4),
        "codigo": indice("Código", 5),
        "vencimento": indice("Vencimento", 6),
        "indice_correcao": indice("Índice / Correção", 7),
        "taxa_compra": indice("Taxa Compra", 8),
        "taxa_venda": indice("Taxa Venda", 9),
        "taxa_indicativa": indice("Taxa Indicativa", 10),
        "desvio_padrao": indice("Desvio Padrão", 11),
        "pu": indice("PU", 12),
        "pct_par": indice("% PU Par / % VNE", 13),
        "duration": indice("Duration", 14),
        "referencia_ntnb": indice("Referência NTNB", 15),
        "pct_reune": indice("% Reune", 16),
    }

    papeis = []
    ignoradas = 0

    for linha in linhas[1:]:
        campos = linha.split(";")
        if len(campos) <= max(v for v in pos.values() if v is not None):
            ignoradas += 1
            continue

        def campo(chave):
            i = pos[chave]
            return campos[i].strip() if i is not None and i < len(campos) else ""

        codigo = campo("codigo")
        if not codigo:
            ignoradas += 1
            continue

        data_ref = data_iso(campo("data_ref"))
        if not data_ref:
            ignoradas += 1
            continue

        indexador, pct_indexador, spread, rotulo_cru = classificar_indexador(campo("indice_correcao"))

        papeis.append({
            "data_ref": data_ref,
            "tipo": classificar_tipo(codigo),
            "codigo": codigo,
            "risco_credito": campo("risco") or None,
            "emissor": " ".join(campo("emissor").split()) or None,
            "serie": campo("serie") or None,
            "emissao": campo("emissao") or None,
            "vencimento": data_iso(campo("vencimento")),
            "indexador": indexador,
            "pct_indexador": pct_indexador,
            "spread": spread,
            "rotulo_indexador": rotulo_cru,
            "taxa_compra": numero(campo("taxa_compra")),
            "taxa_venda": numero(campo("taxa_venda")),
            "taxa_indicativa": numero(campo("taxa_indicativa")),
            "desvio_padrao": numero(campo("desvio_padrao")),
            "pu": numero(campo("pu")),
            "pct_par": numero(campo("pct_par")),
            "duration": numero(campo("duration")),
            "referencia_ntnb": campo("referencia_ntnb") or None,
            "pct_reune": numero(campo("pct_reune")),
        })

    # O arquivo NÃO traz só o último dia: vem uma série de ~5 dias úteis
    # (confirmado rodando contra o arquivo real — 1671 linhas pra 336
    # papéis distintos, igual ao padrão achado antes na planilha que o
    # usuário exportou do site da ANBIMA). O site quer uma FOTO de hoje,
    # não uma série histórica, então fica só a data mais recente.
    if not papeis:
        return [], None, ignoradas
    data_mais_recente = max(p["data_ref"] for p in papeis)
    papeis_do_dia = [p for p in papeis if p["data_ref"] == data_mais_recente]
    for p in papeis_do_dia:
        del p["data_ref"]
    data_base = datetime.fromisoformat(data_mais_recente).strftime("%d/%m/%Y")

    return papeis_do_dia, data_base, ignoradas


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print("Baixando as taxas de CRI/CRA da ANBIMA...")
    try:
        texto = baixar()
    except Exception as erro:  # noqa: BLE001
        print(f"\nERRO: não consegui baixar o arquivo da ANBIMA: {erro}")
        return 1

    papeis, data_base, ignoradas = interpretar(texto)
    if not papeis:
        print("\nERRO: o arquivo foi baixado mas nenhuma linha foi interpretada.")
        print("Provável mudança de layout — confira o separador e os nomes das colunas.")
        return 1

    total_cri = sum(1 for p in papeis if p["tipo"] == "CRI")
    total_cra = sum(1 for p in papeis if p["tipo"] == "CRA")
    com_taxa = sum(1 for p in papeis if p["taxa_indicativa"] is not None)

    por_indexador = {}
    for p in papeis:
        por_indexador[p["indexador"]] = por_indexador.get(p["indexador"], 0) + 1

    print(f"\n  data-base: {data_base}")
    print(f"  {len(papeis)} papéis lidos ({ignoradas} linhas ignoradas) — {total_cri} CRI, {total_cra} CRA")
    print(f"  com taxa indicativa do dia: {com_taxa}")
    for chave, quantos in sorted(por_indexador.items(), key=lambda x: -x[1]):
        print(f"    {chave:10s} {quantos:5d}")

    papeis.sort(key=lambda p: (p["tipo"], -(p["taxa_indicativa"] or -1), p["codigo"]))

    saida = {
        "_leia_me": (
            "Taxas INDICATIVAS do mercado secundário de CRI e CRA, publicadas pela ANBIMA. "
            "É a referência de onde o papel está sendo negociado entre instituições — NÃO é uma "
            "oferta disponível para compra nem preço de emissão nova. A ANBIMA não precifica todo "
            "papel todo dia: muitos CRIs e CRAs aparecem aqui com os campos de taxa em branco "
            "(não negociaram nesse dia) — isso não significa que o papel deixou de existir. "
            "'indexador'/'spread' vêm do texto de 'rotulo_indexador' (a coluna Índice/Correção do "
            "arquivo da ANBIMA) — é a correção CONTRATADA, não a taxa de mercado de hoje; para isso "
            "use 'taxa_indicativa'. 'tipo' (CRI ou CRA) é inferido pelo padrão do código, a ANBIMA "
            "não publica essa classificação explicitamente."
        ),
        "fonte": "ANBIMA — Taxas de CRI e CRA (mercado secundário)",
        "fonte_url": URL_FONTE,
        "data_base": data_base,
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(papeis),
        "total_cri": total_cri,
        "total_cra": total_cra,
        "com_taxa_indicativa": com_taxa,
        "por_indexador": por_indexador,
        "papeis": papeis,
    }

    os.makedirs(os.path.dirname(CAMINHO_SAIDA), exist_ok=True)
    with open(CAMINHO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    tamanho = os.path.getsize(CAMINHO_SAIDA) / 1024
    print(f"\n  {CAMINHO_SAIDA} gravado ({tamanho:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
