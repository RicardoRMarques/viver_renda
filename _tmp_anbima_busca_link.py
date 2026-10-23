#!/usr/bin/env python3
"""Descartável — follow-up: o parâmetro ?q=<codigo> pareceu filtrar de
verdade (92 -> 5 linhas) no teste anterior. Confirma se é filtro real
(checando o TEXTO das linhas, não só "aparece em algum lugar da página")
e se fica estável numa carga fresca, sem navegador aberto antes."""
from playwright.sync_api import sync_playwright

URL_BASE = "https://data.anbima.com.br/busca/certificado-de-recebiveis?view=precos"
CODIGO_TESTE = "CRA025002S1"
CODIGO_INEXISTENTE = "ZZZZZINVALIDO999"


def checar(pagina, url, rotulo):
    print(f"\n=== {rotulo} ===")
    print(f"  {url}")
    pagina.goto(url, wait_until="networkidle", timeout=45_000)
    pagina.wait_for_timeout(2500)

    linhas_loc = pagina.locator("table tbody tr")
    n = linhas_loc.count()
    print(f"  linhas na tabela: {n}")
    for i in range(min(n, 8)):
        try:
            texto = linhas_loc.nth(i).inner_text().replace("\n", " | ")[:150]
        except Exception:
            texto = "?"
        print(f"    linha {i}: {texto}")


def main():
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        pagina = navegador.new_page()

        checar(pagina, URL_BASE, "baseline sem filtro")
        checar(pagina, f"{URL_BASE}&q={CODIGO_TESTE}", f"com q={CODIGO_TESTE}")
        checar(pagina, f"{URL_BASE}&q={CODIGO_INEXISTENTE}", f"com q={CODIGO_INEXISTENTE} (nao deveria achar nada)")

        # segunda rodada numa pagina NOVA (sem historico de navegacao) pra
        # garantir que nao e so cache/estado residual
        pagina2 = navegador.new_page()
        checar(pagina2, f"{URL_BASE}&q={CODIGO_TESTE}", f"pagina nova, direto com q={CODIGO_TESTE}")

        navegador.close()


if __name__ == "__main__":
    main()
