-- 漱石コーパス層(C-T2a)。作品/版の正規化と、思想・創作を分離するためのタグ基盤。
-- 正本仕様: docs/CORPUS_T1_SPEC.md / 上位指示: docs/received/CORPUS_SPEC_v0.1_received.pdf
--
-- 方針(指示書§1.1「既存Thinkerを壊さない」):
-- - 既存テーブルへは nullable もしくは default 付きの列追加のみ。既存の insert/select は
--   影響を受けず、既存データの意味も変えない。
-- - 検索でフィルタする値(corpus_role / speaker_role 等)は実カラムにして索引を張る。
--   別テーブルに逃がすと vector検索RPCに join が必要になり、既存の思想モードRPCを
--   触ることになるため。長い尾(底本・注記など)は jsonb にまとめる。
-- - RLSは既存規約どおり「有効・ポリシー無し(deny-all)」。アクセスは service_role のみ。
-- - rollback は本ファイルで追加した4テーブルの drop と、追加列の drop で足りる。

-- ── 1. canonical_works: 版をまたぐ作品の同一性(C-T1 §1.1) ──
-- 青空文庫の漱石は113エントリあるが、版違いを束ねると107作品になる。
-- ⚠️ 作品名の完全一致で束ねてはいけない。「吾輩は猫である」(000789 新字新仮名)と
-- 「吾輩ハ猫デアル」(000790 旧字旧仮名)はタイトル文字列自体が異なる。
-- 同定は 作品名読み → 正規化タイトル → 本文hash → 人手確認 の順で行う(C-T1 §1.2)。
create table public.canonical_works (
  canonical_work_id text primary key,
  person_id text not null references public.personas(person_id),
  canonical_title text not null,
  -- 同定の第一候補。青空文庫CSVの「作品名読み」。表記が割れても読みは一致する
  canonical_title_reading text,
  title_variants text[] not null default '{}',
  -- 初出。実データの充足率は48%(113件中54件)しかないため nullable
  first_publication text,
  ndc text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index canonical_works_person_idx on public.canonical_works (person_id);
create index canonical_works_reading_idx on public.canonical_works (canonical_title_reading);
create trigger canonical_works_updated_at before update on public.canonical_works
  for each row execute function public.set_updated_at();

-- ── 2. work_editions: 版(青空文庫の1エントリ = 1版) ──
create table public.work_editions (
  edition_id text primary key,
  canonical_work_id text not null references public.canonical_works(canonical_work_id),
  aozora_work_id text not null,
  -- 新字新仮名 / 新字旧仮名 / 旧字旧仮名。実データの分布は 84 / 15 / 14
  orthography text not null,
  work_status text not null default 'published'
    check (work_status in ('published', 'in_progress')),
  -- 既定の検索で使う版。原則として読みやすい新字新仮名版(指示書§2.3)
  is_primary_retrieval_edition boolean not null default false,
  card_url text,
  -- テキストファイルが存在しない版がある(000790 吾輩ハ猫デアル)。nullable
  text_file_url text,
  text_encoding text,
  text_charset text,
  -- 底本・親本・入力に使用した版など(指示書§2.4)。項目ごとに充足率が違うため jsonb
  bottom_text jsonb not null default '{}',
  input_by text,
  -- 校正者は1件欠落あり(113件中112件)。nullable
  proofread_by text,
  aozora_published_at date,
  aozora_updated_at date,
  copyright_status text not null default 'public_domain',
  license_note text,
  -- 再現性のため取得の事実を残す(指示書§15-19)
  retrieved_at timestamptz,
  content_sha256 text,
  parser_version text,
  duplicate_of text references public.work_editions(edition_id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- 1作品につき既定検索版は最大1件(版違いで同じ段落を重複して返さないため)
create unique index work_editions_primary_unique
  on public.work_editions (canonical_work_id) where (is_primary_retrieval_edition);
create index work_editions_work_idx on public.work_editions (canonical_work_id, work_status);
create index work_editions_aozora_idx on public.work_editions (aozora_work_id);
create trigger work_editions_updated_at before update on public.work_editions
  for each row execute function public.set_updated_at();

-- ── 3. aozora_manifest_entries: 一覧の記録(作業中を含む。指示書§2.1) ──
-- 公式CSVには公開作品しか載らない。作業中8件は作家別HTMLページからのみ取得できる。
-- 作業中は本文取得・Index登録・L2/L3候補生成を行わず、記録だけ残す。
create table public.aozora_manifest_entries (
  entry_id text primary key,
  person_id text not null references public.personas(person_id),
  aozora_work_id text not null,
  title text not null,
  orthography text,
  work_status text not null check (work_status in ('published', 'in_progress')),
  listed_at timestamptz not null default now(),
  source_page_url text
);
create index aozora_manifest_person_idx
  on public.aozora_manifest_entries (person_id, work_status);

-- ── 4. canonical_work_review_queue: 作品同定が割れた場合の人手確認(C-T1 §1.2 段4) ──
-- 自動統合しない。読み・正規化タイトル・本文hashのいずれでも決まらない場合はここへ。
create table public.canonical_work_review_queue (
  queue_id uuid primary key default gen_random_uuid(),
  person_id text not null references public.personas(person_id),
  aozora_work_ids text[] not null,
  reason text not null,
  status text not null default 'open' check (status in ('open', 'resolved', 'dismissed')),
  resolved_canonical_work_id text references public.canonical_works(canonical_work_id),
  created_at timestamptz not null default now()
);
create index canonical_work_review_open_idx
  on public.canonical_work_review_queue (person_id, status);

-- ── 5. sources への追加(文書単位のタグ。指示書§7.1) ──
-- すべて nullable または default 付き。既存の思想モードの読み書きは影響を受けない。
alter table public.sources
  add column edition_id text references public.work_editions(edition_id),
  -- 全作品を一つのIndexへ入れないための論理的な用途分離(指示書§3)
  add column corpus_role text check (corpus_role in (
    'core_thought', 'supporting_thought', 'creative_grammar', 'character_judgment',
    'narrative_reference', 'style_reference', 'biographical_context',
    'validation_only', 'excluded')),
  -- ⚠️ NDCだけで決めない(指示書§7.2)。実データでもNDC 914(随筆)47件に講演・評論が同居
  add column document_genre text check (document_genre in (
    'lecture', 'essay', 'criticism', 'literary_theory', 'preface', 'afterword',
    'letter', 'interview', 'memoir', 'travelogue', 'novel', 'short_story',
    'sketch', 'advertisement', 'announcement', 'other')),
  add column authority_level text check (authority_level in (
    'author_direct', 'author_contextual', 'fictional_indirect',
    'third_party', 'editorial', 'unknown')),
  -- 今回は aozora のみ取り込むが、将来 NDL 等を additive で足せるようにしておく
  add column source_provider text not null default 'manual_upload' check (source_provider in (
    'aozora', 'ndl', 'manual_upload', 'licensed_source')),
  add column corpus_metadata jsonb not null default '{}';
create index sources_corpus_role_idx on public.sources (person_id, corpus_role);
create index sources_edition_idx on public.sources (edition_id);

-- ── 6. source_chunks への追加(チャンク単位のタグ。指示書§7.4) ──
-- ⚠️ 既存の speaker(clean.pyが付ける話者ラベルの正規化結果)と、新規の speaker_role
-- (役割の分類値)は別物。前者は文字列、後者は下記のenum。
alter table public.source_chunks
  add column speaker_role text check (speaker_role in (
    'author_direct', 'narrator', 'character', 'quoted_person',
    'interviewer', 'editor', 'unknown')),
  add column character_id text,
  add column addressee text,
  add column claim_type text check (claim_type in (
    'normative_claim', 'descriptive_observation', 'conceptual_distinction',
    'priority_claim', 'prohibition', 'exception', 'autobiographical_report',
    'historical_report', 'hypothetical_example', 'quotation',
    'literary_analysis', 'fictional_statement', 'meta_commentary')),
  add column assertion_status text check (assertion_status in (
    'asserted', 'attributed', 'hypothetical', 'questioned',
    'ironic', 'ambiguous', 'rejected_by_author')),
  -- ⚠️ ここの candidate は「チャンクの適格性」。カードの承認状態(draft)とは別概念
  add column thought_eligibility text check (thought_eligibility in
    ('candidate', 'support', 'excluded')),
  add column creative_eligibility text check (creative_eligibility in
    ('candidate', 'support', 'excluded')),
  add column is_quotation boolean not null default false,
  add column is_hypothetical boolean not null default false,
  add column is_ironic boolean not null default false,
  add column tag_confidence numeric,
  add column classification_reason text,
  add column tag_review_status text not null default 'unreviewed' check (tag_review_status in
    ('unreviewed', 'auto_ok', 'needs_review', 'reviewed', 'corrected')),
  add column tag_reviewed_by text,
  add column tag_reviewed_at timestamptz,
  add column tagger_version text,
  -- ルビ・外字・注記・raw_text など原情報(指示書§8.3〜8.5)
  add column chunk_metadata jsonb not null default '{}';
create index source_chunks_role_idx
  on public.source_chunks (person_id, speaker_role, thought_eligibility);
create index source_chunks_review_idx
  on public.source_chunks (tag_review_status) where (tag_review_status = 'needs_review');

-- ── 7. creative_cards への追加(C-T0 §3 の衝突1・2) ──
-- 創作カードの根拠が「漱石自身の創作論」か「小説本文での実演」かを区別する(指示書§11.2)。
alter table public.creative_cards
  add column evidence_type text check (evidence_type in (
    'author_creative_theory', 'demonstrated_in_fiction', 'critic_interpretation'));
-- 指示書§11.2 の card_type に 'criticism' があるため追加する。
-- 既存データが0件であることを確認済みのため、check制約の入替は安全。
alter table public.creative_cards drop constraint creative_cards_card_type_check;
alter table public.creative_cards add constraint creative_cards_card_type_check
  check (card_type in (
    'style', 'narrative', 'motif', 'character', 'perspective', 'ending',
    'criticism', 'prohibition',
    'setting', 'dialogue', 'rhythm', 'theme', 'historical_language'));

-- ── 8. thought_evidence_links への追加(反対証拠。指示書§11.1) ──
-- 既存は「支持する根拠」のみ。規則の承認判断には反対例・例外の確認が要る(指示書§12.1)。
-- default 'support' なので既存行の意味は変わらない。
alter table public.thought_evidence_links
  add column link_polarity text not null default 'support'
    check (link_polarity in ('support', 'counter'));

-- ── 9. RLS(既存規約: 有効・ポリシー無し = deny-all) ──
alter table public.canonical_works enable row level security;
alter table public.work_editions enable row level security;
alter table public.aozora_manifest_entries enable row level security;
alter table public.canonical_work_review_queue enable row level security;

comment on table public.canonical_works is
  '版をまたぐ作品の同一性。青空文庫の漱石113エントリ → 107作品。'
  '同定は作品名の完全一致に依存させない(「吾輩は猫である」と「吾輩ハ猫デアル」は'
  'タイトル文字列が異なる)。docs/CORPUS_T1_SPEC.md §1.2 参照。';
comment on column public.source_chunks.speaker_role is
  '発話主体の役割分類。既存の speaker 列(clean.pyによる話者ラベル正規化の結果)とは別物。'
  '小説中の登場人物の発言を作者本人の思想として扱わないための基幹フィールド。';
