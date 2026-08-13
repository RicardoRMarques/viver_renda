// ============================================================
// WORKER viverderenda-bolsai — versão com todos os modos usados
// pelo protótipo de ficha do ativo.
//
// Modos disponíveis (?mode=...):
//   (vazio)     -> /fundamentals/{ticker}            indicadores atuais (ação)
//   history     -> /fundamentals/{ticker}/history    histórico trimestral
//   prices      -> /stocks/{ticker}/history          cotações diárias (gráfico)
//   dividends   -> /dividends/{ticker}               proventos + DY 12m
//   fii         -> /fiis/{ticker}                    fundamentos de FII
//   fii-dist    -> /fiis/{ticker}/distributions      distribuições mensais
//   company     -> /companies/{ticker}               CNPJ, setor, cidade, site
//   macro       -> /macro/{serie}                    cdi, ipca, selic...
//   debug       -> diagnóstico (não expõe a chave)
//
// ATENÇÃO: o secret precisa se chamar BOLSAI_API_KEY (Settings >
// Variables and Secrets). Nome errado = todas as chamadas falham.
// ============================================================

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ticker = (url.searchParams.get('ticker') || '').toUpperCase();
    const mode = url.searchParams.get('mode') || '';

    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Content-Type': 'application/json; charset=utf-8',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: { ...cors, 'Access-Control-Allow-Headers': '*' } });
    }

    const BASE = 'https://api.usebolsai.com/api/v1';
    const headers = { 'X-API-Key': env.BOLSAI_API_KEY };

    // Repassa a resposta da bolsai como veio (status + corpo).
    const repassar = async (caminho, params) => {
      const qs = params ? `?${new URLSearchParams(params)}` : '';
      const r = await fetch(`${BASE}${caminho}${qs}`, { headers });
      return new Response(await r.text(), { status: r.status, headers: cors });
    };

    // ---------- diagnóstico (antes da validação de ticker) ----------
    if (mode === 'debug') {
      let status = null, corpo = null;
      try {
        const t = await fetch(`${BASE}/fundamentals/PETR4`, { headers });
        status = t.status; corpo = (await t.text()).slice(0, 300);
      } catch (e) { corpo = `erro de rede: ${e.message}`; }
      return new Response(JSON.stringify({
        secret_encontrado: Boolean(env.BOLSAI_API_KEY),
        nome_esperado: 'BOLSAI_API_KEY',
        variaveis_disponiveis: Object.keys(env),
        status_bolsai: status,
        resposta_bolsai: corpo,
      }, null, 2), { headers: cors });
    }

    // ---------- séries macro (não usam ticker) ----------
    if (mode === 'macro') {
      const serie = url.searchParams.get('serie') || 'cdi';
      const params = {};
      for (const p of ['start', 'end', 'limit']) {
        const v = url.searchParams.get(p);
        if (v) params[p] = v;
      }
      return repassar(`/macro/${encodeURIComponent(serie)}`, params);
    }

    if (!ticker) {
      return new Response(JSON.stringify({ error: 'ticker obrigatório' }), { status: 400, headers: cors });
    }

    const t = encodeURIComponent(ticker);

    // ---------- cotações diárias (gráfico) ----------
    if (mode === 'prices') {
      const params = { limit: url.searchParams.get('limit') || '252' };
      for (const p of ['start', 'end']) {
        const v = url.searchParams.get(p);
        if (v) params[p] = v;
      }
      return repassar(`/stocks/${t}/history`, params);
    }

    // ---------- proventos (Bazin + gráfico de dividendos) ----------
    if (mode === 'dividends') {
      return repassar(`/dividends/${t}`, { years: url.searchParams.get('years') || '5' });
    }

    // ---------- FIIs ----------
    if (mode === 'fii')      return repassar(`/fiis/${t}`);
    if (mode === 'fii-dist') {
      return repassar(`/fiis/${t}/distributions`, { years: url.searchParams.get('years') || '5' });
    }

    // ---------- dados cadastrais da empresa ----------
    if (mode === 'company')  return repassar(`/companies/${t}`);

    // ---------- histórico trimestral de indicadores ----------
    if (mode === 'history') {
      return repassar(`/fundamentals/${t}/history`, { limit: url.searchParams.get('limit') || '40' });
    }

    // ---------- padrão: indicadores atuais ----------
    return repassar(`/fundamentals/${t}`);
  },
};
