-- 全テーブル定義(仕様5章)
-- ID体系: ドメインIDは意味を持つ文字列(BOOK_001_CH03_012 等)のため text 主キー。
-- チャット系はUUID。

-- 5.1 personas: 人物固有情報をコードから分離(横展開準備)
create table public.personas (
  person_id text primary key,
  display_name text not null,
  system_prompt text not null default '',
  first_person text not null default '俺',
  banned_terms_exact text[] not null default '{}',
  banned_terms_contextual text[] not null default '{}',
  style_rules jsonb not null default '{}',
  quote_policy jsonb not null default '{}',
  safety_policy jsonb not null default '{}',
  fallback_card_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger personas_updated_at before update on public.personas
  for each row execute function public.set_updated_at();

-- 5.2 sources: 原典
create table public.sources (
  source_id text primary key,
  person_id text not null references public.personas(person_id),
  title text not null,
  source_type text not null check (source_type in
    ('book','video_transcript','interview','dialogue','lecture','article','essay','profile','other')),
  author text,
  file_type text,
  language text not null default 'ja',
  priority text not null default 'support' check (priority in
    ('core','important','support','style','archive')),
  status text not null default 'raw',
  original_file_path text,
  clean_text_path text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger sources_updated_at before update on public.sources
  for each row execute function public.set_updated_at();

-- 5.3 source_chunks: 原典本文の意味単位分割
-- related_thought_ids は thought_evidence_links(正本)からの派生列(materialized column)
create table public.source_chunks (
  chunk_id text primary key,
  source_id text not null references public.sources(source_id) on delete cascade,
  person_id text not null references public.personas(person_id),
  chapter_id text,
  chapter_title text,
  section_title text,
  source_page int,
  printed_page int,
  char_start int,
  char_end int,
  locator_note text,
  chunk_type text not null default 'body',
  speaker text,
  -- verbatim: 本人発言そのものかという物理的事実(話者正規化結果から導出)
  verbatim boolean not null default false,
  -- 動画・対談のQAペア用
  question text,
  answer text,
  timestamp_start text,
  timestamp_end text,
  text text not null,
  summary text,
  related_thought_ids text[] not null default '{}',
  evidence_roles text[] not null default '{}',
  chunker_version text not null,
  chunk_hash text not null,
  embedding extensions.vector(1536),
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger source_chunks_updated_at before update on public.source_chunks
  for each row execute function public.set_updated_at();

-- 5.4 chunk_distillations: チャンク単位蒸留(編集的解釈。本人発言として引用禁止)
create table public.chunk_distillations (
  distillation_id text primary key,
  chunk_id text not null references public.source_chunks(chunk_id) on delete cascade,
  summary text,
  keywords text[] not null default '{}',
  claims text[] not null default '{}',
  candidate_thought_ids text[] not null default '{}',
  related_concepts text[] not null default '{}',
  evidence_roles text[] not null default '{}',
  misreading_risks text[] not null default '{}',
  importance text not null default 'normal',
  status text not null default 'draft',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger chunk_distillations_updated_at before update on public.chunk_distillations
  for each row execute function public.set_updated_at();

-- 5.5 source_distillations: 原典単位蒸留
create table public.source_distillations (
  distilled_id text primary key,
  source_id text not null references public.sources(source_id) on delete cascade,
  core_summary text,
  main_themes text[] not null default '{}',
  strong_thought_ids text[] not null default '{}',
  unique_contributions text[] not null default '{}',
  best_used_for text[] not null default '{}',
  not_best_for text[] not null default '{}',
  representative_chunk_ids text[] not null default '{}',
  embedding extensions.vector(1536),
  status text not null default 'draft',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger source_distillations_updated_at before update on public.source_distillations
  for each row execute function public.set_updated_at();

-- 5.6 thought_cards: 思想カード(回答方針の最上位。本人発言そのものではない)
create table public.thought_cards (
  card_id text primary key,
  person_id text not null references public.personas(person_id),
  thought_id text not null,
  title text not null,
  importance text not null default 'normal',
  status text not null default 'draft' check (status in
    ('draft','reviewing','approved','rejected','deprecated')),
  version int not null default 1,
  core_claim text,
  distinctions jsonb not null default '[]',
  answer_policy text[] not null default '{}',
  prohibitions text[] not null default '{}',
  related_thought_ids text[] not null default '{}',
  representative_chunk_ids text[] not null default '{}',
  -- 検索用テキスト(embedding付与対象。ルーターのLLM分類器入力にも使う)
  search_text text,
  embedding extensions.vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- 同一人物・同一thought_idのapprovedカードは1枚のみ(ID直接取得の一意性を保証)
create unique index thought_cards_approved_unique
  on public.thought_cards (person_id, thought_id) where (status = 'approved');
create trigger thought_cards_updated_at before update on public.thought_cards
  for each row execute function public.set_updated_at();

-- thought_cards 編集履歴(approved後の編集も履歴を残す。仕様3.4 / 16.5)
create table public.thought_card_revisions (
  revision_id bigint generated always as identity primary key,
  card_id text not null references public.thought_cards(card_id) on delete cascade,
  version int not null,
  snapshot jsonb not null,
  edited_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);

-- 5.7 thought_questions: 質問対応情報(質問→thought_idの入口)
create table public.thought_questions (
  question_id text primary key,
  person_id text not null references public.personas(person_id),
  question text not null,
  target_thought_id text not null,
  target_card_id text references public.thought_cards(card_id),
  intent text not null default 'definition' check (intent in
    ('definition','misunderstanding','comparison','daily_advice','application',
     'critical_question','example_request','relationship_question')),
  answer_direction text,
  embedding extensions.vector(1536),
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger thought_questions_updated_at before update on public.thought_questions
  for each row execute function public.set_updated_at();

-- 5.8 thought_evidence_links: 思想↔チャンク対応の正本
create table public.thought_evidence_links (
  link_id text primary key,
  person_id text not null references public.personas(person_id),
  thought_id text not null,
  source_id text not null references public.sources(source_id) on delete cascade,
  chunk_id text not null references public.source_chunks(chunk_id) on delete cascade,
  evidence_role text not null check (evidence_role in
    ('definition','distinction','prohibition','example','application',
     'style','biographical','quote','historical','metaphor')),
  strength text not null default 'medium',
  usage text,
  -- quote_allowed: この文脈で引用に使ってよいかという運用判断(verbatimとは分離)
  quote_allowed boolean not null default false,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger thought_evidence_links_updated_at before update on public.thought_evidence_links
  for each row execute function public.set_updated_at();

-- 5.9 concept_aliases: 表記ゆれ・関連語・誤解語(ルーター前処理入力)
create table public.concept_aliases (
  concept_id text not null,
  person_id text not null references public.personas(person_id),
  canonical_label text not null,
  aliases text[] not null default '{}',
  negative_aliases text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (person_id, concept_id)
);
create trigger concept_aliases_updated_at before update on public.concept_aliases
  for each row execute function public.set_updated_at();

-- 5.10 user_profiles
create table public.user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('admin','tester')),
  display_name text,
  created_at timestamptz not null default now(),
  last_login_at timestamptz
);

-- 5.11 chat_sessions
create table public.chat_sessions (
  session_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  person_id text not null references public.personas(person_id),
  title text,
  status text not null default 'active',
  summary text,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger chat_sessions_updated_at before update on public.chat_sessions
  for each row execute function public.set_updated_at();

-- 5.12 chat_messages(削除時は content を null化し deleted_at を入れる。仕様8.4)
create table public.chat_messages (
  message_id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.chat_sessions(session_id) on delete cascade,
  role text not null check (role in ('user','assistant')),
  content text,
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

-- 5.13 answer_traces: 回答時の内部処理ログ(admin参照パネル用)
-- user_query は削除時にハッシュ化、構造データは匿名保持(仕様8.4)
create table public.answer_traces (
  trace_id uuid primary key default gen_random_uuid(),
  message_id uuid references public.chat_messages(message_id) on delete set null,
  person_id text references public.personas(person_id),
  user_query text,
  query_kind text,
  routing_method text not null check (routing_method in
    ('vector','llm_classifier','fallback','none')),
  fallback_card_used boolean not null default false,
  selected_thought_ids text[] not null default '{}',
  retrieved_card_ids text[] not null default '{}',
  retrieved_chunk_ids text[] not null default '{}',
  top_hits jsonb not null default '[]',
  guard_result jsonb not null default '{}',
  created_at timestamptz not null default now()
);

-- 5.14 ingestion_jobs
create table public.ingestion_jobs (
  job_id uuid primary key default gen_random_uuid(),
  source_id text not null references public.sources(source_id) on delete cascade,
  status text not null default 'pending' check (status in
    ('pending','running','succeeded','failed')),
  current_step text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger ingestion_jobs_updated_at before update on public.ingestion_jobs
  for each row execute function public.set_updated_at();

-- 5.15 agent_runs: LLM呼び出しログ(コスト記録)
create table public.agent_runs (
  agent_run_id uuid primary key default gen_random_uuid(),
  job_id uuid references public.ingestion_jobs(job_id) on delete set null,
  agent_name text not null,
  model text,
  input_ref text,
  output_json jsonb,
  status text not null default 'success',
  cost numeric(12,6),
  created_at timestamptz not null default now()
);

-- 5.16 evaluation_logs
create table public.evaluation_logs (
  evaluation_id uuid primary key default gen_random_uuid(),
  user_query text,
  selected_thought_ids text[] not null default '{}',
  answer text,
  scores jsonb not null default '{}',
  issues jsonb not null default '[]',
  created_at timestamptz not null default now()
);
