-- 創作モード(Creative Mode) v0.1 の基盤テーブル。
-- 正本仕様: docs/CREATIVE_MODE_SPEC_v0.2.md / 実装設計: docs/T1_CREATIVE_MODE_DESIGN.md
--
-- 方針(仕様§12):
-- - additive のみ。既存テーブル・既存migrationには一切触れない(既存思想モードの
--   不変条件と既存テストを構造的に無傷に保つため)。
-- - 思想カード(thought_cards)との共用ではなく別テーブルにする。共用すると
--   approved部分一意制約 (person_id, thought_id) の改変が必要になり破壊的なため。
-- - RLSは有効・ポリシー無し(deny-all)。アクセスは全て service_role 経由
--   (20260716000001_firebase_auth.sql で確立した現行規約)。
-- - rollback は本ファイルの4テーブルを drop するだけでよい(既存データに無関係)。
--
-- v0.1では creative_rules(L3創作規則) は作らない(仕様§19.1で延期)。
-- 後付けできるよう creative_traces に規則用の空カラムだけ確保してある。

-- ── 1. creative_profiles: 作家×作品群のプロファイル(仕様§4) ──
-- 「夏目漱石」全作品の特徴と「夢十夜」の特徴を混同しないため、人物(personas)の
-- 下に作品群単位のプロファイルを持つ。『夢十夜』はコードにハードコードせず、
-- このテーブルのデータとして表現する。
create table public.creative_profiles (
  profile_id text primary key,
  -- 親キーは person_id(personas)。指示書v0.1の author_id という概念は現行実装に無い。
  person_id text not null references public.personas(person_id),
  name text not null,
  slug text not null unique,
  description text,
  -- このプロファイルが参照する原典の範囲(例: {"work_group":"夢十夜"})。
  source_scope jsonb not null default '{}',
  -- 仮名遣い・字体ポリシー(例: 新字新仮名)。生成文の正書法は全文に効く決定のため
  -- v0.2でnot null必須へ昇格させた(仕様§改訂サマリ6)。原典側の表記と一致させること。
  orthography_policy text not null,
  target_language text not null default 'ja',
  historical_period text,
  -- 生成の既定設定。guard閾値もここに入れる(コード直書き禁止・仕様§8.1)。
  -- 例: {"use_rag":true,"use_cards":true,"rules":"off",
  --      "guard":{"ngram_n":10,"lcs_threshold":20,
  --               "ngram_overlap_ratio_max":0.05,"max_regenerations":2}}
  default_generation_settings jsonb not null default '{}',
  -- AI生成物である旨の常時表示文(仕様§5.1)。UIは本文と同一ビューで必ず表示する。
  disclosure_text text not null,
  -- 表示題名の型(例: '{title}（AI創作）')。素の「第十一夜」を表示させないための
  -- 誤認防止を題名レベルで固定する(仕様§5.1)。
  display_title_format text not null,
  copyright_policy text,
  status text not null default 'draft' check (status in ('draft', 'active', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index creative_profiles_person_idx on public.creative_profiles (person_id, status);
create trigger creative_profiles_updated_at before update on public.creative_profiles
  for each row execute function public.set_updated_at();

-- ── 2. creative_cards: 人手承認された創作カード(仕様§3 L2) ──
-- thought_cards と同じ規律(status 5値・approvedのみ生成に使用)を移植する。
-- 起草は人手ではなく、既存の蒸留機構を創作用プロンプトに差し替えた自動draft →
-- 管理画面で人間が承認、という流れ(仕様§7)。
create table public.creative_cards (
  card_id text primary key,
  profile_id text not null references public.creative_profiles(profile_id),
  -- 一枚につき一つの特徴。v0.1最低限の6種+将来拡張6種(仕様§3 L2)。
  card_type text not null check (card_type in (
    'style', 'narrative', 'motif', 'character', 'ending', 'prohibition',
    'setting', 'dialogue', 'perspective', 'rhythm', 'theme', 'historical_language')),
  title text not null,
  summary text,
  description text,
  positive_patterns jsonb not null default '[]',
  negative_patterns jsonb not null default '[]',
  required_elements jsonb not null default '[]',
  prohibited_elements jsonb not null default '[]',
  examples jsonb not null default '[]',
  counterexamples jsonb not null default '[]',
  -- 原文の対応箇所(source_chunks.chunk_id)。thought_cards の
  -- representative_chunk_ids と同様、FK制約は張らず配列で保持する。
  -- 独立リンクテーブルへの分離は必要になった時点で行う(v0.1は据え置き)。
  evidence_chunk_ids text[] not null default '{}',
  origin_type text not null default 'distilled' check (origin_type in ('distilled', 'manual')),
  confidence text,
  status text not null default 'draft' check (status in
    ('draft', 'reviewing', 'approved', 'rejected', 'deprecated')),
  version int not null default 1,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- 生成時は profile_id + status='approved' で引く(仕様§6.2 Step3)。
create index creative_cards_profile_status_idx on public.creative_cards (profile_id, status);
create trigger creative_cards_updated_at before update on public.creative_cards
  for each row execute function public.set_updated_at();

-- ── 3. creative_generations: 生成ジョブ(仕様§6.1) ──
-- 生成はフロントのリクエスト内で実行しない。App Hosting のリクエスト上限5分に対し
-- outline→draft→guard→再生成の直列実行は超過し得るため、ingestion_jobs /
-- distillation_jobs と同型のジョブテーブルにして worker がポーリングする。
create table public.creative_generations (
  job_id uuid primary key default gen_random_uuid(),
  profile_id text not null references public.creative_profiles(profile_id),
  -- ユーザー入力(モチーフ・状況・読後感・時代・文字数・追加制約)。
  brief_raw jsonb not null,
  -- Step1で構造化したbrief(motif/situation/emotional_target/period/length/constraints)。
  brief_normalized jsonb,
  -- {use_rag, use_cards, rules, preset_name, ...}。5値enumではなく直交フラグ+
  -- プリセット名で保持し、延期した比較実験(仕様§11)へデータ互換で移行できるようにする。
  generation_settings jsonb not null,
  -- 同一リクエストの多重送信で複数ジョブが作られないようDB制約で担保(仕様§13)。
  idempotency_key text unique,
  status text not null default 'pending' check (status in
    ('pending', 'running', 'succeeded', 'failed')),
  -- brief | profile | cards | sources | outline | draft | guard | save | done
  current_step text,
  outline jsonb,
  final_text text,
  -- creative_profiles.display_title_format から組み立てた表示題名。
  -- 素の題名は保存しない(誤認防止・仕様§5.1)。
  display_title text,
  -- 先頭に失敗分類タグを付ける: invariant_violation / guard_exhausted / llm_error / unknown
  error_message text,
  -- 実行者のFirebase UID。chat_sessions.user_id と同様、user_profiles へのFKは張らない
  -- (Firebase移行で auth.users へのFKを外した現行規約に合わせる。20260716000001参照)。
  -- 閾値較正やevalをCLIから流す場合は 'cli' 等の非UID値を入れる。
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- workerのポーリング(pending を created_at 順に1件)用。
create index creative_generations_pending_idx on public.creative_generations (status, created_at);
create index creative_generations_creator_idx on public.creative_generations (created_by, created_at desc);
create trigger creative_generations_updated_at before update on public.creative_generations
  for each row execute function public.set_updated_at();

comment on table public.creative_generations is
  '創作生成ジョブ。claimは既存のingestion_jobs/distillation_jobsと同じく非排他'
  '(select pending → status=running に更新するだけ)で、単一worker前提'
  '(Cloud Run min=max=1・no-cpu-throttling)に依存する。複数worker化する場合は'
  'update ... where status=''pending'' returning によるatomic claimが必須。';

-- ── 4. creative_traces: 生成過程の監査(仕様§3 L4) ──
-- 既存 answer_traces はチャット固有の形なので変更せず、創作用に新設する。
-- succeeded / failed の両終端で必ず書く(生成失敗も監査可能にするため)。
create table public.creative_traces (
  trace_id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.creative_generations(job_id) on delete cascade,
  profile_id text not null,
  -- 使用した承認済みカード(仕様§16: 使用カードをtraceで確認できること)。
  used_card_ids text[] not null default '{}',
  -- 投入した原典。v0.1は関連する一夜の全文投入なので source 単位が主で、
  -- カードのevidenceに紐づく補助投入が chunk 単位になる(仕様§6.2 Step4)。
  injected_source_ids text[] not null default '{}',
  injected_chunk_ids text[] not null default '{}',
  -- L3(創作規則)用の器。v0.1では常に空。規則を後から追加してもtrace形式を
  -- 変えずに済むよう最初から確保しておく(仕様§3 L3)。
  fired_rule_ids text[] not null default '{}',
  rejected_rule_ids text[] not null default '{}',
  rule_decisions jsonb not null default '{}',
  -- {similarity:{lcs_len,lcs_text,matched_chunk_ids,ngram_ratio,passed},
  --  misattribution:{...}, prohibitions:[{card_id,verdict}], safety:{...}}
  -- 作家固有の創作制約とシステム安全規則は別キーに分けて保存する(仕様§8.4)。
  guard_results jsonb not null default '{}',
  -- ステップ名 → 値。例 {"brief":"claude-haiku-...","draft":"claude-sonnet-5"}
  model_ids jsonb not null default '{}',
  -- プロンプトのversion/hash。既存実装にはプロンプト版管理が無いため創作系で新規導入。
  prompt_versions jsonb not null default '{}',
  token_usage jsonb not null default '{}',
  latency_ms int,
  regeneration_count int not null default 0,
  created_at timestamptz not null default now()
);
-- 1ジョブ1行が基本だが、worker再起動時の孤児回収(running→pending)で同一ジョブが
-- 再実行され得るため一意制約は張らない。参照時は created_at 降順の最新を採る。
create index creative_traces_job_idx on public.creative_traces (job_id, created_at desc);

-- ── 5. RLS(現行規約: 有効・ポリシー無し = deny-all) ──
-- anon/authenticatedキーでは一切アクセスできない。service_roleはRLSをバイパスするため
-- アプリ(サーバー側)とworkerからは通常どおり読み書きできる。
alter table public.creative_profiles enable row level security;
alter table public.creative_cards enable row level security;
alter table public.creative_generations enable row level security;
alter table public.creative_traces enable row level security;
