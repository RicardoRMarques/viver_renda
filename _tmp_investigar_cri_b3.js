// Descartável — quarta rodada: confirma se o GetDownload é mesmo público
// (chamada direta, sem navegador/sessão) e decodifica o base64 pra ver a
// estrutura real das linhas (quantas, campos, exemplos recentes/ativos).

async function main() {
  console.log('---- Teste direto (fetch puro, sem navegador/cookies) ----');
  const resp = await fetch('https://sistemaswebb3-balcao.b3.com.br/featuresCRIProxy/CriCall/GetDownload', {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
  });
  console.log('Status:', resp.status);
  const base64 = await resp.text();
  console.log('Tamanho do corpo (base64):', base64.length, 'chars');

  const decodificado = Buffer.from(base64, 'base64').toString('latin1');
  console.log('Tamanho decodificado:', decodificado.length, 'chars');

  const linhas = decodificado.split('\r\n').filter(Boolean);
  console.log('Total de linhas (com cabeçalho):', linhas.length);
  console.log('\nCabeçalho:', linhas[0]);
  console.log('\nPrimeiras 3 linhas de dados:');
  linhas.slice(1, 4).forEach(l => console.log(' ', l));
  console.log('\nÚltimas 3 linhas (mais recentes, provavelmente):');
  linhas.slice(-3).forEach(l => console.log(' ', l));

  // Quantas ainda não venceram (coluna "Data de Vencimento")?
  const campos = linhas[0].split(';');
  const idxVencimento = campos.findIndex(c => c.toLowerCase().includes('vencimento'));
  console.log('\nÍndice da coluna de vencimento:', idxVencimento, '->', campos[idxVencimento]);
  if (idxVencimento >= 0) {
    const hoje = new Date();
    let ativos = 0;
    for (const l of linhas.slice(1)) {
      const partes = l.split(';');
      const dataStr = partes[idxVencimento]; // dd/mm/aaaa
      if (!dataStr) continue;
      const [d, m, a] = dataStr.split('/').map(Number);
      if (!d || !m || !a) continue;
      const dataVenc = new Date(a, m - 1, d);
      if (dataVenc > hoje) ativos++;
    }
    console.log('CRIs com vencimento no futuro (ainda "vivos"):', ativos, 'de', linhas.length - 1);
  }
}

main().catch(e => console.log('Erro:', e.message));
