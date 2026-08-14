/**
 * gerar-boletim.js
 * ------------------------------------------------------------------
 * Gera o "Boletim de Mercado" diário (arquivo HTML autônomo, no mesmo
 * layout do template original) combinando 3 arquivos que os robôs já
 * existentes do site produzem no repositório:
 *
 *   - indices.json   → Ibovespa, Dólar, Euro, Bitcoin, Selic, IPCA...
 *   - noticias.json   → { destaques: [...], top3: [...] } — dois grupos
 *     separados (Investing.com por categoria FIIs/Ações/Economia, e
 *     InfoMoney/Money Times), pra "Destaques da Bolsa" e "Top 3 Notícias"
 *     não mostrarem sempre as mesmas 3 manchetes repetidas.
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

// Calcula o último dia útil ANTES da data informada, pulando sábado e
// domingo — assim, numa segunda-feira, o "fechamento" aponta corretamente
// para a sexta-feira anterior (a bolsa não funciona no fim de semana).
// Não tem calendário de feriados (isso exigiria uma fonte de dados à
// parte), mas já corrige o caso mais comum e mais visível: a virada do
// fim de semana.
function ultimoDiaUtilAntesDe(data) {
  let candidato = new Date(data.getTime() - 24 * 60 * 60 * 1000);
  while (candidato.getUTCDay() === 0 || candidato.getUTCDay() === 6) {
    candidato = new Date(candidato.getTime() - 24 * 60 * 60 * 1000);
  }
  return candidato;
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

// Casa o rótulo do indices.json com os nomes procurados. Por padrão aceita
// prefixo ("DÓLAR" casa com "Dólar (USD/BRL)"), o que é prático — mas
// perigoso quando um rótulo é prefixo de outro. É o caso de "IPCA
// (mensal)" e "IPCA (12 meses)": buscar por "IPCA" devolveria o primeiro
// da lista. Para esses, use exato=true.
function buscarIndicador(indices, ...nomesPossiveis) {
  let exato = false;
  if (typeof nomesPossiveis[nomesPossiveis.length - 1] === 'boolean') {
    exato = nomesPossiveis.pop();
  }
  const alvo = nomesPossiveis.map(n => n.toUpperCase());
  return (indices || []).find(i => {
    const label = (i.label || '').trim().toUpperCase();
    if (exato) return alvo.some(a => label === a);
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
// Alguns itens (Ibovespa, Dólar, Bitcoin...) têm "valor" numérico + uma
// variação do dia. Outros (IPCA, Selic) são taxas — o indices.json guarda
// isso em "valor_pct", sem variação diária (não faz sentido "variar" uma
// taxa anual do dia pro outro). Por isso tratamos os dois casos.
function montarIndicadoresHtml(indices) {
  const definicoes = [
    { chaves: ['IBOVESPA'], label: 'Ibovespa' },
    { chaves: ['IFIX'], label: 'IFIX' },
    { chaves: ['DÓLAR', 'DOLAR', 'USD/BRL'], label: 'Dólar (USD/BRL)' },
    { chaves: ['EURO', 'EUR/BRL'], label: 'Euro (EUR/BRL)' },
    { chaves: ['BITCOIN', 'BTC'], label: 'Bitcoin' },
    { chaves: ['ETHEREUM', 'ETH'], label: 'Ethereum' },
    { chaves: ['SELIC'], label: 'Selic' },
    // Sem exato: 'CDI' não é prefixo de nenhum outro rótulo no
    // indices.json, então o casamento por prefixo já é seguro aqui.
    { chaves: ['CDI'], label: 'CDI' },
    // ATENÇÃO: precisa ser o rótulo COMPLETO. O indices.json tem
    // "IPCA (mensal)" e "IPCA (12 meses)", e buscarIndicador casa por
    // prefixo devolvendo o primeiro — usar só 'IPCA' aqui pegava o
    // mensal e exibia com o rótulo de 12 meses (valor errado).
    { chaves: ['IPCA (12 MESES)'], label: 'IPCA (12 meses)', exato: true },
    { chaves: ['IGP-M (MENSAL)', 'IGP-M'], label: 'IGP-M (mensal)' },
  ];

  return definicoes.map(({ chaves, label, exato }) => {
    const item = buscarIndicador(indices, ...chaves, Boolean(exato));
    if (!item) return `<div class="indicator"><div class="label">${label}</div><div class="value">—</div></div>`;

    // Caso 1: item de cotação (tem "valor" numérico) → valor + variação do dia
    if (typeof item.valor === 'number') {
      const valor = fmtValorIndicador(item);
      const variacao = fmtVariacao(item);
      return `<div class="indicator"><div class="label">${label}</div><div class="value ${variacao.classe}">${valor}${variacao.texto}</div></div>`;
    }

    // Caso 2: item de taxa (IPCA, Selic — só tem "valor_pct") → mostra a taxa em si
    if (typeof item.valor_pct === 'number') {
      const sufixo = (label === 'Selic' || label === 'CDI') ? '% a.a.' : '%';
      return `<div class="indicator"><div class="label">${label}</div><div class="value">${item.valor_pct.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${sufixo}</div></div>`;
    }

    return `<div class="indicator"><div class="label">${label}</div><div class="value">—</div></div>`;
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
// fonte da notícia como texto de apoio. O título agora é um link clicável
// pra matéria original, quando o campo "link" existe.
// Miniatura da notícia. Só entra se a URL for absoluta http(s) — links
// relativos vindos de RSS quebrariam, já que o boletim é servido de outro
// domínio. Se a imagem falhar no carregamento (link expirado, hotlink
// bloqueado), o onerror remove o elemento e o texto ocupa o espaço todo,
// em vez de deixar um ícone de imagem quebrada no boletim.
function montarMiniaturaHtml(url, alt, classe) {
  if (typeof url !== 'string') return '';
  const limpa = url.trim();
  if (!/^https?:\/\//i.test(limpa)) return '';
  const seguro = limpa.replace(/"/g, '&quot;');
  const altSeguro = String(alt || 'Imagem da notícia').replace(/"/g, '&quot;');
  return `<img src="${seguro}" alt="${altSeguro}" class="${classe}" loading="lazy" `
    + `referrerpolicy="no-referrer" onerror="this.remove()">`;
}

function montarNoticiasHtml(noticias) {
  const lista = Array.isArray(noticias) ? noticias.slice(0, 3) : [];
  if (lista.length === 0) {
    return '<div class="news-item"><div class="n-title">Sem notícias disponíveis hoje</div></div>';
  }
  return lista.map((n, i) => {
    const titulo = n.titulo || 'Sem título';
    const tituloHtml = n.link
      ? `<a href="${n.link}" target="_blank" rel="noopener noreferrer" class="n-link">${i + 1}. ${titulo}</a>`
      : `${i + 1}. ${titulo}`;
    const thumb = montarMiniaturaHtml(n.imagem, titulo, 'n-thumb');
    return `
<div class="news-item${thumb ? ' com-thumb' : ''}">
  ${thumb}
  <div class="n-conteudo">
    <div class="n-title">${tituloHtml}</div>
    ${n.fonte ? `<div class="n-body">Fonte: ${n.fonte}</div>` : ''}
  </div>
</div>`;
  }).join('\n');
}

// ---------- Bloco editorial (Destaques da Bolsa + Alerta de Risco) ----------
// Usa boletim-editorial.json se existir; senão gera um fallback automático
// a partir do grupo "destaques" do noticias.json (Investing.com: FIIs +
// Ações + Economia — já vem pronto, sem precisar recortar aqui).
function carregarEditorial(noticiasDestaques) {
  const editorial = lerJsonSeExistir(path.join(RAIZ, 'boletim-editorial.json'), null);
  if (editorial && (editorial.destaques || editorial.alerta)) {
    return {
      destaques: editorial.destaques || [],
      alerta: editorial.alerta || 'Sem observações de risco cadastradas para hoje.',
    };
  }

  const destaquesFallback = (Array.isArray(noticiasDestaques) ? noticiasDestaques.slice(0, 4) : [])
    .map(n => ({ titulo: n.titulo, obs: n.fonte || '', link: n.link || null, imagem: n.imagem || null }));

  return {
    destaques: destaquesFallback,
    alerta: 'Este é um alerta padrão: mercados financeiros envolvem risco de perda. Diversifique, avalie seu perfil de investidor e evite posições alavancadas sem gestão de risco adequada. Acompanhe o calendário do Copom e os resultados corporativos da temporada para eventos que podem gerar volatilidade.',
  };
}

function montarDestaquesHtml(destaques) {
  if (!destaques || destaques.length === 0) {
    return '<div class="mover-row"><span class="tk">Sem destaques cadastrados hoje</span></div>';
  }
  return destaques.map(d => {
    const tituloHtml = d.link
      ? `<a href="${d.link}" target="_blank" rel="noopener noreferrer" class="tk-link">${d.titulo}</a>`
      : d.titulo;
    const thumb = montarMiniaturaHtml(d.imagem, d.titulo, 'tk-thumb');
    return `<div class="mover-row${thumb ? ' com-thumb' : ''}">${thumb}`
      + `<span class="tk">${tituloHtml}</span>`
      + `${d.obs ? `<span class="pct">${d.obs}</span>` : ''}</div>`;
  }).join('\n  ');
}

// ---------- Template do boletim (mesmo layout do original) ----------
function montarHtml({ dataExtenso, dataArquivo, indicadoresHtml, destaquesHtml, tabelaFiisHtml, tabelaAcoesHtml, noticiasHtml, alertaTexto }) {
  return `<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Boletim de Mercado | Dividendos Viver de Renda</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  /* Paleta igual à do site (Dividendos | Viver de Renda) */
  :root{
    --bg:#0b1220; --card:#101b2e; --card-border:rgba(255,255,255,0.08);
    --gold:#31c3ff; --gold-soft:#31c3ff; --green:#7cff92; --red:#ff5c5c;
    --text:#e8eefc; --muted:#a9b6d3;
  }
  body.light{
    --bg:#eef2f9; --card:#ffffff; --card-border:rgba(15,30,60,0.1);
    --gold:#0f8fce; --gold-soft:#0f8fce; --green:#0e9e46; --red:#d64545;
    --text:#16223b; --muted:#5b6b8c;
  }
  body{transition:background 0.2s ease, color 0.2s ease;}
  .theme-toggle{position:absolute;top:20px;right:20px;background:var(--card);border:1px solid var(--card-border);border-radius:50%;width:38px;height:38px;display:flex;align-items:center;justify-content:center;font-size:18px;cursor:pointer;user-select:none;}
  .voltar-site-barra{
    position:sticky;top:0;z-index:50;
    background:var(--bg);border-bottom:1px solid var(--card-border);
    padding:10px 0;margin:0 0 20px;
  }
  .voltar-site{display:inline-flex;align-items:center;gap:5px;color:var(--muted);font-size:12px;text-decoration:none;font-family:'Inter',sans-serif;}
  .voltar-site:hover{color:var(--gold-soft);}
  .voltar-site svg{width:14px;height:14px;flex-shrink:0;}
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
  /* Miniaturas. A regra .com-thumb só entra quando existe imagem, então
     linhas sem foto continuam com o layout original, sem buraco. O "tk"
     vira flex:1 para o título ocupar o espaço restante e a fonte ficar
     encostada à direita, como já era. */
  .mover-row.com-thumb{gap:10px;}
  .mover-row.com-thumb .tk{flex:1;min-width:0;}
  .tk-thumb{width:44px;height:44px;flex-shrink:0;border-radius:6px;object-fit:cover;
    background:var(--card-border);display:block;}
  .news-item{background:var(--card);border:1px solid var(--card-border);border-radius:10px;padding:12px 14px;margin-bottom:10px;}
  .news-item.com-thumb{display:flex;gap:12px;align-items:flex-start;}
  .news-item.com-thumb .n-conteudo{flex:1;min-width:0;}
  .n-thumb{width:64px;height:64px;flex-shrink:0;border-radius:8px;object-fit:cover;
    background:var(--card-border);display:block;}
  .news-item .n-title{font-weight:600;font-size:13.5px;margin-bottom:4px;}
  .n-title a.n-link, .tk a.tk-link { color: var(--text); text-decoration: none; border-bottom: 1px dashed var(--gold); }
  .n-title a.n-link:hover, .tk a.tk-link:hover { color: var(--gold-soft); border-bottom-style: solid; }
  .news-item .n-body{font-size:12.5px;color:var(--muted);line-height:1.5;}
  .alert{margin-top:30px;background:linear-gradient(135deg,#0f2438,#0a1a2b);border:1px solid var(--gold);border-radius:12px;padding:16px;font-size:12.5px;color:var(--gold-soft);line-height:1.6;}
  .section-com-lyket{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;}
  .section-com-lyket [data-lyket-type]{
    font-size:12px;
    background:#f2f2f2;
    border-radius:20px;
    padding:4px 10px;
    display:inline-flex;
    align-items:center;
    box-shadow:0 1px 4px rgba(0,0,0,0.35);
  }
  .section-com-lyket [data-lyket-type] * { color:#1a1a1a !important; }
  .grupo-whatsapp-card{
    display:flex;align-items:center;gap:12px;margin-top:12px;
    background:var(--card);border:1px solid var(--card-border);border-radius:12px;
    padding:10px 14px;text-decoration:none;
  }
  .grupo-whatsapp-card img{border-radius:6px;background:#fff;padding:3px;flex-shrink:0;}
  .grupo-whatsapp-card span{display:flex;flex-direction:column;gap:2px;}
  .grupo-whatsapp-card strong{color:var(--text);font-size:13.5px;}
  .grupo-whatsapp-card small{color:var(--muted);font-size:11.5px;line-height:1.4;}
  .alert b{color:var(--gold-soft);}
  footer{text-align:center;margin-top:32px;color:var(--muted);font-size:11px;font-family:'IBM Plex Mono',monospace;}
</style>
<script src="https://unpkg.com/@lyket/widget@latest/dist/lyket.js?apiKey=pt_d2c19f94733791f2b25c44987dd17e"></script>
</head>
<body>
<div class="wrap">

<div class="voltar-site-barra">
  <a class="voltar-site" href="https://viverderenda.dev.br/">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"></path><path d="m12 19-7-7 7-7"></path></svg>
    Voltar ao site
  </a>
</div>

<header>
  <div class="theme-toggle" id="themeToggle" onclick="toggleTheme()">🌙</div>
  <div class="brand">DIVIDENDOS | VIVER DE RENDA</div>
  <div class="sub">Boletim de Mercado · ${dataExtenso}</div>
</header>

<h2 class="section section-com-lyket">
  <span>Indicadores Principais</span>
  <div data-lyket-type="updown" data-lyket-namespace="boletim" data-lyket-id="boletim-likedislike-${dataArquivo}"></div>
</h2>

<a href="https://chat.whatsapp.com/GDq8JlzWgwHJAspiVDcwSZ" target="_blank" rel="noopener noreferrer" class="grupo-whatsapp-card">
  <img src="https://api.qrserver.com/v1/create-qr-code/?size=90x90&data=https%3A%2F%2Fchat.whatsapp.com%2FGDq8JlzWgwHJAspiVDcwSZ" alt="QR Code do grupo do WhatsApp" width="70" height="70" />
  <span>
    <strong>Entre no grupo do WhatsApp</strong>
    <small>Dividendos | Viver de Renda — aponte a câmera ou toque aqui</small>
  </span>
</a>

<div class="grid">
  ${indicadoresHtml}
</div>

<h2 class="section">Destaques da Bolsa</h2>
<div class="movers">
  ${destaquesHtml}
</div>

<h2 class="section">FIIs - Maiores Dividend Yield</h2>
<table>
  <tr><th>Ticker</th><th>DY (12m)</th><th>Preço</th><th>Nome</th></tr>
  ${tabelaFiisHtml}
</table>

<h2 class="section">Ações - Maiores Dividend Yield</h2>
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

// ---------- Notificação por e-mail (opt-in) ----------
// Depois de gerar o boletim, avisa por e-mail quem marcou "quero receber"
// no site (tabela assinantes_boletim, opt-in — ver
// sql/criar-tabela-assinantes-boletim.sql). Usa as MESMAS credenciais já
// configuradas nos Secrets do robô Python pros Alertas de Preço
// (SUPABASE_SERVICE_ROLE_KEY e RESEND_API_KEY) — não precisa configurar
// nada novo. Se essas variáveis não estiverem definidas, essa etapa é
// pulada silenciosamente, sem quebrar a geração do boletim.
const SUPABASE_URL = 'https://mzknjnupizprfatfmxqg.supabase.co';
const REMETENTE_BOLETIM = 'Dividendos | Viver de Renda <boletim@mail.viverderenda.dev.br>';

// Resumo curto (só os índices mais relevantes) pro corpo do e-mail — não
// é o boletim inteiro, só um gancho pra pessoa clicar e ver o resto.
function montarResumoIndicesEmail(indices) {
  const definicoesResumo = [
    { chaves: ['IBOVESPA'], label: 'Ibovespa' },
    { chaves: ['SELIC'], label: 'Selic' },
    { chaves: ['DÓLAR', 'DOLAR', 'USD/BRL'], label: 'Dólar' },
    { chaves: ['IFIX'], label: 'IFIX' },
  ];
  const linhas = definicoesResumo.map(({ chaves, label }) => {
    const item = buscarIndicador(indices, ...chaves);
    if (!item) return null;
    if (typeof item.valor === 'number') {
      const variacao = fmtVariacao(item);
      return `<tr><td style="padding:4px 12px 4px 0;color:#666;">${label}</td><td style="padding:4px 0;font-weight:700;">${fmtValorIndicador(item)}<span style="color:${variacao.classe === 'up' ? '#1a9c4d' : '#c0392b'};"> ${variacao.texto}</span></td></tr>`;
    }
    if (typeof item.valor_pct === 'number') {
      const sufixo = (label === 'Selic' || label === 'CDI') ? '% a.a.' : '%';
      return `<tr><td style="padding:4px 12px 4px 0;color:#666;">${label}</td><td style="padding:4px 0;font-weight:700;">${item.valor_pct.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${sufixo}</td></tr>`;
    }
    return null;
  }).filter(Boolean);
  return linhas.length > 0
    ? `<table style="border-collapse:collapse;">${linhas.join('')}</table>`
    : '<p>Confira os índices completos no link abaixo.</p>';
}

// Busca todos os assinantes ativos via a view assinantes_boletim_com_email
// (usa a service_role key, que ignora RLS de propósito — é um script de
// backend confiável, não o navegador de ninguém).
async function buscarAssinantesBoletim(serviceRoleKey) {
  const url = `${SUPABASE_URL}/rest/v1/assinantes_boletim_com_email?select=email`;
  const resp = await fetch(url, {
    headers: {
      apikey: serviceRoleKey,
      Authorization: `Bearer ${serviceRoleKey}`,
    },
  });
  if (!resp.ok) throw new Error(`Supabase respondeu ${resp.status} ao buscar assinantes`);
  const linhas = await resp.json();
  return linhas.map(l => l.email).filter(Boolean);
}

async function enviarEmailBoletim(resendKey, destinatario, dataExtenso, resumoHtml, urlBoletim) {
  const corpoHtml = `
    <h2>Boletim de Mercado — ${dataExtenso}</h2>
    <p>Resumo de hoje:</p>
    ${resumoHtml}
    <p style="margin-top:20px;">
      <a href="${urlBoletim}" style="background:#1a73e8;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;display:inline-block;">Ver boletim completo</a>
    </p>
    <p style="margin-top:24px;font-size:12px;color:#888;">
      Você recebeu este e-mail porque marcou "Quero receber o Boletim de Mercado por e-mail" na sua conta em
      <a href="https://viverderenda.dev.br/">viverderenda.dev.br</a>.
      Pra parar de receber, entre na sua conta (botão "Entrar" no topo do site) e desmarque essa opção.
    </p>`;

  const resp = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${resendKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: REMETENTE_BOLETIM,
      to: [destinatario],
      subject: `Boletim de Mercado — ${dataExtenso}`,
      html: corpoHtml,
    }),
  });
  if (!resp.ok) {
    const corpoErro = await resp.text().catch(() => '');
    throw new Error(`Resend respondeu ${resp.status}: ${corpoErro}`);
  }
}

async function notificarAssinantes({ dataExtenso, nomeArquivo, indices }) {
  const serviceRoleKey = (process.env.SUPABASE_SERVICE_ROLE_KEY || '').trim();
  const resendKey = (process.env.RESEND_API_KEY || '').trim();

  if (!serviceRoleKey || !resendKey) {
    console.log('[boletim] Notificação por e-mail pulada (SUPABASE_SERVICE_ROLE_KEY e/ou RESEND_API_KEY não configurados nos Secrets).');
    return;
  }

  let emails;
  try {
    emails = await buscarAssinantesBoletim(serviceRoleKey);
  } catch (e) {
    console.warn(`[boletim] Aviso: falha ao buscar assinantes do boletim: ${e.message}`);
    return;
  }

  if (emails.length === 0) {
    console.log('[boletim] Nenhum assinante do boletim por e-mail no momento.');
    return;
  }

  const resumoHtml = montarResumoIndicesEmail(indices);
  const urlBoletim = `https://viverderenda.dev.br/boletins/${nomeArquivo}`;

  let enviados = 0;
  for (const email of emails) {
    try {
      await enviarEmailBoletim(resendKey, email, dataExtenso, resumoHtml, urlBoletim);
      enviados++;
    } catch (e) {
      console.warn(`[boletim] Aviso: falha ao enviar e-mail do boletim para ${email}: ${e.message}`);
    }
  }
  console.log(`[boletim] E-mail enviado para ${enviados}/${emails.length} assinante(s).`);
}

// ---------- Retenção: apaga boletins com mais de 7 dias ----------
// Reconciliação (parte 2): o caminho inverso do de cima — se um arquivo
// foi apagado manualmente da pasta (ex: durante um teste), a entrada dele
// continuava presa no index.json para sempre, e o site continuava
// mostrando um boletim que não existe mais. Aqui removemos do índice
// qualquer entrada cujo arquivo .html não exista de fato na pasta.
function removerEntradasSemArquivo(indiceAtual) {
  const mantidos = indiceAtual.filter(entrada => {
    const existe = fs.existsSync(path.join(PASTA_BOLETINS, entrada.arquivo));
    if (!existe) console.log(`[boletim] Removido do índice (arquivo não existe mais na pasta): ${entrada.arquivo}`);
    return existe;
  });
  return mantidos;
}

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

// Reconciliação: o site só enxerga os boletins que estão listados no
// index.json (ele não lê a pasta diretamente). Se algum arquivo
// "Boletim_de_Mercado_DD-MM-AAAA.html" for colocado manualmente na pasta
// (ex: para teste) sem passar pelo script, ele fica "invisível" pro site.
// Esta função varre a pasta e adiciona ao índice qualquer arquivo válido
// que ainda não esteja lá, usando a própria data no nome do arquivo.
function reconciliarComPasta(indiceAtual) {
  const regex = /^Boletim_de_Mercado_(\d{2})-(\d{2})-(\d{4})\.html$/;
  const arquivosNaPasta = fs.readdirSync(PASTA_BOLETINS).filter(f => regex.test(f));
  const jaNoIndice = new Set(indiceAtual.map(e => e.arquivo));

  for (const arquivo of arquivosNaPasta) {
    if (jaNoIndice.has(arquivo)) continue;

    const [, dd, mm, yyyy] = arquivo.match(regex);
    // Meio-dia UTC-3 (meio-dia de Brasília) evita qualquer problema de
    // fuso horário na hora de calcular a idade do arquivo depois.
    const dataIso = new Date(`${yyyy}-${mm}-${dd}T12:00:00-03:00`).toISOString();

    indiceAtual.push({
      arquivo,
      dataIso,
      dataExibicao: `${dd}-${mm}-${yyyy}`,
      labelDia: formatarDataExtenso(new Date(`${yyyy}-${mm}-${dd}T12:00:00-03:00`)),
    });
    console.log(`[boletim] Reconciliado (achado na pasta, adicionado ao índice): ${arquivo}`);
  }

  return indiceAtual;
}

// ---------- Execução principal ----------
async function main() {
  fs.mkdirSync(PASTA_BOLETINS, { recursive: true });

  const agora = agoraSaoPaulo();
  const dataArquivo = formatarDataArquivo(agora); // DD-MM-AAAA
  const dataExtenso = formatarDataExtenso(agora);

  const indices = lerJsonSeExistir(path.join(RAIZ, 'indices.json'), []);
  const noticiasRaw = lerJsonSeExistir(path.join(RAIZ, 'noticias.json'), { destaques: [], top3: [] });
  const ranking = lerJsonSeExistir(path.join(RAIZ, 'ranking.json'), {});

  // Compatibilidade: o coletor de notícias passou a gerar dois grupos
  // separados ({ destaques, top3 }) em vez de uma lista única — mas se por
  // algum motivo o noticias.json ainda estiver no formato antigo (lista
  // simples, de antes dessa mudança, ou de uma execução anterior do robô
  // que ainda não rodou com a versão nova), usamos a mesma lista pros dois
  // blocos, em vez de quebrar a geração do boletim.
  const noticiasDestaques = Array.isArray(noticiasRaw) ? noticiasRaw : (noticiasRaw.destaques || []);
  const noticiasTop3 = Array.isArray(noticiasRaw) ? noticiasRaw : (noticiasRaw.top3 || []);

  const indicadoresHtml = montarIndicadoresHtml(indices);
  const tabelaFiisHtml = montarTabelaFiis(ranking);
  const tabelaAcoesHtml = montarTabelaAcoes(ranking);
  const noticiasHtml = montarNoticiasHtml(noticiasTop3);
  const { destaques, alerta } = carregarEditorial(noticiasDestaques);
  const destaquesHtml = montarDestaquesHtml(destaques);

  const html = montarHtml({
    dataExtenso,
    dataArquivo,
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

  indiceAtual = reconciliarComPasta(indiceAtual);
  indiceAtual = removerEntradasSemArquivo(indiceAtual);
  indiceAtual = limparBoletinsAntigos(indiceAtual, agora.getTime());
  // Mantém sempre ordenado do mais novo pro mais antigo
  indiceAtual.sort((a, b) => new Date(b.dataIso) - new Date(a.dataIso));

  fs.writeFileSync(caminhoIndice, JSON.stringify(indiceAtual, null, 2), 'utf-8');
  console.log(`[boletim] Índice atualizado: boletins/index.json (${indiceAtual.length} boletim(ns) na semana)`);

  await notificarAssinantes({ dataExtenso, nomeArquivo, indices });
}

main().catch(erro => {
  console.error('[boletim] Erro fatal:', erro);
  process.exit(1);
});
