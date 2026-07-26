# クラウドSupabase セットアップ / 同僚オンボード

DBを**クラウドSupabase**に載せ、複数人で同じDBを共有できるようにした記録と手順(2026-07-08)。
※初代人物(執行草舟)の時代に書かれた記録。手順自体は人物に依存しない。

> **2026-07-16更新**: 認証はFirebase Auth、ホスティングはFirebase App Hostingへ移行し、
> **.env / frontend/.env.local は廃止**した(下記2〜3の手順は旧方式)。
> 現在のオンボードは [docs/FIREBASE_MIGRATION.md](./docs/FIREBASE_MIGRATION.md) の
> 「6. ローカル開発」を参照: `gcloud auth application-default login` だけでキーが
> Secret Managerから自動取得される。ログインアカウントはFirebaseコンソールで発行。

## 構成
- **project ref**: `cnhqrjmvchtqkauynevi` / URL `https://cnhqrjmvchtqkauynevi.supabase.co` / region `ap-southeast-2`
- **正本はこのクラウドDB**。各自ローカルの Worker / フロントをここに接続する(ローカルDockerのDBは開発用に残してよいが、共同作業では使わない)。
- 接続先は `.env`(Worker用)と `frontend/.env.local`(フロント用)の URL + キーで切り替わる。両方 gitignore 済み。

## 同僚のオンボード手順
1. リポジトリを clone: `git clone git@github.com:Willwesternavenue/thinker-soseki.git`
2. `.env`(リポジトリ直下)を作成:
   ```
   SUPABASE_URL=https://cnhqrjmvchtqkauynevi.supabase.co
   SUPABASE_ANON_KEY=<anonキー>
   SUPABASE_SERVICE_ROLE_KEY=<service_roleキー>
   ANTHROPIC_API_KEY=<各自のキー>
   OPENAI_API_KEY=<各自のキー>
   HF_TOKEN=<Whisper使う場合のみ>
   ```
3. `frontend/.env.local` を作成:
   ```
   NEXT_PUBLIC_SUPABASE_URL=https://cnhqrjmvchtqkauynevi.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=<anonキー>
   SUPABASE_SERVICE_ROLE_KEY=<service_roleキー>
   ANTHROPIC_API_KEY=<各自のキー>
   OPENAI_API_KEY=<各自のキー>
   ```
   - anon/service_roleキーは Supabaseダッシュボード → Project Settings → API、または
     `supabase projects api-keys --project-ref cnhqrjmvchtqkauynevi` で取得。
   - **service_roleキーは全権限(RLS貫通)**。Slack等に平文で貼らない。1Password等で共有。
4. フロント起動(Apple Siliconは **arm64 Node v24.15.0** 必須):
   ```
   export PATH="/Users/<you>/.nvm/versions/node/v24.15.0/bin:$PATH"
   cd frontend && npm run dev
   ```
5. Worker(取り込み・カード生成の処理エンジン)は**どこか1台が常駐**していれば全員で共有できる:
   ```
   cd worker && uv run python -m src.main
   ```

## 初回移行でやったこと(再現用メモ)
1. スキーマ: `supabase link --project-ref cnhqrjmvchtqkauynevi` → `supabase db push`
2. データ: ローカルから `supabase db dump --local --data-only -f snapshot.sql` →
   `PGPASSWORD=... psql -h aws-0-ap-southeast-2.pooler.supabase.com -p 5432 -U postgres.cnhqrjmvchtqkauynevi -d postgres -f snapshot.sql`
   - **Session pooler(5432)** を使う(Transaction 6543は大量INSERTの復元に不向き)。直結 `db.<ref>.supabase.co` はIPv6のみで繋がらないことがある。
3. 移行時に踏んだ問題と対処(コミット済み):
   - **RPCがリモートで作成失敗** (`operator does not exist: extensions.vector <=> extensions.vector`):
     vector/pgroonga拡張が `extensions` スキーマにあり、関数作成時の search_path に無く演算子解決できず。
     → 該当4関数に `set search_path = extensions, public, pg_temp` を付与(f6d6137)。
   - **glossary_terms が122件に二重化**: マイグレーションが初期61件をseedしており、db pushで61 + dump復元で61 = 122。
     → マイグレーションの初期seedを撤去(8401b96)。既存クラウドはローカル正本のid以外を削除してdedup済み。
4. データ件数(正): sources 19 / source_chunks 2389 / thought_cards 106 / thought_questions 1011 / glossary_terms 61

## 運用上の注意
- 無料枠は **7日間アクセスなしでプロジェクト一時停止**(ダッシュボードのボタンで再開)。DBサイズ上限500MB(現状の実データ約50MBで余裕)。
- **DBパスワード**(psql直結用)を再発行してもアプリは動く(アプリはanon/service_roleキーで接続、キーは変わらない)。psqlで直接触るときだけ新パスワードが要る。
- `auth.users` も移行済み(検証ユーザーのログイン可)。storageの実ファイル(blob)は移行していない(アーカイブ用途で、回答時RAGはDB完結のため影響なし)。
