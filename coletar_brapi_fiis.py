import requests
import json
import os

# Insira o seu token da Brapi aqui
TOKEN_BRAPI = "SEU_TOKEN_AQUI"

# Listas dos principais FIIs do mercado separados por setor
FIIS_LOGISTICA = ['BTLG11', 'HGLG11', 'XPLG11', 'VILG11', 'LVBI11', 'BRCO11', 'GGRC11', 'RBRL11', 'ALZR11', 'PATL11']
FIIS_PAPEL = ['MXRF11', 'KNCR11', 'KNIP11', 'CPTS11', 'IRDM11', 'VGIR11', 'HGCR11', 'RECR11', 'MCCI11', 'CVBI11']

def buscar_cotacoes(tickers):
    """Busca os dados dos tickers na Brapi e retorna uma lista de dicionários."""
    tickers_str = ','.join(tickers)
    url = f"https://brapi.dev/api/quote/{tickers_str}?token={TOKEN_BRAPI}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        dados = response.json().get('results', [])
        
        lista_formatada = []
        for ativo in dados:
            lista_formatada.append({
                "ticker": ativo.get("symbol"),
                "nome": ativo.get("shortName", ativo.get("symbol")),
                "preco": ativo.get("regularMarketPrice", 0),
                "variacao_pct": ativo.get("regularMarketChangePercent", 0)
            })
        return lista_formatada
    except Exception as e:
        print(f"Erro ao buscar dados na Brapi: {e}")
        return []

def atualizar_ranking_fiis():
    """Busca, filtra e atualiza o ranking.json com 4 logísticas e 2 papéis em alta."""
    print("Buscando dados de FIIs de Logística...")
    dados_logistica = buscar_cotacoes(FIIS_LOGISTICA)
    
    print("Buscando dados de FIIs de Papel...")
    dados_papel = buscar_cotacoes(FIIS_PAPEL)

    # Ordena os fundos pela variação percentual (do maior para o menor)
    dados_logistica.sort(key=lambda x: x['variacao_pct'], reverse=True)
    dados_papel.sort(key=lambda x: x['variacao_pct'], reverse=True)

    # Extrai o Top 4 de Logística e o Top 2 de Papel
    top_4_logistica = dados_logistica[:4]
    top_2_papel = dados_papel[:2]

    # Junta os 6 FIIs em uma única lista
    melhores_fiis = top_4_logistica + top_2_papel

    # (Opcional) Ordenar a lista final inteira pela variação para exibir do maior pro menor no site
    melhores_fiis.sort(key=lambda x: x['variacao_pct'], reverse=True)

    arquivo_json = 'ranking.json'
    
    # Tenta carregar o JSON existente para não apagar os dados de Ações
    if os.path.exists(arquivo_json):
        try:
            with open(arquivo_json, 'r', encoding='utf-8') as f:
                ranking_atual = json.load(f)
        except json.JSONDecodeError:
            ranking_atual = {"acoes": {}, "fiis": []}
    else:
        ranking_atual = {"acoes": {}, "fiis": []}

    # Atualiza apenas a chave "fiis"
    ranking_atual['fiis'] = melhores_fiis

    # Salva o arquivo atualizado
    with open(arquivo_json, 'w', encoding='utf-8') as f:
        json.dump(ranking_atual, f, ensure_ascii=False, indent=2)
        
    print("✅ ranking.json atualizado com os 4 melhores FIIs de Logística e 2 de Papel!")

# Executar a função (pode colocar isso no final do seu script principal)
if __name__ == "__main__":
    atualizar_ranking_fiis()