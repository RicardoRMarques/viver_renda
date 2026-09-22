-- ============================================================
-- Calculadora de Aportes — tabelas no Supabase
-- ------------------------------------------------------------
-- Guardam, por usuário logado, o que a calculadora mostra:
--   calc_aportes             -> um ativo por linha (posição, peso-alvo,
--                               preço médio, teto e P/VP digitados...)
--   calc_aportes_config      -> valor do aporte e as opções da tela
--   calc_aportes_lancamentos -> compras e vendas (a posição e o preço
--                               médio dos ativos saem daqui)
--
-- Já aplicado no projeto em 21-22/09/2026 (migrações calc_aportes_tabelas,
-- calc_aportes_lancamentos, calc_aportes_pvp_manual, calc_aportes_yield_bazin).
-- Este arquivo é o histórico versionado e pode ser rodado de novo sem
-- risco: tudo é "if not exists" e as políticas são recriadas.
--
-- RLS é obrigatório aqui: o site usa a chave anônima no navegador, então
-- sem política cada visitante leria a carteira dos outros.
-- ============================================================

-- ---------- 1. Ativos ----------
create table if not exists public.calc_aportes (
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null,
  cotas integer not null default 0,
  peso_alvo numeric,
  preco_medio numeric,
  preco_teto numeric,          -- teto digitado pelo usuário (vence o automático)
  proventos_12m numeric,       -- proventos recebidos, informados por ele
  setor text,                  -- setor/segmento digitado (sobrepõe o da fonte)
  preco_manual numeric,        -- preço digitado (ex.: o do home broker agora)
  preco_planilha numeric,      -- preço que veio colado da planilha (reserva)
  teto_planilha numeric,       -- teto que veio colado da planilha (reserva)
  ordem integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (user_id, ticker)
);
comment on table public.calc_aportes is 'Calculadora de Aportes: ativos da carteira de cada usuário (posição, preço médio, peso-alvo, teto manual, proventos 12m).';

-- P/VP: a fonte automática não cobre todo mundo (FI-Infra, por exemplo),
-- e é o P/VP que calcula o preço teto dos fundos.
alter table public.calc_aportes add column if not exists pvp_manual numeric;
alter table public.calc_aportes add column if not exists pvp_planilha numeric;
comment on column public.calc_aportes.pvp_manual is 'P/VP digitado pelo usuário (FI-Infra e outros que a fonte não traz). Usado no teto automático.';

-- ---------- 2. Configuração ----------
create table if not exists public.calc_aportes_config (
  user_id uuid primary key references auth.users(id) on delete cascade,
  aporte numeric,
  respeitar_teto boolean not null default true,
  usar_tudo boolean not null default true,
  pvp_max numeric not null default 1,      -- teto do FII  = VP/cota x este fator
  updated_at timestamptz not null default now()
);
comment on table public.calc_aportes_config is 'Calculadora de Aportes: valor do aporte e opções de cada usuário.';

alter table public.calc_aportes_config add column if not exists yield_bazin numeric not null default 6;
comment on column public.calc_aportes_config.yield_bazin is 'Yield mínimo (%) usado no preço teto de Bazin das ações: teto = dividendos 12m ÷ yield.';

-- ---------- 3. Compras & Vendas ----------
-- O id é gerado no navegador (crypto.randomUUID) para o site poder
-- apagar na nuvem exatamente o lançamento que saiu da tela.
create table if not exists public.calc_aportes_lancamentos (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  data date not null,
  operacao text not null check (operacao in ('compra','venda')),
  ticker text not null,
  quantidade numeric not null check (quantidade > 0),
  preco numeric not null check (preco >= 0),
  taxas numeric not null default 0,   -- taxas/ISS/emolumentos (rateados por dia no site)
  irrf numeric not null default 0,    -- só registro; não entra no custo
  ordem integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table public.calc_aportes_lancamentos is 'Calculadora de Aportes: compras e vendas de cada usuário. Quantidade acumulada e preço médio são calculados no site a partir daqui.';
create index if not exists calc_aportes_lancamentos_user_idx on public.calc_aportes_lancamentos (user_id, data);

-- ---------- 4. RLS: cada um só enxerga as próprias linhas ----------
alter table public.calc_aportes enable row level security;
alter table public.calc_aportes_config enable row level security;
alter table public.calc_aportes_lancamentos enable row level security;

drop policy if exists calc_aportes_select_proprio on public.calc_aportes;
drop policy if exists calc_aportes_insert_proprio on public.calc_aportes;
drop policy if exists calc_aportes_update_proprio on public.calc_aportes;
drop policy if exists calc_aportes_delete_proprio on public.calc_aportes;
create policy calc_aportes_select_proprio on public.calc_aportes for select using ((select auth.uid()) = user_id);
create policy calc_aportes_insert_proprio on public.calc_aportes for insert with check ((select auth.uid()) = user_id);
create policy calc_aportes_update_proprio on public.calc_aportes for update using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy calc_aportes_delete_proprio on public.calc_aportes for delete using ((select auth.uid()) = user_id);

drop policy if exists calc_aportes_config_select_proprio on public.calc_aportes_config;
drop policy if exists calc_aportes_config_insert_proprio on public.calc_aportes_config;
drop policy if exists calc_aportes_config_update_proprio on public.calc_aportes_config;
drop policy if exists calc_aportes_config_delete_proprio on public.calc_aportes_config;
create policy calc_aportes_config_select_proprio on public.calc_aportes_config for select using ((select auth.uid()) = user_id);
create policy calc_aportes_config_insert_proprio on public.calc_aportes_config for insert with check ((select auth.uid()) = user_id);
create policy calc_aportes_config_update_proprio on public.calc_aportes_config for update using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy calc_aportes_config_delete_proprio on public.calc_aportes_config for delete using ((select auth.uid()) = user_id);

drop policy if exists calc_lanc_select_proprio on public.calc_aportes_lancamentos;
drop policy if exists calc_lanc_insert_proprio on public.calc_aportes_lancamentos;
drop policy if exists calc_lanc_update_proprio on public.calc_aportes_lancamentos;
drop policy if exists calc_lanc_delete_proprio on public.calc_aportes_lancamentos;
create policy calc_lanc_select_proprio on public.calc_aportes_lancamentos for select using ((select auth.uid()) = user_id);
create policy calc_lanc_insert_proprio on public.calc_aportes_lancamentos for insert with check ((select auth.uid()) = user_id);
create policy calc_lanc_update_proprio on public.calc_aportes_lancamentos for update using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy calc_lanc_delete_proprio on public.calc_aportes_lancamentos for delete using ((select auth.uid()) = user_id);


-- ============================================================
-- Consultas administrativas (rodar no SQL Editor do painel — é o
-- privilégio de admin que lê auth.users; a chave do site não lê).
-- ============================================================

-- Carteira de cada usuário, com e-mail
-- select u.email, a.ticker, a.cotas, a.preco_medio, a.peso_alvo, a.updated_at
--   from public.calc_aportes a join auth.users u on u.id = a.user_id
--  order by u.email, a.ordem;

-- Quem usa a calculadora, quanto aporta e quantos lançamentos tem
-- select u.email,
--        (select count(*) from public.calc_aportes x where x.user_id = u.id) as ativos,
--        (select count(*) from public.calc_aportes_lancamentos l where l.user_id = u.id) as lancamentos,
--        c.aporte, c.pvp_max, c.yield_bazin, c.updated_at
--   from auth.users u
--   left join public.calc_aportes_config c on c.user_id = u.id
--  where exists (select 1 from public.calc_aportes x where x.user_id = u.id)
--  order by c.updated_at desc nulls last;

-- Ativos mais usados na calculadora
-- select ticker, count(distinct user_id) as pessoas, sum(cotas) as cotas
--   from public.calc_aportes group by ticker order by pessoas desc, cotas desc;

-- Apagar os dados da calculadora de um usuário (sem mexer na conta dele)
-- delete from public.calc_aportes_lancamentos where user_id = '<uuid>';
-- delete from public.calc_aportes            where user_id = '<uuid>';
-- delete from public.calc_aportes_config     where user_id = '<uuid>';
