# Firebase セットアップ手順（漱石版）

> 目的: 管理画面（T3）と創作UI（T5）を動かすための認証基盤を用意する。
> `frontend/src/proxy.ts` が `/api` 以外の全ページを `__session` Cookie で守っているため、
> **画面を1枚でも開くには Firebase Auth が必要**。
>
> ⚠️ worker（コーパス取り込み・生成パイプライン）は Firebase に**一切依存しない**。
> ここまでの C-T2〜C-T7 は Firebase なしで完了している。
>
> maurice で踏んだ罠は [NEW_PERSON_CHECKLIST.md](NEW_PERSON_CHECKLIST.md) §6 が正本。
> 本書はそれを漱石の実値に落としたもの。

## 費用について

**Spark（無料）プランで足りる**。Firebase Auth のメール/パスワード認証は無料枠に含まれる。
課金（Blaze）が必要になるのは **App Hosting でデプロイする段階**なので、
ローカル開発だけなら $0 で進められる。

---

## 手順

### 1. Firebase プロジェクトを作る（ユーザー作業）

1. https://console.firebase.google.com/ を開く
2. 「プロジェクトを追加」→ プロジェクト名に **`thinker-soseki`** と入力
3. Google アナリティクスは**不要**（オフでよい）
4. 作成完了後、**プロジェクトIDを控える**

> ⚠️ **最大の罠**: Firebase は入力した名前が既に使われていると
> **別サフィックス付きのIDを自動で割り当てる**（maurice では `thinker-maurice` と
> `thinker-maurice-9082f` の2つが生まれて混乱した）。
> 画面に表示された**実際のプロジェクトID**を必ず確認すること。以降すべてこの値を使う。
> 「プロジェクトの設定」→「全般」→「プロジェクトID」で確認できる。

### 2. メール/パスワード認証を有効化（忘れやすい）

1. 左メニュー「構築」→「Authentication」→「始める」
2. 「Sign-in method」タブ →「メール/パスワード」→ **有効にする** → 保存

> これを忘れるとログイン画面で必ず失敗する。maurice でも一度踏んだ。

### 3. Web アプリを登録して firebaseConfig を取得

1. 「プロジェクトの設定」→「全般」→ 下部の「マイアプリ」
2. **Web アイコン `</>`** を選ぶ
3. アプリのニックネームは `web-frontend` などでよい。
   **「Firebase Hosting も設定する」はチェックしない**（App Hosting を使うため）
4. 表示される `firebaseConfig` の**6項目**を控える

```js
const firebaseConfig = {
  apiKey: "...",              // ← 公開情報。秘密ではない
  authDomain: "....firebaseapp.com",
  projectId: "...",
  storageBucket: "....firebasestorage.app",
  messagingSenderId: "...",
  appId: "1:...:web:..."
};
```

### 4. サービスアカウントキーを作る（ローカル開発用・必須）

> ⚠️ **firebase-admin は ADC の quota project を無視する**。
> `gcloud auth application-default login` だけでは動かない（maurice で確認済み）。
> **サービスアカウントキーが必須**。

1. 「プロジェクトの設定」→「サービス アカウント」タブ
2. 「新しい秘密鍵の生成」→ JSON がダウンロードされる
3. **`~/.config/gcp-keys/soseki-adminsdk.json` に置く**（リポジトリには入れない）

```bash
mkdir -p ~/.config/gcp-keys
mv ~/Downloads/thinker-soseki-*-firebase-adminsdk-*.json ~/.config/gcp-keys/soseki-adminsdk.json
chmod 600 ~/.config/gcp-keys/soseki-adminsdk.json
```

### 5. ローカル用の環境変数ファイルを作る

`~/.config/gcp-keys/soseki.env` を作る（**リポジトリには入れない**）。

```bash
export GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcp-keys/soseki-adminsdk.json
export SUPABASE_URL=http://127.0.0.1:55421
export SUPABASE_SERVICE_ROLE_KEY=<supabase status の SECRET_KEY>
export ANTHROPIC_API_KEY=<実キー>
export OPENAI_API_KEY=<実キー>
```

> `SUPABASE_SERVICE_ROLE_KEY` は `supabase status -o json` の `SECRET_KEY` を使う。
> **ソースには絶対に書かない**（GitHub の push protection に弾かれる。実際に一度弾かれた）。

---

## 私に渡してほしい値

以下をもらえれば、コード側の差し替えは私が行う。

| 項目 | 取得場所 |
|---|---|
| **プロジェクトID** | 設定 → 全般 → プロジェクトID |
| **firebaseConfig の6項目** | 設定 → 全般 → マイアプリ → Web アプリ |

秘密ではないので、そのまま貼ってもらって構わない（`apiKey` は公開前提の値）。
**サービスアカウントの JSON は渡さないこと**（これは秘密。ローカルに置くだけでよい）。

差し替える箇所は3ファイル:

- `frontend/src/lib/const.ts` — `GCP_PROJECT_ID` と `FIREBASE_CONFIG` 6項目
- `worker/src/config.py` — `GCP_PROJECT_ID`
- `scripts/src/operation/createUser.ts` — `GCP_PROJECT_ID`
- `frontend/package.json` — deploy スクリプトの `--project`

---

## 差し替え後にできること

### admin ユーザーの作成

```bash
cd scripts
npm install
source ~/.config/gcp-keys/soseki.env
SUPABASE_URL=http://127.0.0.1:55421 npm run create-user -- --email <あなたのメール> --role admin
```

パスワードは自動生成されて表示される（`--password` で指定も可）。

### 画面の起動

```bash
cd frontend
npm install
source ~/.config/gcp-keys/soseki.env
npm run dev
```

http://localhost:3000 でログインできれば成功。

---

## Supabase クラウドについて（今は不要）

現在は**ローカル Supabase だけで開発している**（$25 の課金を避けるため）。
上記の手順は `SUPABASE_URL=http://127.0.0.1:55421` を前提にしている。

クラウドの Supabase プロジェクトを作る場合は、`const.ts` / `config.py` /
`createUser.ts` の `SUPABASE_URL` を実値に変え、`supabase link` → `supabase db push`
を実行する。手順は [NEW_PERSON_CHECKLIST.md](NEW_PERSON_CHECKLIST.md) §5・§6 を参照。

## 本番デプロイについて（さらに先）

App Hosting でのデプロイには **Blaze プラン（従量課金）**が必要になる。
その段階の手順（請求先リンク・Secret Manager 登録・App Hosting backends:create・
Cloud Run の `--set-secrets`）は [NEW_PERSON_CHECKLIST.md](NEW_PERSON_CHECKLIST.md) §6 に
maurice の実績としてまとめてある。

> ⚠️ 請求先アカウントには**プロジェクト数のクォータ**がある。
> maurice では `012B6E-...` が上限に達しており `01B6B4-...` を使った。
