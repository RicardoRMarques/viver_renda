// Script descartável — inspeciona as chamadas de rede reais que o site
// ANBIMA Data faz ao carregar a página de CRI/CRA, pra descobrir se existe
// um endpoint JSON público por trás da tela (sem precisar de login).
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  const chamadas = [];
  page.on('response', async (resp) => {
    const url = resp.url();
    const tipo = resp.request().resourceType();
    if (tipo === 'xhr' || tipo === 'fetch' || url.includes('/api/') || url.includes('strapi')) {
      let corpo = '';
      try {
        corpo = (await resp.text()).slice(0, 800);
      } catch (e) {
        corpo = `(erro lendo corpo: ${e.message})`;
      }
      chamadas.push({ url, status: resp.status(), tipo, corpo });
    }
  });

  console.log('Navegando para data.anbima.com.br/certificado-de-recebiveis ...');
  try {
    await page.goto('https://data.anbima.com.br/certificado-de-recebiveis', {
      waitUntil: 'networkidle',
      timeout: 45000,
    });
  } catch (e) {
    console.log('goto deu erro/timeout (seguindo mesmo assim):', e.message);
  }
  await page.waitForTimeout(5000);

  console.log(`\n=== ${chamadas.length} chamadas de API/XHR capturadas ===`);
  for (const c of chamadas) {
    console.log(`\n--- ${c.status} [${c.tipo}] ${c.url}`);
    console.log(c.corpo);
  }

  await page.screenshot({ path: 'anbima-data-cri-cra.png', fullPage: true });
  console.log('\nScreenshot salvo.');

  await browser.close();
})();
