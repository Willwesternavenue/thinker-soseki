-- RLS(Row Level Security)ポリシー(仕様9章、Phase 1必須)
-- API層のチェックだけに依存せず、DB側でも強制する。
-- Python Worker / Next.jsサーバー内部処理は service_role キーで動作(RLSバイパス)。

-- ロール判定関数(security definer で user_profiles 自身のRLS再帰を回避)
create or replace function public.is_admin()
returns boolean
language sql stable security definer
set search_path = public
as $$
  select exists (
    select 1 from public.user_profiles
    where user_id = auth.uid() and role = 'admin'
  );
$$;

-- 全テーブルでRLS有効化
alter table public.personas enable row level security;
alter table public.sources enable row level security;
alter table public.source_chunks enable row level security;
alter table public.chunk_distillations enable row level security;
alter table public.source_distillations enable row level security;
alter table public.thought_cards enable row level security;
alter table public.thought_card_revisions enable row level security;
alter table public.thought_questions enable row level security;
alter table public.thought_evidence_links enable row level security;
alter table public.concept_aliases enable row level security;
alter table public.user_profiles enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.answer_traces enable row level security;
alter table public.ingestion_jobs enable row level security;
alter table public.agent_runs enable row level security;
alter table public.evaluation_logs enable row level security;

-- ── adminのみアクセス可能なテーブル(testerは直接アクセス不可) ──
-- sources / thought_cards / answer_traces 等へのtester直接アクセスを遮断(仕様9章)

create policy "admin all" on public.personas
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.sources
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.source_chunks
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.chunk_distillations
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.source_distillations
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.thought_cards
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.thought_card_revisions
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.thought_questions
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.thought_evidence_links
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.concept_aliases
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.ingestion_jobs
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.agent_runs
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin read" on public.answer_traces
  for select using (public.is_admin());
create policy "admin all" on public.evaluation_logs
  for all using (public.is_admin()) with check (public.is_admin());

-- ── user_profiles: 自分の行のみ閲覧、adminは全件 ──
create policy "own profile read" on public.user_profiles
  for select using (user_id = auth.uid() or public.is_admin());
create policy "admin manage profiles" on public.user_profiles
  for all using (public.is_admin()) with check (public.is_admin());

-- ── chat_sessions: 自分のセッションのみ(tester)、adminは全件閲覧 ──
create policy "own sessions" on public.chat_sessions
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "admin read sessions" on public.chat_sessions
  for select using (public.is_admin());

-- ── chat_messages: 自分のセッション経由のみ、adminは全件閲覧 ──
create policy "own messages" on public.chat_messages
  for all using (
    exists (
      select 1 from public.chat_sessions s
      where s.session_id = chat_messages.session_id
        and s.user_id = auth.uid()
    )
  ) with check (
    exists (
      select 1 from public.chat_sessions s
      where s.session_id = chat_messages.session_id
        and s.user_id = auth.uid()
    )
  );
create policy "admin read messages" on public.chat_messages
  for select using (public.is_admin());
