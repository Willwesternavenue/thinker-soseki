-- コーパスの用途分離を検索へ効かせる(C-T7)。
-- 正本仕様: docs/CORPUS_T1_SPEC.md §5.1・§6 / 上位指示 §10。
--
-- 方針(指示書§1.1「既存Thinkerを壊さない」):
-- 既存RPCへ **default 付きの新パラメータ**を足した新版を create or replace で定義する。
-- default は「絞り込まない」なので、思想モードの既存呼び出しは**無変更で従来どおり**動く。
-- 論理Index(author_thought_core_index 等8種)は物理分割せず、この絞り込みの
-- プリセットとしてコード側に持つ。

-- ⚠️ パラメータを増やすと `create or replace` は**置換ではなく多重定義**になる。
-- 旧シグネチャが残ると PostgREST が候補を選べず、既存の3引数呼び出しが
-- PGRST203「Could not choose the best candidate function」で失敗する。
-- そのため旧版を明示的に落としてから作り直す(実DBで確認した)。
drop function if exists public.match_source_chunks_all(
  extensions.vector(1536), text, int);
drop function if exists public.search_source_chunks_fulltext(
  text, text[], text, int);

-- ── 1. 思想IDで絞らないベクトル検索(コーパス絞り込み対応版) ──
create or replace function public.match_source_chunks_all(
  query_embedding extensions.vector(1536),
  target_person_id text default 'natsume_soseki',
  match_count int default 20,
  -- 追加。null = 従来どおり絞らない
  target_corpus_roles text[] default null,
  target_speaker_roles text[] default null,
  -- 版違いで同じ段落を重複して返さないための絞り込み(仕様§1.3)
  primary_edition_only boolean default false
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
  similarity float,
  -- 追加。回答時に「作者本人の発言か、作中人物・語り手か」を区別して示すため(指示書§10.1)
  speaker_role text,
  corpus_role text
)
language sql stable
set search_path = extensions, public, pg_temp
as $$
  select
    c.chunk_id,
    c.source_id,
    c.chapter_title,
    c.section_title,
    c.source_page,
    c.printed_page,
    c.text,
    c.summary,
    c.related_thought_ids,
    c.evidence_roles,
    c.verbatim,
    1 - (c.embedding <=> query_embedding) as similarity,
    c.speaker_role,
    s.corpus_role
  from public.source_chunks c
  join public.sources s on s.source_id = c.source_id
  left join public.work_editions e on e.edition_id = s.edition_id
  where c.status = 'active'
    and c.person_id = target_person_id
    and c.embedding is not null
    -- 絞り込みは「指定があれば効く」。null なら従来どおり全件
    and (target_corpus_roles is null or s.corpus_role = any(target_corpus_roles))
    and (target_speaker_roles is null or c.speaker_role = any(target_speaker_roles))
    -- 版が紐づかない文書(既存の思想モードで投入したもの)は除外しない
    and (
      not primary_edition_only
      or s.edition_id is null
      or e.is_primary_retrieval_edition
    )
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- ── 2. 全文検索(PGroonga)も同じ絞り込みに対応させる ──
create or replace function public.search_source_chunks_fulltext(
  query_text text,
  thought_ids text[] default null,
  target_person_id text default 'natsume_soseki',
  match_count int default 20,
  target_corpus_roles text[] default null,
  target_speaker_roles text[] default null,
  primary_edition_only boolean default false
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
  score float,
  speaker_role text,
  corpus_role text
)
language sql stable
set search_path = extensions, public, pg_temp
as $$
  select
    c.chunk_id,
    c.source_id,
    c.chapter_title,
    c.section_title,
    c.source_page,
    c.printed_page,
    c.text,
    c.summary,
    c.related_thought_ids,
    c.evidence_roles,
    c.verbatim,
    pgroonga_score(c.tableoid, c.ctid)::float as score,
    c.speaker_role,
    s.corpus_role
  from public.source_chunks c
  join public.sources s on s.source_id = c.source_id
  left join public.work_editions e on e.edition_id = s.edition_id
  where c.status = 'active'
    and c.person_id = target_person_id
    and c.text &@~ query_text
    and (thought_ids is null or c.related_thought_ids && thought_ids)
    and (target_corpus_roles is null or s.corpus_role = any(target_corpus_roles))
    and (target_speaker_roles is null or c.speaker_role = any(target_speaker_roles))
    and (
      not primary_edition_only
      or s.edition_id is null
      or e.is_primary_retrieval_edition
    )
  order by pgroonga_score(c.tableoid, c.ctid) desc
  limit match_count;
$$;

-- ── 3. answer_traces に推論表示の項目を追加(指示書§13) ──
-- 現代の未知質問へ答える場合、原典とAIの外挿を分けて残す。
-- すべて nullable なので既存の書き込み(after() での insert)は無変更で動く。
alter table public.answer_traces
  -- 直接の原典があったか / 何を根拠にしたか
  add column direct_source_ids text[] not null default '{}',
  add column retrieved_claims jsonb not null default '[]',
  -- 発火した規則と、棄却した規則(棄却理由も残す。指示書§18)
  add column activated_rules jsonb not null default '[]',
  add column rejected_rules jsonb not null default '[]',
  -- 原典に無い部分をAIが外挿したか
  add column system_inference text,
  add column inference_confidence text,
  -- 直接資料がない現代質問での留保
  add column abstention_reason text,
  -- どの論理Indexを引いたか(思想/創作/人物のルーティング検証用)
  add column retrieval_route jsonb not null default '{}';

comment on column public.answer_traces.system_inference is
  '原典に直接の根拠が無く、規則からの外挿で答えた部分。原典とAI推論を混同させないため'
  '必ず分けて残す(指示書§13)。文体が似ていることを思想的一致とみなさない。';
