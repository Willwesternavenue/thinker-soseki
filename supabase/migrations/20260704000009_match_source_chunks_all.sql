-- 思想IDで絞らない原典チャンクのベクトル検索。
-- fact / person_or_work など思想カードに紐づかない質問(ルーティング=none)でも、
-- 原典本文を根拠として取得できるようにするための経路(仕様の穴埋め)。
create or replace function public.match_source_chunks_all(
  query_embedding extensions.vector(1536),
  target_person_id text default 'natsume_soseki',
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
  order by embedding <=> query_embedding
  limit match_count;
$$;
