#!/usr/bin/env python3
"""
Fase 2 da investigação da interface web do IF.data (www3.bcb.gov.br/ifdata).

O QUE JÁ SABEMOS (fase 1, execução real de 09/09/2026)
------------------------------------------------------
    GET https://www3.bcb.gov.br/ifdata/rest/relatorios   -> 200

devolve um CATÁLOGO de arquivos JSON estáticos, um bloco por data-base:

    [{"dt":200003,"files":[{"f":"200003/cadastro200003_1005.json"},
                           {"f":"200003/dados200003_1.json"},
                           {"f":"200003/filtro200003.json"},
                           {"f":"200003/info200003.json"},
                           {"f":"200003/sel200003.json","sel":[{"id":1005,
                              "n":"Conglomerados ..."}]}]}, ...]

Isso muda o jogo: em vez da API OData do Olinda — que monta a resposta na
hora e devolveu HTTP 500 em seis de oito execuções — aqui são ARQUIVOS
PRONTOS. Servidor de arquivo estático não engasga como gerador de
consulta, e o HTML da página ainda citava /ifdata/rest/arquivos e
/ifdata/rest/relatorios2025a2030.

O QUE FALTA DESCOBRIR (é o que este script faz)
------------------------------------------------
  1. Qual a data-base mais recente publicada.
  2. Por qual caminho se baixa um arquivo listado em "files".
  3. O que tem dentro de cada tipo (info, sel, filtro, cadastro, dados):
     onde está a razão social, onde está o Índice de Basileia, e como os
     dois se ligam.

Não grava nada. Só olha e imprime.

    python3 descobrir_ifdata_web.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("IFDATA_BASE", "https://www3.bcb.gov.br/ifdata/")
TEMPO_LIMITE = 60
CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": BASE,
}


def log(m):
    print(m, flush=True)


def baixar(url):
    """(status, texto). Erro vira resultado, não exceção."""
    req = urllib.request.Request(url, headers=CABECALHOS)
    try:
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def baixar_json(url):
    status, texto = baixar(url)
    if status != 200:
        return status, None
    try:
        return status, json.loads(texto)
    except Exception:  # noqa: BLE001
        return status, None


def resumir(valor, profundidade=0, max_itens=2):
    """Descreve a forma de um JSON sem despejar megabytes no log."""
    espaco = "  " * (profundidade + 1)
    if isinstance(valor, list):
        log(f"{espaco}lista com {len(valor)} itens")
        for item in valor[:max_itens]:
            texto = json.dumps(item, ensure_ascii=False)
            log(f"{espaco}  {texto[:300]}")
        return
    if isinstance(valor, dict):
        log(f"{espaco}objeto com as chaves: {list(valor.keys())[:20]}")
        for chave, dentro in list(valor.items())[:4]:
            if isinstance(dentro, (list, dict)):
                log(f"{espaco}  '{chave}':")
                resumir(dentro, profundidade + 2, max_itens)
            else:
                log(f"{espaco}  '{chave}': {str(dentro)[:120]}")
        return
    log(f"{espaco}{str(valor)[:200]}")


def main():
    log("=" * 70)
    log("1) CATÁLOGO — quais data-bases existem e que arquivos cada uma tem")
    log("=" * 70)

    catalogo = None
    for caminho in ("rest/relatorios2025a2030", "rest/relatorios"):
        url = BASE + caminho
        status, dados = baixar_json(url)
        log(f"  [{status}] {url}")
        if isinstance(dados, list) and dados:
            catalogo = dados
            log(f"        {len(dados)} data-bases")
            break

    if not catalogo:
        log("\n  Não consegui ler o catálogo. Fim.")
        return 1

    # a mais recente é a que interessa
    def numero_dt(bloco):
        try:
            return int(bloco.get("dt", 0))
        except Exception:  # noqa: BLE001
            return 0

    catalogo.sort(key=numero_dt, reverse=True)
    log(f"\n  Data-bases mais recentes: {[b.get('dt') for b in catalogo[:8]]}")

    bloco = catalogo[0]
    dt = bloco.get("dt")
    log(f"\n  MAIS RECENTE: {dt}")
    log(f"  bloco completo:")
    log(f"    {json.dumps(bloco, ensure_ascii=False)[:1500]}")

    arquivos = [f.get("f") for f in bloco.get("files", []) if f.get("f")]
    log(f"\n  {len(arquivos)} arquivos nessa data-base:")
    for a in arquivos:
        log(f"    {a}")

    log("\n" + "=" * 70)
    log("2) POR QUAL CAMINHO SE BAIXA UM DESSES ARQUIVOS")
    log("=" * 70)

    # 'info' costuma ser o menor: bom pra testar sem puxar megabytes
    alvo = next((a for a in arquivos if "info" in a), arquivos[0] if arquivos else None)
    if not alvo:
        log("  Nenhum arquivo listado. Fim.")
        return 1

    prefixos = ["rest/arquivos/", "rest/", "", "arquivos/", "rest/arquivo/",
                "rest/relatorios/"]
    prefixo_bom = None
    for prefixo in prefixos:
        url = BASE + prefixo + alvo
        status, dados = baixar_json(url)
        log(f"  [{status}] {url}")
        if dados is not None:
            prefixo_bom = prefixo
            log(f"        -> FUNCIONOU")
            break

    if prefixo_bom is None:
        log("\n  Nenhum prefixo serviu. Os arquivos devem estar atrás de outra rota.")
        return 1

    log("\n" + "=" * 70)
    log(f"3) O QUE TEM DENTRO (prefixo '{prefixo_bom}')")
    log("=" * 70)

    for arquivo in arquivos:
        url = BASE + prefixo_bom + arquivo
        status, dados = baixar_json(url)
        nome = arquivo.split("/")[-1]
        if dados is None:
            log(f"\n  --- {nome} --- [{status}] não veio como JSON")
            continue
        log(f"\n  --- {nome} --- [{status}]")
        resumir(dados)

    log("\n" + "=" * 70)
    log("COMO LER: preciso identificar, entre os arquivos acima, (a) qual traz")
    log("a razão social por código de instituição e (b) qual traz o Índice de")
    log("Basileia. Com isso o robô passa a baixar dois arquivos estáticos por")
    log("trimestre, e o Olinda instável sai de cena.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
