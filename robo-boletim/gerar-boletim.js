/**
 * gerar-boletim.js
 * ------------------------------------------------------------------
 * Gera o "Boletim de Mercado" diário (arquivo HTML autônomo, no mesmo
 * layout do template original) combinando 3 arquivos que os robôs já
 * existentes do site produzem no repositório:
 *
 *   - indices.json   → Ibovespa, Dólar, Euro, Bitcoin, Selic, IPCA...
 *   - noticias.json   → últimas notícias de mercado
 *   - ranking.json    → rankings de ações e FIIs (DY, valor de mercado...)
 *
 * Esses 3 arquivos são lidos diretamente da raiz do repositório — este
 * script NÃO chama nenhuma API externa. Ele só formata e monta o HTML.
 * Por isso, o workflow do GitHub Actions precisa rodar DEPOIS que esses
 * 3 arquivos já tiverem sido atualizados no dia (veja o comentário no
 * arquivo do workflow sobre ajustar o horário/ordem se necessário).
 *
 * Saída:
 *   - boletins/Boletim_de_Mercado_DD-MM-AAAA.html   (o boletim do dia)
 *   - boletins/index.json                            (lista dos últimos
 *     boletins, usada pelo site para montar a listinha "boletins da
 *     semana" com o botão de compartilhar)
 *
 * Retenção: qualquer boletim com mais de 7 dias é apagado automaticamente
 * (arquivo .html + entrada no index.json), então o repositório nunca
 * acumula boletins antigos.
 * ------------------------------------------------------------------
 * COMO PERSONALIZAR "Destaques da Bolsa" e "Alerta de Risco":
 * Esses dois blocos são editoriais/opinativos (não dá para extrair só
 * de números). Se existir um arquivo `boletim-editorial.json` na raiz
 * do repositório (ver `boletim-editorial.exemplo.json`), o robô usa o
 * conteúdo de lá. Se não existir, ele gera um fallback automático
 * neutro (repete as manchetes das notícias e usa um aviso genérico),
 * para nunca travar a geração do boletim.
 * ------------------------------------------------------------------
 */

const fs = require('fs');
const path = require('path');

const RAIZ = path.resolve(__dirname, '..'); // raiz do repositório
const PASTA_BOLETINS = path.join(RAIZ, 'boletins');
const DIAS_RETENCAO = 7;

// ---------- Utilidades de data ----------
function agoraSaoPaulo() {
  // Brasil não tem horário de verão desde 2019 → BRT = UTC-3 o ano todo.
  const agoraUtc = new Date();
  return new Date(agoraUtc.getTime() - 3 * 60 * 60 * 1000);
}

function formatarDataArquivo(data) {
  const dd = String(data.getUTCDate()).padStart(2, '0');
  const mm = String(data.getUTCMonth() + 1).padStart(2, '0');
  const yyyy = data.getUTCFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

function formatarDataExtenso(data) {
  const dias = ['domingo', 'segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado'];
  const diaSemana = dias[data.getUTCDay()];
  const dd = String(data.getUTCDate()).padStart(2, '0');
  const meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
  const mm = meses[data.getUTCMonth()];
  const yyyy = data.getUTCFullYear();
  return `${diaSemana.charAt(0).toUpperCase()}${diaSemana.slice(1)}, ${dd} de ${mm.charAt(0).toUpperCase()}${mm.slice(1)} de ${yyyy}`;
}

// ---------- Leitura segura dos arquivos-fonte ----------
function lerJsonSeExistir(caminho, fallback) {
  try {
    const conteudo = fs.readFileSync(caminho, 'utf-8');
    return JSON.parse(conteudo);
  } catch (e) {
    console.warn(`[boletim] Aviso: não consegui ler ${path.basename(caminho)} (${e.message}). Usando fallback vazio.`);
    return fallback;
  }
}

// ---------- Formatação de números ----------
function fmtValorIndicador(item) {
  if (typeof item?.valor !== 'number') return '—';
  const prefixo = item.prefixo === 'pontos' ? '' : (item.prefixo || '');
  const sufixo = item.prefixo === 'pontos' ? ' pts' : '';
  const num = item.valor.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${prefixo}${num}${sufixo}`;
}

function fmtVariacao(item) {
  const variacao = typeof item?.variacao_pct === 'number' ? item.variacao_pct : null;
  if (variacao === null) return { texto: '', classe: '' };
  const seta = variacao >= 0 ? '▲' : '▼';
  const classe = variacao >= 0 ? 'up' : 'down';
  return { texto: ` ${seta} ${Math.abs(variacao).toFixed(2)}%`, classe };
}

function buscarIndicador(indices, ...nomesPossiveis) {
  const alvo = nomesPossiveis.map(n => n.toUpperCase());
  return (indices || []).find(i => {
    const label = (i.label || '').trim().toUpperCase();
    return alvo.some(a => label === a || label.startsWith(a + ' ') || label.startsWith(a + '('));
  }) || null;
}

function fmtCompacto(valor) {
  if (typeof valor !== 'number') return '—';
  if (valor >= 1e9) return `${(valor / 1e9).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} bi`;
  if (valor >= 1e6) return `${(valor / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mi`;
  return valor.toLocaleString('pt-BR', { maximumFractionDigits: 2 });
}

// ---------- Montagem dos indicadores principais (grid do topo) ----------
function montarIndicadoresHtml(indices) {
  const definicoes = [
    { chaves: ['IBOVESPA'], label: 'Ibovespa' },
    { chaves: ['DÓLAR', 'DOLAR', 'USD/BRL'], label: 'Dólar (USD/BRL)' },
    { chaves: ['EURO', 'EUR/BRL'], label: 'Euro (EUR/BRL)' },
    { chaves: ['BITCOIN', 'BTC'], label: 'Bitcoin' },
    { chaves: ['IPCA'], label: 'IPCA (12 meses)' },
    { chaves: ['DOW JONES', 'DOW'], label: 'Dow Jones' },
    { chaves: ['NASDAQ'], label: 'Nasdaq' },
    { chaves: ['PETRÓLEO', 'PETROLEO', 'BRENT'], label: 'Petróleo Brent' },
    { chaves: ['SELIC'], label: 'Selic' },
    { chaves: ['IFIX'], label: 'IFIX' },
  ];

  return definicoes.map(({ chaves, label }) => {
    const item = buscarIndicador(indices, ...chaves);
    if (!item) return `<div class="indicator"><div class="label">${label}</div><div class="value">—</div></div>`;
    const valor = fmtValorIndicador(item);
    const variacao = fmtVariacao(item);
    return `<div class="indicator"><div class="label">${label}</div><div class="value ${variacao.classe}">${valor}${variacao.texto}</div></div>`;
  }).join('\n  ');
}

// ---------- Montagem da tabela de FIIs / Ações (a partir do ranking.json) ----------
// Importante: o ranking.json usa os campos reais { ticker, nome, valor, preco }
// — "valor" é a própria métrica do ranking (aqui, o DY em %). Não existe
// P/VP nem P/L nesses dados (o robô de ranking não calcula isso), então
// a coluna "Destaque" usa o nome do ativo, que é o único texto descritivo
// disponível na fonte.
function montarTabelaFiis(ranking) {
  const lista = (ranking?.fiis?.dividend_yield || []).slice(0, 6);
  if (lista.length === 0) {
    return '<tr><td colspan="4" class="rationale">Ranking de FIIs ainda não disponível hoje.</td></tr>';
  }
  return lista.map(f => `
  <tr>
    <td class="name">${f.ticker || '—'}</td>
    <td>${typeof f.valor === 'number' ? f.valor.toFixed(2) + '%' : '—'}</td>
    <td>${typeof f.preco === 'number' ? 'R$ ' + f.preco.toFixed(2) : '—'}</td>
    <td class="rationale">${f.nome || '—'}</td>
  </tr>`).join('');
}

function montarTabelaAcoes(ranking) {
  const lista = (ranking?.acoes?.dividend_yield || []).slice(0, 6);
  if (lista.length === 0) {
    return '<tr><td colspan="4" class="rationale">Ranking de ações ainda não disponível hoje.</td></tr>';
  }
  return lista.map(a => `
  <tr>
    <td class="name">${a.ticker || '—'}</td>
    <td>${typeof a.valor === 'number' ? a.valor.toFixed(2) + '%' : '—'}</td>
    <td>${typeof a.preco === 'number' ? 'R$ ' + a.preco.toFixed(2) : '—'}</td>
    <td class="rationale">${a.nome || '—'}</td>
  </tr>`).join('');
}

// ---------- Montagem das notícias (top 3, vindas do noticias.json) ----------
// Campos reais do noticias.json: { titulo, link, fonte, imagem } — não
// existe um campo de resumo/descrição na fonte, por isso mostramos só a
// fonte da notícia como texto de apoio.
function montarNoticiasHtml(noticias) {
  const lista = Array.isArray(noticias) ? noticias.slice(0, 3) : [];
  if (lista.length === 0) {
    return '<div class="news-item"><div class="n-title">Sem notícias disponíveis hoje</div></div>';
  }
  return lista.map((n, i) => `
<div class="news-item">
  <div class="n-title">${i + 1}. ${n.titulo || 'Sem título'}</div>
  ${n.fonte ? `<div class="n-body">Fonte: ${n.fonte}</div>` : ''}
</div>`).join('\n');
}

// ---------- Bloco editorial (Destaques da Bolsa + Alerta de Risco) ----------
// Usa boletim-editorial.json se existir; senão gera um fallback automático.
function carregarEditorial(noticias) {
  const editorial = lerJsonSeExistir(path.join(RAIZ, 'boletim-editorial.json'), null);
  if (editorial && (editorial.destaques || editorial.alerta)) {
    return {
      destaques: editorial.destaques || [],
      alerta: editorial.alerta || 'Sem observações de risco cadastradas para hoje.',
    };
  }

  // Fallback automático: usa as manchetes das notícias como "destaques"
  // e um aviso genérico de risco (sempre válido, nunca trava a geração).
  const destaquesFallback = (Array.isArray(noticias) ? noticias.slice(0, 4) : [])
    .map(n => ({ titulo: n.titulo, obs: n.fonte || '' }));

  return {
    destaques: destaquesFallback,
    alerta: 'Este é um alerta padrão: mercados financeiros envolvem risco de perda. Diversifique, avalie seu perfil de investidor e evite posições alavancadas sem gestão de risco adequada. Acompanhe o calendário do Copom e os resultados corporativos da temporada para eventos que podem gerar volatilidade.',
  };
}

function montarDestaquesHtml(destaques) {
  if (!destaques || destaques.length === 0) {
    return '<div class="mover-row"><span class="tk">Sem destaques cadastrados hoje</span></div>';
  }
  return destaques.map(d => `<div class="mover-row"><span class="tk">${d.titulo}</span>${d.obs ? `<span class="pct">${d.obs}</span>` : ''}</div>`).join('\n  ');
}

// ---------- Template do boletim (mesmo layout do original) ----------
function montarHtml({ dataExtenso, dataFechamento, indicadoresHtml, destaquesHtml, tabelaFiisHtml, tabelaAcoesHtml, noticiasHtml, alertaTexto }) {
  return `<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Boletim de Mercado | Dividendos Viver de Renda</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0a0a0a; --card:#141414; --card-border:#232323;
    --gold:#d4af37; --gold-soft:#e8c766; --green:#3ecf8e; --red:#ff5c5c;
    --text:#f2f2f2; --muted:#9a9a9a;
  }
  body.light{
    --bg:#f7f5f0; --card:#ffffff; --card-border:#e2ddd1;
    --gold:#b8860b; --gold-soft:#96690a; --green:#1f9d63; --red:#d64545;
    --text:#1a1a1a; --muted:#6b6b6b;
  }
  body{transition:background 0.2s ease, color 0.2s ease;}
  .theme-toggle{position:absolute;top:20px;right:20px;background:var(--card);border:1px solid var(--card-border);border-radius:50%;width:38px;height:38px;display:flex;align-items:center;justify-content:center;font-size:18px;cursor:pointer;user-select:none;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;padding:0 0 40px;}
  .wrap{max-width:640px;margin:0 auto;padding:24px 18px;}
  header{text-align:center;margin-bottom:28px;position:relative;}
  header .brand{font-family:'Fraunces',serif;font-weight:700;font-size:22px;color:var(--gold-soft);letter-spacing:0.5px;}
  header .sub{color:var(--muted);font-size:13px;margin-top:4px;font-family:'IBM Plex Mono',monospace;}
  h2.section{font-family:'Fraunces',serif;font-weight:600;font-size:17px;color:var(--gold-soft);margin:30px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--card-border);}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
  .indicator{background:var(--card);border:1px solid var(--card-border);border-radius:10px;padding:12px 14px;}
  .indicator .label{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:0.4px;}
  .indicator .value{font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:500;margin-top:4px;}
  .up{color:var(--green);} .down{color:var(--red);}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th{text-align:left;color:var(--muted);font-weight:500;font-size:11px;text-transform:uppercase;padding:6px 4px;border-bottom:1px solid var(--card-border);}
  td{padding:8px 4px;border-bottom:1px solid #1c1c1c;font-family:'IBM Plex Mono',monospace;font-size:12.5px;}
  td.name{font-family:'Inter',sans-serif;font-weight:600;color:var(--text);}
  .rationale{font-family:'Inter',sans-serif;font-size:11px;color:var(--muted);}
  .movers{display:flex;flex-direction:column;gap:6px;}
  .mover-row{display:flex;justify-content:space-between;align-items:center;background:var(--card);border:1px solid var(--card-border);border-radius:8px;padding:9px 12px;}
  .mover-row .tk{font-weight:600;font-size:13px;}
  .mover-row .pct{font-family:'IBM Plex Mono',monospace;font-size:13px;}
  .news-item{background:var(--card);border:1px solid var(--card-border);border-radius:10px;padding:12px 14px;margin-bottom:10px;}
  .news-item .n-title{font-weight:600;font-size:13.5px;margin-bottom:4px;}
  .news-item .n-body{font-size:12.5px;color:var(--muted);line-height:1.5;}
  .alert{margin-top:30px;background:linear-gradient(135deg,#2a1a05,#1a1005);border:1px solid var(--gold);border-radius:12px;padding:16px;font-size:12.5px;color:var(--gold-soft);line-height:1.6;}
  .alert b{color:var(--gold-soft);}
  footer{text-align:center;margin-top:32px;color:var(--muted);font-size:11px;font-family:'IBM Plex Mono',monospace;}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="theme-toggle" id="themeToggle" onclick="toggleTheme()">🌙</div>
  <div class="brand">DIVIDENDOS | VIVER DE RENDA</div>
  <div class="sub">Boletim de Mercado · ${dataExtenso}</div>
  <div class="sub" style="margin-top:2px;opacity:0.8;">Dados referentes ao fechamento de ${dataFechamento}</div>
</header>

<h2 class="section">Indicadores Principais</h2>
<div class="grid">
  ${indicadoresHtml}
</div>

<h2 class="section">Destaques da Bolsa</h2>
<div class="movers">
  ${destaquesHtml}
</div>

<h2 class="section">6 Melhores FIIs</h2>
<table>
  <tr><th>Ticker</th><th>DY (12m)</th><th>Preço</th><th>Nome</th></tr>
  ${tabelaFiisHtml}
</table>

<h2 class="section">6 Melhores Ações de Dividendos</h2>
<table>
  <tr><th>Ticker</th><th>DY (TTM)</th><th>Preço</th><th>Nome</th></tr>
  ${tabelaAcoesHtml}
</table>

<h2 class="section">Top 3 Notícias</h2>
${noticiasHtml}

<div class="alert">
  ⚠ <b>Alerta de Risco:</b> ${alertaTexto}
</div>

<footer>
  Fonte: HG Brasil · Fontes públicas de mercado — dados sujeitos a variação intradiária.<br>
  Este boletim tem caráter meramente informativo e não constitui recomendação de compra ou venda de ativos.<br>
  Dividendos | Viver de Renda — viverderenda.dev.br
</footer>

</div>
<script>
  function toggleTheme(){
    const body = document.body;
    const btn = document.getElementById('themeToggle');
    body.classList.toggle('light');
    btn.textContent = body.classList.contains('light') ? '☀️' : '🌙';
  }
</script>
</body>
</html>
`;
}

// ---------- Retenção: apaga boletins com mais de 7 dias ----------
function limparBoletinsAntigos(indiceAtual, hojeMs) {
  const limiteMs = DIAS_RETENCAO * 24 * 60 * 60 * 1000;
  const mantidos = [];

  for (const entrada of indiceAtual) {
    const idadeMs = hojeMs - new Date(entrada.dataIso).getTime();
    if (idadeMs > limiteMs) {
      const caminhoArquivo = path.join(PASTA_BOLETINS, entrada.arquivo);
      if (fs.existsSync(caminhoArquivo)) {
        fs.unlinkSync(caminhoArquivo);
        console.log(`[boletim] Removido (mais de ${DIAS_RETENCAO} dias): ${entrada.arquivo}`);
      }
    } else {
      mantidos.push(entrada);
    }
  }
  return mantidos;
}

// ---------- Execução principal ----------
function main() {
  fs.mkdirSync(PASTA_BOLETINS, { recursive: true });

  const agora = agoraSaoPaulo();
  const dataArquivo = formatarDataArquivo(agora); // DD-MM-AAAA
  const dataExtenso = formatarDataExtenso(agora);

  // "Fechamento" = dia anterior (o boletim de segunda usa o fechamento
  // de sexta-feira automaticamente, pois soma -1 dia corrido).
  const fechamento = new Date(agora.getTime() - 24 * 60 * 60 * 1000);
  const dataFechamento = formatarDataArquivo(fechamento).replace(/-/g, '/');

  const indices = lerJsonSeExistir(path.join(RAIZ, 'indices.json'), []);
  const noticias = lerJsonSeExistir(path.join(RAIZ, 'noticias.json'), []);
  const ranking = lerJsonSeExistir(path.join(RAIZ, 'ranking.json'), {});

  const indicadoresHtml = montarIndicadoresHtml(indices);
  const tabelaFiisHtml = montarTabelaFiis(ranking);
  const tabelaAcoesHtml = montarTabelaAcoes(ranking);
  const noticiasHtml = montarNoticiasHtml(noticias);
  const { destaques, alerta } = carregarEditorial(noticias);
  const destaquesHtml = montarDestaquesHtml(destaques);

  const html = montarHtml({
    dataExtenso,
    dataFechamento,
    indicadoresHtml,
    destaquesHtml,
    tabelaFiisHtml,
    tabelaAcoesHtml,
    noticiasHtml,
    alertaTexto: alerta,
  });

  const nomeArquivo = `Boletim_de_Mercado_${dataArquivo}.html`;
  fs.writeFileSync(path.join(PASTA_BOLETINS, nomeArquivo), html, 'utf-8');
  console.log(`[boletim] Gerado: boletins/${nomeArquivo}`);

  // Atualiza o índice usado pelo site (boletins/index.json)
  const caminhoIndice = path.join(PASTA_BOLETINS, 'index.json');
  let indiceAtual = lerJsonSeExistir(caminhoIndice, []);

  // Evita duplicar entrada se o robô rodar 2x no mesmo dia
  indiceAtual = indiceAtual.filter(e => e.arquivo !== nomeArquivo);
  indiceAtual.unshift({
    arquivo: nomeArquivo,
    dataIso: agora.toISOString(),
    dataExibicao: dataArquivo,
    labelDia: dataExtenso,
  });

  indiceAtual = limparBoletinsAntigos(indiceAtual, agora.getTime());
  // Mantém sempre ordenado do mais novo pro mais antigo
  indiceAtual.sort((a, b) => new Date(b.dataIso) - new Date(a.dataIso));

  fs.writeFileSync(caminhoIndice, JSON.stringify(indiceAtual, null, 2), 'utf-8');
  console.log(`[boletim] Índice atualizado: boletins/index.json (${indiceAtual.length} boletim(ns) na semana)`);
}

main();
