# 思想蒸留型RAG / X執行 MVP

執行草舟の思想・語り口をもとにした会員向けAIアバター「X執行」。
仕様: [xshigyo_mvp_spec_v1_1.md](./xshigyo_mvp_spec_v1_1.md)

原典チャンクの一括ベクトル検索ではなく、質問 → thought_id ルーティング → approved思想カード必読 → 原典チャンク補強、という固定ワークフローで回答する。

## 構成

```
frontend/   Next.js (App Router) — Chat UI / Admin UI / Auth UI / Chat API (回答時RAGフロー)
worker/     Python Worker — テキスト抽出・整形・チャンク化・embedding・蒸留 (service_roleで動作)
supabase/   Supabase CLI設定・migrations・seed
```

- LLM: Claude(軽蒸留・Guard judge = Haiku、重蒸留・カード生成・回答 = Sonnet)
- Embedding: OpenAI `text-embedding-3-small`(1536次元)
- DB: Supabase PostgreSQL + pgvector(正確検索)+ PGroonga(日本語全文検索)。アクセスはサーバー側 service_role のみ
- 認証: **Firebase Auth**(email/password + `__session` Cookie)。ホスティング: **Firebase App Hosting**(frontend)+ Cloud Run(worker)— 手順は [docs/FIREBASE_MIGRATION.md](./docs/FIREBASE_MIGRATION.md)
- 回答は非ストリーミング(Output Guard通過後に返却)

## セットアップ

前提: Node.js 20+ / uv / gcloud CLI。**.envファイルは使わない**
(秘匿キーはSecret Manager、非秘匿設定は `frontend/src/lib/const.ts` / `worker/src/config.py`)。

```bash
# 1. 秘匿キー取得のためのADC(初回のみ。al-thinker-devのSecret Manager閲覧権限が必要)
gcloud auth application-default login

# 2. frontend(クラウドSupabase・本物のFirebase Authに接続される)
cd frontend && npm install && npm run dev

# 3. worker(ingestion_jobsをポーリングする常駐プロセス)
cd worker && uv sync && uv run python -m src.main
```

ローカルSupabaseで動かす場合は `supabase start` の上で、シェルで
`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` を一時的にexportして起動する。

## 検証ユーザーの作成(admin 1名 + tester 2名)

一般ユーザー登録フォームは意図的に作らない。管理者が発行する
(Firebase Authユーザー作成+`user_profiles`ロール登録を一括で行うスクリプトあり):

```bash
cd scripts && npm install   # 初回のみ
npm run create-user -- --email admin@example.com --role admin --name 管理者
npm run create-user -- --email tester1@example.com   # tester、パスワード自動生成
```

Firebaseコンソール(Authentication → Users)からの手動発行でもよい。その場合、
初回ログイン時に `user_profiles` へ tester として自動作成されるので、adminは
SQLエディタで `update user_profiles set role='admin' where user_id='<UID>';` を実行する。

## 運用フロー

> 新しい人物向けの投入手順(persona登録〜カード承認)の詳細は
> [docs/CONTENT_INGESTION.md](./docs/CONTENT_INGESTION.md) を参照。以下は概要。

1. **原典投入**: `/admin/sources` からPDF/Word/TXTをアップロード → Workerが extract → clean(話者正規化)→ chunk → embed → 軽蒸留 を自動実行(`/admin/jobs` で監視・再実行)
2. **蒸留**(Phase 2、CLIで実行):
   ```bash
   cd worker
   uv run python -m src.distill heavy        # 重蒸留(importance=highチャンク)
   uv run python -m src.distill source BOOK_001
   uv run python -m src.distill cards        # 思想カード候補生成(draft)
   uv run python -m src.distill questions    # 質問対応情報生成
   uv run python -m src.distill all          # 上記一括
   ```
3. **レビュー**: `/admin/cards` でdraftカードを編集・承認(approved化で本番回答に使用)。原典リンクの承認とquote_allowed設定もカード詳細で行う。`/admin/chunks` で重要度変更、`/admin/questions` で質問の追加・テスト検索
4. **チャット**: `/chat`。adminは各回答の「参照情報を見る」でルーティング・ヒットチャンク・Guard結果を確認できる
5. **評価**: `/admin/evaluations` で評価セット(`evaluation/questions.json` を対象人物用に用意)を実行し、5観点スコアを記録。フォールバック発生質問はthought_questions追加候補として集計される

## テスト

```bash
cd worker && uv run pytest          # チャンカー決定性・話者正規化・verbatim導出など(16件)
cd frontend && npx vitest run       # 引用可能フィルタ・カード統合・Guard完全一致など(11件)
supabase db reset                   # migration + seed の適用確認
```
