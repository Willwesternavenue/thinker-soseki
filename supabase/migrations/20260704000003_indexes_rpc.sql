-- インデックスとRPC関数(仕様12章)
-- MVP規模(数千チャンク)ではpgvectorは正確検索(ANNインデックスなし)。
-- 規模拡大後にHNSWを導入する。

-- related_thought_ids(派生列)の配列検索用GINインデックス
create index source_chunks_related_thought_ids_gin
  on public.source_chunks using gin (related_thought_ids);

-- PGroonga 日本語全文検索インデックス
create index source_chunks_text_pgroonga
  on public.source_chunks using pgroonga (text);

-- よく使うフィルタ
create index source_chunks_source_id_idx on public.source_chunks (source_id);
create index thought_questions_status_idx on public.thought_questions (person_id, status);
create index thought_cards_person_thought_idx on public.thought_cards (person_id, thought_id);
create index thought_evidence_links_thought_idx on public.thought_evidence_links (person_id, thought_id);
create index thought_evidence_links_chunk_idx on public.thought_evidence_links (chunk_id);
create index chat_messages_session_idx on public.chat_messages (session_id, created_at);
create index chat_sessions_user_idx on public.chat_sessions (user_id, updated_at desc);
create index ingestion_jobs_status_idx on public.ingestion_jobs (status, created_at);
create index answer_traces_fallback_idx on public.answer_traces (fallback_card_used, created_at);

-- 12.1 質問対応情報のベクトル検索
create or replace function public.match_thought_questions(
  query_embedding extensions.vector(1536),
  target_person_id text default 'x_shigyo',
  match_count int default 20
)
returns table (
  question_id text,
  target_thought_id text,
  target_card_id text,
  question text,
  intent text,
  answer_direction text,
  similarity float
)
language sql stable
set search_path = extensions, public, pg_temp
as $$
  select
    question_id,
    target_thought_id,
    target_card_id,
    question,
    intent,
    answer_direction,
    1 - (embedding <=> query_embedding) as similarity
  from public.thought_questions
  where status = 'active'
    and person_id = target_person_id
    and embedding is not null
  order by embedding <=> query_embedding
  limit match_count;
$$;

-- 12.2 thought_idで絞った原典チャンクのベクトル検索
create or replace function public.match_source_chunks_by_thoughts(
  query_embedding extensions.vector(1536),
  thought_ids text[],
  target_person_id text default 'x_shigyo',
  match_count int default 20
)
returns table (
  chunk_id text,
  source_id text,
  chapter_title text,
  section_title text,
  source_page int,
  printed_page int,
  text text,
  summary text,
  related_thought_ids text[],
  evidence_roles text[],
  verbatim boolean,
  similarity float
)
language sql stable
set search_path = extensions, public, pg_temp
as $$
  select
    chunk_id,
    source_id,
    chapter_title,
    section_title,
    source_page,
    printed_page,
    text,
    summary,
    related_thought_ids,
    evidence_roles,
    verbatim,
    1 - (embedding <=> query_embedding) as similarity
  from public.source_chunks
  where status = 'active'
    and person_id = target_person_id
    and embedding is not null
    and related_thought_ids && thought_ids
  order by embedding <=> query_embedding
  limit match_count;
$$;

-- PGroonga 全文検索(thought_idフィルタは任意)
create or replace function public.search_source_chunks_fulltext(
  query_text text,
  thought_ids text[] default null,
  target_person_id text default 'x_shigyo',
  match_count int default 20
)
returns table (
  chunk_id text,
  source_id text,
  chapter_title text,
  section_title text,
  source_page int,
  printed_page int,
  text text,
  summary text,
  related_thought_ids text[],
  evidence_roles text[],
  verbatim boolean,
  score float
)
language sql stable
set search_path = extensions, public, pg_temp
as $$
  select
    chunk_id,
    source_id,
    chapter_title,
    section_title,
    source_page,
    printed_page,
    text,
    summary,
    related_thought_ids,
    evidence_roles,
    verbatim,
    pgroonga_score(tableoid, ctid)::float as score
  from public.source_chunks
  where status = 'active'
    and person_id = target_person_id
    and text &@~ query_text
    and (thought_ids is null or related_thought_ids && thought_ids)
  order by score desc
  limit match_count;
$$;

-- 派生列の再生成: thought_evidence_links(正本)から source_chunks.related_thought_ids を同期
-- 承認・リンク編集後のインデックス更新(仕様6.11)で呼ぶ
create or replace function public.rebuild_related_thought_ids(
  target_person_id text default 'x_shigyo'
)
returns void
language sql
as $$
  update public.source_chunks sc
  set related_thought_ids = coalesce(agg.tids, '{}')
  from (
    select c.chunk_id,
           (select array_agg(distinct l.thought_id)
              from public.thought_evidence_links l
             where l.chunk_id = c.chunk_id) as tids
    from public.source_chunks c
    where c.person_id = target_person_id
  ) agg
  where sc.chunk_id = agg.chunk_id
    and sc.related_thought_ids is distinct from coalesce(agg.tids, '{}');
$$;
