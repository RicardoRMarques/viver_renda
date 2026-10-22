#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletar_cri.py
---------------------------------------------------------------------
Baixa o cadastro de CRIs (Certificados de Recebíveis Imobiliários) da
Consulta Pública da B3 e grava em data/cri.json.

O que este arquivo É e o que ele NÃO é
----------------------------------------
É o cadastro de EMISSÃO de cada série de CRI negociada na B3: emissor,
remuneração CONTRATADA no lançamento do papel (ex: "IPCA + 6,53%"),
datas de emissão/vencimento, volume emitido, ISIN. NÃO é uma taxa de
mercado secundário atualizada todo dia, como a tela de Debêntures
mostra — a B3 não publica isso para CRI num arquivo público. Para um
papel pós-fixado (DI+x%, IPCA+x%, IGP-M+x%) a remuneração contratada
ainda descreve o que o título paga hoje, porque o "+x%" é fixo e só o
indexador-base flutua; para PREFIXADO não há garantia de que a taxa
original ainda reflita o que o mercado pagaria por ele agora.

Por que precisa de um navegador (Playwright), e não só requests
-----------------------------------------------------------------
A página da Consulta Pública (sistemaswebb3-balcao.b3.com.br) está
atrás de um desafio do Cloudflare: uma chamada HTTP comum ao endpoint
que alimenta o botão "Exportar CSV" devolve 200 com corpo VAZIO, sem
nenhum erro explícito. Só funciona abrindo a página com um navegador
de verdade primeiro (o desafio roda em JavaScript) e então reusando os
cookies daquela sessão para chamar o mesmo endpoint que o botão usa.
Testado e confirmado em produção em 22/10/2026 — ver investigação na
sessão que criou este robô.

Formato do arquivo (descoberto testando, não documentado)
------------------------------------------------------------
Endpoint: https://sistemaswebb3-balcao.b3.com.br/featuresCRIProxy/CriCall/GetDownload
Resposta: texto em BASE64 que, decodificado (latin-1), é um CSV com:

  separador:    ; (ponto e vírgula), linha termina com \\r\\n
  decimais:     vírgula
  datas:        dd/mm/aaaa

  0  Código                        6  Volume Emissão
  1  Emissor                       7  Remuneração
  2  Agente Fiduciário             8  Data de Emissão
  3  N° da Emissão                 9  Data de Vencimento
  4  N° da série                  10  Data Final de Distribuição
  5  Qtd Emitida                  11  ISIN

O arquivo é o cadastro HISTÓRICO inteiro (achamos papéis emitidos desde
2007) — a maior parte já venceu. Este robô filtra pra só gravar quem
ainda não venceu: um CRI vencido não é coisa que dá para comprar hoje.

A coluna "Remuneração" vem em formatos diferentes por indexador:
  "IPCA+6,53060%"            -> indexador puro + spread
  "100,00%  DI+3,50000%"     -> X% do indexador (DI/SELIC) + spread
  "PREFIXADO"                -> só o nome, sem taxa publicada no arquivo
  ""                         -> sem informação (~440 papéis no arquivo;
                                 entram como indexador "outro", sem rótulo)

Saída: data/cri.json
---------------------------------------------------------------------
"""

import base64
import json
import os
import re
import sys
from datetime import date, datetime, timezone

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

URL_PAGINA = "https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/"
URL_DOWNLOAD = "https://sistemaswebb3-balcao.b3.com.br/featuresCRIProxy/CriCall/GetDownload"
URL_FONTE = "https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/"

CAMINHO_SAIDA = os.path.join("data", "cri.json")

TEMPO_LIMITE_PAGINA = 45_000  # ms

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Cabeçalho esperado do CSV, na ordem — serve de DETECTOR DE MUDANÇA: se a
# B3 reordenar ou renomear colunas, o robô avisa no log em vez de gravar
# dado errado calado (mesma ideia do robô de Debêntures com o .xls).
CABECALHO_ESPERADO = (
    "Código", "Emissor", "Agente Fiduciário", "N° da Emissão", "N° da série",
    "Qtd Emitida", "Volume Emissão", "Remuneração", "Data de Emissão",
    "Data de Vencimento", "Data Final de Distribuição", "ISIN",
)

# Os indexadores que a própria B3 lista (endpoint GetIndexers, conferido
# em 22/10/2026). IGP-DI nunca apareceu numa amostra real, mas está na
# lista oficial — fica coberto por precaução.
INDEXADORES_CONHECIDOS = {
    "DI", "IGP-DI", "IGP-M", "INCC-DI", "INCC-M", "INPC", "IPCA",
    "IPC-FIPE", "PREFIXADO", "SELIC", "TR",
}


# ---------------------------------------------------------------------
# Conversores
# ---------------------------------------------------------------------

def numero(bruto):
    """"300.000,00" -> 300000.0. Vazio ou não numérico -> None."""
    texto = (bruto or "").strip()
    if not texto:
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


def classificar_remuneracao(bruto):
    """Interpreta o campo "Remuneração" cru do arquivo da B3.

    Devolve (indexador, pct_indexador, spread, rotulo_cru):
      indexador      chave normalizada ("ipca", "igp_m", "outro"...)
      pct_indexador  o "X%" antes do indexador, quando o papel paga uma
                     FRAÇÃO dele (ex: 100,00 em "100,00% DI+3,5%") — a
                     grande maioria é 100%, mas não vale assumir.
      spread         o "+Y%" depois do indexador. None quando o papel
                     só tem o nome do indexador, sem taxa publicada
                     (todo caso de PREFIXADO visto no arquivo).
      rotulo_cru     o texto original, para mostrar quando não dá para
                     classificar.
    """
    texto = (bruto or "").strip()
    if not texto:
        return "outro", None, None, texto

    compacto = re.sub(r"\s+", "", texto.upper())

    # "100,00%DI+3,50000%" — fração do indexador + spread.
    m = re.match(r"^([\d.,]+)%([A-Z\-]+)\+([\d.,]+)%$", compacto)
    if m:
        pct_idx, idx, spread = m.group(1), m.group(2), m.group(3)
        if idx in INDEXADORES_CONHECIDOS:
            return _chave(idx), numero(pct_idx), numero(spread), texto
        return "outro", None, None, texto

    # "IPCA+6,53060%" — indexador puro + spread.
    m = re.match(r"^([A-Z\-]+)\+([\d.,]+)%$", compacto)
    if m:
        idx, spread = m.group(1), m.group(2)
        if idx in INDEXADORES_CONHECIDOS:
            return _chave(idx), None, numero(spread), texto
        return "outro", None, None, texto

    # Só o nome do indexador, sem taxa (todo PREFIXADO visto no arquivo).
    if compacto in INDEXADORES_CONHECIDOS:
        return _chave(compacto), None, None, texto

    return "outro", None, None, texto


def _chave(indexador_b3):
    """"IGP-M" -> "igp_m". Vira a chave usada em todo o resto do robô."""
    return indexador_b3.lower().replace("-", "_")


def rotulo_remuneracao(indexador, pct_indexador, spread, rotulo_cru):
    """Monta um texto legível para a remuneração, para a tela usar direto."""
    if indexador == "outro":
        return rotulo_cru or "—"
    nome = indexador.upper().replace("_", "-")
    prefixo = f"{pct_indexador:g}% do {nome}".replace(".", ",") if pct_indexador is not None else nome
    if spread is None:
        return prefixo if pct_indexador is not None else nome
    return f"{prefixo} + {spread:.2f}%".replace(".", ",")


# ---------------------------------------------------------------------
# Download (via navegador — ver docstring do módulo)
# ---------------------------------------------------------------------

def baixar_csv_base64():
    """Abre a Consulta Pública de CRIs e clica em "Exportar CSV" de
    verdade, capturando a resposta do GetDownload que o clique dispara.

    Por que precisa ser um clique de verdade, e não um fetch programado
    ------------------------------------------------------------------
    Testamos três jeitos de chamar o GetDownload: context.request.get()
    do Playwright, fetch() disparado via page.evaluate(), e clicar no
    botão "Exportar CSV" de verdade. Só o clique passa pelo desafio do
    Cloudflare — os outros dois devolvem 200 com corpo vazio, mesmo
    reaproveitando os cookies certos da mesma página. O desafio parece
    validar algo ligado à interação real (clique com user gesture), não
    só a sessão/cookies."""
    with sync_playwright() as navegador_ctx:
        navegador = navegador_ctx.chromium.launch()
        try:
            pagina = navegador.new_page(user_agent=USER_AGENT)
            pagina.goto(URL_PAGINA, wait_until="networkidle", timeout=TEMPO_LIMITE_PAGINA)
            pagina.wait_for_timeout(1500)

            botao = pagina.locator('button:has-text("Exportar CSV")').first
            with pagina.expect_response(
                lambda r: "CriCall/GetDownload" in r.url, timeout=TEMPO_LIMITE_PAGINA
            ) as resposta_info:
                botao.click(timeout=10_000)
            resposta = resposta_info.value

            if resposta.status != 200:
                raise RuntimeError(f"HTTP {resposta.status} ao chamar {URL_DOWNLOAD}")

            texto_base64 = resposta.text()
            if not texto_base64:
                raise RuntimeError(
                    "corpo vazio — provável bloqueio do desafio do Cloudflare "
                    "(a chamada precisa vir logo depois de carregar a página)"
                )
            return texto_base64
        finally:
            navegador.close()


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------

def interpretar(texto_base64):
    """Decodifica o CSV e devolve (cris_ativos, total_no_arquivo, ignoradas)."""
    bruto = base64.b64decode(texto_base64).decode("latin-1")
    linhas = [l for l in bruto.split("\r\n") if l]
    if not linhas:
        return [], 0, 0

    # A linha de cabeçalho termina com ";" (campo vazio no fim) — remove.
    cabecalho = linhas[0].split(";")
    if cabecalho and cabecalho[-1] == "":
        cabecalho = cabecalho[:-1]

    if tuple(cabecalho) != CABECALHO_ESPERADO:
        print("  AVISO: o cabeçalho do CSV da B3 mudou do que o robô esperava.")
        print(f"    esperado: {CABECALHO_ESPERADO}")
        print(f"    veio:     {tuple(cabecalho)}")
        print("    seguindo com as posições fixas de sempre, pode sair errado.")

    hoje = date.today()
    total_no_arquivo = 0
    cris = []
    ignoradas = 0

    for linha in linhas[1:]:
        campos = linha.split(";")
        if len(campos) < len(CABECALHO_ESPERADO):
            ignoradas += 1
            continue
        total_no_arquivo += 1

        vencimento = data_iso(campos[9])
        if not vencimento:
            ignoradas += 1
            continue
        if date.fromisoformat(vencimento) <= hoje:
            continue  # já venceu — não é coisa que dá pra comprar hoje

        indexador, pct_indexador, spread, rotulo_cru = classificar_remuneracao(campos[7])

        cris.append({
            "codigo": campos[0].strip(),
            "isin": campos[11].strip(),
            "emissor": " ".join(campos[1].split()),
            "agente_fiduciario": " ".join(campos[2].split()),
            "numero_emissao": campos[3].strip() or None,
            "numero_serie": campos[4].strip() or None,
            "quantidade_emitida": numero(campos[5]),
            "volume_emissao": numero(campos[6]),
            "indexador": indexador,
            "pct_indexador": pct_indexador,
            "spread": spread,
            "rotulo_remuneracao": rotulo_remuneracao(indexador, pct_indexador, spread, rotulo_cru),
            "data_emissao": data_iso(campos[8]),
            "data_vencimento": vencimento,
            "data_final_distribuicao": data_iso(campos[10]),
        })

    return cris, total_no_arquivo, ignoradas


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print("Abrindo a Consulta Pública de CRIs da B3 (navegador headless)...")
    try:
        texto_base64 = baixar_csv_base64()
    except Exception as erro:  # noqa: BLE001 — qualquer falha aqui vira log, não traceback cru
        print(f"\nERRO: não consegui baixar o arquivo da B3: {erro}")
        return 1

    cris, total_no_arquivo, ignoradas = interpretar(texto_base64)
    if not cris:
        print("\nERRO: o arquivo foi baixado mas nenhum CRI ativo foi interpretado.")
        print("Provável mudança de layout — confira o cabeçalho e o separador.")
        return 1

    por_indexador = {}
    for c in cris:
        por_indexador[c["indexador"]] = por_indexador.get(c["indexador"], 0) + 1
    volume_total = sum(c["volume_emissao"] for c in cris if c["volume_emissao"])

    print(f"\n  {total_no_arquivo} séries no arquivo da B3 ({ignoradas} linhas ignoradas por formato)")
    print(f"  {len(cris)} ainda não venceram — são essas que vão para o site")
    for chave, quantos in sorted(por_indexador.items(), key=lambda x: -x[1]):
        print(f"    {chave:10s} {quantos:5d}")

    # Mais recente primeiro deixa o arquivo legível para quem abrir no olho;
    # a tela reordena como quiser.
    cris.sort(key=lambda c: c["data_emissao"] or "", reverse=True)

    saida = {
        "_leia_me": (
            "Cadastro de EMISSÃO de CRIs (Certificados de Recebíveis Imobiliários) negociados na B3, "
            "só os que ainda não venceram. 'rotulo_remuneracao' é a remuneração CONTRATADA no lançamento "
            "do papel — a B3 não publica uma taxa de mercado secundário atualizada todo dia para CRI (diferente "
            "da tela de Debêntures, que usa a ANBIMA). Para papel pós-fixado (DI/IPCA/IGP-M + spread) isso "
            "ainda descreve o que o título paga hoje, porque só o indexador-base flutua; para PREFIXADO não há "
            "garantia nenhuma de que siga valendo. CRI é isento de Imposto de Renda para pessoa física por lei "
            "(diferente de debênture incentivada, que depende de cada papel) — mas não tem FGC, e a maioria "
            "exige um investimento mínimo alto (na casa de R$ 10 a 20 mil), que este arquivo não informa."
        ),
        "fonte": "B3 — Consulta Pública de CRIs",
        "fonte_url": URL_FONTE,
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(cris),
        "total_no_arquivo_b3": total_no_arquivo,
        "por_indexador": por_indexador,
        "volume_total_emissao": volume_total,
        "cris": cris,
    }

    os.makedirs(os.path.dirname(CAMINHO_SAIDA), exist_ok=True)
    with open(CAMINHO_SAIDA, "w", encoding="utf-8") as arquivo:
        json.dump(saida, arquivo, ensure_ascii=False, indent=1)

    tamanho = os.path.getsize(CAMINHO_SAIDA) / 1024
    print(f"\n  {CAMINHO_SAIDA} gravado ({tamanho:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
