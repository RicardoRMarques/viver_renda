#!/usr/bin/env python3
"""Descartável — confere no site de produção (viverderenda.dev.br) se o
link do código pro histórico de preços da ANBIMA está mesmo aparecendo
na seção CRI, pra quem tem taxa_indicativa. Usuário reportou "não criou
o link dos ativos" com um print da visão padrão (ordenada por Emissão),
onde nenhuma das 22 linhas visíveis tem taxa — o que é esperado (só 109
dos 4443 CRIs têm). Este teste ordena por "Taxa hoje" pra trazer os que
têm taxa pro topo e confirma se o link aparece ali."""
from playwright.sync_api import sync_playwright

URL = "https://viverderenda.dev.br/#cri"


def main():
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        pagina = navegador.new_page(viewport={"width": 1280, "height": 900})
        pagina.goto(URL, wait_until="networkidle", timeout=45_000)
        pagina.wait_for_timeout(2000)

        print("=== Visão padrão (como o usuário viu) ===")
        linhas = pagina.locator("#criConteudo table tbody tr").count()
        links = pagina.locator("#criConteudo a.deb-link-codigo").count()
        print(f"  linhas: {linhas}, com link: {links}")

        print("\n=== Ordenando por 'Taxa hoje (ANBIMA)' ===")
        th = pagina.locator('#criConteudo th[data-coluna="taxa_indicativa"]')
        print(f"  cabeçalho encontrado: {th.count() > 0}")
        if th.count():
            th.click()
            pagina.wait_for_timeout(800)
            linhas2 = pagina.locator("#criConteudo table tbody tr").count()
            links2 = pagina.locator("#criConteudo a.deb-link-codigo").count()
            print(f"  linhas: {linhas2}, com link: {links2}")
            if links2:
                primeiro = pagina.locator("#criConteudo a.deb-link-codigo").first
                print(f"  primeiro link: texto={primeiro.inner_text()!r} href={primeiro.get_attribute('href')!r}")

        print("\n=== Resumo 'Com taxa de mercado hoje' ===")
        resumo_texto = pagina.inner_text("#criResumo")
        print(" ", resumo_texto.replace("\n", " | ")[:300])

        navegador.close()


if __name__ == "__main__":
    main()
