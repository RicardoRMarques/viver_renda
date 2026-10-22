// Descartável — terceira rodada: entender o GetDownload (B3 — Consulta
// Pública de CRIs) de verdade — URL completa, corpo da resposta, headers
// da requisição, e se dá pra pedir mais do que o padrão sem preencher filtro.
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    acceptDownloads: true,
  });

  const detalhes = {};
  page.on('request', (req) => {
    const url = req.url();
    if (url.includes('CriCall')) {
      detalhes[url + '|' + req.method()] = { headers: req.headers(), postData: req.postData() };
    }
  });
  page.on('response', async (resp) => {
    const url = resp.request().url();
    if (url.includes('CriCall')) {
      let corpo = '(erro ao ler)';
      try { corpo = await resp.text(); } catch (e) {}
      console.log(`\n>>> ${resp.request().method()} ${url}`);
      console.log(`    status: ${resp.status()}`);
      const chave = url + '|' + resp.request().method();
      if (detalhes[chave]?.postData) console.log(`    postData: ${detalhes[chave].postData}`);
      console.log(`    corpo (${corpo.length} chars): ${corpo.slice(0, 3000)}`);
    }
  });

  await page.goto('https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/', { waitUntil: 'networkidle', timeout: 45000 }).catch(e => console.log('goto erro:', e.message));
  await page.waitForTimeout(2500);

  // Clica direto em "Exportar CSV" sem preencher nada (replica o que já
  // funcionou na rodada anterior, mas agora capturando o corpo de verdade).
  const botaoCsv = page.locator('button:has-text("Exportar CSV")').first();
  try {
    await Promise.all([
      page.waitForEvent('download', { timeout: 8000 }),
      botaoCsv.click({ timeout: 5000 }),
    ]);
  } catch (e) {
    console.log('Sem download (ou erro):', e.message);
  }
  await page.waitForTimeout(1500);

  await browser.close();
})();
