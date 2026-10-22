// Descartável — confirma se a B3 tem uma Consulta Pública de CRA igual
// à de CRI (mesmo padrão de endpoint, mesmo desafio do Cloudflare).
const { chromium } = require('playwright');

const CANDIDATOS = [
  'https://sistemaswebb3-balcao.b3.com.br/featuresCRAPage/',
  'https://sistemaswebb3-balcao.b3.com.br/featuresCraPage/',
  'https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/#/cra',
  'https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-fixa/cra/cras-listados/',
];

(async () => {
  const browser = await chromium.launch();

  for (const url of CANDIDATOS) {
    console.log(`\n========== ${url} ==========`);
    const page = await browser.newPage({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    });

    const chamadas = [];
    page.on('response', (resp) => {
      const u = resp.request().url();
      if (u.includes('CraCall') || u.includes('CRACall') || u.includes('CriCall')) {
        chamadas.push({ method: resp.request().method(), url: u, status: resp.status() });
      }
    });

    let resp;
    try {
      resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
      console.log('status da página:', resp ? resp.status() : '(sem resposta)');
      console.log('título:', await page.title());
    } catch (e) {
      console.log('erro no goto:', e.message);
      await page.close();
      continue;
    }
    await page.waitForTimeout(1500);

    console.log('Chamadas CraCall/CriCall capturadas no load:', chamadas.length);
    chamadas.forEach(c => console.log(`  [${c.status}] ${c.method} ${c.url}`));

    const botoes = await page.locator('button').allTextContents();
    console.log('Botões na página:', JSON.stringify(botoes));

    const botaoCsv = page.locator('button:has-text("Exportar CSV")').first();
    const existeCsv = await botaoCsv.count();
    console.log('Botão "Exportar CSV" existe:', existeCsv > 0);

    if (existeCsv) {
      chamadas.length = 0;
      try {
        const [resposta] = await Promise.all([
          page.waitForResponse((r) => r.url().includes('GetDownload'), { timeout: 10000 }),
          botaoCsv.click({ timeout: 8000 }),
        ]);
        console.log('GetDownload status:', resposta.status());
        const texto = await resposta.text();
        console.log('corpo (tamanho):', texto.length);
        if (texto.length > 0) {
          const decodificado = Buffer.from(texto, 'base64').toString('latin1');
          const linhas = decodificado.split('\r\n').filter(Boolean);
          console.log('linhas (com cabeçalho):', linhas.length);
          console.log('cabeçalho:', linhas[0]);
          console.log('exemplo:', linhas[1]);
        }
      } catch (e) {
        console.log('erro ao clicar/capturar Exportar CSV:', e.message);
      }
    }

    await page.close();
  }

  // A página de CRI pode ser o mesmo app Angular que serve CRA via rota
  // interna (abas, menu) — procura qualquer link/texto "CRA" nela.
  console.log('\n========== Procurando CRA dentro da própria página de CRI ==========');
  const page = await browser.newPage({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
  });
  await page.goto('https://sistemaswebb3-balcao.b3.com.br/featuresCRIPage/', { waitUntil: 'networkidle', timeout: 30000 }).catch(e => console.log('goto erro:', e.message));
  await page.waitForTimeout(1500);
  const linksCra = await page.locator('a, button').evaluateAll(
    els => els.filter(e => /CRA/i.test(e.textContent || '') || /CRA/i.test(e.href || '')).map(e => ({ tag: e.tagName, texto: (e.textContent || '').trim(), href: e.href || null }))
  );
  console.log('Elementos com "CRA":', JSON.stringify(linksCra));
  await page.close();

  await browser.close();
})();
