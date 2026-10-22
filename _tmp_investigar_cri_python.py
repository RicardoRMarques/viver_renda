#!/usr/bin/env python3
"""Descartável — confirma que o GetDownload funciona via Playwright Python
(context.request, reaproveitando os cookies da sessão que passou pelo
desafio do Cloudflare) e levanta a diversidade real do campo "Remuneração"
pra desenhar o parser de indexador do robô de verdade."""
import base64
import re
import sys
from collections import Counter
from datetime import datetime

from playwright.sync_api import sync_playwright

URL_PAGINA = "https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/"
URL_DOWNLOAD = "https://sistemaswebb3-balcao.b3.com.br/featuresCRIProxy/CriCall/GetDownload"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL_PAGINA, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1500)

        resp = page.context.request.get(URL_DOWNLOAD)
        print("status:", resp.status)
        base64_texto = resp.text()
        print("tamanho base64:", len(base64_texto))

        decodificado = base64.b64decode(base64_texto).decode("latin1")
        linhas = [l for l in decodificado.split("\r\n") if l]
        print("total de linhas (com cabeçalho):", len(linhas))
        print("cabeçalho:", linhas[0])

        campos_cab = linhas[0].split(";")
        idx_remuneracao = campos_cab.index("Remuneração")
        idx_vencimento = campos_cab.index("Data de Vencimento")

        amostras = {}
        hoje = datetime.now()
        ativos = 0
        for linha in linhas[1:]:
            partes = linha.split(";")
            if len(partes) <= max(idx_remuneracao, idx_vencimento):
                continue
            rem = partes[idx_remuneracao]
            venc = partes[idx_vencimento]
            try:
                d, m, a = map(int, venc.split("/"))
                if datetime(a, m, d) > hoje:
                    ativos += 1
            except ValueError:
                pass

            chave = None
            for marcador in ("IPCA", "IGP-M", "IGPM", "INCC-DI", "INCC-M", "INPC", "IPC-FIPE", "PREFIXADO", "SELIC", "TR", "DI"):
                if marcador in rem.upper():
                    chave = marcador
                    break
            if chave and chave not in amostras:
                amostras[chave] = []
            if chave and len(amostras[chave]) < 4:
                amostras[chave].append(rem)

        print(f"\nCRIs com vencimento no futuro: {ativos} de {len(linhas) - 1}")
        print("\nAmostras de 'Remuneração' por indexador encontrado:")
        for chave, exemplos in amostras.items():
            print(f"  {chave}:")
            for ex in exemplos:
                print(f"    {ex!r}")

        # Quantas linhas NÃO bateram em nenhum marcador conhecido?
        sem_marcador = 0
        exemplos_sem = []
        for linha in linhas[1:]:
            partes = linha.split(";")
            if len(partes) <= idx_remuneracao:
                continue
            rem = partes[idx_remuneracao].upper()
            if not any(m in rem for m in ("IPCA", "IGP-M", "IGPM", "INCC", "INPC", "IPC-FIPE", "PREFIXADO", "SELIC", "TR", "DI")):
                sem_marcador += 1
                if len(exemplos_sem) < 5:
                    exemplos_sem.append(partes[idx_remuneracao])
        print(f"\nLinhas sem nenhum marcador de indexador reconhecido: {sem_marcador}")
        for ex in exemplos_sem:
            print(f"  {ex!r}")

        browser.close()


if __name__ == "__main__":
    main()
