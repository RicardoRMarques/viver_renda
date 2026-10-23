#!/usr/bin/env python3
"""Descartável — investiga a página "clássica" da ANBIMA de Taxas de CRI e
CRA (não é a SPA em data.anbima.com.br, é a página antiga do site
institucional) pra ver o formulário de busca e o botão de exportar CSV,
e capturar a requisição/resposta reais do export (headers, cookies, corpo)."""
from playwright.sync_api import sync_playwright

URL = "https://www.anbima.com.br/pt_br/informar/precos-e-indices/precos/taxas-de-cri-e-cra/taxas-de-cri-e-cra.htm"


def main():
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        contexto = navegador.new_context()
        pagina = contexto.new_page()

        print(f"Abrindo {URL} ...")
        try:
            pagina.goto(URL, timeout=45_000, wait_until="networkidle")
        except Exception as e:
            print(f"goto deu timeout/erro: {e}")
        pagina.wait_for_timeout(3000)

        print("\n=== TÍTULO ===")
        print(pagina.title())
        print("\n=== URL FINAL ===")
        print(pagina.url)

        print("\n=== BOTÕES/LINKS/INPUTS DA PÁGINA (texto + atributos relevantes) ===")
        els = pagina.locator("button, a, input, select")
        total = els.count()
        print(f"total: {total}")
        for i in range(min(total, 150)):
            el = els.nth(i)
            try:
                tag = el.evaluate("e => e.tagName")
                texto = (el.inner_text(timeout=500) or "").strip()
            except Exception:
                tag, texto = "?", ""
            try:
                attrs = el.evaluate(
                    "e => ({id:e.id, name:e.name, type:e.type, value:e.value, href:e.href})"
                )
            except Exception:
                attrs = {}
            if texto or attrs.get("href") or attrs.get("id") or attrs.get("name"):
                print(f"[{i}] <{tag}> texto={texto!r} attrs={attrs}")

        candidatos = pagina.get_by_text("CSV", exact=False)
        n_csv = candidatos.count()
        print(f"\nelementos com texto 'CSV': {n_csv}")

        if n_csv > 0:
            print("\n=== CLICANDO NO PRIMEIRO BOTÃO/LINK COM 'CSV' ===")
            try:
                with pagina.expect_response(
                    lambda r: "download" in r.url.lower() or "export" in r.url.lower() or "csv" in r.url.lower(),
                    timeout=20_000,
                ) as info:
                    candidatos.first.click(timeout=10_000)
                resp = info.value
                req = resp.request
                print(f"\n{req.method} {req.url}")
                print("headers da requisição:")
                for k, v in req.headers.items():
                    print(f"  {k}: {v}")
                print(f"\nstatus={resp.status}")
                for k, v in resp.headers.items():
                    print(f"  {k}: {v}")
                corpo = resp.body()
                print(f"\ntamanho do corpo: {len(corpo)} bytes")
                texto_corpo = corpo.decode("latin-1", errors="replace")
                print("primeiros 1000 chars:")
                print(texto_corpo[:1000])
            except Exception as e:
                print(f"falhou: {e}")

        print("\n=== COOKIES NO CONTEXTO ===")
        for c in contexto.cookies():
            print(f"  {c['name']}={c['value'][:40]}...  domain={c['domain']}")

        navegador.close()


if __name__ == "__main__":
    main()
