# T0 リポジトリ監査レポート（正式版）

> 指示書 §18「初回の回答で提出するもの」1〜12項目の形式による正本。
> 監査対象: 上流リポジトリ `/Users/will/thinker-maurice`（GitHub: Willwesternavenue/thinker-maurice）**main = `b92e60f`**。
> thinker-soseki は本コミットからフォークするため、フォーク直後のコードと同一内容。
> 作成: 2026-07-26。土台: [T0_AUDIT_NOTES.md](T0_AUDIT_NOTES.md)（実地調査メモ）を全項目実コードと再照合済み（訂正9件は §1.9 に明記）。

---

## 1. リポジトリ監査結果

### 1.1 構成

```
frontend/   Next.js 16.2.10 + React 19.2.4 (App Router) — Chat UI / Admin UI / Auth / Chat API
worker/     Python 3.12 + uv — ingestion/distillation ジョブのポーリング常駐プロセス
supabase/   config.toml + migrations 19本（seed.sql は不存在。config.toml が参照するが宙吊り）
scripts/    createUser.ts（tsx + firebase-admin。admin/tester アカウント発行）
docs/       CONTENT_INGESTION.md / FIREBASE_MIGRATION.md / GCP_MIGRATION.md / judgment_rules_spec_v0_2.md / regression_suite_spec_v0_2.md
```

⚠️ `frontend/AGENTS.md`: 「コードを書く前に `node_modules/next/dist/docs/` の該当ガイドを読むこと」（Next.js 16 は学習データと異なる breaking changes あり）。創作モードUI実装時に必読。

### 1.2 DB schema（migrations 19本から確認）

主要テーブル（migration `20260704000002_tables.sql` ほか）:

- `personas` — person_id(text PK), display_name, system_prompt, first_person, banned_terms_exact/contextual, style_rules/quote_policy/safety_policy(jsonb), fallback_card_id
- L1: `sources` / `source_chunks`(embedding vector(1536), verbatim, importance, status)
- 蒸留: `chunk_distillations` / `source_distillations`
- L2: `thought_cards` — card_id(text PK), person_id FK, thought_id, status(draft/reviewing/approved/rejected/deprecated), version, core_claim, distinctions(jsonb), answer_policy[], prohibitions[], representative_chunk_ids[], search_text, embedding。**部分一意制約**: `(person_id, thought_id) where status='approved'`。付随: `thought_card_revisions` / `thought_questions`(ルーティング用・embedding付) / `thought_evidence_links`(承認・quote_allowed) / `concept_aliases`
- L3: `judgment_rules` + `judgment_rule_versions/evidence/examples/reviews`（rule_family_id, rule_scope check(judgment/dialogue/response_policy), rule_type 10種, lifecycle, creation_method 等）
- L4: `answer_traces`（`l3_shadow` カラムは 20260713000003 で追加）
- ジョブ: `ingestion_jobs`(job_id uuid PK, source_id FK, status check(pending/running/succeeded/failed), current_step, error_message) / `distillation_jobs`(+person_id FK, kind check(all/heavy/cards/questions), result jsonb)
- その他: `chat_sessions` / `chat_messages` / `user_profiles`(user_id=Firebase UID, role check(admin/tester)) / `agent_runs` / `evaluation_logs` / `glossary_terms` / `transcript_drafts` / `worker_heartbeats`（**複数形**）
- RLS: **HEAD時点では全テーブル有効・ポリシー0本 = deny-all**。初期migrationで作られたポリシー群は `20260716000001_firebase_auth.sql` が全削除（アクセスは全て service_role）
- 拡張: pgvector(`extensions.vector`) + PGroonga。共通トリガ `public.set_updated_at()`

### 1.3 RPC（SQL関数）

5関数すべてに `target_person_id text default 'merleau_ponty'` がハードコード（person置換対象）:

- `match_thought_questions` / `match_source_chunks_by_thoughts`（メモの「match_source_chunks」の正式名）/ `match_source_chunks_all` / `search_source_chunks_fulltext`（PGroonga）/ `rebuild_related_thought_ids`

### 1.4 person_id ハードコード（`merleau_ponty` リテラル全箇所）

- frontend 13ファイル: `lib/rag/pipeline.ts:44` / `lib/rag/l3shadow.ts:20` / `lib/rag/rag.test.ts:10` / `app/chat/actions.ts:90` / `admin/cards/actions.ts:118,167` / `admin/cards/distill-actions.ts:51` / `admin/sources/actions.ts:73` / `admin/questions/actions.ts:65,109` / `admin/transcripts/actions.ts:101` / `admin/persona/page.tsx:12` / `api/admin/eval/route.ts:34` / `lib/rag/session.ts:45,53`(コメント)
- worker 7ファイル: `ingest_source.py:26` / `import_cards.py:15` / `import_judgment_rules.py:19` / `review_cards.py:27`（定数）、`steps/gen_cards.py:43` / `steps/gen_questions.py:89` / `steps/distill_heavy.py:52`（default引数）
- migrations: RPC default 4ファイル + `transcript_drafts` / `glossary_terms` の列default
- `docs/CONTENT_INGESTION.md` 自身が「person置換フェーズ」（grep で置換前IDゼロ件を確認せよ）と明記。置換の参考コミット: maurice の `9587aaa`

### 1.5 回答パイプライン（`frontend/src/lib/rag/pipeline.ts` — 思想モードの正本）

1. 分類 `classifyQuery`（queryKind / needsThoughtCards。Haiku）
2. ルーティング `router.ts`: 概念エイリアス展開 → thought_questions 類似照合（閾値 0.6/0.5+votes2）→ カードLLM分類。routing_method を trace に記録
3. approved カードのみ取得（`cards.ts` `.eq("status","approved")`）→ merge
4. **不変条件（pipeline.ts:124-129）**: needsThoughtCards でカード0枚なら throw。フォールバックは `personas.fallback_card_id` かつ status=approved 必須
5. evidence 取得: vector（`match_source_chunks_all`）+ PGroonga 全文（`search_source_chunks_fulltext`）+ evidence_links、merge/diversify、引用可否フィルタ
6. 回答生成: `MODEL_ANSWER` 非ストリーミング（api/chat/route.ts, maxDuration=120）
7. Output Guard: 完全一致検査 → LLM judge（`MODEL_LIGHT`）→ 再生成は最大1回 → safe answer フォールバック
8. `after()` で answer_traces 保存（**App Hosting cpu>=1 必須** — apphosting.yaml に明記）

### 1.6 モデル・キー・設定

- モデルは定数直書き・**provider abstraction は存在しない**: `llm.ts:5-6` MODEL_ANSWER=`claude-sonnet-5` / MODEL_LIGHT=`claude-haiku-4-5-20251001`。worker `config.py:29-33` MODEL_LIGHT_DISTILL / MODEL_HEAVY_DISTILL / MODEL_CARD_DRAFT + MODEL_PRICES
- Embedding: OpenAI `text-embedding-3-small`(1536) のみ（frontend `lib/embedding.ts` / worker embed step）
- 秘匿キー: **env優先 → Secret Manager フォールバック**（`lib/secrets.ts` / `config.py:_load_secrets`。3キー: SUPABASE_SERVICE_ROLE_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY）
- 非秘匿設定: `frontend/src/lib/const.ts` / `worker/src/config.py`。⚠️ maurice では GCP_PROJECT_ID が frontend=`thinker-maurice-9082f` / worker=`thinker-maurice` と**別値**（Firebase が別サフィックスIDを生成した経緯。漱石スタックでは統一を目指すが同事象に注意）
- **プロンプトは全てコード内のインライン定数で無版**（唯一のプロンプト専用ファイルは `transcripts/prompts.ts`）。版管理・レジストリは存在しない → 創作モードの「prompt version を trace に保存」は新規に導入する

### 1.7 worker ジョブ機構（創作 generation ジョブの雛形）

- `main.py run_forever()`: 5秒間隔ポーリング。`ingestion_jobs` → `distillation_jobs` の順に pending を1件処理
- **claim は非排他**（select pending → status='running' に更新するだけ。SELECT FOR UPDATE 等なし）。**単一プロセス前提**がコードに明記されており、Cloud Run min=max=1 運用がこれを担保
- 状態遷移: `pending → running → succeeded | failed`（error_message は2000字切詰め）。`current_step` に段階を記録（ingestion は extract/clean/chunk/embed/distill_light の5段）
- 起動時 `_reclaim_orphaned_jobs()` が running を pending に戻す（孤児回収）
- heartbeat: 10秒毎に `worker_heartbeats` へ upsert（idle/processing, current_job_id）。admin UI がバナー表示
- `--once` モード / Cloud Run 用 $PORT ヘルスサーバあり

### 1.8 蒸留・カード自動起草機構（創作カード起草に転用する機構）

- ingestion: extract → clean(話者正規化) → chunk(CHUNKER_VERSION=v1・決定的chunk_id) → embed → distill_light(Haiku)
- `src.distill` CLI: `heavy | source <id> | cards | questions | all`
- `gen_cards.py`: candidate_thought_ids ごとに evidence 集約、**MIN_EVIDENCE_CHUNKS=2 未満はカード化しない** / 既存非rejectedカードのある thought_id はスキップ / `status='draft'` で生成（MODEL_CARD_DRAFT）/ draft の evidence_links も同時生成
- `gen_questions.py`: カード毎の想定質問 draft 生成
- テスト: worker pytest 28件（チャンカー決定性・クリーニング・ingest・インタビュー）/ frontend vitest 約43件（RAG純関数・transcript prep）/ **E2E基盤なし**

### 1.9 監査メモ（T0_AUDIT_NOTES.md）からの訂正・補強 9件

1. migrations は **19本**（18本ではない）
2. テーブル名は `worker_heartbeats`（複数形）。またメモ未記載のテーブルあり: `source_distillations` / `thought_card_revisions` / `concept_aliases` / `chat_messages` / `agent_runs` / `judgment_rule_*` 4本
3. RLS deny-all は **HEAD時点の状態**（初期はポリシーが存在し、20260716000001 で全削除された履歴）
4. RPC の正式名は `match_source_chunks_by_thoughts`（`match_source_chunks` という関数は無い）
5. `src.distill` には `source <id>` サブコマンドもある
6. **ジョブ claim に排他機構なし**（単一worker前提）— creative_generations 設計への直接の含意（§9）
7. プロンプトは無版のインライン定数（prompt version 保存は創作モードで新規導入）
8. frontend / worker で GCP_PROJECT_ID が別値（maurice 固有の経緯）
9. TS の `QueryKind` 文字列ユニオンに既に `"creative"` が存在（`lib/rag/types.ts:10` / `classify.ts:12`）。DB衝突ではないが命名が重複するため、思想モード分類の "creative"（創作的質問）と創作モード（Creative Mode）を混同しない命名規約が必要

---

## 2. 現行Thinkerで再利用できる箇所

| 再利用対象 | 創作モードでの使い方 |
|---|---|
| `ingestion_jobs`/`distillation_jobs` のジョブ型 + worker ポーリング + 孤児回収 + heartbeat | `creative_generations` を同型で新設し、worker に新ジョブ種別として追加（5分制約の回避。§7） |
| 蒸留機構（chunk→distill→`gen_cards.py` の draft 生成 + MIN_EVIDENCE 不変条件） | 創作用プロンプトに差し替えて**創作カードの自動draft→人間承認**を実現 |
| `thought_cards` の承認フロー設計（status 5値 + approved 部分一意 + approvedのみ使用の不変条件） | `creative_cards` に同じ規律を移植 |
| L1 基盤（`sources`/`source_chunks`/embedding/PGroonga・RPC群） | 夢十夜の ingestion にそのまま使用。創作 metadata は sources の追加JSONで |
| Output Guard の2段構成（機械検査→LLM judge→再生成上限→安全側） | Creative Guard の骨格（検査内容を原文類似・誤認防止に差し替え） |
| `after()` trace 保存パターン / answer_traces の設計思想 | `creative_traces` の書込みパターン（ただしジョブ型では worker 側書込みが主） |
| 管理画面パターン（/admin/jobs の監視UI・/admin/cards の承認UI・server actions + requireAdmin） | creative profile / card / generation 管理画面 |
| 認証（proxy.ts + lib/auth.ts + __session 14日 + admin/tester）/ 秘匿キー解決 / `set_updated_at()` トリガ / vector(1536) / RLS deny-all 規約 | そのまま流用 |
| `scripts/createUser.ts` / CONTENT_INGESTION.md の人物投入手順 | natsume_soseki 立ち上げ |

## 3. 変更が必要な箇所

1. **person置換**: `merleau_ponty` → `natsume_soseki`（§1.4 の全箇所 + UI呼称 + `const.ts`/`config.py` の接続先。完了判定は grep ゼロ件）
2. **新規テーブル4本の additive migration**（§4）
3. **worker**: `creative_generations` のポーリング分岐 + 生成ステップ実装（brief正規化/outline/draft/guard）。創作用蒸留プロンプト（カードdraft用）の追加
4. **frontend**: 創作画面（入力+ポーリング+結果タブ）/ 管理画面（profile・creative card 承認・generation 監視）/ server actions・API route の追加
5. **プロンプト版管理の新規導入**（創作系プロンプトのみ。既存プロンプトには触れない）: version 定数を持たせ trace に保存
6. **Guard 閾値の設定値化**（原文類似 n-gram 閾値等。コード直書き禁止の指示に対応）
7. 命名規約: 既存 `QueryKind "creative"` と衝突しない接頭辞（DB/コードとも `creative_` プレフィックスの新規名前空間で統一）

## 4. 推奨schema（v0.1 新設4テーブル）

既存規約（text PK または uuid、person_id FK、status check、set_updated_at トリガ、RLS有効・ポリシー無し）に従う。

```sql
-- 1) creative_profiles: 作家×作品群プロファイル（親キーは person_id。author_id は使わない）
create table creative_profiles (
  profile_id text primary key,
  person_id text not null references personas(person_id),
  name text not null, slug text not null unique, description text,
  source_scope jsonb not null,            -- 例 {"work_group":"夢十夜"}
  orthography_policy text not null,       -- 必須: 仮名遣い・字体（例 '新字新仮名'）
  target_language text, historical_period text,
  default_generation_settings jsonb,      -- {use_rag, use_cards, rules, temperature, length…}
  disclosure_text text not null,          -- 誤認防止表示文
  display_title_format text not null,     -- 例 '{title}（AI創作）'
  copyright_policy text,
  status text not null default 'draft' check (status in ('draft','active','archived')),
  created_at timestamptz default now(), updated_at timestamptz default now()
);

-- 2) creative_cards: 承認制の創作カード（thought_cards の規律を移植）
create table creative_cards (
  card_id text primary key,
  profile_id text not null references creative_profiles(profile_id),
  card_type text not null check (card_type in
    ('style','narrative','motif','character','ending','prohibition',
     'setting','dialogue','perspective','rhythm','theme','historical_language')),
  title text not null, summary text, description text,
  positive_patterns jsonb, negative_patterns jsonb,
  required_elements jsonb, prohibited_elements jsonb,
  examples jsonb, counterexamples jsonb,
  evidence_chunk_ids text[],              -- v0.1 は配列で保持（独立リンクテーブルは必要時に分離）
  origin_type text not null default 'distilled' check (origin_type in ('distilled','manual')),
  confidence text,
  status text not null default 'draft' check (status in ('draft','reviewing','approved','rejected','deprecated')),
  version int not null default 1, reviewed_by text, reviewed_at timestamptz,
  created_at timestamptz default now(), updated_at timestamptz default now()
);

-- 3) creative_generations: ジョブテーブル（ingestion_jobs/distillation_jobs と同型のライフサイクル）
create table creative_generations (
  job_id uuid primary key default gen_random_uuid(),
  profile_id text not null references creative_profiles(profile_id),
  brief_raw jsonb not null,               -- ユーザー入力
  brief_normalized jsonb,                 -- Step1 の構造化結果
  generation_settings jsonb not null,     -- {use_rag, use_cards, rules, preset_name, temperature…}
  idempotency_key text unique,            -- 多重送信防止
  status text not null default 'pending' check (status in ('pending','running','succeeded','failed')),
  current_step text,                      -- brief/profile/cards/sources/outline/draft/guard/save
  outline jsonb, final_text text, display_title text,
  error_message text,
  created_by text not null,               -- user_profiles.user_id
  created_at timestamptz default now(), updated_at timestamptz default now()
);

-- 4) creative_traces: L4 監査（answer_traces は変更しない）
create table creative_traces (
  trace_id uuid primary key default gen_random_uuid(),
  job_id uuid not null references creative_generations(job_id),
  profile_id text not null,
  used_card_ids text[] not null default '{}',
  injected_source_ids text[] not null default '{}',  -- 全文投入した作品/章の識別子
  injected_chunk_ids text[] not null default '{}',
  fired_rule_ids text[] not null default '{}',       -- L3用に確保（v0.1 は常に空）
  rejected_rule_ids text[] not null default '{}',    -- 同上
  rule_decisions jsonb,                              -- 同上
  guard_results jsonb not null default '{}',         -- 類似度数値・該当箇所・誤認検査・再生成回数
  model_ids jsonb not null,                          -- ステップ別 model
  prompt_versions jsonb not null,                    -- ステップ別 prompt version/hash
  token_usage jsonb, latency_ms int, regeneration_count int default 0,
  created_at timestamptz default now()
);
```

延期: `creative_rules`（L3）/ `creative_projects` / `creative_evaluations` / 独立 `creative_guard_results`（v0.1 は traces 内 jsonb）。

## 5. 既存テーブル共用案と別テーブル案の比較

| 観点 | 共用案（thought_cards 等に domain 列追加） | 別テーブル案（creative_* 新設） |
|---|---|---|
| 既存思想モードへの侵襲 | migration が既存テーブルを触る。**approved 部分一意 `(person_id, thought_id)` が創作カードに不適合**（創作カードに thought_id が無い）で制約改変が必要 = 破壊的 | **ゼロ**（additive のみ。既存テスト構造的に無傷） |
| フィールド適合 | thought_cards は core_claim/answer_policy 等思想特化。創作カードの patterns/elements 系と大きく乖離し、nullable だらけになる | 創作用に最適な形を定義できる |
| 指示書適合 | §1.1「破壊的migration禁止」「大規模抽象化の先行禁止」に抵触リスク | §12「既存テーブルへdomain追加が危険なら別テーブル」に合致 |
| 承認フロー再利用 | UIとクエリを domain 分岐だらけにする | status 規律を**設計として**移植（コードは新規だがパターン同一） |
| 将来の統合 | 早い | 創作モード実証後に統合を再検討（合意済みの方針） |
| 名前衝突 | — | grep 済み: `creative_*` テーブル名の衝突ゼロ（§1.9-9 の TS 文字列のみ注意） |

## 6. 推奨案と理由

**別テーブル案（§4 の4テーブル新設)で確定**。理由:

1. 既存思想モードの不変条件（approved 部分一意・カード必須 throw・Guard）が構造的に無傷 = 指示書 §1.1（最重要方針）を最も確実に満たす
2. thought_cards との共用は部分一意制約の改変を要求し、それ自体が破壊的 migration
3. ジョブ実行モデル（5分制約回避）が `creative_generations` という独立ジョブテーブルを自然に要求する
4. 認識済みコスト: カード承認UI等でコード類似が生じるが、v0.1 の速度と安全を優先し、統合は創作モード実証後に検討（ユーザー合意済み）

## 7. generation sequence（v0.1・ジョブ型）

```
[frontend]                          [DB]                        [worker]
POST 創作生成(server action)
  └ idempotency_key 検査 ──▶ creative_generations INSERT(pending)
  └ job_id を即返却                                      5秒ポーリングで pending を claim
UI: 「生成中」+ ポーリング                                 status=running
  GET generation(job_id)            current_step 更新 ◀── Step1 brief正規化(軽量モデル)
                                                          Step2 profile 検証(自動fallback禁止)
                                                          Step3 approvedカード取得
                                                            └ 0枚 → status=failed(不変条件) + trace
                                                          Step4 原典投入(関連一夜の全文+evidence chunk)
                                                          Step5 outline生成(高性能モデル)
                                                          Step6 draft生成(style自己検査込み)
                                                          Step7 Creative Guard
                                                            ├ 文字n-gram原文類似(閾値=設定値)
                                                            ├ 誤認防止・prohibitionカード検査(judge)
                                                            └ 違反→再生成(上限)→安全側failed
                                    final_text/outline ◀── Step8 保存: generations 更新(succeeded)
                                    creative_traces INSERT(設定フラグ+preset名+model+prompt version+usage)
UI: 完了検知 → 「第十一夜（AI創作）」+ disclosure 常時表示
```

失敗時も trace を必ず残す（生成失敗の監査ログ要件）。

## 8. UI構成（v0.1）

- ナビ: **思想対話 / 創作 / 管理** を明示分離
- `/creative`（仮）: 入力フォーム（profile / モチーフ / 状況 / 読後感 / 時代 / 文字数 / 追加制約）→ 生成ボタン → 生成中ポーリング表示 → 結果タブ **作品 / 構成(outline) / 使用カード / Creative Trace / Guard**
- 管理（既存 /admin パターン踏襲・server actions + requireAdmin）:
  - `/admin/creative-profiles` 一覧・編集
  - `/admin/creative-cards` 一覧・編集・**承認**（蒸留draftの検収。evidence 対照表示）
  - `/admin/creative-generations` ジョブ監視（/admin/jobs パターン + heartbeat バナー流用）・trace/guard 確認
- 思想用と創作用のデータを誤認しない表示（画面名・ラベルに「創作」を明示）
- 評価UI・比較タブは延期

## 9. migrationリスク

| リスク | 対応 |
|---|---|
| 既存テーブルへの影響 | 新テーブルのみで**ゼロ**。既存 migration には一切触れない |
| RPC の person default 置換（person置換フェーズ） | 創作モードとは独立の作業。`create or replace function` の additive migration で default 値のみ変更（maurice 9587aaa と同型） |
| RLS | 新テーブルも「RLS有効・ポリシー無し・service_role アクセス」規約に従う（規約逸脱が最大のリスク） |
| ジョブ多重実行 | 既存 claim は非排他・単一worker前提（§1.9-6）。漱石も Cloud Run min=max=1 を踏襲。**将来複数worker化するなら atomic claim（`update … where status='pending' returning`）が必須**である旨を schema コメントに残す |
| idempotency | `creative_generations.idempotency_key unique` で多重送信を DB 制約で防止 |
| rollback | 4テーブルとも drop で完全 rollback 可能（既存データ無関係） |
| migration 前後の既存テスト | worker pytest 28件 + frontend vitest 約43件 + `supabase db reset` を通す（E2Eは無い） |

## 10. 実装タスク T0〜T8

v0.2 改訂案 §17 に対応（旧 T6 評価・比較はスコープ延期で消滅、以降繰り上げ）:

- **T0 監査** — 本レポートで完了
- **T1 正本仕様** — v0.2 改訂案の発注者承認 → schema diff / API diff / sequence / Guard・trace 仕様 / テスト計画の確定
- **T2 DB** — §4 の additive migration + repository/service + migration テスト
- **T3 管理機能** — profile / creative card 承認 / generation 監視
- **T4 生成 pipeline（worker）** — ジョブ分岐 + Step1〜8 + Guard + trace
- **T5 ユーザーUI** — 創作入力 / ポーリング / 結果タブ / disclosure
- **T6 『夢十夜』初期 profile** — 原典 ingestion（底本記録）→ 創作用蒸留プロンプトでカード draft → 人間承認 → test fixtures
- **T7 ドキュメント同期** — architecture / schema / API / operations / limitations / copyright方針
- （並行）環境構築: フォーク → person置換 → 新スタック接続（HANDOFF.md のチェックリスト）

## 11. 不明点・コードから確認できなかった点

1. **青空文庫テキストの取り込み形態**: 既存 extract が対応する入力形式（ルビ記法・注記の除去方針）は実データ投入時に要検証。夢十夜の底本（新字新仮名版）の特定と記録は T6 で実施
2. **orthography_policy の実効性**: 生成モデルが歴史的仮名遣いをどの程度安定して守れるかは PoC 未確認（v0.1 は新字新仮名を既定とし、PoC で確認していない能力を約束しない）
3. **原文類似検査の閾値初期値**: 文字 n-gram 長・一致率の適正値はコーパスで実測して較正する（設定値化するので後から調整可能）
4. **新スタックの実値**: GCP/Firebase/Supabase の新プロジェクトID・URL 類はユーザー作成後に確定（maurice では frontend/worker で projectId が割れた事象に注意）
5. **worker の同時実行**: 単一worker前提を漱石でも維持するか（v0.1 は維持。生成ジョブと ingestion ジョブが同一プロセスで直列になるため、生成の待ち時間が許容できるかは運用で確認）
6. 既存 `QueryKind "creative"`（思想モードの質問分類）と創作モードの命名衝突の扱い（コードコメントと画面ラベルで区別。必要なら分類側のリネームは**しない** — 既存挙動維持のため）

## 12. MVP（v0.1）で実施しない事項

- L3 創作規則の全体（creative_rules / rule firing / shadow・assist）— trace にフィールドだけ確保
- revision 独立ステージ（draft プロンプトへ統合）
- baseline / rag_only 比較モードの実行・ブラインド評価・評価UI（§10）・比較実験基盤（§11）— 設定フラグ+preset名の保存のみ実施
- creative_projects / creative_evaluations / 独立 guard_results テーブル
- semantic search による原典取得（小コーパスのため全文投入で代替）
- fine-tuning / 長編生成 / 真作判定 / 完全自動承認 / 存命作家の無許可模倣 / 権利不明コーパス / 長期状態機構 / 統計検定 / 思想・創作モードの完全統合
