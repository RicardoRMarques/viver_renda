# Serviços e APIs — Dividendos | Viver de Renda

Documentação de todos os serviços externos, APIs e credenciais usados no
projeto `viverderenda.dev.br`. Guardar isso versionado no repositório
(sem colocar chaves/senhas de verdade aqui — só nomes, URLs e onde cada
credencial está configurada).

---

## 1. Hospedagem e domínio

| Serviço | Uso no projeto | Painel |
|---|---|---|
| **GitHub Pages** | Hospeda o site estático (`index.html`) | Repositório → Settings → Pages |
| **GitHub Actions** | Roda os robôs automáticos (coleta de dados, boletim diário) | Repositório → Actions |
| **Registro.br** | Registro e DNS do domínio `viverderenda.dev.br` | [registro.br](https://registro.br) → Painel do domínio → DNS → Modo Avançado → Configurar Zona DNS |

**Registros DNS importantes hoje:**
- `A` (×4) → aponta pro GitHub Pages (`185.199.108.153` a `.111.153`)
- `CNAME` `www` → `ricardormarques.github.io`
- Registros de e-mail do Resend no subdomínio `mail.viverderenda.dev.br` (DKIM, SPF, MX, DMARC — ver seção 4)

---

## 2. Dados de mercado (índices, cotações, notícias)

| Serviço | Uso no projeto | Onde configurado |
|---|---|---|
| **[HG Brasil — API Finance](https://console.hgbrasil.com/documentation/finance)** | Fonte principal de cotações: Ibovespa, IFIX, Dólar, Euro, Bitcoin, Selic, ações e FIIs individuais (Consulta Rápida, carrossel do topo) | Chave (`CHAVE_HG`) direto no `index.html` (client-side, é uma chave pública/gratuita) e também usada pelo robô Python (`coletar_hgbrasil.py`, variável de ambiente `HGBRASIL_TOKEN` nos Secrets do GitHub) |
| **Investing.com** (RSS) | Notícias de mercado (seção "destaques" do `noticias.json`) | Consumido via RSS público pelo robô, sem chave de API |
| **InfoMoney** (RSS) | Notícias de mercado (seção "top3" do `noticias.json`) | Consumido via RSS público pelo robô, sem chave de API |
| **[FIIs.com.br](https://fiis.com.br/noticias/)** | 1 notícia de destaque sobre Fundos Imobiliários, fixada no topo da seção de notícias | Sem API/RSS pública — o robô faz *scraping* da página (função `_buscar_noticia_fii()` em `coletar_hgbrasil.py`); se o layout do site mudar, essa função pode parar de achar a notícia (falha de forma silenciosa, sem quebrar o resto) |

**Onde os dados coletados viram arquivo:**
- `indices.json` — índices e cotações (robô roda a cada 15 min via `coletar-fiis.yml`)
- `noticias.json` — notícias (mesmo robô, mesmo intervalo)
- `ranking.json` — ranking de Ações/FIIs (1× por dia, 9h Brasília)
- `boletins/` — boletim diário em PDF/HTML (robô `gerar-boletim.js`, seg. a sex., 6h05 Brasília)

---

## 3. Banco de dados e autenticação

| Serviço | Uso no projeto | Painel |
|---|---|---|
| **[Supabase](https://supabase.com)** | Banco de dados (Postgres) + autenticação de usuários (login via Magic Link) | [supabase.com/dashboard](https://supabase.com/dashboard) — projeto **Viverderenda** |

**Tabelas em uso:**
- `historico_indices` — histórico de valores de índices (`ativo`, `valor`, `created_at`) — leitura pública, escrita fechada
- `favoritos_carrossel` — ativos favoritados por cada usuário logado, sincronizados entre aparelhos (`user_id`, `ticker`, `created_at`) — cada usuário só vê/mexe nos próprios (Row Level Security)

**Credenciais usadas no `index.html`:**
- `SUPABASE_URL` — URL pública do projeto (`https://mzknjnupizprfatfmxqg.supabase.co`)
- `SUPABASE_KEY` — **Publishable key** (segura para expor no front-end; a segurança de verdade vem das políticas de RLS de cada tabela)

**Onde configurar/revisar:**
- Tabelas e políticas (RLS): Supabase → Table Editor / SQL Editor
- Credenciais da API: Supabase → Settings → API
- Login (Magic Link): Supabase → Authentication → Providers / Emails

---

## 4. Envio de e-mail (login sem senha)

| Serviço | Uso no projeto | Painel |
|---|---|---|
| **[Resend](https://resend.com)** | Envia os e-mails de login (Magic Link) do Supabase Auth, com o domínio próprio (`mail.viverderenda.dev.br`) em vez do domínio genérico do Supabase | [resend.com/domains](https://resend.com/domains) |

**Configuração:**
- Domínio verificado: `mail.viverderenda.dev.br` (registros DKIM, SPF, MX e DMARC cadastrados no Registro.br — ver seção 1)
- SMTP customizado configurado em: Supabase → Authentication → Emails → SMTP Settings
  - Host: `smtp.resend.com`
  - Port: `465`
  - Username: `resend`
  - Password: API key do Resend (permissão restrita a "Sending access" no domínio `mail.viverderenda.dev.br`)
- Templates de e-mail traduzidos para português em: Supabase → Authentication → Emails → Templates (Magic Link, Confirm Signup, Invite User)

---

## 5. Bibliotecas de front-end (via CDN, sem conta/chave)

Essas são só `<script>` carregados via CDN no `index.html` — não têm painel/conta, é só manter o link funcionando:

| Biblioteca | Uso |
|---|---|
| [Chart.js](https://www.chartjs.org/) | Gráficos interativos nas calculadoras (Juros Compostos, Aposentadoria, Financiamento) |
| [jsPDF](https://github.com/parallax/jsPDF) + [jsPDF-AutoTable](https://github.com/simonbengtsson/jsPDF-AutoTable) | Geração dos relatórios em PDF |
| [SheetJS (xlsx)](https://sheetjs.com/) | Exportação da planilha Excel (Financiamento Imobiliário) |
| [Supabase JS SDK](https://supabase.com/docs/reference/javascript) | Cliente JS para conectar com Supabase (banco + login) |
| [flagcdn.com](https://flagcdn.com/) | Imagens de bandeiras no Conversor de Moedas |

---

## 6. Reações e engajamento

| Serviço | Uso no projeto | Painel |
|---|---|---|
| **[Lyket](https://lyket.dev/)** | Botões de reação (👍 👎 👏) no Boletim de Mercado Diário | Painel do Lyket — chave de API integrada no `index.html` |

---

## 7. Outros

| Serviço | Uso no projeto |
|---|---|
| **Cloudflare** | *(se aplicável — confirmar com Ricardo qual função exata: Worker de chat IA e/ou proxy de DNS/CDN)* |

> ⚠️ Nota: esse item ficou como placeholder — não tive detalhes suficientes na nossa conversa sobre o uso exato do Cloudflare no projeto (Worker? CDN? Zero Trust?). Me confirma o que é pra eu completar essa linha certinho.

---

## Resumo de onde cada credencial mora

| Credencial | Onde fica |
|---|---|
| `HGBRASIL_TOKEN` | GitHub → Settings → Secrets and variables → Actions (robô) + hardcoded no `index.html` (chave pública client-side) |
| Chave do Lyket | Hardcoded no `index.html` (chave pública client-side) |
| `SUPABASE_URL` / `SUPABASE_KEY` (publishable) | Hardcoded no `index.html` (seguro por design — segurança real é via RLS) |
| API key do Resend | Só dentro do painel do Supabase (SMTP Settings) — nunca no código do site |
| Login do Registro.br / Supabase / Resend / GitHub | Gerenciados por Ricardo, fora do repositório |

---

*Última atualização: agosto de 2026. Manter este arquivo atualizado sempre que um novo serviço externo for integrado ao projeto.*
