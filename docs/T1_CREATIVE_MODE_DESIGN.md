# T1 正本仕様: Creative Mode v0.1 実装設計書

> 上位仕様: [CREATIVE_MODE_SPEC_v0.2.md](CREATIVE_MODE_SPEC_v0.2.md)（正本・発注者承認 2026-07-26）。
> 根拠監査: [T0_AUDIT_REPORT.md](T0_AUDIT_REPORT.md)（maurice `b92e60f`）。原典取得: [AOZORA_INGESTION.md](AOZORA_INGESTION.md)。
> 本書は §17 T1 の成果物（schema diff / API diff / UI構成 / 生成sequence / Guard仕様 / trace仕様 / migration方針 / テスト計画 / タスク分割）の正本。
> ファイルパス・行番号は maurice `b92e60f` 基準（フォーク後も同一）。

## 0. 前提

- 実装先: thinker-maurice をフォークした `thinker-soseki`（person_id = `natsume_soseki`、新規スタック）
- v0.1 スコープ: creative_profiles + creative_cards（承認フロー）/ ジョブ型段階生成（brief→outline→draft）/ 原文類似 Guard + 誤認防止 / creative_traces。L3・比較モード・評価UIは延期（SPEC §19.1）
- 既存思想モードのコード・テーブル・テストには一切触れない（person置換を除く）
- **環境依存**: 実行確認には新スタック（GCP/Firebase/Supabase、ユーザー作成）が必要。T2〜T5 のコード実装とテスト（LLM/DB モック）は先行可能

## 1. Schema diff

新規 migration 1本: `supabase/migrations/20260726000001_creative_mode.sql`（日付連番は実施日で更新）。
DDL は T0 レポート §4 の4テーブルを正とし、以下を追補する。

**インデックス・トリガ・RLS:**

```sql
create index creative_cards_profile_status_idx on creative_cards (profile_id, status);
create index creative_generations_pending_idx on creative_generations (status, created_at);  -- workerポーリング用
create index creative_generations_creator_idx on creative_generations (created_by, created_at desc);
create index creative_traces_job_idx on creative_traces (job_id);

-- 既存共通トリガ関数 public.set_updated_at() を再利用
create trigger set_updated_at before update on creative_profiles for each row execute function public.set_updated_at();
create trigger set_updated_at before update on creative_cards for each row execute function public.set_updated_at();
create trigger set_updated_at before update on creative_generations for each row execute function public.set_updated_at();

-- RLS 規約: 有効化のみ・ポリシー無し（deny-all、service_role のみアクセス）
alter table creative_profiles enable row level security;
alter table creative_cards enable row level security;
alter table creative_generations enable row level security;
alter table creative_traces enable row level security;

comment on table creative_generations is
  '創作生成ジョブ。claimは既存ジョブ同様に非排他（単一worker前提・Cloud Run min=max=1）。複数worker化する場合は update…where status=''pending'' returning による atomic claim が必須';
```

**設計判断（T0 §4 からの確定事項）:**

- `creative_profiles.default_generation_settings` jsonb に guard 設定を内包する（§5 参照）。閾値のコード直書き禁止要件をここで満たす
- `creative_cards.evidence_chunk_ids text[]` で開始（独立リンクテーブルは v0.2 で必要になったら分離）
- `creative_generations.idempotency_key text unique` — 多重送信防止は DB 制約で担保
- 既存テーブル・既存 migration への変更: **ゼロ**

## 2. API diff（frontend）

既存パターン（CRUD・管理系 = server actions + `requireAdmin()` / 長時間・クライアントfetch系 = route handler）に従う。**既存ルートの変更はなし**、以下を追加。

### 2.1 ユーザー向け（`frontend/src/app/creative/`）

| 操作 | 実装 | 内容 |
|---|---|---|
| 生成開始 | server action `createCreativeGeneration(input)` | `requireUser()` → profile が `status='active'` か検証（曖昧時の自動fallback禁止・エラー返却）→ クライアント生成の `idempotency_key`（uuid）付きで `creative_generations` に INSERT（unique 衝突時は既存 job_id を返す = 冪等）→ `job_id` 即返却 |
| 状態取得 | server action `getCreativeGeneration(jobId)` | `requireUser()` + `created_by` 一致（または admin）検証 → status / current_step / 完了時は final_text・display_title・outline を返す。UI が 3〜5秒間隔でポーリング |
| trace取得 | server action `getCreativeTrace(jobId)` | 同上の権限で creative_traces を返す（結果タブ用） |

### 2.2 管理向け（`frontend/src/app/admin/creative-*/actions.ts`）

- `creative-profiles`: 一覧 / 作成 / 編集 / status 変更（draft→active→archived）。orthography_policy・disclosure_text・display_title_format は必須バリデーション
- `creative-cards`: 一覧（profile・card_type・status フィルタ）/ 編集 / **approve**（reviewed_by=操作者UID, reviewed_at=now）/ reject / deprecate。approve 時に evidence_chunk_ids の chunk 実在を検証
- `creative-generations`: 一覧（ジョブ監視。既存 /admin/jobs のパターン + `worker_heartbeats` バナー流用）/ 失敗ジョブの trace・guard 結果表示 / 再実行（新ジョブとして複製）

### 2.3 追加しないもの

- 生成の route handler（チャットと違い応答は job_id のみで軽いため server action で足りる）
- 創作カード draft 生成の管理UIトリガ — `distillation_jobs.kind` の check 制約 `(all,heavy,cards,questions)` に値追加が必要になり**既存テーブルの変更**に当たるため、v0.1 では worker CLI（§3.3）で運用し UI 化は v0.2 で判断

## 3. Worker diff

### 3.1 ポーリングループ（`worker/src/main.py`）

`run_forever()` に `run_creative_once()` を追加（ingestion → distillation → creative の優先順で1件処理）。既存パターン踏襲:

- pending を created_at 順に1件 select → status='running'（非排他 claim・単一worker前提）
- `_reclaim_orphaned_jobs()` の対象に `creative_generations` を追加（running→pending 復帰）
- heartbeat payload の current_job_id に創作ジョブも載せる
- 失敗時: status='failed' + error_message（2000字切詰め）+ **trace は必ず書く**（§6）

### 3.2 生成モジュール（新規 `worker/src/creative/`）

```
creative/
  generate.py    -- ジョブ orchestration（Step1〜8。current_step を逐次更新）
  prompts.py     -- 全プロンプト定数 + PROMPT_VERSIONS dict（§7）
  guard.py       -- 原文類似・誤認防止・prohibition検査（§5）
  sources.py     -- 関連一夜の選定・全文投入コンテキスト組立
  cards.py       -- approved creative_cards 取得（0枚で CreativeInvariantError）
```

ステップと `current_step` 値（UI の進捗表示に使用）:

| Step | current_step | モデル | 内容 |
|---|---|---|---|
| 1 | `brief` | MODEL_CREATIVE_LIGHT | brief 正規化（motif/situation/emotional_target/period/length/constraints を構造化 → brief_normalized に保存） |
| 2 | `profile` | — | profile 存在・active 検証（fallback 禁止） |
| 3 | `cards` | — | approved カード取得。0枚 → failed（不変条件） |
| 4 | `sources` | MODEL_CREATIVE_LIGHT | motif 等から関連一夜（1〜2篇）を選定し全文投入 + カード evidence chunk 補助投入。識別子を trace 用に記録 |
| 5 | `outline` | MODEL_CREATIVE_MAIN | 導入/中心の異常/反復・変化/転換/終結/説明しない要素 |
| 6 | `draft` | MODEL_CREATIVE_MAIN | 本文生成（orthography_policy 準拠・style 自己検査込み・文字数指定） |
| 7 | `guard` | 機械 + MODEL_CREATIVE_LIGHT | §5。違反→ Step6 から再生成（上限 max_regenerations）→ 超過で failed |
| 8 | `save` | — | generations 更新（succeeded, final_text, display_title, outline）+ traces INSERT |

`display_title` は `creative_profiles.display_title_format`（例 `{title}（AI創作）`）から必ず組み立てる。素の題名は保存しない。

### 3.3 CLI（新規）

- `python -m src.creative_distill --profile <slug>` — 既存 `gen_cards.py` の機構（evidence 集約・MIN_EVIDENCE_CHUNKS=2・既存カードskip・draft 生成）を創作用プロンプトで再実装した創作カード自動 draft。thought_id の代わりに card_type×観点キーで集約
- `python -m src.aozora_fetch --work-id 799` — 青空文庫取得・前処理（[AOZORA_INGESTION.md](AOZORA_INGESTION.md) §2-3 の実装: zip取得(GitHubミラー既定)→CP932→UTF-8→注記除去→底本metadata抽出→sources 登録へ接続）

### 3.4 モデル定数（`worker/src/config.py` へ追加。既存定数は不変）

```python
MODEL_CREATIVE_MAIN = "claude-sonnet-5"          # outline / draft
MODEL_CREATIVE_LIGHT = "claude-haiku-4-5-20251001"  # brief / sources選定 / guard judge
```

## 4. 生成 sequence

T0 レポート §7 の図を正とする（frontend INSERT → worker ポーリング → Step1〜8 → UI ポーリング完了検知）。
補足の確定事項:

- ポーリング間隔: worker 5秒（既存 POLL_INTERVAL_SEC）/ UI 3〜5秒
- 1ジョブの目標所要: 60〜180秒（outline+draft+guard で LLM 3〜5回呼び出し）。5分制約はジョブ型のため無関係だが、生成ジョブと ingestion ジョブが単一workerで直列になる点は運用注意（T0 §11-5）
- 失敗の分類: `invariant_violation`（カード0枚等）/ `guard_exhausted`（再生成上限）/ `llm_error` / `unknown` を error_message 先頭タグで区別（管理画面のフィルタ用）

## 5. Creative Output Guard 仕様

実行順: 機械検査 → LLM judge。**閾値・設定はすべて `creative_profiles.default_generation_settings.guard` から読む**（コード直書き禁止）。既定値:

```json
{
  "guard": {
    "ngram_n": 10,
    "lcs_threshold": 20,
    "ngram_overlap_ratio_max": 0.05,
    "max_regenerations": 2
  }
}
```

### 5.1 原文類似（機械検査）

1. 正規化: NFKC → 空白・約物除去（生成文・原典とも同一の正規化）
2. profile の source_scope に属する全 source_chunks から文字 n-gram（n=`ngram_n`）集合を構築（ジョブ内キャッシュ）
3. 検査:
   - **最長共通部分文字列** ≥ `lcs_threshold` 文字 → 違反（該当文字列・対応 chunk_id を記録）
   - 生成文 n-gram のうち原典側に存在する比率 > `ngram_overlap_ratio_max` → 違反（定型句の偶然一致を許容しつつ大量転写を検出）
4. 閾値の初期値は夢十夜コーパスで較正する（T0 §11-3。設定値なので後から調整可能）

### 5.2 誤認防止 + prohibition 検査（LLM judge）

- 機械: 定型句ブラックリスト（「未発表」「発見された」「真作」「本人が書いた」等）の部分一致
- LLM judge（MODEL_CREATIVE_LIGHT・既存 guard.ts の judge パターン踏襲）: (a) 真作誤認を招く表現がないか (b) approved な prohibition カード各項への違反がないか、を項目別 verdict で返させる
- システム安全規則は既存の safety 検査をそのまま適用し、**creative 固有検査とは trace 上も別キーで保存**

### 5.3 違反時フロー

違反 → 違反内容を draft プロンプトに追記して Step 6 から再生成 → `max_regenerations` 到達で **安全側 failed**（部分成果と guard 結果を trace に残し管理者確認へ）。公開系の自動リトライ緩和はしない。

## 6. Trace 仕様

`creative_traces`（schema は T0 §4）。書込み規則:

- **succeeded / failed の両終端で必ず INSERT**（生成失敗の監査ログ要件）。書込み主体は worker
- `guard_results` jsonb 構造: `{similarity: {lcs_len, lcs_text, matched_chunk_ids, ngram_ratio, passed}, misattribution: {...}, prohibitions: [{card_id, verdict}], safety: {...}, regeneration_count}`
- `model_ids` / `prompt_versions`: ステップ名 → 値の dict（例 `{"brief":"v1","outline":"v1","draft":"v1","guard_judge":"v1"}`）
- `generation_settings`（generations 側）に `{use_rag:true, use_cards:true, rules:"off", preset_name:"cards_only"}` を常に保存 — 延期した比較実験へのデータ互換（SPEC §5.3）
- `fired_rule_ids` / `rejected_rule_ids` / `rule_decisions` は v0.1 では常に空（L3 用の器のみ）

## 7. プロンプト版管理（新規導入・創作系のみ）

- `worker/src/creative/prompts.py` に全プロンプトを定数として集約し、`PROMPT_VERSIONS = {"brief": "v1", ...}` を併置。プロンプト本文を変更する PR は同一コミットで version を上げる（レビュー規約としてコメントに明記）
- 全プロンプト共通の禁止事項ブロック（SPEC §14: 本人と名乗らない / 長い引用をしない / 出典なき有名句を作らない / 象徴の意味を解説しない / profile 外の特徴を混ぜない / カード内容を本文に露出しない / orthography_policy 準拠）を単一定数で共有
- 既存プロンプト（思想モード・蒸留）には触れない

## 8. UI 構成

### 8.1 ナビゲーション

ヘッダを **思想対話 / 創作 / 管理** に分離（既存レイアウトへの追加。呼称に「創作」を明示し思想モードと誤認させない）。

### 8.2 `/creative`（ユーザー画面）

- 入力フォーム: profile 選択（active のみ）/ モチーフ / 状況 / 読後感 / 時代設定 / 文字数 / 追加制約 → 生成ボタン
- 送信時に uuid を idempotency_key として発行（二重クリック・リトライ安全）
- 生成中: current_step に応じた進捗表示（brief→…→guard の8段）+ ポーリング
- 完了: タブ **作品 / 構成 / 使用カード / Creative Trace / Guard**
  - 作品タブ: `display_title`（「◯◯（AI創作）」固定）+ 本文 + **disclosure_text を本文と同一ビュー内に常時表示**（タブ切替で消えない位置）
  - 失敗時: 分類別メッセージ（カード未承認 / guard 超過 等）
- 実装前に `frontend/AGENTS.md` の指示どおり `node_modules/next/dist/docs/` の該当ガイドを読むこと（Next.js 16 breaking changes）

### 8.3 管理画面

- `/admin/creative-profiles` — 一覧・編集（必須3フィールドのバリデーション表示）
- `/admin/creative-cards` — 一覧（type/status フィルタ）・編集・承認。**承認画面は card 内容と evidence chunk 原文を左右対照表示**（既存 /admin/cards の承認UIパターン踏襲）
- `/admin/creative-generations` — ジョブ一覧（status/step/所要時間）・heartbeat バナー・trace / guard 詳細・再実行
- 画面名・パンくずに「創作」を明示（思想用データとの誤認防止）

## 9. Migration 方針

- additive のみ・既存 migration/テーブル不変・`drop table creative_*` 4本で完全 rollback 可
- 手順: `supabase db reset` でローカル検証 → 既存テスト（worker pytest 28 / frontend vitest 約43）green 確認 → `supabase db push`
- seed は使わない（既存方針どおり）。『夢十夜』profile・カードは T6 で CLI + 管理画面から投入し、test fixtures はテストコード内に持つ
- person置換（`merleau_ponty`→`natsume_soseki`、RPC default 含む）は創作モードとは**独立の先行タスク**（環境構築チェックリスト側。参考: maurice 9587aaa）

## 10. テスト計画

### 10.1 worker（pytest 追加）

- `tests/test_aozora.py` — CP932→UTF-8 / ルビ・注記除去 / ヘッダ・フッタ分離 / 底本metadata抽出（夢十夜の実テキスト断片を fixture に）
- `tests/test_creative_guard.py` — 正規化の同一性 / LCS 閾値境界 / n-gram 比率 / 閾値が settings から読まれること（直書きでないこと）/ 誤認定型句検出
- `tests/test_creative_generate.py`（LLM・DB モック）— approved のみ取得 / 0枚で invariant 失敗 + trace 書込み / guard 違反→再生成→上限で failed + trace / display_title が format から組み立てられる / prompt_versions・generation_settings が trace に入る
- `tests/test_creative_distill.py` — evidence 不足でカード化しない / 既存カード skip / draft status で生成

### 10.2 frontend（vitest 追加）

- idempotency_key 付き作成 action の冪等分岐 / ポーリング状態遷移の純関数 / 入力バリデーション / disclosure・題名表示の組み立て

### 10.3 Integration / Regression

- brief→…→trace の一気通貫（LLM モック・ローカル supabase）
- **既存スイートを一切変更せず green**（worker 28 / frontend 約43）+ `supabase db reset` 通過
- E2E 基盤は無い（現状どおり）。SPEC §15.3 の6シナリオを T6 完了時に手動実施し結果を記録

## 11. タスク分割（実装順・依存関係）

| # | タスク | 依存 | 主な成果物 |
|---|---|---|---|
| T2a | **完了 2026-07-26** migration 作成 + `db reset` 全チェーン適用 + 制約9項目の実地検証 + 既存テスト green | フォーク | `20260726000001_creative_mode.sql` |
| T2b | **完了 2026-07-26** worker repository 層（profiles/cards/generations/traces の CRUD）+ 実DBに対する結合テスト14件 | T2a | `worker/src/creative/repo.py` |
| T3a | admin: creative-profiles 画面 + actions | T2a | profile 登録が可能に |
| T3b | admin: creative-cards 画面 + 承認フロー | T2a | カード承認が可能に |
| T4a | worker: ポーリング分岐 + Step1〜4 + 不変条件 | T2b | ジョブが cards/sources まで走る |
| T4b | worker: outline / draft + prompts.py（版管理） | T4a | 本文生成 |
| T4c | worker: guard.py + 再生成フロー + trace 書込み | T4b | §5・§6 完成 |
| T5 | `/creative` UI + ポーリング + 結果タブ + admin generations 監視 | T2a（表示は T4c） | ユーザー導線完成 |
| T6a | `aozora_fetch` CLI + 夢十夜 ingestion（底本記録） | 新スタック + person置換 | 夢十夜コーパス |
| T6b | `creative_distill` CLI → カード draft → 管理画面で承認 → 閾値較正 | T3b, T4c, T6a | approved 初期カード一式 |
| T6c | E2E 手動シナリオ実施・受入条件（SPEC §16）照合 | 全部 | 受入記録 |
| T7 | ドキュメント同期（architecture / schema / API / operations / limitations） | T6c | docs 更新 |

並行トラック（コードと独立）: 環境構築チェックリスト（HANDOFF.md — GitHubリポジトリ・Firebase・Supabase はユーザー作成分担）。
T2〜T5 は新スタック無しで実装・モックテストまで進められる。実生成の確認は T6a 以降。

### ローカル検証環境（T2a で整備）

`supabase db reset` による全 migration チェーンの適用が**ローカルで可能**（Docker + Supabase CLI）。
pgvector 0.8.2 / PGroonga 3.2.5 はローカルスタックに同梱されている。

⚠️ **soseki 専用のローカルスタックを使うこと**。フォーク直後は `supabase/config.toml` の
`project_id` とポートが maurice と同一（`thinkerllm` / 55321-55329）で、同じコンテナ群を
共有してしまう。この状態で `supabase db reset` を実行すると maurice のローカルDBまで消える。
T2a で soseki 側を `project_id = "thinker-soseki"` / ポート **55421-55429** に分離済み
（決定事項「maurice とは完全分離」をローカル開発環境にも適用したもの）。

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"  # docker が PATH に無い場合
supabase start          # soseki専用スタック(55421-55429)
supabase db reset       # 全migrationを最初から適用
psql -h 127.0.0.1 -p 55422 -U postgres -d postgres   # パスワード postgres
```

実DB（Supabase クラウド）への適用は、新スタック作成後に `supabase link` → `db push` で行う。
