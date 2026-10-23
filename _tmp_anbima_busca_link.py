#!/usr/bin/env python3
"""Descartável — investiga se a página de busca de CRI/CRA da ANBIMA
(data.anbima.com.br/busca/certificado-de-recebiveis) aceita um parâmetro
de URL pra filtrar direto por código ou emissor, de um jeito que a gente
possa linkar por papel/emissor sem precisar de JS/interação do usuário."""
from playwright.sync_api import sync_playwright

URL_BASE = "https://data.anbima.com.br/busca/certificado-de-recebiveis?view=precos"

# Códigos/emissores reais que sabemos existir no arquivo de hoje
# (coletar_cri_cra_anbima.py -> data/cri_cra_precos.json).
CODIGO_TESTE = "CRA025002S1"
EMISSOR_TESTE = "OPEA"


def inspecionar_busca(pagina):
    pagina.goto(URL_BASE, wait_until="networkidle", timeout=45_000)
    pagina.wait_for_timeout(2000)

    campo = pagina.locator('input[type="search"], input[type="text"], input[placeholder*="usca" i]').first
    print(f"campo de busca encontrado: {campo.count() > 0}")
    if campo.count() == 0:
        print("nao achei campo de busca -- abortando")
        return

    campo.click()
    campo.fill(CODIGO_TESTE)
    pagina.wait_for_timeout(500)
    # tenta Enter, que é o jeito mais comum de disparar a busca
    campo.press("Enter")
    pagina.wait_for_timeout(2500)

    print(f"\nURL apos digitar codigo '{CODIGO_TESTE}' + Enter:")
    print(f"  {pagina.url}")

    corpo = pagina.inner_text("body")
    achou = CODIGO_TESTE in corpo
    print(f"  o codigo aparece na pagina depois da busca: {achou}")

    # conta quantas linhas de resultado existem agora (tentativa generica)
    linhas = pagina.locator("table tbody tr, [role=row]").count()
    print(f"  linhas/resultados visiveis: {linhas}")


def testar_url_direta(pagina, url, rotulo):
    print(f"\n=== Testando URL direta ({rotulo}) ===")
    print(f"  {url}")
    pagina.goto(url, wait_until="networkidle", timeout=45_000)
    pagina.wait_for_timeout(2500)
    corpo = pagina.inner_text("body")
    achou = CODIGO_TESTE in corpo or EMISSOR_TESTE.upper() in corpo.upper()
    print(f"  URL final apos load: {pagina.url}")
    print(f"  conteudo relevante aparece: {achou}")
    linhas = pagina.locator("table tbody tr, [role=row]").count()
    print(f"  linhas/resultados visiveis: {linhas}")


def main():
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        pagina = navegador.new_page()

        print("=== 1) Interagindo com o campo de busca na pagina ===")
        inspecionar_busca(pagina)
        url_apos_busca = pagina.url

        print("\n=== 2) Testando se essa MESMA url funciona numa carga fresca (sem interacao) ===")
        pagina2 = navegador.new_page()
        if "?" in url_apos_busca and url_apos_busca != URL_BASE:
            testar_url_direta(pagina2, url_apos_busca, "URL capturada apos a busca")
        else:
            print("  a URL nao mudou com a busca -- provavelmente eh so estado em memoria (React), sem refletir na URL")

        print("\n=== 3) Tentando padroes de query string plausiveis, direto, sem interacao ===")
        padroes = [
            f"{URL_BASE}&busca={CODIGO_TESTE}",
            f"{URL_BASE}&search={CODIGO_TESTE}",
            f"{URL_BASE}&query={CODIGO_TESTE}",
            f"{URL_BASE}&q={CODIGO_TESTE}",
            f"{URL_BASE}&termo={CODIGO_TESTE}",
            f"{URL_BASE}&codigo={CODIGO_TESTE}",
            f"{URL_BASE}&filtroTermo={CODIGO_TESTE}",
        ]
        for url in padroes:
            testar_url_direta(pagina2, url, url.split("&")[-1])

        navegador.close()


if __name__ == "__main__":
    main()
