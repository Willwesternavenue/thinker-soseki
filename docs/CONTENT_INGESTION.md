# コンテンツ投入手順書(汎用)

新しい人物(persona)向けに、原典の取り込みから思想カードの承認までを行う手順。
特定の人物・プロジェクトに依存しない形で記述する。実際の値は各自の環境のものに
置き換えること(`<...>` はプレースホルダ)。

- インフラ構築(GCP/Firebase/Supabase/デプロイ)は [FIREBASE_MIGRATION.md](./FIREBASE_MIGRATION.md) を参照。
- 本書はインフラ構築が済み、DBに migration が適用済み(`supabase db push`)である前提。

## 用語

| プレースホルダ | 意味 | 例 |
|---|---|---|
| `<PERSON_ID>` | 対象人物の識別子。`personas.person_id` | `maurice` |
| `<PROJECT_ID>` | GCP/Firebase プロジェクトID | — |
| `<SUPABASE_URL>` | Supabase プロジェクトURL | — |

> **注意**: コードは現状 `person_id` を定数で持っている(`frontend/src/lib/const.ts` 等の
> 各所、worker の各スクリプト)。新規人物で動かす前に、これらを `<PERSON_ID>` に統一して
> あること(person置換フェーズ)を確認する。未置換だと別人物のデータを読み書きしてしまう。

## 全体像

```text
[原典ファイル] --ingest--> sources(pending job)
     │
     ▼  取り込みワーカー(常駐): extract → clean → chunk → embed → distill_light
[チャンク + 軽蒸留]
     │
     ▼  蒸留パイプライン: heavy → source → cards → questions
[思想カード候補 + 質問対応]
     │
     ▼  管理画面でレビュー
[承認済みカード] --> 回答生成に使用
```

取り込みワーカーは2種類のジョブをポーリングする:
- `ingestion_jobs`: 原典1件ごとの extract〜distill_light
- `distillation_jobs`: 横断的な heavy蒸留・カード生成・質問生成

---

## 0. 事前準備: persona を登録する

回答の人格・話し方・禁止語を定義する `personas` 行を1件作成する。カード生成もこの
persona を参照するため、**最初に必ず登録する**。

```sql
insert into public.personas (
  person_id, display_name, system_prompt, first_person,
  banned_terms_exact, banned_terms_contextual, style_rules,
  quote_policy, safety_policy, fallback_card_id
) values (
  '<PERSON_ID>',
  '<表示名>',
  '<システムプロンプト: 役割・人物像・コア思想・話し方の基盤>',
  '<一人称>',                 -- 例: 私 / 俺
  '{}',                       -- 完全一致で禁止する語
  '{}',                       -- 文脈依存で禁止する語
  '{}'::jsonb,                -- 話し方ルール
  '{}'::jsonb,                -- 引用ポリシー
  '{}'::jsonb,                -- 安全ポリシー
  null                        -- フォールバックカード(後で設定可)
);
```

`fallback_card_id` は「該当カードが無い問い」に使う基本姿勢カード。運用開始後に
該当カードを作って設定してもよい。

## 1. 管理アカウントを作成する

原典アップロードやカード承認は admin ロールで行う。

```bash
cd scripts
npm install
npm run create-user -- --email <admin@example.com> --role admin --name <管理者名>
```

- Firebase Auth にユーザーを発行し、`user_profiles` にロールを登録する。
- Firebase コンソール(Authentication → Users)での手動発行 + `user_profiles` への
  行追加でも同じ(どちらでもよい)。
- 認証は ADC(`gcloud auth application-default login`。`<PROJECT_ID>` の権限が必要)。

## 2. 原典を取り込む

原典ファイル(txt / docx / pdf)を取り込み、`sources` 行と `ingestion_jobs(pending)` を
作成する。実処理は次章の常駐ワーカーが行う。

```bash
cd worker

# 単体(タイトルを明示)
uv run python -m src.ingest_source --file "<path/to/file.txt>" --title "<タイトル>"

# ディレクトリ一括
uv run python -m src.ingest_source --dir "<path/to/dir>" --priority <priority>

# 登録せず抽出結果だけ確認(ドライラン)
uv run python -m src.ingest_source --dir "<path/to/dir>" --dry-run
```

- **チャンク化の判定**: 話者ラベル付き(`A:` `B:` のような対談形式)は QAペア単位、
  ラベル無しはモノローグ単位でチャンク化する。
- **タイトル/URLの自動抽出(任意)**: txt の先頭数行に `動画名：〜` のようなヘッダー行や
  URL行があれば、タイトル・`source_url` に自動格納する。無いファイルはファイル名を
  タイトルにする。この慣習を使うかは任意。
- **source_type / priority**: 書籍・対談・インタビュー・講演・記事・随筆・プロフィール・
  文書など。`--priority` は蒸留の優先度(高いものから重蒸留対象になる)。
- 取り込みは連番で `source_id`(例 `BOOK_001`)を採番する。同じ内容の再取り込みは
  チャンクの `chunk_hash` 差分で判定され、変更分だけ再処理される。

## 3. 取り込みワーカーを起動する

`ingestion_jobs` / `distillation_jobs` をポーリングする常駐プロセス。

```bash
cd worker
uv run python -m src.main
```

各原典に対して **extract → clean → chunk → embed → distill_light** を順に実行する。
進捗は `ingestion_jobs.current_step`(N/5)で確認できる。失敗時は `status='failed'` に
なるので、管理画面から `pending` に戻して再実行する。

> 本番では worker を Cloud Run 等に常駐させる([FIREBASE_MIGRATION.md](./FIREBASE_MIGRATION.md))。
> ローカルで一括投入するだけなら、このコマンドを起動しっぱなしにしておけばよい。

## 4. 重蒸留 → カード生成 → 質問生成

軽蒸留まで終わったら、横断的な蒸留とカード候補生成を行う。2通りの起動方法がある。

**A. CLIで直接**

```bash
cd worker
uv run python -m src.distill all       # heavy → source(全原典) → cards → questions
# 個別に流す場合:
uv run python -m src.distill heavy     # 重蒸留(優先度の高い未処理チャンク)
uv run python -m src.distill source <SOURCE_ID>   # 原典単位蒸留
uv run python -m src.distill cards     # 思想カード候補生成(横断)
uv run python -m src.distill questions # 質問対応情報生成(全カード)
```

**B. 管理画面から**

`/admin/cards` の蒸留トリガーで `distillation_jobs` を作成すると、常駐ワーカー(3章)が
同じ処理を実行する。長時間処理をUIから投げたい場合はこちら。

- **cards** は persona と原典蒸留の内容から思想カード**候補**を生成する(この時点では
  未承認)。
- **questions** は各カードに対し、想定される質問文(検索ルーティング用)を生成する。

## 5. カードをレビューして承認する

**承認済み(approved)のカードだけが回答生成に使われる。** 生成直後の候補は未承認。

1. 管理画面 `/admin/cards` で候補カードを開く。
2. 各カードの **中核命題 / 区別 / 禁止事項 / 回答方針** が人物の思想として正しいか確認。
3. 正しければ承認(`approved`)、不適切なら不採用(`rejected`)、要修正なら編集して承認。
4. 重要度(importance)を必要に応じて設定する。

> 検索は importance で絞り込まない(承認済みカード全体が対象)。importance は人間側の
> 優先度ラベルであり、「回答に使われるか」とは無関係な点に注意。

## 6. (任意)外部で作成したカード・判断規則をインポートする

コーパスからの自動生成ではなく、外部で用意したカード/判断規則(JSON)を投入する場合。

```bash
cd worker
uv run python -m src.import_cards            # カードJSONを取り込む
uv run python -m src.import_judgment_rules   # 判断規則JSONを取り込む
```

- 各スクリプトが読む入力ファイルのパスはスクリプト冒頭の定数で定義されている。対象人物用の
  ファイルを用意し、そのパスに合わせて調整する。
- 判断規則(L3)は任意。使わなければスキップしてよい。

## 7. 動作確認

1. チャット画面で対象人物に想定質問を投げる。
2. 回答が返り、`answer_traces` に記録が入ることを確認(参照カード・原典が紐づく)。
3. 参照情報パネルで、意図したカード・原典が引かれているか確認する。

---

## 付録A: このパイプラインで使う環境変数

worker / frontend が参照する秘匿キー(Secret Manager 経由、またはローカルは ADC):

- `SUPABASE_SERVICE_ROLE_KEY`
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`

非秘匿の接続先(`SUPABASE_URL` / `<PROJECT_ID>`)は `frontend/src/lib/const.ts` と
`worker/src/config.py` に定義。ローカルで別DBに向ける時のみ環境変数 `SUPABASE_URL` で上書き可能。

## 付録B: つまずきやすい点

- **person_id 未置換**: 新規人物用に定数を揃えていないと、別人物のデータを読み書きする。
  投入前に `grep -rn '<置換前ID>'` で残存ゼロを確認する。
- **persona 未登録でカード生成**: 0章を飛ばすとカード生成が persona を参照できず失敗する。
- **Supabase 無料枠の自動停止**: 一定期間無操作でプロジェクトが停止する。継続投入時は留意。
- **ジョブが failed のまま**: 管理画面で `pending` に戻すと再実行される。差分のみ再処理される
  ため再実行は安価。
