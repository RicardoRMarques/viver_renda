// Descartável — investiga se dá pra montar uma base de CRIs a partir de
// fontes públicas (B3 consulta, B3 listados, ferramenta da ANBIMA),
// capturando as chamadas de rede reais feitas por cada página.
const { chromium } = require('playwright');

async function investigar(nome, url, acao) {
  console.log(`\n========== ${nome} ==========`);
  console.log(`URL: ${url}`);
  const browser = await chromium.launch();
  const page = await browser.newPage({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  });

  const chamadasInteressantes = [];
  page.on('response', async (resp) => {
    const req = resp.request();
    const ct = resp.headers()['content-type'] || '';
    const url2 = req.url();
    const relevante = (ct.includes('json') || ct.includes('csv') || ct.includes('excel') || ct.includes('spreadsheet') || url2.includes('/api/') || url2.includes('.csv'))
      && !url2.includes('.js') && !url2.includes('.css') && !url2.includes('analytics') && !url2.includes('gtm');
    if (relevante) {
      let tamanho = 'n/a';
      try { const body = await resp.body(); tamanho = body.length; } catch (e) {}
      chamadasInteressantes.push({ method: req.method(), url: url2, status: resp.status(), contentType: ct, tamanho });
    }
  });

  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
  } catch (e) {
    console.log('Erro no goto:', e.message);
  }
  await page.waitForTimeout(2000);

  if (acao) {
    try { await acao(page); } catch (e) { console.log('Erro na ação:', e.message); }
    await page.waitForTimeout(3000);
  }

  console.log(`Título da página: ${await page.title()}`);
  console.log(`Chamadas relevantes capturadas: ${chamadasInteressantes.length}`);
  chamadasInteressantes.forEach(c => console.log(`  [${c.status}] ${c.method} ${c.url} (${c.contentType}, ${c.tamanho} bytes)`));

  await browser.close();
}

(async () => {
  await investigar(
    'B3 — Consulta Pública de CRIs',
    'https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/',
    async (page) => {
      // Tenta disparar uma busca sem filtro (botão "Buscar"/"Pesquisar"), se existir.
      const botao = page.locator('button:has-text("Buscar"), button:has-text("Pesquisar"), input[type="submit"]').first();
      if (await botao.count()) await botao.click({ timeout: 5000 }).catch(() => {});
    }
  );

  await investigar(
    'B3 — CRIs Listados',
    'https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-fixa/cri/cris-listados/',
    null
  );

  await investigar(
    'ANBIMA — Ferramenta CRI',
    'https://www.anbima.com.br/pt_br/informar/ferramenta/mercado-de-capitais/certificados-de-recebiveis-imobiliarios.htm',
    async (page) => {
      const botao = page.locator('button:has-text("Buscar"), button:has-text("Pesquisar"), button:has-text("Consultar")').first();
      if (await botao.count()) await botao.click({ timeout: 5000 }).catch(() => {});
    }
  );
})();
