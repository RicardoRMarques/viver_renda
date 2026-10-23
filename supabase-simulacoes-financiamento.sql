-- ============================================================
-- Simulador de Financiamento Imobiliário — últimas simulações salvas
-- ------------------------------------------------------------
-- Guarda os PARÂMETROS de entrada de cada simulação (não a tabela de
-- amortização mês a mês inteira) — o site recalcula tudo de novo ao
-- "carregar" uma simulação salva, igual faz ao digitar os campos na
-- mão. Até 5 por usuário: o site apaga a mais antiga quando salva a 6ª.
--
-- A coluna "amortizacao" guarda o ESTADO da amortização extra (até 5
-- avulsas + regra de FGTS recorrente), não a lista expandida/mesclada
-- que a simulação usou — é o mesmo formato de estadoAmortizacao no
-- front-end, pra "carregar" poder restaurar exatamente o que a pessoa
-- tinha configurado no modal "Amortizar" (e não só o resultado final).
--
-- Já aplicado no projeto em 23/10/2026 (migração simulacoes_financiamento)
-- e 24/10/2026 (coluna amortizacao). Este arquivo é o histórico
-- versionado e pode ser rodado de novo sem risco: tudo é "if not
-- exists" e as políticas são recriadas.
--
-- RLS é obrigatório aqui: o site usa a chave anônima no navegador, então
-- sem política cada visitante leria as simulações dos outros.
-- ============================================================

create table if not exists public.simulacoes_financiamento (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  valor numeric not null,
  sistema text not null check (sistema in ('sac', 'price')),
  taxa_pct numeric not null,
  periodo text not null check (periodo in ('anual', 'mensal')),
  parcelas integer not null,
  ano_inicio integer not null,
  mes_inicio0 integer not null check (mes_inicio0 between 0 and 11),
  total_pago numeric,
  total_juros numeric,
  primeira_parcela numeric,
  ultima_parcela numeric,
  created_at timestamptz not null default now()
);
comment on table public.simulacoes_financiamento is 'Simulador de Financiamento Imobiliário: últimas simulações salvas por usuário logado (até 5 — o site apaga a mais antiga ao salvar a 6ª).';

alter table public.simulacoes_financiamento add column if not exists amortizacao jsonb;
comment on column public.simulacoes_financiamento.amortizacao is 'Estado da amortização extra aplicada (até 5 avulsas + regra de FGTS recorrente), no mesmo formato de estadoAmortizacao no front-end: {lista: [{ponto,valor,modo}], fgts: {inicio,valor,modo}|null}. Null quando a simulação não tinha amortização extra.';

create index if not exists simulacoes_financiamento_user_idx on public.simulacoes_financiamento (user_id, created_at);

-- ---------- RLS: cada um só enxerga as próprias simulações ----------
alter table public.simulacoes_financiamento enable row level security;

drop policy if exists simfinanc_select_proprio on public.simulacoes_financiamento;
drop policy if exists simfinanc_insert_proprio on public.simulacoes_financiamento;
drop policy if exists simfinanc_delete_proprio on public.simulacoes_financiamento;
create policy simfinanc_select_proprio on public.simulacoes_financiamento for select using ((select auth.uid()) = user_id);
create policy simfinanc_insert_proprio on public.simulacoes_financiamento for insert with check ((select auth.uid()) = user_id);
create policy simfinanc_delete_proprio on public.simulacoes_financiamento for delete using ((select auth.uid()) = user_id);

-- A partir de 30/10/2026 o Supabase para de conceder acesso automático à
-- API de Dados pra tabelas novas — grant explícito, na mesma migração
-- que cria a tabela (ver SERVICOS-E-APIS.md).
grant select, insert, delete on table public.simulacoes_financiamento to authenticated;


-- ============================================================
-- Consultas administrativas (rodar no SQL Editor do painel)
-- ============================================================

-- Quantas simulações cada usuário tem salvas
-- select u.email, count(*) as simulacoes, max(s.created_at) as ultima
--   from public.simulacoes_financiamento s join auth.users u on u.id = s.user_id
--  group by u.email order by ultima desc;

-- Apagar as simulações salvas de um usuário (sem mexer na conta dele)
-- delete from public.simulacoes_financiamento where user_id = '<uuid>';
