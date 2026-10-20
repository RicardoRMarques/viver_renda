// Script descartável, v2 — além do corpo das respostas, captura os
// HEADERS DE REQUISIÇÃO (principalmente Authorization) que o navegador
// manda pra data-api.prd.anbima.com.br, pra descobrir exatamente como o
// token (visto como "token cannot be blank" num pedido sem ele) é obtido
// e usado.
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  const chamadasApi = [];
  const chamadasFirebase = [];

  page.on('request', (req) => {
    const url = req.url();
    if (url.includes('data-api.prd.anbima.com.br')) {
      chamadasApi.push({ fase: 'request', url, headers: req.headers() });
    }
    if (url.includes('firebaseinstallations.googleapis.com') || url.includes('identitytoolkit.googleapis.com') || url.includes('securetoken.googleapis.com')) {
      chamadasFirebase.push({ fase: 'request', url, headers: req.headers(), postData: req.postData() });
    }
  });

  page.on('response', async (resp) => {
    const url = resp.url();
    if (url.includes('data-api.prd.anbima.com.br')) {
      let corpo = '';
      try { corpo = (await resp.text()).slice(0, 1500); } catch (e) { corpo = `(erro: ${e.message})`; }
      chamadasApi.push({ fase: 'response', url, status: resp.status(), corpo });
    }
    if (url.includes('firebaseinstallations.googleapis.com') || url.includes('identitytoolkit.googleapis.com') || url.includes('securetoken.googleapis.com')) {
      let corpo = '';
      try { corpo = (await resp.text()).slice(0, 1500); } catch (e) { corpo = `(erro: ${e.message})`; }
      chamadasFirebase.push({ fase: 'response', url, status: resp.status(), corpo });
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
  await page.waitForTimeout(6000);

  console.log(`\n\n######## CHAMADAS FIREBASE (${chamadasFirebase.length}) ########`);
  for (const c of chamadasFirebase) {
    console.log(`\n--- [${c.fase}] ${c.status ?? ''} ${c.url}`);
    if (c.headers) console.log('headers:', JSON.stringify(c.headers, null, 2));
    if (c.postData) console.log('postData:', c.postData);
    if (c.corpo) console.log('corpo:', c.corpo);
  }

  console.log(`\n\n######## CHAMADAS data-api (${chamadasApi.length}) ########`);
  for (const c of chamadasApi) {
    console.log(`\n--- [${c.fase}] ${c.status ?? ''} ${c.url}`);
    if (c.headers) console.log('headers:', JSON.stringify(c.headers, null, 2));
    if (c.corpo) console.log('corpo:', c.corpo);
  }

  await browser.close();
})();
