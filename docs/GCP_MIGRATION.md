# 【旧版】GCP移行ランブック(Vercel → Cloud Run)

> **このランブックは [FIREBASE_MIGRATION.md](./FIREBASE_MIGRATION.md) で置き換えられた**(2026-07-16)。
> 実際の移行先は Firebase App Hosting(frontend)+ Cloud Run(worker)+ Firebase Auth で、
> .env と NEXT_PUBLIC_* ビルド引数は廃止済み。以下は素のCloud Runで動かす場合の参考として残す。

Supabaseのプロジェクト移管(Project Transfer)完了後の、ホスティング移行手順。
Supabaseはそのまま(SaaS)。GCPに載るのは frontend と worker の2つ。

```text
[ユーザー] → Cloud Run: frontend (Next.js)  ─┐
                                             ├─→ Supabase (Postgres/pgvector/Auth) ← 変更なし
             Cloud Run: worker (Pythonポーリング) ─┘
             Secret Manager(キー類) / Artifact Registry(イメージ)
```

## 0. 前提・注意

- **コードはGitHubリポジトリごと移すこと(ZIP不可推奨)**。ZIPはgit履歴・push/pull運用・
  マイグレーション管理が切れる。新オーナーへは GitHub の Transfer ownership か
  `git push --mirror` を使う。
- `.env` / `frontend/.env.local` は gitignore のため**リポジトリに含まれない**。
  必要な変数は本書 2章 の一覧を新環境で用意する。
- Supabase Transfer後の確認: URL・anon/service_roleキーは原則不変だがダッシュボードで要確認。
  無料プランは**7日アクセスなしで一時停止**するため、本番運用はPro推奨。
  新管理者のマシンで `supabase login` → `supabase link --project-ref <ref>` を再設定し、
  以後のスキーマ変更は必ず `supabase db push`(SQLエディタ直接適用は履歴がズレる)。

## 1. GCP初期設定(1回だけ)

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com

# イメージ置き場
gcloud artifacts repositories create thinkerllm \
  --repository-format=docker --location=asia-northeast1
```

## 2. Secret Manager にキーを登録

コードが参照する環境変数(実測の全一覧):

| 変数 | frontend | worker | 備考 |
|---|---|---|---|
| SUPABASE_URL | - | ✅ | |
| NEXT_PUBLIC_SUPABASE_URL | ✅(ビルド時) | - | クライアント公開 |
| NEXT_PUBLIC_SUPABASE_ANON_KEY | ✅(ビルド時) | - | クライアント公開 |
| SUPABASE_SERVICE_ROLE_KEY | ✅ | ✅ | **全権限。Secret必須** |
| ANTHROPIC_API_KEY | ✅ | ✅ | 移管先組織のキーを使う |
| OPENAI_API_KEY | ✅ | ✅ | embedding用 |
| L3_MODE | ✅ | - | `assist`(未設定ならshadow) |
| HF_TOKEN | - | 任意 | Whisper利用時のみ |

```bash
printf '%s' '<value>' | gcloud secrets create SUPABASE_SERVICE_ROLE_KEY --data-file=-
printf '%s' '<value>' | gcloud secrets create ANTHROPIC_API_KEY --data-file=-
printf '%s' '%s' '<value>' | gcloud secrets create OPENAI_API_KEY --data-file=-
# (SUPABASE_URL / L3_MODE は秘密ではないので --set-env-vars でよい)
```

## 3. frontend のビルドとデプロイ

**NEXT_PUBLIC_* はビルド時にJSへ焼き込まれる**ため、build-arg で渡す(実行時envでは効かない)。

```bash
cd frontend
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/<PROJECT_ID>/thinkerllm/frontend:v1 \
  --build-arg NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co \
  --build-arg NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
# ※gcloud builds submitがbuild-arg非対応のバージョンの場合は cloudbuild.yaml を使うか、
#   ローカルで docker build --platform linux/amd64 して push する

gcloud run deploy thinkerllm-frontend \
  --image asia-northeast1-docker.pkg.dev/<PROJECT_ID>/thinkerllm/frontend:v1 \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --timeout=300 \
  --memory=1Gi \
  --min-instances=0 \
  --no-cpu-throttling \
  --set-env-vars L3_MODE=assist \
  --set-secrets SUPABASE_SERVICE_ROLE_KEY=SUPABASE_SERVICE_ROLE_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest
```

**重要フラグの理由**:
- `--timeout=300` — 回答生成は非ストリーミングで実測27〜82秒。Vercelの
  `export const maxDuration`(route.tsの120/800秒指定)はVercel固有で、Cloud Runでは
  **サービス側timeoutが上限**になる。transcripts/process(800秒)を使う場合は
  `--timeout=900` にするか、そのルートを使う時だけ引き上げる
- `--no-cpu-throttling` — **必須**。回答パイプラインは `after()`(レスポンス送信後に
  trace保存・L3判定回収・セッション要約)を使っており、既定のCPUスロットリングだと
  レスポンス後の処理が凍結してtraceが落ちる
- `--min-instances=0` はコールドスタート(1〜2秒)許容の場合。管理UIの快適さ優先なら 1

## 4. worker のデプロイ

ポーリング常駐プロセス。Cloud Runサービスとして動かすため、`$PORT` があるときだけ
最小ヘルスサーバが立つ(src/main.py。ローカル実行では従来どおり何も変わらない)。

```bash
cd worker
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/<PROJECT_ID>/thinkerllm/worker:v1

gcloud run deploy thinkerllm-worker \
  --image asia-northeast1-docker.pkg.dev/<PROJECT_ID>/thinkerllm/worker:v1 \
  --region asia-northeast1 \
  --no-allow-unauthenticated \
  --min-instances=1 --max-instances=1 \
  --no-cpu-throttling \
  --memory=1Gi \
  --set-env-vars SUPABASE_URL=https://<ref>.supabase.co \
  --set-secrets SUPABASE_SERVICE_ROLE_KEY=SUPABASE_SERVICE_ROLE_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest
```

**重要フラグの理由**:
- `--min-instances=1 --max-instances=1` — **必ず1台**。0だと止まり(取り込み・カード生成が
  全停止)、2台以上だと同じジョブを二重処理しうる(ジョブ回収は単一worker前提の設計)
- `--no-cpu-throttling` — リクエスト外で常時ポーリングするため必須
- 代替: 月額固定が読みやすい **GCE e2-small VM + docker run --restart=always** でもよい

## 5. Vercel撤収チェックリスト

- [ ] Vercelダッシュボードの環境変数一覧を控える(GCP側と過不足照合)
- [ ] カスタムドメインがあればDNSをCloud Run(ドメインマッピング or LB)へ切替
- [ ] 切替後、旧Vercelデプロイを停止/削除(二重稼働で両方からSupabaseを叩かない)
- [ ] `export const maxDuration` はコードに残してよい(Vercel固有・Cloud Runでは無視される)

## 6. 移行後の動作検証

1. `GET /login` が200
2. adminでログイン → チャットで1問(life_advice系)→ 回答が返る
3. Supabaseで `answer_traces` の最新行に `l3_shadow.mode = "assist"` が記録されている
   (= after() がCloud Run上で動いている証拠。**これが無ければ --no-cpu-throttling を疑う**)
4. ジョブ画面でWorker稼働バナーが「稼働中」(heartbeat)
5. 原典を1件アップロード → 5ステップ完走
6. 評価タブで1問実行 → evaluation_logs に記録

## 7. コスト目安(asia-northeast1)

- frontend: min 0なら実質リクエスト分のみ(月数百円〜)。min 1なら+$15前後/月
- worker: 常時1台(1CPU/1Gi, always-on)で$20〜30/月程度。GCE e2-smallなら$15前後/月
- 支配的なのはLLM API費(Anthropic/OpenAI)でGCP費ではない
