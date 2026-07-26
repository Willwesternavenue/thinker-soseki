# Thinker「創作モード」指示書 v0.2

> **状態: 正本（発注者承認 2026-07-26。[CREATIVE_MODE_SPEC_v0.1.md](CREATIVE_MODE_SPEC_v0.1.md) を置き換える）**。
> 本改訂は [CREATIVE_MODE_REVIEW.md](CREATIVE_MODE_REVIEW.md)（レビュー・合意済み改訂方針）と
> [T0_AUDIT_REPORT.md](T0_AUDIT_REPORT.md)（実コード監査）を v0.1 に反映したもの。
> v0.1 からの変更点は冒頭の「改訂サマリ」に集約し、本文は自己完結の仕様として書き直している。
> 実装レベルの設計は [T1_CREATIVE_MODE_DESIGN.md](T1_CREATIVE_MODE_DESIGN.md)、原典取得は [AOZORA_INGESTION.md](AOZORA_INGESTION.md) を参照。

## 改訂サマリ（v0.1 → v0.2）

| # | 変更 | 理由（詳細はレビュー該当節） |
|---|---|---|
| 1 | **v0.1 スコープを Style + Narrative Grammar の「カードまで」に削減**。L3 創作規則（旧§3 L3・§5.3 shadow/assist・§10 評価UI・§11 比較実験基盤）は v0.2 以降へ | 旧§2.3 と旧§16 の自己矛盾解消。既存思想モードでも L3 は後から shadow で追加された経緯を踏襲（レビュー論点2） |
| 2 | **生成はフロントのリクエスト内でなく worker のジョブ型**。`creative_generations` を既存 `ingestion_jobs` / `distillation_jobs` と同型のジョブテーブルとし worker がポーリング | App Hosting のリクエスト上限5分。多段生成を直列すると超過し得る（実測: 現行チャット27〜82秒）（レビュー論点3-2） |
| 3 | **生成モードの5値enumを直交フラグに変更**: `{use_rag, use_cards, rules}` + プリセット名を trace に保存 | 「根拠ソース」と「規則適用」は直交する次元。enum は比較実験の組合せを塞ぐ（レビュー論点4-1） |
| 4 | **初期カードは人手起草でなく、既存蒸留機構（gen_cards.py 系）の創作用プロンプト差し替えで自動 draft → 人間承認** | 動く機構が既にあり、承認フローの不変条件もそのまま使える（レビュー論点3-3） |
| 5 | `creative_profiles` の親キーは **`person_id`**（`author_id` という概念は現行実装に存在しない） | `personas` テーブルが正本（レビュー論点3-4） |
| 6 | **仮名遣い・字体ポリシー（`orthography_policy`）を creative_profiles の必須フィールドに昇格** | 生成文の正書法（新字新仮名 vs 歴史的仮名遣い）は全文に効く決定。夢十夜は文語混じり口語・青空文庫は新字新仮名版が普及（レビュー論点4-2） |
| 7 | **v0.1 の原典取得は「関連する一夜の全文をコンテキスト投入」に簡略化**。semantic search は全作品拡張時の将来投資 | 夢十夜は10篇・数万字の小コーパス。パブリックドメインで全文投入可能（レビュー論点4-3） |
| 8 | **表示題名を「第十一夜（AI創作）」等、誤認防止を題名レベルで固定** | 「第十一夜」という題名自体が真作誤認リスク（レビュー論点4-4） |
| 9 | 旧§14「provider abstraction を維持」→ **「既存の定数パターンを踏襲」**（abstraction は存在しない） | 実コード監査の事実（レビュー論点3-1） |
| 10 | L1 metadata に**青空文庫の底本（版・正字法）の特定・記録**を必須化 | evidence の正本性確保。漱石は没後100年超のパブリックドメインで著作権面は安全だが底本は明記する（レビュー論点4-5） |
| 11 | 実装先は maurice への追加でなく、**thinker-maurice をフォークした独立リポジトリ `thinker-soseki` + 新規スタック**（GCP/Firebase/Supabase 新設） | 現行コードは person_id ハードコードの「1デプロイ=1人物」設計。切替式は旧§1.1（大規模抽象化の先行禁止）と矛盾（レビュー論点1） |

削除でなく**延期**した機能は §19 に列挙し、v0.1 の trace 設計は延期機能を後付けできる形（フラグ・プリセット名・生成設定の保存）を最初から持つ。

---

## 0. この指示の目的

既存のThinkerに、特定作家の表面的な文体だけでなく、物語構成・象徴操作・人物配置・説明抑制・終結方法などの「創作規則」を参照して、新しい作品を生成する**創作モード（Creative Mode）**を追加する。

最初の対象は、夏目漱石『夢十夜』を参照した新作「第十一夜」の生成。

ただし、夏目漱石や『夢十夜』にハードコードした専用機能にはせず、将来的に以下へ展開できる汎用構造とする。

* 他の作家、作品群
* 存命作家本人の創作支援
* 文体カードによる文章変換
* 物語構成・脚本・詩などの創作規則
* 思想モードと創作モードを組み合わせた生成
* 通常生成、RAG、カード、創作規則の比較実験

**実装形態**: thinker-maurice をフォークした独立リポジトリ `thinker-soseki`（person_id = `natsume_soseki`、新規 GCP/Firebase/Supabase スタック）。thinker-maurice を upstream とし、汎用的な修正は還流する。

## 1. 最重要方針

### 1.1 既存Thinkerを壊さない

既存の思想回答モードは、現在の挙動を完全に維持する。
特に以下を破壊・置換・意味変更しない。

* L1原典 / L2思想カード / L3判断規則 / L4判断トレース
* thought_idルーティング / 承認済みカード必須の不変条件
* shadow／assistモード / Output Guard / answer_traces
* 既存の規則承認・版管理 / 既存API / 既存管理画面 / 既存テスト

v0.1では、既存テーブルの名称変更や大規模な抽象化を先に行わない。
創作モードは**追加テーブル・追加ルート・worker への追加ジョブ種別**のみで実装し、既存の思想モードに対する破壊的migrationを避ける。

### 1.2 リポジトリ監査を正本とする

監査は実施済み（[T0_AUDIT_REPORT.md](T0_AUDIT_REPORT.md)）。本改訂の名称・構造はすべて監査済みの実コードに接地している。今後も、実装時に本仕様と実コードが食い違う場合はコードとDBを正本として扱い、仕様側を改訂する。

## 2. 創作モードの定義

創作モードの目的は、単に「〇〇風の語尾・語彙」で文章を書くことではない。
以下の三つを分離して扱う。

### 2.1 Style

表層的な文章表現。
例：語彙 / 文長 / 文末 / 句読点 / 漢語・和語の比率 / 地の文と会話文の比率 / 語り手の距離 / 心理描写の直接性 / 時代語 / 比喩 / 反復 / 音のリズム

### 2.2 Narrative Grammar

物語をどのように組み立てるかという創作上の操作。
例：題材選択 / 日常物の異常化 / 異常の受容のさせ方 / 象徴の反復 / 時間・因果のずらし / 情報開示の順序 / 語り手と登場人物の配置 / 終結の反転・発見 / 説明せず残すもの

### 2.3 Creative Intent／Thought

作品の背後にある問題意識、価値判断、思想的緊張。
例：個人と社会 / 自由と責任 / 近代化と内発性 / 自意識と他者 / 知性と滑稽さ / 生と死 / 時間と記憶

**v0.1 は Style と Narrative Grammar を「承認済みカード」として実装する**。条件発火型の L3 創作規則は v0.2 以降（§19）。思想カード・判断規則との統合は可能な設計にしてよいが、最初から密結合させない。

## 3. 4層構造の創作モードへの対応

### L1：創作原典

作家の小説、随筆、評論、講演、書簡など。既存の `sources` / `source_chunks` を使用し、創作用 metadata は sources の追加 metadata（JSON）で持つ（既存 schema への侵襲的変更をしない）。

必須 metadata：

* author / work_title / work_group（例: 夢十夜） / work_type / publication_year
* **底本**: 青空文庫の底本情報（底本名・出版社・入力に用いた版・**正字法**（新字新仮名等））を特定して記録する。evidence の正本性の根拠となる
* copyright_status（漱石はパブリックドメイン） / language / source_url / ingestion_version
* narrator_scope / speaker_scope / character_id / chapter・section

小説内の登場人物の発言を作家本人の思想として扱わないため、発話主体を最低限 `author / narrator / character / unknown` で区別できるようにする。

### L2：創作カード

既存の思想カード（`thought_cards`）と同じく、**人手承認された宣言的知識**。新テーブル `creative_cards` に保存する（§12）。

カード種別（v0.1 最低限）：

```text
style / narrative / motif / character / ending / prohibition
```

必要に応じて追加可能：`setting / dialogue / perspective / rhythm / theme / historical_language`

一枚につき一つの特徴。フィールドは §12 の schema を正とする。

**起草フロー**: 人手起草ではなく、既存の蒸留機構（worker の distill → gen_cards 系）に創作用プロンプトを差し替えた**自動 draft 生成 → 管理画面で人間が承認**。evidence（原文対応箇所）が最低件数に満たないカードは draft 化しない既存の不変条件を踏襲する。

`status=approved` 以外のカードは生成に使用してはいけない（既存思想モードと同一の不変条件）。

### L3：創作規則 — v0.1 スコープ外

条件発火型の創作規則（trigger_conditions / action / application_stage 等）は **v0.2 以降に延期**（§19）。
ただし v0.1 の設計は L3 後付けを妨げないこと：

* 生成設定に `rules: off | shadow | assist` フィールドを最初から持ち、v0.1 は常に `off` で保存する
* creative_traces に「発火規則 / 棄却規則」用のフィールド（v0.1 では空）を確保する

### L4：Creative Trace

創作物についても生成過程を監査可能にする。新テーブル `creative_traces` に保存（既存 `answer_traces` は変更しない）。

保存対象（v0.1）：

* generation ID / creative profile / 入力 brief（正規化後の構造化 brief 含む）
* 投入した原典（source ID・chunk ID または全文投入した作品の識別子）
* 使用した承認済み creative card ID の一覧
* outline / draft（段階生成物）
* Guard 結果（原文類似度検査の数値・該当箇所、誤認防止検査、再生成回数）
* 生成設定: `{use_rag, use_cards, rules}` フラグ + プリセット名 / model ID / prompt version / temperature
* latency / token usage / created_at
* （L3 用に確保・v0.1 では空）発火規則 / 棄却規則 / 発火・棄却理由

## 4. Creative Profile

作家全体と特定作品群を区別するプロファイル。新テーブル `creative_profiles`。

例：`person: natsume_soseki / profile: 夢十夜`

フィールド（§12 の schema を正とする）：

```text
id
person_id            -- personas への FK（author_id という概念は使わない）
name / slug / description
source_scope         -- このプロファイルが参照する原典の範囲（例: work_group='夢十夜'）
orthography_policy   -- 必須。仮名遣い・字体（例: 新字新仮名）。生成文の正書法を規定
target_language / historical_period
default_generation_settings
disclosure_text      -- 誤認防止表示文
display_title_format -- 表示題名の型（例: 「{title}（AI創作）」）
copyright_policy
status / created_at / updated_at
```

重要：

* 「夏目漱石」全作品の特徴と「夢十夜」の特徴を混同しない
* 同じ作者でも作品群別にプロファイルを作れる / 一人の作者に複数プロファイル可
* 将来、存命作家の本人承認プロファイルも扱える
* 『夢十夜』をコードに直接ハードコードしない（profile データとして持つ）

## 5. v0.1の対象機能

### 5.1 最初の生成ユースケース

夏目漱石『夢十夜』を参考にした、新しい独立短編「第十一夜」の生成。

生成物は、夏目漱石本人の作品、未発表作、真作であるかのように表示してはいけない。

* **表示題名は誤認防止を題名レベルで固定**する。例: 「第十一夜（AI創作）」。素の「第十一夜」を表示題名にしない。題名の型は `creative_profiles.display_title_format` で管理する
* 画面上に常に以下と同等の表示を行う（`creative_profiles.disclosure_text`）：

> 本文はAIが公開原典と承認済み創作カードを参照して生成した創作物であり、原作者本人の作品ではありません。

### 5.2 入力項目

最低限：

* creative profile / 題材・中心モチーフ / 物語の核となる状況 / 感情・読後感 / 時代設定 / 文字数 / 追加制約

例：

```text
プロファイル：夢十夜
モチーフ：鏡
状況：鏡の中の自分が一日ずつ年を取る
読後感：不安、静かな恐怖
時代設定：明治
文字数：1,500字
```

将来拡張用：視点 / 登場人物数 / 会話量 / 象徴の強さ / 文語度 / 原作品への近似度 / 独創性 / 終結タイプ

### 5.3 生成設定（旧「生成モード」）

5値 enum（baseline/rag_only/cards_only/shadow/assist）は廃止し、**直交フラグの設定オブジェクト**にする：

```text
{
  use_rag:  bool,        -- L1 原典検索/投入を使うか
  use_cards: bool,       -- 承認済み創作カードを使うか
  rules: off|shadow|assist  -- L3 規則適用（v0.1 は常に off）
}
```

旧5モードは**プリセット名**として定義し、設定オブジェクトと共に trace へ保存する：

| プリセット | use_rag | use_cards | rules |
|---|---|---|---|
| B0 baseline | false | false | off |
| B1 rag_only | true | false | off |
| B2 cards_only | true | true | off |
| shadow | true | true | shadow |
| Proposed assist | true | true | assist |

**v0.1 で実装するのは B2（cards_only）のみ**。他プリセットは UI に出さないが、設定オブジェクト・プリセット名が最初から trace に保存されるため、後日の比較実験（§19）にデータ互換で移行できる。

不変条件: `use_cards=true` で承認済み創作カードが0枚の場合は生成を続行しない（明示的なエラーでジョブを失敗させ、trace に記録する）。

## 6. 生成パイプライン

### 6.1 実行形態: worker のジョブ型（必須）

生成はフロントエンドのリクエスト内で実行しない。**App Hosting のリクエスト上限は5分**であり、多段生成＋Guard＋再生成の直列実行は超過し得る。

* フロントは `creative_generations` にジョブ行を INSERT して即応答する（§13）
* worker が既存の `ingestion_jobs` / `distillation_jobs` と同型のポーリングで claim し、段階生成を実行、進捗と結果を同じ行へ書き戻す
* UI は「生成中」表示 → ポーリングで完了を検知して表示する

### 6.2 生成ステップ（v0.1）

一回のプロンプトで全文を生成せず、以下の段階に分離する（旧10段から L3 発火を除き、revision を draft に統合した6段 + 保存）。

* **Step 1：Brief正規化** — ユーザー入力から motif / situation / emotional_target / period / length / constraints / requested_profile を構造化（軽量モデル）。
* **Step 2：Creative Profile決定** — 指定 profile が存在し `status` が利用可能か確認。曖昧な場合に別作者・別プロファイルへ自動 fallback しない。
* **Step 3：承認済みカード取得** — `status=approved` の creative card のみ取得。0枚なら生成を続行せずジョブを失敗させる（不変条件）。
* **Step 4：原典コンテキスト取得（簡略版）** — 夢十夜は10篇・小コーパスのため、brief（motif 等）に関連する一夜（1〜2篇）を選び**全文をコンテキスト投入**する。カードの evidence が指す chunk も補助投入する。semantic search による検索は将来の全作品拡張時に導入（§19）。投入した原典の識別子を trace へ保存。
* **Step 5：Outline生成** — 全文の前に内部 outline を作る（高性能モデル）。最低限：導入 / 中心となる異常 / 反復・変化 / 転換 / 終結 / 説明しない要素。
* **Step 6：Draft生成（Style自己検査込み）** — outline・承認済みカード・原典コンテキストから本文を生成（高性能モデル）。原文の長い引用や既存の一夜の改作は行わない。旧 Step 8（Style Revision）は独立ステージにせず、draft プロンプト内の自己検査項目（文体一貫性 / 現代語混入 / 説明過剰 / 象徴乱立 / 視点破綻 / 終結の冗長説明 / 禁止カード違反）として統合する。
* **Step 7：Creative Output Guard** — §8 を実行。違反時は自動再生成（上限あり）、上限到達で安全側失敗。
* **Step 8：保存** — 最終本文 / outline / 使用カード / 投入原典 / guard 結果 / 生成設定 / trace を保存し、ジョブを完了にする。

## 7. 『夢十夜』用の初期カード

初期カードは**既存蒸留機構の創作用プロンプト差し替えによる自動 draft → 管理画面で人間承認**で作る。AIの一般知識だけで approved にしてはいけない（evidence 必須の既存不変条件を踏襲）。

自動 draft の誘導・検収の目安として、以下を初期カードの候補観点とする。

**Style Cards** — 「こんな夢を見た」に相当する簡潔な導入 / 異常な出来事を淡々と記述 / 説明より観察を優先 / 長文と短い認識文の対比 / 過度に現代的な説明語を避ける / 夢の意味を本文中で解説しない

**Narrative Cards** — 一夜ごとに独立した短編 / 中心象徴は原則一つ / 異常は一度に一要素から始める / 異常を夢の内部では自然な事実として扱う / 時間・因果・生死の境界を揺らす / 反復に変化を加える / 最後に認識をずらす / 終結後に教訓を説明しない

**Motif Cards** — 夢 / 待つこと / 時間 / 死 / 記憶 / 身体 / 石 / 水 / 闇 / 声 / 道 / 乗物 / 手紙 / 鏡
（既存十夜のモチーフを再利用する場合も、既存話のプロットを複製しない）

**Prohibition Cards** — 「これは〇〇を象徴していた」と説明する / 科学的原因を説明する / 夢オチをさらに夢オチで閉じるだけの安易な構成 / 現代SNS的な語り口 / 漱石本人が書いたと誤認させる表示 / 原文の長い連続転載 / 既存十夜の人物・出来事の単純な再演 / 複数の象徴の詰め込み / 最後にテーマを要約する

## 8. Creative Output Guard

既存 Output Guard とは別レイヤで実装する（既存 Guard は変更しない）。

### 8.1 原文類似検査

生成文と L1 原典の長い一致を検出する。日本語では空白区切りが使えないため、**文字 n-gram（character shingles）** を基本とし、必要に応じて longest common substring / embedding 類似＋文字列検査を併用する。

* 閾値は設定値として管理し、コードへ直書きしない
* 一致が閾値超過: 該当箇所を記録 → 自動再生成 → 再生成後も違反なら安全側エラー（ジョブ失敗）とし管理者確認へ

### 8.2 誤認防止

生成物・UI で以下を禁止する: 「夏目漱石の未発表作」「発見された第十一夜」「本人が書いた」等、真作であるかのような表示。表示側は §5.1 の題名固定・disclosure 常時表示で担保し、生成文側は Guard の検査項目とする。

### 8.3 文体・物語違反（カード準拠）

approved な prohibition カードに反する表現（禁止語 / 過剰説明 / 象徴の意味の直接説明 / 終結後の解説 / profile 外の時代語）を検査する。LLM 検査（judge）＋機械検査の併用。L3 規則ベースの検査は v0.2 以降。

### 8.4 システム安全規則

既存の安全規則をそのまま適用する。作家固有の創作制約とシステム安全規則は混同せず、trace 上でも別々に保存する。

## 9. UI／UX

### 9.1 メニュー

既存思想回答モードと混同しないよう明示的に分ける。例：思想対話 / 創作 / 管理

### 9.2 創作画面

入力（左側または上部）：Creative Profile / 題材・モチーフ / 状況 / 読後感 / 時代 / 文字数 / 追加制約 / 生成ボタン

生成はジョブ型のため、実行中は「生成中」の進捗表示（ポーリング）。

結果表示タブ（v0.1）：

```text
作品 / 構成(outline) / 使用カード / Creative Trace / Guard
```

（「比較」タブは比較モードと共に延期）

### 9.3 管理画面

最低限（v0.1）：

* Creative Profile 一覧・編集
* Creative Card 一覧・編集・**承認**（自動 draft の検収を含む）・evidence link 確認
* generation 一覧（ジョブ状態の監視。既存 /admin/jobs のパターンを踏襲）
* trace 確認 / guard 違反確認

既存の思想カード管理UIを流用する場合も、思想用データと創作用データを誤認しない表示にする。Creative Rule 管理は L3 と共に延期。

## 10. 評価機能 — v0.1 スコープ外（データのみ確保）

評価UI（Style Fidelity / Narrative Grammar Fidelity / Originality・Safety の軸別評価）は v0.2 以降に延期する。
ただし v0.1 の時点で、後から評価可能なデータ（生成設定・プリセット名・trace・guard 結果・token usage・latency）をすべて保存する。評価軸の定義は旧 v0.1 指示書 §10 を将来仕様として保持する。

## 11. 比較実験 — v0.1 スコープ外（データ互換のみ確保）

B0/B1/B2/Proposed の比較実験基盤・ブラインド評価は延期する。§5.3 の直交フラグ＋プリセット名保存により、後日の比較実験にデータ互換で移行できることを v0.1 の要件とする。

## 12. DB・migration方針

原則（v0.1 から変更なし）：

* additive migration / destructive migration 禁止 / 既存データの意味変更禁止
* rollback 可能 / migration 前後で既存テストを実行 / seed と本番データを分離
* 原典全文を migration SQL へ埋め込まない

**新テーブル方式で確定**（既存テーブルへの domain カラム追加は行わない。既存テストが構造的に無傷であることを優先）。

v0.1 で新設するテーブル：

```text
creative_profiles     -- §4
creative_cards        -- §3 L2（evidence links を含む）
creative_generations  -- ジョブテーブル。ingestion_jobs / distillation_jobs と同型のライフサイクル
creative_traces       -- §3 L4
```

延期（§19）：`creative_rules` / `creative_projects` / `creative_evaluations`。
guard 結果は v0.1 では creative_traces 内に保存し、独立テーブル `creative_guard_results` は必要になった時点で分離する。

## 13. API方針

現行実装のパターン（server actions ＋ 一部 API route）に従い、既存ルートへ混ぜず創作用に追加する。

概念上必要な操作（v0.1）：

```text
GET    creative profiles / profile detail
POST   creative generation        -- ジョブ行を作成して generation ID を即返す
GET    creative generation        -- 状態・結果のポーリング
POST   creative generation regenerate
GET    creative trace

CRUD   creative cards
POST   creative card approve/reject
```

* 生成 POST は **idempotency** を考慮する（同一リクエストの多重送信で複数ジョブが意図せず作られない）
* timeout / retry はジョブ型により worker 側の責務となる（既存ジョブの再試行パターンを踏襲）

## 14. モデル・プロンプト方針

* **既存の定数パターンを踏襲する**（provider abstraction は現行実装に存在しない。新設もしない）。モデル名は既存の定数定義（frontend `llm.ts` / worker `config.py` 相当）に集約し、処理コードへ散在させない
* 役割分担: brief 正規化＝軽量モデル / outline・draft＝高性能モデル / guard judge＝軽量モデル＋機械検査
* 各 prompt には version または hash を持たせ、trace へ保存する

プロンプト中で明示する事項（v0.1 から変更なし）：

* 原作者本人として名乗らない / 原文を長く引用しない / 出典のない有名句を作らない
* 象徴の意味を解説しない / 指定 profile 外の特徴を混ぜない
* 使用カードの内容を本文へ露出させない / `orthography_policy` に従った正書法で書く

## 15. テスト要件

### 15.1 Unit Tests

* creative profile scope（profile 外カードが混入しない）
* approved card のみ取得 / unapproved card 除外
* use_cards=true かつ approved 0枚での失敗
* evidence link 保存 / trace 保存（生成設定・プリセット名・prompt version を含む）
* guard: 文字 n-gram 一致検査 / 誤認表現の検出 / 閾値が設定値から読まれる

### 15.2 Integration Tests（LLM は mock）

* brief → profile → cards → 原典投入 → outline → draft → guard → trace の一気通貫
* approved card 0件時の失敗
* Guard 違反時の再生成 / 再生成上限到達時の安全側失敗
* ジョブの idempotency / 生成失敗時にも監査ログ（trace）が残る
* 既存思想モードへ影響しない

### 15.3 E2E（最低限のシナリオ）

1. 管理者が『夢十夜』profile を開く
2. 蒸留由来の draft creative card を承認する
3. 「鏡」を motif として生成ジョブを投入する
4. 生成中表示 → 完了後「第十一夜（AI創作）」として作品を表示する
5. 使用カード / Creative Trace / Guard 結果を確認する
6. 既存思想対話が従来どおり動作する

### 15.4 Regression

創作モード追加後も、既存の思想モードの全テストを緑にする。既存テストを削除・skip しない。

## 16. 受入条件（v0.1）

以下をすべて満たした場合に v0.1 完了とする。

* 既存思想モードの挙動が変わらない / 既存テストがすべて通る
* 『夢十夜』creative profile が登録できる（orthography_policy・disclosure_text・display_title_format を含む）
* 蒸留由来の draft カードを管理画面で承認できる
* approved カードのみを用いて生成できる / approved 0枚では生成が安全側失敗する
* 生成が worker のジョブ型で実行され、5分制約に依存しない
* outline と本文を段階生成し、投入原典・使用カード・guard 結果・生成設定（フラグ＋プリセット名）・model・prompt version を trace で確認できる
* 原文類似検査が動き、違反時に再生成または安全側失敗になる
* AI 生成物であることが常に表示され、題名が「（AI創作）」等で固定される
* 夏目漱石以外の profile を追加できる構造になっている
* 後日の比較評価（§19）に必要なデータが保存されている

（旧§16 のうち L3 規則・shadow/assist 差・5モード区別に関する条件は §19 の延期項目に移動）

## 17. 実装フェーズ

**T0：リポジトリ監査** — 完了（[T0_AUDIT_REPORT.md](T0_AUDIT_REPORT.md)）

**T1：正本仕様** — 本改訂の承認 → Creative Mode 仕様書 / schema diff / API diff / UI 構成 / 生成 sequence / Guard 仕様 / trace 仕様 / migration 方針 / テスト計画 / タスク分割

**T2：DB・domain model** — additive migration（creative_profiles / creative_cards / creative_generations / creative_traces）/ repository・service / migration test

**T3：管理機能** — profile 管理 / creative card 管理・承認 / evidence 確認 / generation 監視

**T4：生成 pipeline（worker）** — ジョブ claim / brief / profile / cards / 原典投入 / outline / draft / guard / trace

**T5：ユーザーUI** — 創作入力 / 生成中ポーリング / 作品表示 / 構成 / trace / guard / disclosure

**T6：『夢十夜』初期 profile** — 原典 ingestion（取得・前処理・底本記録は [AOZORA_INGESTION.md](AOZORA_INGESTION.md)）/ 蒸留による初期カード draft / 承認 / sample prompts / test fixtures

**T7：ドキュメント同期** — architecture / schema / API / operations / tests / limitations / copyright・disclosure 方針

（旧 T6「評価・比較」はスコープ延期により削除し、旧 T7・T8 を繰り上げ）

## 18. 初回の回答で提出するもの

提出済み（T0_AUDIT_REPORT.md が旧§18 の 1〜12 項目に対応）。本改訂は 2026-07-26 に発注者承認済みで、T1 に進行中。

## 19. v0.1で実施しないもの

### 19.1 延期（v0.2 以降で実施予定。設計はデータ互換を確保済み）

* **L3 創作規則の全体**（creative_rules テーブル / rule firing / shadow・assist / application_stage）
* revision 独立ステージ（v0.1 は draft プロンプトへ統合）
* baseline / rag_only 比較モードの実行（フラグ・プリセット名の保存のみ v0.1 で実施）
* ブラインド評価 / 評価UI（旧§10）/ 比較実験基盤（旧§11）
* creative_projects（複数 generation のグルーピング）/ creative_evaluations テーブル
* semantic search による原典取得（全作品拡張時に導入。v0.1 は関連一夜の全文投入）

### 19.2 スコープ外（v0.1 指示書から変更なし）

* fine-tuning / 継続事前学習 / 漱石専用モデルの訓練
* 長編小説生成 / 複数章の長期一貫性
* 自動的な作家本人認定 / 真作判定
* 完全自動のカード承認 / 完全自動の規則承認
* 存命作家の無許可模倣 / 非公開・権利不明コーパスの取り込み
* Creative Trace を次の推論へ影響させる長期状態機構
* 統計的有意差検定 / 思想モードと創作モードの完全統合

## 20. 実装上の最終原則

* 作家の文章を模倣することと、作家の創作原理を構造化することを区別する
* 原作者・語り手・登場人物を混同しない
* 宣言的な創作特徴（カード）と条件付き創作操作（規則）を分離する
* 未承認のカードを生成へ使用しない
* 生成品質とシステム安全性を別に評価する
* 原文との類似を必ず検査する / AI 生成であることを隠さない
* PoC で確認していない能力を実現済みと表示しない
* 『夢十夜』に最適化しつつ、他作家へ追加可能な構造にする
* 既存思想モードの安定性を最優先する
