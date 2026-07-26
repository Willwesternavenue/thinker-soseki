# 新しい人物でThinkerを立ち上げるときのチェックリスト

> Thinker を別人物向けにフォークするときの**置換漏れ・事故防止**チェックリスト。
> 3世代の実績から作成: `x_shigyo`（執行草舟）→ `merleau_ponty`（メルロ=ポンティ、commit 9587aaa）
> → `natsume_soseki`（夏目漱石、commit 30e969a）。**実際に踏んだ失敗のみ**を載せている。
>
> 汎用手順（personas登録・原典投入・蒸留・カード承認）は [CONTENT_INGESTION.md](CONTENT_INGESTION.md) を参照。
> 本書はそれと重複せず、「フォーク時に人物・スタック依存の値を漏れなく差し替える」ことだけを扱う。
>
> ⚠️ このファイルは汎用知見なので、更新したら upstream（thinker-maurice）へ還流すること。

## 0. 5分で終わる完了判定（先に読む）

作業後、以下がすべてゼロ件になれば置換は完了。**1件でも残っていたら未完**。

```bash
# git管理下のファイルだけを対象にする(node_modules・ロックファイル・ローカル設定の誤検出を防ぐ)
git ls-files | grep -vE "\.(lock|lockb)$|package-lock\.json|uv\.lock" | xargs grep -n \
  "<旧PERSON_ID>\|<旧人物名の各表記>\|<旧GCPプロジェクトID>\|<旧SupabaseプロジェクトRef>\|<旧リポジトリ名>" \
  2>/dev/null
```

⚠️ `grep -r` を素で使うと `.claude/` 配下のローカル設定などgit管理外のファイルが引っかかり、
本物の漏れが埋もれる。**`git ls-files` 経由で検索する**こと。

**旧リポジトリ名を必ず検索対象に入れること**。person_id や人物名だけを検索すると §4 の外部リンクを取りこぼす
（実際に `thinkerllm` という文字列が2世代連続で見落とされ、管理画面のGitHubリンクが別リポジトリを指したままだった）。

**旧人物名は表記ごとに検索する**。1つの綴りだけでは漏れる
（例: メルロ=ポンティなら `メルロ` と `Merleau` の両方、漱石なら `漱石` と `Soseki`）。

---

## 1. リポジトリ・ローカル環境の分離

- [ ] GitHub に新リポジトリを作成 → 上流を clone して `origin` 付替え、`upstream` に元リポジトリを設定
- [ ] **`supabase/config.toml` の `project_id` を変える**
      ⚠️ **最重要**。ここが上流と同じままだと、**ローカルの Docker コンテナ群を上流と共有する**。
      その状態で `supabase db reset` を実行すると**上流のローカルDBまで消える**。
      漱石で実際に発生（`project_id = "thinkerllm"` を共有していた）。
- [ ] **ポートも変える**（`[api]` `[db]` `[studio]` `[inbucket]` `[analytics]` `[db.pooler]` の `port`）
      同一ポートだと `supabase start` が既存スタックと衝突する。
      実績: maurice 55321-55329 / soseki 55421-55429（他プロジェクトが 54321-54329 を使用中だった）
- [ ] 分離できたか確認: `docker ps --format "{{.Names}}"` に `supabase_db_<新project_id>` が出ること

```bash
# docker が PATH に無い環境がある(Docker Desktop)
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
supabase start && supabase db reset   # 全migrationが通ることを確認
```

## 2. person_id の置換（コード・SQL）

`<旧PERSON_ID>` → `<新PERSON_ID>`。上流の置換コミット（9587aaa / 30e969a）が実例。

- [ ] frontend: `lib/rag/pipeline.ts` / `lib/rag/l3shadow.ts` の `PERSON_ID` 定数
- [ ] frontend: 各 server action（`app/chat/actions.ts`、`app/admin/{cards,sources,questions,transcripts}/actions.ts`、
      `app/admin/cards/distill-actions.ts`、`app/admin/persona/page.tsx`、`app/api/admin/eval/route.ts`）
- [ ] worker: `ingest_source.py` / `import_cards.py` / `import_judgment_rules.py` / `review_cards.py` の `PERSON_ID` 定数
- [ ] worker: `steps/gen_cards.py` / `steps/gen_questions.py` / `steps/distill_heavy.py` の**デフォルト引数**
- [ ] **migrations の SQL デフォルト値**（見落としやすい）
      - RPC 5関数の `target_person_id text default '<旧PERSON_ID>'`
        （`match_thought_questions` / `match_source_chunks_by_thoughts` / `match_source_chunks_all` /
         `search_source_chunks_fulltext` / `rebuild_related_thought_ids`）
      - `transcript_drafts` / `glossary_terms` の `person_id text not null default '<旧PERSON_ID>'`
- [ ] frontend: `lib/rag/rag.test.ts` のテストフィクスチャ

**DB側でも確認する**（コードの grep だけでは不十分。migration を適用して実物を見る）:

```bash
psql -h 127.0.0.1 -p <db port> -U postgres -d postgres -tAc \
 "select p.proname||' :: '||pg_get_function_arguments(p.oid) from pg_proc p
  join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'
  and (p.proname like 'match_%' or p.proname like '%_fulltext' or p.proname like 'rebuild_%');"
```

## 3. 人物固有ロジック（単純置換では済まない・要書き直し）

ここは sed で置換して終わりにできない。**人物ごとに中身を作り替える**必要がある。

- [ ] **`lib/rag/session.ts` の呼称正規化**（`SUBJECT_CANONICAL` と `normalizeSubjectReferences`）
      検索クエリ内の人物表記を正規名に寄せる関数。人物によって揺れ方が全く違う:
      - 執行草舟: 敬称ノイズ除去
      - メルロ=ポンティ: フランス複合姓の区切り（`= ＝ ・ -`）と長音の揺れ
      - 夏目漱石: 号のみ（漱石）/ 姓+号 / 本名（夏目金之助）/ ローマ字（Natsume Sōseki）
      ⚠️ 姓だけの正規化は**やらない**（「夏目」は地名・他人名と衝突する）。敬称付きのみ対象にする。
      ⚠️ 正規名は**原典で最も多い表記**に合わせる（青空文庫なら著者名表記）。ここがずれると検索が弱くなる。
- [ ] **`lib/rag/rag.test.ts` の正規化テスト**を新ロジックに合わせて書き直す（置換ではなく改訂）
- [ ] **`worker/src/steps/clean.py` の `SELF_SPEAKER_LABELS`**（対談原典で本人発話に付く話者ラベル）
      ⚠️ **`worker/tests/test_clean.py` も必ず直す**。上流では2世代にわたりテスト側が旧ラベル
      （「社長:」）のまま放置され、`pytest` が3件赤のままだった。**フォーク直後にテストを回して確認すること**。
- [ ] `lib/transcripts/prompts.ts` の整形プロンプト内の人物名
- [ ] `worker/src/steps/clean.py` の話者正規化まわりのコメント

## 4. 表示名・外部リンク（2世代連続で漏れた領域）

- [ ] ブランド名 `X<人物>`: `app/layout.tsx`(title) / `app/login/page.tsx` / `app/chat/chat-client.tsx` /
      `components/admin-nav.tsx` / `app/admin/persona/page.tsx` / `app/admin/help/page.tsx`
- [ ] 原典の既定著者: `app/admin/sources/upload-form.tsx`(defaultValue) /
      `app/admin/transcripts/actions.ts`(author) / `worker/src/ingest_source.py`(`--author` default)
- [ ] `app/admin/architecture/page.tsx` の説明文（「思想家・◯◯のAIアバターです」等）と
      パイプライン説明内の人物表記例
- [ ] **GitHubリンク**（⚠️ 2世代とも見落とし）
      - `app/admin/architecture/page.tsx` の `GITHUB` 定数
      - `app/admin/help/page.tsx` のリンク生成部
      両方とも元リポジトリ `thinkerllm` を指したままだった。**自リポジトリに差し替える**。
- [ ] help ページのドキュメントリンクの**リンク切れ**も確認
      （前人物の時代の仕様書へのリンクが残る。実際に `xshigyo_mvp_spec_v1_1.md` 等の
       存在しないファイルへのリンクが残っていた）
- [ ] **`README.md`**（⚠️ 2世代とも見落とし）
      リポジトリの入口なのに初代人物（執行草舟）の説明のまま残っていた。
      タイトル・説明文・仕様書へのリンクを新人物のものに書き直す。
- [ ] `docs/` 内の仕様書（`judgment_rules_spec_*` / `regression_suite_spec_*` 等）に
      前人物名が例として出てくる。**設計の正本として読み継ぐ文書なら**人物名を一般化するか
      注記を付ける（歴史的資料として残す判断でもよいが、放置か意図かを明示する）

## 5. 接続先（クラウド。**最も事故が重い**）

⚠️ **原則: 新スタックが未確定の間は、上流の実値を残さず `<新プロジェクト名>-TBD` 等のプレースホルダにする。**
実値を残すと「ローカル開発のつもりで**上流の本番DBに書き込む**」事故が起きる。

- [ ] `frontend/src/lib/const.ts`: `GCP_PROJECT_ID` / `FIREBASE_CONFIG` 6項目 / `SUPABASE_URL`
- [ ] `worker/src/config.py`: `GCP_PROJECT_ID` / `SUPABASE_URL`
- [ ] `frontend/package.json`: `deploy` / `deploy:worker` スクリプト内のプロジェクトID
- [ ] **`scripts/src/operation/createUser.ts`: `GCP_PROJECT_ID` と `SUPABASE_URL`**
      ⚠️ 漱石で実際に漏れた。GCPプロジェクトIDだけ置換して **`SUPABASE_URL` を見落とし**、
      環境変数なしで `npm run create-user` を実行すると**上流の本番DBに user_profiles 行を書き込む**状態だった。
      **接続先は3ファイル（const.ts / config.py / createUser.ts）にある**ことを忘れない。

**確認**: 旧Supabaseプロジェクトの ref 文字列（`https://<ref>.supabase.co` の `<ref>`）で grep してゼロ件。

### 5.1 GCPリソース名も人物固有にする

- [ ] `frontend/package.json` の `deploy:worker` 内、**Artifact Registry のリポジトリ名**と
      **Cloud Run のサービス名**（上流は両方 `thinkerllm` / `thinkerllm-worker` という汎用名）
      ⚠️ プロジェクトが違えば衝突はしないが、**上流と同名だと `--project` を取り違えたときに
      上流本番の worker を上書きしうる**。人物固有名（例 `thinker-soseki-worker`）にして、
      事故を「名前が違うから失敗する」形で防ぐ。
      → 変えたら、Artifact Registry を**その名前で作る**こと（`gcloud artifacts repositories create <名前>`）。
      docs/GCP_MIGRATION.md の例は汎用名のままなので読み替える。

## 6. クラウド作成時の罠（maurice 実績）

- [ ] Firebase プロジェクト作成 → **⚠️ 別サフィックス付きの projectId が作られることがある**
      （maurice では `thinker-maurice` と `thinker-maurice-9082f` の2つが生まれ、frontend と worker で
       別々の値を持つ状態になった）。**frontend と worker の `GCP_PROJECT_ID` を必ず突き合わせる**。
- [ ] Firebase Authentication → **メール/パスワードを有効化**（忘れやすい）
- [ ] **firebase-admin は ADC の quota project を無視する** → ローカルは**サービスアカウントキー必須**
      （`GOOGLE_APPLICATION_CREDENTIALS`）。`gcloud auth application-default login` だけでは動かない。
- [ ] Supabase: **CLI のログインアカウントとプロジェクト所有アカウントの不一致**に注意（maurice で発生）
- [ ] ローカル秘匿キー: `~/.config/gcp-keys/<person>.env` に4行
      （`GOOGLE_APPLICATION_CREDENTIALS` / `SUPABASE_SERVICE_ROLE_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）
- [ ] 本番化時: 請求先リンク（⚠️ プロジェクト数クォータ超過の請求先アカウントがある）→
      Secret Manager 登録 → App Hosting `backends:create` → `grantaccess` 3キー →
      Cloud Run は `--set-secrets` + `secretAccessor`
- [ ] **App Hosting は `cpu >= 1` 必須**（`after()` でのtrace保存がCPU割当を要求する）
- [ ] **worker の Cloud Run は `min=max=1` + `no-cpu-throttling`**
      （0だと停止、2以上だとジョブの二重処理。claim が非排他のため）

## 7. 費用を抑えたい場合（ローカル先行開発）

- Supabase は**ローカルスタックだけで開発できる**（クラウドプロジェクト作成を後回しにできる）。
  `SUPABASE_URL=http://127.0.0.1:<api port>` と `SUPABASE_SERVICE_ROLE_KEY`（`supabase status` の Secret）を環境変数で渡す
- **worker は Firebase に一切依存しない**ので、生成・蒸留系の開発は Firebase 無しで進められる
  （ジョブテーブルに psql で行を入れれば worker が拾う）
- 一方 **frontend は全ページが `proxy.ts` の認証で守られている**ため、
  画面を1枚でも開くには Firebase が必要（Auth の無料枠で足りる。課金が要るのはデプロイ時）

## 8. 完了確認（必ず実行）

- [ ] §0 の grep が全てゼロ件
- [ ] `supabase db reset` が全migration通過
- [ ] `cd worker && uv run pytest` が**全件green**（⚠️ 上流が赤のまま渡してくることがある）
- [ ] `cd frontend && npx vitest run` が全件green
- [ ] `cd frontend && npx tsc --noEmit` がエラーゼロ
- [ ] 管理画面のGitHubリンクを実際にクリックして自リポジトリに飛ぶこと（§4）

## 9. 次にやること（本書の範囲外）

`personas` への行INSERT、フォールバックカード、admin ユーザー作成、原典投入、蒸留、カード承認は
[CONTENT_INGESTION.md](CONTENT_INGESTION.md) の手順に従う。
