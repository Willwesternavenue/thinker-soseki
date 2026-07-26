# T0 監査メモ（thinker-maurice 実地調査・2026-07-24〜26）

> **正式版は [T0_AUDIT_REPORT.md](T0_AUDIT_REPORT.md)**（b92e60f と再照合済み・訂正9件を反映）。本メモは歴史的資料として保持。

前セッションで thinker-maurice のオンボード〜本番デプロイを行った際に**実コードで確認した事実**。
T0 正式レポートの土台。フォーク後のコードで再検証してから正本化すること（コミット b92e60f 時点の情報）。

## リポジトリ構成

```
frontend/   Next.js 16 (App Router, Turbopack) — Chat UI / Admin UI / Auth / Chat API(回答RAGフロー)
worker/     Python 3.12 + uv — ingestion/distillationジョブのポーリング常駐プロセス
supabase/   CLI設定・migrations(18本)・seedなし
scripts/    ユーザー作成スクリプト(tsx, firebase-admin)
docs/       FIREBASE_MIGRATION.md / CONTENT_INGESTION.md ほか
```

⚠️ frontend/AGENTS.md に「この Next.js は学習データと異なる breaking changes あり。
`node_modules/next/dist/docs/` を読んでから書け」とある。創作モードUI実装時に必読。

## DB スキーマ（migrations から確認済み）

- `personas` — person_id(text PK), display_name, system_prompt, first_person, banned_terms_*, style_rules/quote_policy/safety_policy(jsonb), fallback_card_id
- `sources` / `source_chunks`(embedding vector(1536), verbatim, importance, status) — L1
- `chunk_distillations` — 軽/重蒸留の結果(claims, candidate_thought_ids, misreading_risks, heavy_json)
- `thought_cards` — L2。card_id(text PK), person_id FK, thought_id, status(draft/reviewing/approved/rejected/deprecated), core_claim, distinctions(jsonb), answer_policy[], prohibitions[], representative_chunk_ids[], search_text, embedding。**approved一意制約**: (person_id, thought_id) where status='approved'
- `thought_questions` — 想定質問(embedding付き、ルーティング用)
- `thought_evidence_links` — カード⇔原典リンク(承認・quote_allowed)
- `judgment_rules` — L3(20260713000002)。rule_family/rule_scope等はここを実照合のこと
- `answer_traces` — L4。チャット固有の形
- `ingestion_jobs` / `distillation_jobs` — **workerがポーリングするジョブテーブル**(創作generationはこの同型で作る)
- `chat_sessions`(person_id FK) / `user_profiles`(user_id=Firebase UID text, role admin|tester)
- `glossary_terms`, `transcript_drafts`, `evaluation_logs`, `worker_heartbeat`
- RLS: 有効だがポリシー無し = deny-all。アクセスは全て service_role(サーバー側)
- RPC: `match_thought_questions` / `match_source_chunks(_all)` 等。**`target_person_id text default 'merleau_ponty'` がSQLにハードコード**(person置換対象)
- 拡張: pgvector + PGroonga(日本語全文)。`set search_path = extensions, public, pg_temp` が必要だった経緯あり(CLOUD_SETUP.md)

## person_id ハードコード箇所（漱石版では natsume_soseki へ置換）

- frontend: `lib/rag/pipeline.ts` (PERSON_ID) / `lib/rag/l3shadow.ts` (PERSON_ID) / `app/chat/actions.ts` / `admin/{sources,cards,questions,transcripts}/actions.ts` / `api/admin/eval/route.ts` 等 約15箇所
- worker: `import_cards.py` / `gen_cards.py`(default引数) / `import_judgment_rules.py`
- migrations: RPC の default 値
- 参考: maurice の置換コミット 9587aaa「person置換: x_shigyo → merleau_ponty」

## 回答パイプライン（frontend/src/lib/rag/pipeline.ts）

1. 分類(queryKind: thought/life_advice/person_or_work/... needsThoughtCards判定) — Haiku
2. ルーティング(router.ts): ①概念エイリアス展開 → ②thought_questions類似照合 → ③カードembedding+LLM。routing_method をtraceに記録
3. approvedカード取得 → mergeThoughtCards
4. **不変条件(pipeline.ts:125)**: needsThoughtCards でカード0枚なら throw(フォールバックカード resolveFallbackCard は personas.fallback_card_id + status=approved 必須)
5. evidence取得(embedding検索+PGroonga、thought_id絞り+全体検索の併用)
6. 回答生成 — Sonnet、非ストリーミング
7. Output Guard(judge=Haiku) 通過後に返却
8. `after()` で answer_traces 保存(App Hosting cpu>=1 必須)

## モデル・キー

- モデルは定数: `llm.ts` MODEL_ANSWER=claude-sonnet-5 / MODEL_LIGHT=claude-haiku-4-5-20251001。worker config.py も同様+MODEL_PRICES
- **provider abstraction は無い**(指示書§14の前提と不一致)
- Embedding: OpenAI text-embedding-3-small(1536次元)のみ。`lib/embedding.ts` / worker embed step
- 秘匿キー解決: **env優先 → Secret Manager フォールバック**(`lib/secrets.ts` / `config.py:_load_secrets`)。ローカルはシェルexportで完結
- 非秘匿設定: `frontend/src/lib/const.ts` / `worker/src/config.py` の定数(.env廃止済み)

## 蒸留・カード生成フロー（worker）

- ingestion: extract → clean(話者正規化) → chunk(CHUNKER_VERSION=v1) → embed → distill_light(Haiku)
- `src.distill heavy|cards|questions|all`(CLI) — 重蒸留(Sonnet) → gen_cards.py がカードdraft生成
- **gen_cards.py の要点**: candidate_thought_ids ごとに evidence を集約し、`MIN_EVIDENCE_CHUNKS=2` 未満はカード化しない。既存カード(rejected以外)がある thought_id はスキップ。→ **創作カードの自動draftはこの機構の創作用プロンプト差し替えで実現可能**
- DISTILL_CONCURRENCY=8 / LLM_MAX_RETRIES=8(529対策)

## 運用制約（本番で確認済み）

- **App Hosting リクエスト上限5分**。チャット実測27〜82秒。多段生成は超過リスク → 創作生成はworkerジョブ型に
- worker は Cloud Run min=max=1・no-cpu-throttling 必須(0=停止、2+=二重処理)
- セッションCookie `__session` 固定(14日)。ログイン検証は proxy.ts + lib/auth.ts
- admin/tester の2ロール。アカウント発行は scripts/createUser.ts(firebase-admin)

## テスト基盤

- worker: `uv run pytest`(16件: チャンカー決定性・話者正規化・verbatim導出)
- frontend: `npx vitest run`(11件: 引用可能フィルタ・カード統合・Guard完全一致)
- `supabase db reset` で migration+seed 適用確認
- E2E基盤は無い

## 管理画面（実在するルート）

`/admin/sources`(アップロード) / `/admin/jobs`(worker監視・heartbeatバナー) / `/admin/cards`(承認・distill実行) /
`/admin/chunks` / `/admin/questions` / `/admin/persona` / `/admin/evaluations` / `/admin/transcripts` / `/admin/architecture`(構成図)

## 指示書との主な不整合（レビュー済み・改訂対象）

1. §14 provider abstraction → 存在しない(定数パターン踏襲に改訂)
2. §4 author_id → person_id に(personasが正)
3. §6 リクエスト内10段パイプライン → workerジョブ型に
4. §7 人手起草前提 → 蒸留機構の再利用で自動draft→承認
5. §5.3 5モードenum → 直交フラグ+プリセット名
6. 仮名遣いポリシー欠落 → creative_profiles 必須フィールドへ
