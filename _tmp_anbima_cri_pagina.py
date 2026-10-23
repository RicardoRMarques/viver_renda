#!/usr/bin/env python3
"""Descartável — investiga a página data.anbima.com.br/busca/certificado-de-recebiveis?view=precos
pra ver se tem botão de exportar e, se tiver, qual chamada de rede ele dispara
(nos moldes do que funcionou pra B3/CRI em coletar_cri.py)."""
import json
import re

from playwright.sync_api import sync_playwright

URL = "https://data.anbima.com.br/busca/certificado-de-recebiveis?view=precos"


def main():
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        pagina = navegador.new_page()

        chamadas = []

        def registrar(resposta):
            try:
                ct = resposta.headers.get("content-type", "")
            except Exception:
                ct = ""
            url = resposta.url
            if any(x in url.lower() for x in ("csv", "export", "download", "xls", "api", "precos", "certificado")):
                chamadas.append((resposta.request.method, url, resposta.status, ct))

        pagina.on("response", registrar)

        print(f"Abrindo {URL} ...")
        try:
            pagina.goto(URL, timeout=45_000, wait_until="networkidle")
        except Exception as e:
            print(f"goto deu timeout/erro (pode ser SPA pesada): {e}")

        pagina.wait_for_timeout(5000)

        print("\n=== TÍTULO DA PÁGINA ===")
        print(pagina.title())

        print("\n=== URL FINAL (pode ter mudado por redirect/login) ===")
        print(pagina.url)

        print("\n=== PROCURANDO BOTÕES/LINKS DE EXPORTAR/DOWNLOAD/CSV/EXCEL ===")
        candidatos = pagina.locator(
            "button, a, [role=button]"
        )
        total = candidatos.count()
        print(f"total de elementos button/a/[role=button] na página: {total}")
        achados = []
        for i in range(min(total, 300)):
            el = candidatos.nth(i)
            try:
                texto = (el.inner_text(timeout=1000) or "").strip()
            except Exception:
                texto = ""
            if re.search(r"export|download|csv|excel|xls|baixar", texto, re.I):
                achados.append(texto)
        print(f"candidatos com texto relevante: {achados[:30]}")

        print("\n=== CHAMADAS DE REDE RELEVANTES CAPTURADAS (passivamente, sem clicar em nada) ===")
        for metodo, url, status, ct in chamadas[:60]:
            print(f"{metodo} {status} {ct}  {url}")

        print(f"\ntotal de chamadas relevantes: {len(chamadas)}")

        # Se achou algum botão candidato, tenta clicar no primeiro e ver o que dispara.
        if achados:
            print("\n=== TENTANDO CLICAR NO PRIMEIRO CANDIDATO ===")
            chamadas.clear()
            try:
                alvo = pagina.get_by_text(achados[0], exact=False).first
                with pagina.expect_response(lambda r: True, timeout=15_000) as info:
                    alvo.click(timeout=10_000)
                print("clique ok, resposta capturada:")
                r = info.value
                print(f"{r.request.method} {r.status} {r.url}")
            except Exception as e:
                print(f"clique falhou ou não disparou resposta nova: {e}")
            pagina.wait_for_timeout(3000)
            print("chamadas relevantes pós-clique:")
            for metodo, url, status, ct in chamadas[:30]:
                print(f"{metodo} {status} {ct}  {url}")

        navegador.close()


if __name__ == "__main__":
    main()
