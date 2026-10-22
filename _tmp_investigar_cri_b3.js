// Descartável — segunda rodada, focada só na Consulta Pública de CRIs da
// B3 (sistemaswebb3-balcao.b3.com.br/featuresCRIPage/), que tem os botões
// "Pesquisar" e "Exportar CSV" — captura TODAS as chamadas de rede (sem
// filtro de content-type) pra achar a API real por trás da busca/export.
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    acceptDownloads: true,
  });

  const todasChamadas = [];
  page.on('response', async (resp) => {
    const req = resp.request();
    const url = req.url();
    if (url.includes('.js') || url.includes('.css') || url.includes('.woff') || url.includes('.png') || url.includes('.svg') || url.includes('gtm') || url.includes('analytics')) return;
    todasChamadas.push({ method: req.method(), url, status: resp.status(), contentType: resp.headers()['content-type'] || '' });
  });
  page.on('download', (download) => {
    console.log('DOWNLOAD disparado:', download.url(), '->', download.suggestedFilename());
  });

  await page.goto('https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/', { waitUntil: 'networkidle', timeout: 45000 }).catch(e => console.log('goto erro:', e.message));
  await page.waitForTimeout(2500);

  console.log('--- Chamadas após carregar a página ---');
  todasChamadas.forEach(c => console.log(`  [${c.status}] ${c.method} ${c.url} (${c.contentType})`));
  todasChamadas.length = 0;

  // Clica em "Pesquisar" (sem preencher filtro, pra trazer tudo).
  const botaoPesquisar = page.locator('button:has-text("Pesquisar")').first();
  console.log('Botão Pesquisar existe:', await botaoPesquisar.count());
  await botaoPesquisar.click({ timeout: 8000 }).catch(e => console.log('erro ao clicar Pesquisar:', e.message));
  await page.waitForTimeout(4000);

  console.log('--- Chamadas após clicar "Pesquisar" ---');
  todasChamadas.forEach(c => console.log(`  [${c.status}] ${c.method} ${c.url} (${c.contentType})`));
  todasChamadas.length = 0;

  // Olha se apareceu alguma tabela/linha de resultado na tela.
  const linhasTabela = await page.locator('table tr, [class*="row"], [class*="linha"]').count();
  console.log('Elementos de tabela/linha na tela:', linhasTabela);

  // Tenta clicar em "Exportar CSV" — pode disparar um download direto.
  const botaoCsv = page.locator('button:has-text("Exportar CSV")').first();
  console.log('Botão Exportar CSV existe:', await botaoCsv.count());
  try {
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 8000 }),
      botaoCsv.click({ timeout: 5000 }),
    ]);
    console.log('Download capturado:', download.url());
  } catch (e) {
    console.log('Sem download direto (ou erro):', e.message);
  }
  await page.waitForTimeout(2000);

  console.log('--- Chamadas após clicar "Exportar CSV" ---');
  todasChamadas.forEach(c => console.log(`  [${c.status}] ${c.method} ${c.url} (${c.contentType})`));

  await browser.close();
})();
