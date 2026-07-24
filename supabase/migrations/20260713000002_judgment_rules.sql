-- L3 Judgment Rule 層(判断文法)。仕様: docs/judgment_rules_spec_v0_2.md
-- MVPは5テーブル(identity / 不変バージョン / 証拠 / 例 / レビュー)。
-- conflicts / search_documents / embeddings は Phase 2(初期規則10〜20件では versions.content 内で足りる)。
-- ドラフト(docs/judgment_rules_initial_draft.md)の検証所見を反映:
--   - examples に target(input/output): response_policy規則は出力を検査するため
--   - evidence に card_id: 原典チャンク未リンク時、カード由来(corpus_inferred)を証拠として認める
--   - 安全フロー例外はメタルール(実行エンジン)側で扱い、規則個別には書かない

-- ============================================================
-- 1. judgment_rules(規則のidentityと現在状態)
-- ============================================================
create table public.judgment_rules (
  rule_id text primary key,                         -- 例: JR_ABSOLUTE_NEGATIVE_001
  person_id text not null references public.personas(person_id),
  -- バリアントの束ね(Corpus/Self-Declaredの時期乖離を別バリアントで表現。単独ならrule_idと同値)
  rule_family_id text not null,
  variant_type text not null default 'integrated' check (variant_type in (
    'historical','current_author','integrated','alternative_interpretation')),
  title text not null,
  rule_scope text not null check (rule_scope in (
    'judgment',          -- 判断文法(value_transformation / distinction / priority 等)
    'dialogue',          -- 対話戦略(question_rule)。TGIのGap分析と統合実装する
    'response_policy'    -- 回答方針(abstention / prohibition)。出力検査が主
  )),
  rule_type text not null check (rule_type in (
    'value_transformation','distinction','priority','contradiction_hold',
    'boundary','exception','prohibition','question_rule','abstention',
    'temporal_override')),
  lifecycle text not null default 'active' check (lifecycle in
    ('active','deprecated','merged','split')),

  -- 生成系譜(予測失敗マイニングの追跡に必須)
  creation_method text not null check (creation_method in (
    'manual','corpus_extraction','prediction_failure',
    'tgi_author_interview','regression_analysis','rule_split','rule_merge')),
  source_card_id text references public.thought_cards(card_id) on delete set null, -- 抽出元カード
  source_gap_id text,                               -- TGI Author Gap(soft ref。テーブル未実装)
  source_failure_case_id text,                      -- 予測失敗ケース(soft ref)
  created_by text,
  creation_rationale text,

  valid_from date,                                  -- 時期有効性(temporal系以外は通常null)
  valid_to date,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index judgment_rules_family_idx on public.judgment_rules(rule_family_id);
create index judgment_rules_scope_type_idx on public.judgment_rules(rule_scope, rule_type);
create trigger judgment_rules_updated_at before update on public.judgment_rules
  for each row execute function public.set_updated_at();

comment on table public.judgment_rules is
  'L3判断文法の規則identity。承認状態はjudgment_rule_versions側で管理する';
comment on column public.judgment_rules.rule_family_id is
  '時期バリアント(historical/current_author)を束ねるID。単独規則ではrule_idと同値';

-- ============================================================
-- 2. judgment_rule_versions(不変スナップショット)
-- ============================================================
-- 承認済みバージョンは原則書き換えない。変更は新バージョンを作る。
-- answer_traces(L4)は rule_version_id を参照し、当時使った規則内容を再現できるようにする。
create table public.judgment_rule_versions (
  rule_version_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id) on delete cascade,
  version int not null,
  status text not null default 'draft' check (status in
    ('draft','reviewing','approved','rejected','deprecated')),
  content jsonb not null,                           -- 共通エンベロープ(仕様3.3)
  change_reason text,
  created_by text,
  created_at timestamptz not null default now(),
  unique (rule_id, version)
);
create index judgment_rule_versions_rule_idx on public.judgment_rule_versions(rule_id);
-- 承認済みバージョンの高速引き当て(回答生成で使うのはapprovedのみ)
create index judgment_rule_versions_approved_idx on public.judgment_rule_versions(rule_id)
  where status = 'approved';

comment on column public.judgment_rule_versions.content is
  '共通エンベロープ: schema_version/trigger_conditions/premises/action/derived_claims/'
  'required_distinctions/exceptions/forbidden_inferences/requires_rule_ids/blocks_rule_ids/'
  'next_rule_candidates/default_priority/explanation。MVPではinput_concepts・conflictsもここに保持';

-- ============================================================
-- 3. judgment_rule_evidence(証拠。反証を含む)
-- ============================================================
create table public.judgment_rule_evidence (
  evidence_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id) on delete cascade,
  chunk_id text references public.source_chunks(chunk_id) on delete set null,
  card_id text references public.thought_cards(card_id) on delete set null, -- カード由来の証拠
  author_answer_id text,                            -- 本人回答(TGI 12.4)。テーブル未実装のためsoft ref
  evidence_role text not null check (evidence_role in (
    'supports','contradicts','defines','bounds','exception','historical')),
  origin_type text not null check (origin_type in (
    'corpus_inferred','author_declared','mixed','prediction_failure','tgi_interview')),
  note text,
  created_at timestamptz not null default now(),
  -- 少なくとも1つの参照先を持つ(空の証拠を禁止)
  constraint judgment_rule_evidence_has_ref check (
    chunk_id is not null or card_id is not null or author_answer_id is not null)
);
create index judgment_rule_evidence_rule_idx on public.judgment_rule_evidence(rule_id);

comment on column public.judgment_rule_evidence.evidence_role is
  'contradicts=反証。都合のよい根拠だけでなく反証も保存し、蓄積した規則は再レビュー対象にする';

-- ============================================================
-- 4. judgment_rule_examples(発火テストデータ)
-- ============================================================
-- Hard Negative(意味的に近いが発火すべきでない)が最重要。
-- target: input=ユーザー入力の発火判定用 / output=response_policy規則の出力検査用。
create table public.judgment_rule_examples (
  example_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id) on delete cascade,
  example_type text not null check (example_type in (
    'positive','hard_negative','boundary','counterexample','adversarial')),
  target text not null default 'input' check (target in ('input','output')),
  example_text text not null,                       -- input時=ユーザー発話 / output時=回答文
  expected_activation boolean,                      -- 発火すべきか(boundaryはnull可)
  expected_reason text,
  dataset_split text not null default 'development' check (dataset_split in
    ('development','validation','holdout')),
  status text not null default 'draft' check (status in
    ('draft','approved','deprecated')),
  created_at timestamptz not null default now()
);
create index judgment_rule_examples_rule_idx on public.judgment_rule_examples(rule_id);

comment on column public.judgment_rule_examples.dataset_split is
  'holdoutはRegression Suiteの公式数値専用。few-shotや検索インデックスに使うとリークする(仕様6章)';

-- ============================================================
-- 5. judgment_rule_reviews(レビュー履歴。スコープ別)
-- ============================================================
-- 「結論は承認されたが理由は未承認」等を表現するため、単一JSONでなくスコープ別の履歴で持つ。
create table public.judgment_rule_reviews (
  review_id uuid primary key default gen_random_uuid(),
  rule_version_id uuid not null references public.judgment_rule_versions(rule_version_id) on delete cascade,
  reviewer_id text,
  reviewer_role text not null check (reviewer_role in (
    'author','editor','researcher','system_evaluator')),
  verdict text not null check (verdict in (
    'approved','approved_with_changes','rejected','uncertain')),
  review_scope text not null check (review_scope in (
    'meaning','reasoning','boundary','historical_validity','current_validity','wording')),
  note text,
  created_at timestamptz not null default now()
);
create index judgment_rule_reviews_version_idx on public.judgment_rule_reviews(rule_version_id);

-- ============================================================
-- RLS(仕様9章: 管理者のみ。testerの直接アクセスを遮断)
-- ============================================================
alter table public.judgment_rules enable row level security;
alter table public.judgment_rule_versions enable row level security;
alter table public.judgment_rule_evidence enable row level security;
alter table public.judgment_rule_examples enable row level security;
alter table public.judgment_rule_reviews enable row level security;

create policy "admin all" on public.judgment_rules
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.judgment_rule_versions
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.judgment_rule_evidence
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.judgment_rule_examples
  for all using (public.is_admin()) with check (public.is_admin());
create policy "admin all" on public.judgment_rule_reviews
  for all using (public.is_admin()) with check (public.is_admin());
