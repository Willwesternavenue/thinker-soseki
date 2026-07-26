# Firebase移行ランブック(Vercel脱却 / .env廃止)

2026-07-16のコード変更に対応する移行・運用手順。旧ランブック
[GCP_MIGRATION.md](./GCP_MIGRATION.md)(素のCloud Run案)はこれで置き換える。

```text
[ユーザー] → Firebase App Hosting: frontend (Next.js) ─┐
                                                       ├─→ Supabase (Postgres/pgvector) ← DB専用(Authは廃止)
             Cloud Run: worker (Pythonポーリング)      ─┘
             Firebase Auth(ログイン) / Secret Manager(秘匿キー)
             プロジェクト: al-thinker-dev(frontend=asia-east1 / worker=asia-northeast1)
```

## 何が変わったか(コード側・適用済み)

| 項目 | 旧 | 新 |
|---|---|---|
| ホスティング | Vercel | **Firebase App Hosting**(Next.js公式アダプタ。リクエスト上限5分) |
| 認証 | Supabase Auth(anonキー+RLS) | **Firebase Auth**(email/password)+ `__session` Cookie(httpOnly, 14日) |
| Supabase | Auth+DB+Storage | **DBとStorageのみ**。サーバーからservice_roleで一本化。anonキー・`@supabase/ssr` 廃止 |
| 権限制御 | RLS(auth.uid()/is_admin) | アプリ層(`requireAdmin`/`requireUser`+user_id絞り込み)。RLSは有効のままポリシー無し=deny-all |
| 秘匿キー | `.env` / `frontend/.env.local` | **Secret Manager**(env未設定なら起動時にコードが直接取得) |
| 非秘匿設定 | .env | `frontend/src/lib/const.ts` / `worker/src/config.py` の定数 |
| user_id | uuid(auth.users FK) | text(Firebase UID)。migration `20260716000001` |

- セッションCookie名が `__session` 固定なのは、Firebase HostingのCDNがこの名前しか
  バックエンドへ通さないため。
- ログインの流れ: クライアントでFirebaseログイン → idTokenを `/api/auth/session` へPOST →
  firebase-adminがセッションCookieを発行。以後の検証は proxy.ts と `lib/auth.ts`。
- 初回ログイン時に `user_profiles` へ tester として自動作成される。admin昇格はSQL:
  `update user_profiles set role='admin' where user_id='<Firebase UID>';`

## 0. 前提

- Firebaseプロジェクト **al-thinker-dev** 作成済み(configは `frontend/src/lib/const.ts`)。
  App Hostingには **Blazeプラン**(従量課金)が必要。
- CLI: `firebase`(`npm i -g firebase-tools` → `firebase login`)と `gcloud`(`gcloud auth login`)。
- コードはGitHubリポジトリのまま移す(App HostingはGitHub連携でビルドする)。

## 1. Firebase Auth のセットアップ

1. コンソール → Authentication → Sign-in method → **メール/パスワードを有効化**(初回のみ)。
2. アカウント発行はスクリプトで(Firebase Authユーザー作成+user_profilesロール登録まで一括):
   ```bash
   cd scripts && npm install   # 初回のみ
   npm run create-user -- --email admin@example.com --role admin --name 管理者
   npm run create-user -- --email tester1@example.com   # tester、パスワード自動生成
   ```
   コンソールのUsersタブから手動発行してもよい(その場合adminは3章のSQLでrole昇格)。

## 2. Secret Manager にキーを登録

**注意: gcloud/firebaseコマンドは常に `--project al-thinker-dev` を明示する。**
複数プロジェクトを扱っているため、`gcloud config set project` でデフォルトを
変えるやり方は使わない(他プロジェクトへの誤操作防止)。

```bash
gcloud services enable secretmanager.googleapis.com --project al-thinker-dev   # 有効化済み(2026-07-16)

printf '%s' '<service_roleキー>' | gcloud secrets create SUPABASE_SERVICE_ROLE_KEY --data-file=- --project al-thinker-dev
printf '%s' '<Anthropicキー>'   | gcloud secrets create ANTHROPIC_API_KEY --data-file=- --project al-thinker-dev
printf '%s' '<OpenAIキー>'      | gcloud secrets create OPENAI_API_KEY --data-file=- --project al-thinker-dev
```

値の更新は `gcloud secrets versions add <名前> --data-file=- --project al-thinker-dev`
(コードは常にlatestを読む)。

## 3. DBマイグレーション(Supabaseはそのまま使う)

```bash
supabase link --project-ref cnhqrjmvchtqkauynevi   # 未linkなら
supabase db push                                    # 20260716000001_firebase_auth.sql
```

**既存ユーザーのデータ引き継ぎ**(任意): 旧Supabaseユーザーのuuidが
user_profiles / chat_sessions に残っている。旧ユーザーを新Firebaseアカウントへ
対応付けるには、SQLエディタで:

```sql
-- 旧uuidの確認: select user_id, display_name, role from user_profiles;
update public.user_profiles set user_id = '<新Firebase UID>' where user_id = '<旧uuid>';
update public.chat_sessions set user_id = '<新Firebase UID>' where user_id = '<旧uuid>';
```

引き継がない場合は放置でよい(旧行は誰からも見えなくなるだけ)。
adminにするユーザーには `role='admin'` を設定するのを忘れない
(初回ログイン自動作成は tester)。

## 4. frontend を App Hosting へデプロイ

**作成済み(2026-07-16)**: バックエンド `web-frontend` / リージョン `asia-east1` /
リポジトリ `AIdeaLab-thinkerllm` / URL https://web-frontend--al-thinker-dev.asia-east1.hosted.app

```bash
cd frontend
firebase apphosting:backends:create --project al-thinker-dev
# 対話で: GitHubリポジトリを接続 / ルートディレクトリ frontend / ブランチ main

# シークレットへのアクセス権をバックエンドへ付与(3つとも。実施済み 2026-07-16)
firebase apphosting:secrets:grantaccess SUPABASE_SERVICE_ROLE_KEY --backend web-frontend --project al-thinker-dev
firebase apphosting:secrets:grantaccess ANTHROPIC_API_KEY --backend web-frontend --project al-thinker-dev
firebase apphosting:secrets:grantaccess OPENAI_API_KEY --backend web-frontend --project al-thinker-dev
# ※これを忘れると rollout が「Error resolving secret version ...」で失敗する
```

- 以後は **mainへのpushで自動ビルド&デプロイ**。手動デプロイは `cd frontend && npm run deploy`
  (= `firebase apphosting:rollouts:create web-frontend --project al-thinker-dev --git-branch main`)。
  **App HostingはGitHubのmainからビルドする**ため、ローカルの変更は commit & push してから実行する。
- 実行設定は [apphosting.yaml](../frontend/apphosting.yaml)。**cpu: 1 を下げないこと**
  (`after()` によるtrace保存が止まる)。
- カスタムドメインはコンソールの App Hosting → ドメイン から追加(DNSをFirebaseへ向ける)。

## 5. worker を Cloud Run へデプロイ

ポーリング常駐プロセス(Firebaseでは動かせないためCloud Run)。`$PORT` があるときだけ
最小ヘルスサーバが立つ(src/main.py)。

2回目以降のデプロイは `cd frontend && npm run deploy:worker` 一発でよい
(ビルド→デプロイを実行。下記コマンドと同内容)。初回は事前に以下のAPI有効化と
リポジトリ作成が必要:

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com --project al-thinker-dev
gcloud artifacts repositories create thinkerllm \
  --repository-format=docker --location=asia-northeast1 \
  --project al-thinker-dev   # 初回のみ

cd worker
gcloud builds submit --project al-thinker-dev \
  --tag asia-northeast1-docker.pkg.dev/al-thinker-dev/thinkerllm/worker:v1

gcloud run deploy thinkerllm-worker \
  --project al-thinker-dev \
  --image asia-northeast1-docker.pkg.dev/al-thinker-dev/thinkerllm/worker:v1 \
  --region asia-northeast1 \
  --no-allow-unauthenticated \
  --min-instances=1 --max-instances=1 \
  --no-cpu-throttling \
  --memory=1Gi
```

- 秘匿キーは環境変数指定不要(config.pyがSecret Managerから直接取得)。ただし
  実行サービスアカウントに `roles/secretmanager.secretAccessor` を付与すること:
  ```bash
  gcloud projects add-iam-policy-binding al-thinker-dev \
    --member "serviceAccount:$(gcloud run services describe thinkerllm-worker \
      --project al-thinker-dev --region asia-northeast1 \
      --format 'value(spec.template.spec.serviceAccountName)')" \
    --role roles/secretmanager.secretAccessor
  ```
- `--min-instances=1 --max-instances=1` は**必ず1台**(0だと取り込み全停止、
  2台以上は同一ジョブ二重処理の恐れ)。`--no-cpu-throttling` はポーリング常駐のため必須。

## 6. ローカル開発(.env不要)

```bash
gcloud auth application-default login

cd frontend && npm install && npm run dev    # 起動時にSecret Managerからキー取得
cd worker && uv sync && uv run python -m src.main
```

- コードがprojectId/quota projectを `al-thinker-dev` に固定しているため、gcloudの
  デフォルトプロジェクトやADCのquota project設定が別プロジェクトでも影響しない。

- 開発者のGoogleアカウントにプロジェクトの `roles/secretmanager.secretAccessor` 以上が必要。
- ローカルSupabaseで動かす場合だけシェルで `SUPABASE_URL=http://127.0.0.1:55321` を
  一時的にexportする(キーもローカル値をexport)。ファイルとしての.envは使わない。
- ログインはローカルでも本物のFirebase Auth(al-thinker-dev)に対して行う。

## 7. 移行後の動作検証

1. `GET /login` が200 → adminでログインできる(初回はuser_profilesに行が自動作成
   されるので `role='admin'` をSQLで付与済みか確認)
2. チャットで1問(life_advice系)→ 回答が返る
3. Supabaseで `answer_traces` に新しい行が入る(= `after()` がApp Hosting上で
   動いている証拠。**入らなければ apphosting.yaml の cpu を疑う**)
4. ジョブ画面でWorker稼働バナーが「稼働中」(heartbeat)
5. 原典を1件アップロード → 5ステップ完走
6. 評価タブで1問実行 → evaluation_logs に記録
7. tester アカウントでログイン → /chat のみ・traceが見えないこと

## 8. Vercel撤収チェックリスト

- [ ] Vercelの環境変数一覧を控える(Secret Manager側と過不足照合)
- [ ] カスタムドメインがあればDNSをApp Hostingへ切替
- [ ] 切替後、旧Vercelプロジェクトを停止/削除(二重稼働で両方からSupabaseを叩かない)
- [ ] Supabaseダッシュボード → Authentication は以後未使用(ユーザー管理はFirebaseコンソール)

## 9. 制約・運用メモ

- **App Hostingのリクエスト上限は5分**。チャット回答(実測27〜82秒)は問題ないが、
  /admin/スクリプト整形の長尺処理は5分で切断されることがある。processed_segmentsを
  都度保存しているため「続きから整形」で再開すればよい(データは失われない)。
- セッションCookieは**14日で失効**(firebase-adminの上限)。失効後は再ログイン。
- パスワードリセット等はFirebaseコンソールから(アプリ内UIは未実装)。
- `export const maxDuration` はVercel固有の指定でコードに残っているが無害。
- コスト目安: App Hosting(min 0)は実質リクエスト分のみ、worker常時1台で$20〜30/月。
  支配的なのはLLM API費でインフラ費ではない。
