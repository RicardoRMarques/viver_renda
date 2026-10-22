// Descartável — descobre se a ANBIMA tem uma página pública (ficha) por
// CRI individual, pesquisável por ISIN/código, pra linkar a partir da
// coluna "Código" da tela de CRI do site — igual já fazemos com
// Debêntures (data.anbima.com.br/debentures/{codigo}/caracteristicas).
const { chromium } = require('playwright');

const ISIN_REAL = 'BRHBSCCRIEI0'; // HABITASEC SECURITIZADORA S/A, real, do cri.json gerado em produção

async function tentarUrlDireta(page, url) {
  console.log(`\n---- Testando URL direta: ${url} ----`);
  try {
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
    console.log('status:', resp ? resp.status() : '(sem resposta)');
    console.log('título:', await page.title());
    const texto = (await page.locator('body').innerText()).slice(0, 400);
    console.log('corpo (400 chars):', texto.replace(/\s+/g, ' '));
  } catch (e) {
    console.log('erro:', e.message);
  }
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  });

  // 1) Tenta alguns padrões de URL "chutados", no estilo do que existe pra debêntures.
  const candidatos = [
    `https://data.anbima.com.br/cri/${ISIN_REAL}/caracteristicas`,
    `https://data.anbima.com.br/certificados-recebiveis-imobiliarios/${ISIN_REAL}/caracteristicas`,
    `https://data.anbima.com.br/cri/${ISIN_REAL}`,
    `https://data.anbima.com.br/titulos/${ISIN_REAL}`,
  ];
  for (const url of candidatos) {
    await tentarUrlDireta(page, url);
  }

  // 2) Abre a ferramenta de busca de CRI de verdade e tenta pesquisar pelo ISIN,
  //    pra ver se algum resultado leva a uma URL com identificador.
  console.log('\n========== Ferramenta de busca da ANBIMA ==========');
  const chamadas = [];
  page.on('response', (resp) => {
    const url = resp.request().url();
    if (url.includes('anbima') && (url.includes('/api/') || url.includes('cri') || url.includes('certificado'))) {
      chamadas.push({ method: resp.request().method(), url, status: resp.status() });
    }
  });

  await page.goto('https://www.anbima.com.br/pt_br/informar/ferramenta/mercado-de-capitais/certificados-de-recebiveis-imobiliarios.htm', { waitUntil: 'networkidle', timeout: 45000 }).catch(e => console.log('goto erro:', e.message));
  await page.waitForTimeout(2000);

  // Tenta achar um campo de busca e digitar o ISIN.
  const camposBusca = await page.locator('input[type="text"], input[type="search"]').count();
  console.log('Campos de busca encontrados:', camposBusca);
  if (camposBusca > 0) {
    await page.locator('input[type="text"], input[type="search"]').first().fill(ISIN_REAL).catch(() => {});
    await page.keyboard.press('Enter').catch(() => {});
    await page.waitForTimeout(2500);
  }

  console.log('URL atual após tentar buscar:', page.url());
  console.log('Chamadas relevantes capturadas:', chamadas.length);
  chamadas.forEach(c => console.log(`  [${c.status}] ${c.method} ${c.url}`));

  // Algum link na página que pareça levar a uma ficha?
  const links = await page.locator('a[href*="cri"], a[href*="certificado"], a[href*="recebiveis"]').evaluateAll(
    els => els.slice(0, 10).map(e => e.href)
  );
  console.log('Links candidatos na página:', links);

  await browser.close();
})();
