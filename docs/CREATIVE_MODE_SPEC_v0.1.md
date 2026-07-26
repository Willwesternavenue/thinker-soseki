# Thinker「創作モード」追加指示書 v0.1

> 発注者から受領した指示書の正本（2026-07-26 受領・原文ママ）。
> これへのレビューと合意済み改訂方針は [CREATIVE_MODE_REVIEW.md](CREATIVE_MODE_REVIEW.md) を参照。

## 0. この指示の目的

既存のThinkerに、特定作家の表面的な文体だけでなく、物語構成・象徴操作・人物配置・説明抑制・終結方法などの「創作規則」を参照して、新しい作品を生成する**創作モード（Creative Mode）**を追加してください。

最初の対象は、夏目漱石『夢十夜』を参照した新作「第十一夜」の生成です。

ただし、夏目漱石や『夢十夜』にハードコードした専用機能にはせず、将来的に以下へ展開できる汎用構造としてください。

* 他の作家、作品群
* 存命作家本人の創作支援
* 文体カードによる文章変換
* 物語構成・脚本・詩などの創作規則
* 思想モードと創作モードを組み合わせた生成
* 通常生成、RAG、カード、創作規則の比較実験

## 1. 最重要方針

### 1.1 既存Thinkerを壊さない

既存の思想回答モードは、現在の挙動を完全に維持してください。
特に以下を破壊・置換・意味変更しないでください。

* L1原典
* L2思想カード
* L3判断規則
* L4判断トレース
* thought_idルーティング
* 承認済みカード必須の不変条件
* shadow／assistモード
* Output Guard
* answer_traces
* 既存の規則承認・版管理
* 既存API
* 既存管理画面
* 既存テスト

v0.1では、既存テーブルの名称変更や大規模な抽象化を先に行わないでください。
創作モードは原則として追加的な変更で実装し、既存の思想モードに対する破壊的migrationを避けてください。

### 1.2 実装前に必ずリポジトリを監査する

この指示書に記載されたテーブル名・画面名・フィールド名を、そのまま実装してはいけません。
まず実際のコード、DB schema、migration、API、プロンプト、テスト、ドキュメントを確認し、現行実装に存在する正式名称を特定してください。

特に確認すること：

* L1原典とチャンクのテーブル
* 思想カードのテーブルと承認状態
* 判断規則、rule_family、rule_scope
* evidence links／origin_type
* answer_tracesの保存形式
* shadow／assistの切替箇所
* Output Guardの構造
* LLM provider／model設定
* ルーティング処理
* 管理画面
* 評価画面
* テスト基盤
* migration運用
* seedデータの方針

存在しない構造を推測で補わず、コードとDBを正本として扱ってください。

## 2. 創作モードの定義

創作モードの目的は、単に「〇〇風の語尾・語彙」で文章を書くことではありません。
以下の三つを分離して扱います。

### 2.1 Style

表層的な文章表現。
例：語彙 / 文長 / 文末 / 句読点 / 漢語・和語の比率 / 地の文と会話文の比率 / 語り手の距離 / 心理描写の直接性 / 時代語 / 比喩 / 反復 / 音のリズム

### 2.2 Narrative Grammar

物語をどのように組み立てるかという創作上の操作。
例：

* どのような題材を選ぶか
* 日常物をどう異常化するか
* 異常を登場人物がどう受け入れるか
* 象徴をどう反復するか
* 時間や因果をどうずらすか
* 情報をどの順番で開示するか
* 語り手と登場人物をどう配置するか
* 最後に何を反転・発見させるか
* 何を説明せず残すか

### 2.3 Creative Intent／Thought

作品の背後にある問題意識、価値判断、思想的緊張。
例：個人と社会 / 自由と責任 / 近代化と内発性 / 自意識と他者 / 知性と滑稽さ / 生と死 / 時間と記憶

創作モードv0.1では、StyleとNarrative Grammarを中心に実装します。
既存の思想カード・判断規則との統合は可能な設計にしてよいですが、最初から思想モードと創作モードを密結合させないでください。

## 3. 4層構造の創作モードへの対応

### L1：創作原典

作家の小説、随筆、評論、講演、書簡など。
創作原典には、可能な限り以下のmetadataを持たせてください。

* author / work_title / work_group／series / work_type / publication_year / edition／source / copyright_status / language / narrator_scope / speaker_scope / character_id / chapter／section / source_urlまたは出典 / ingestion_version

小説内の登場人物の発言を、作家本人の思想として扱わないため、少なくとも次を区別できるようにしてください。

* author / narrator / character / unknown

v0.1で既存L1 schemaへ直接追加することが侵襲的な場合は、追加metadata JSONまたは創作原典用の関連テーブルを用いてください。

### L2：創作カード

既存の思想カードと同じく、人手承認された宣言的知識です。
創作カードの種別は最低限、以下を扱います。

```text
style
narrative
motif
character
ending
prohibition
```

必要に応じて追加可能：

```text
setting
dialogue
perspective
rhythm
theme
historical_language
```

各カードは原則として一枚につき一つの特徴を持たせます。
推奨フィールド：

```text
id
creative_profile_id
card_type
title
summary
description
positive_patterns
negative_patterns
required_elements
prohibited_elements
examples
counterexamples
evidence_links
origin_type
confidence
status
version
reviewed_by
reviewed_at
created_at
updated_at
```

`status=approved`以外のカードは、assist生成に使用してはいけません。

### L3：創作規則

創作規則は、入力や生成途中の状態に応じて、どの創作操作を適用するかを表します。
既存判断規則の共通エンベロープを再利用できるか確認してください。

最低限必要な概念：

```text
trigger_conditions
premises
action
derived_effects
required_elements
forbidden_elements
exceptions
requires_rule_ids
blocks_rule_ids
priority
rule_family_id
rule_scope
application_stage
status
evidence_links
origin_type
schema_version
```

創作規則の`rule_scope`候補：

```text
creative_style
creative_narrative
creative_motif
creative_character
creative_ending
creative_originality
```

`application_stage`候補：

```text
brief
outline
draft
revision
guard
```

例：

```text
trigger_conditions:
中心となる日常物が提示された

premises:
夢の内部では異常な出来事も当然の事実として扱われる

action:
対象物の性質を一つだけ異常化し、その原因を説明しない

derived_effects:
読者には不安が生じるが、語り手は異常を当然として受け入れる

forbidden_elements:
科学的説明
心理学的説明
社会問題の寓意であることの明示
```

### L4：Creative Trace

創作物についても、生成過程を監査可能にします。
保存対象：

* creative project／generation ID
* 選択された作家・作品プロファイル
* 入力brief
* 取得したL1 chunk ID
* 使用したL2 card ID
* 発火したL3 rule ID
* 棄却したL3 rule ID
* 発火・棄却理由
* outline / draft / revision
* Output Guard結果
* 原文類似度検査結果
* model ID / prompt version / generation mode / temperature等の生成設定
* latency / token usage / regeneration count / created_at

既存`answer_traces`を汎用化するか、`creative_traces`を新設するかは、実コードを監査して判断してください。
v0.1では既存`answer_traces`を無理に変更せず、追加テーブルで実装する方を優先してください。

## 4. Creative Profile

作家全体と、特定作品群を区別できるプロファイルを導入してください。

例：

```text
Author: 夏目漱石
Profile: 夢十夜
```

推奨概念：`creative_profiles`

推奨フィールド：

```text
id
author_id
name
slug
description
source_scope
target_language
historical_period
default_generation_settings
disclosure_text
copyright_policy
status
created_at
updated_at
```

重要：

* 「夏目漱石」全作品の特徴と「夢十夜」の特徴を混同しない
* 同じ作者でも作品群別にプロファイルを作れる
* 一人の作者に複数プロファイルを持てる
* 将来、存命作家の本人承認プロファイルも扱える
* 『夢十夜』をコードに直接ハードコードしない

## 5. v0.1の対象機能

### 5.1 最初の生成ユースケース

夏目漱石『夢十夜』を参考にした、新しい独立短編「第十一夜」の生成。

生成物は、夏目漱石本人の作品、未発表作、真作であるかのように表示してはいけません。
画面上に常に以下と同等の表示を行ってください。

> 本文はAIが公開原典と承認済み創作カード・規則を参照して生成した創作物であり、原作者本人の作品ではありません。

### 5.2 入力項目

最低限：

* creative profile / 題材／中心モチーフ / 物語の核となる状況 / 感情・読後感 / 時代設定 / 文字数 / 追加制約 / 生成モード

例：

```text
プロファイル：夢十夜
モチーフ：鏡
状況：鏡の中の自分が一日ずつ年を取る
読後感：不安、静かな恐怖
時代設定：明治
文字数：1,500字
```

将来拡張用：

* 視点 / 登場人物数 / 会話量 / 象徴の強さ / 文語度 / 原作品への近似度 / 独創性 / 終結タイプ

### 5.3 生成モード

既存のshadow／assistの思想を維持し、最低限以下を区別してください。

```text
baseline
rag_only
cards_only
shadow
assist
```

意味：

* `baseline`：作家名・作品名とbriefのみ
* `rag_only`：L1原典検索を使用
* `cards_only`：L1＋承認済みL2カード
* `shadow`：L3規則を判定・記録するが、生成には反映しない
* `assist`：L3規則を生成へ反映する

`baseline`と比較系モードは、管理者・評価者向けとし、一般公開画面では非表示でも構いません。

## 6. 生成パイプライン

一回のプロンプトで全文を生成せず、最低限以下の段階へ分離してください。

* **Step 1：Brief正規化** — ユーザー入力から motif / situation / emotional_target / period / length / constraints / requested_profile を構造化する。
* **Step 2：Creative Profile決定** — 指定profileが存在し、利用可能な状態か確認する。曖昧な場合に別作者へ自動fallbackしない。
* **Step 3：承認済みカード取得** — assist／cards_onlyでは、承認済み創作カードを必須とする。
  不変条件：assistまたはcards_onlyで、承認済み創作カードが0枚の場合は生成を続行しない。例外送出または明示的なエラーを返す。
* **Step 4：L1原典取得** — profileに直接紐づく原典 / カードのevidenceに紐づく原典 / briefとのsemantic search / motif／ending／narrativeに応じた検索。取得結果はtraceへ保存する。
* **Step 5：L3創作規則発火** — 規則を brief / outline / draft / revision / guard の段階別に判定。発火規則と棄却規則を保存。shadowでは判定結果を生成promptへ渡さない。assistでは承認済み規則のみを生成promptへ渡す。
* **Step 6：Outline生成** — 全文の前に内部的なoutlineを作る。最低限：導入 / 中心となる異常 / 反復／変化 / 転換 / 終結 / 説明しない要素。outlineはユーザー表示可能にするが、初期UIでは管理者表示でもよい。
* **Step 7：Draft生成** — outline、承認済みカード、発火規則、L1補助資料をもとに本文を生成する。原文の長い引用や既存の一夜の改作は行わない。
* **Step 8：Style Revision** — 初稿の内容を大きく変えず、文体の一貫性 / 現代語の混入 / 説明過剰 / 象徴の乱立 / 視点の破綻 / 語り手の距離 / 終結の冗長な説明 / 同一表現の反復 / 禁止カード・禁止規則違反 を検査・補正する。
* **Step 9：Creative Output Guard** — 後述するGuardを実行する。
* **Step 10：保存・表示** — 最終本文 / outline / generation mode / 使用カード / 使用規則 / 検索chunk / guard結果 / generation settings / trace を保存する。

## 7. 『夢十夜』用の初期カード・規則

初期実装では、以下を候補としてカード化してください。
ただし、必ず原文と対応箇所を確認し、AIの一般知識だけでapprovedにしてはいけません。

**Style Cards**

* 「こんな夢を見た」に相当する簡潔な導入
* 異常な出来事を淡々と記述
* 説明より観察を優先
* 長文と短い認識文の対比
* 過度に現代的な説明語を避ける
* 夢の意味を本文中で解説しない

**Narrative Cards**

* 一夜ごとに独立した短編
* 中心象徴は原則一つ
* 異常は一度に一要素から始める
* 異常を夢の内部では自然な事実として扱う
* 時間・因果・生死の境界を揺らす
* 反復に変化を加える
* 最後に認識をずらす
* 終結後に教訓を説明しない

**Motif Cards**

* 夢 / 待つこと / 時間 / 死 / 記憶 / 身体 / 石 / 水 / 闇 / 声 / 道 / 乗物 / 手紙 / 鏡

既存十夜のモチーフをそのまま再利用する場合も、既存話のプロットを複製しない。

**Prohibition Cards**

* 「これは〇〇を象徴していた」と説明する
* 科学的原因を説明する
* 夢オチをさらに夢オチで閉じるだけの安易な構成
* 現代SNS的な語り口
* 漱石本人が書いたと誤認させる表示
* 原文の長い連続転載
* 既存十夜の人物・出来事の単純な再演
* 複数の象徴を詰め込みすぎる
* 最後にテーマを要約する

## 8. Creative Output Guard

既存Output Guardとは別レイヤまたは拡張可能な構造で実装する。
最低限、以下を検査する。

### 8.1 原文類似検査

生成文とL1原典の長い一致を検出する。
日本語では単純な空白区切りが使えないため、以下のいずれかを検討する。

* 文字n-gram / character shingles / longest common substring / MinHash / embedding類似＋文字列検査

閾値は設定値として管理し、コードへ直書きしない。
一致が閾値を超えた場合：

* 該当箇所を記録
* 自動再生成
* 再生成後も違反する場合は管理者確認
* 公開生成では安全側エラー

### 8.2 誤認防止

以下を生成物やUIで禁止する。

* 「夏目漱石の未発表作」
* 「発見された第十一夜」
* 「本人が書いた」
* 真作であるかのような表示

### 8.3 文体・物語規則違反

* 禁止語 / 過剰説明 / 視点崩壊 / 象徴の意味の直接説明 / 終結後の解説 / profile外の時代語 / 登場人物／語り手の混同

### 8.4 システム安全規則

既存の安全規則をそのまま適用する。
作家固有の創作規則とシステム安全規則は混同しない。
trace上でも別々に保存する。

## 9. UI／UX

### 9.1 メニュー

既存思想回答モードと混同しないように、明示的に分ける。
例：思想対話 / 創作 / 管理 / 評価

### 9.2 創作画面

推奨構成：

左側または上部：Creative Profile / 題材／モチーフ / 状況 / 読後感 / 時代 / 文字数 / 追加制約 / 生成モード / 生成ボタン

結果表示タブ：

```text
作品
構成
使用カード
Creative Trace
Guard
比較
```

初期MVPで「比較」まで難しい場合は、管理者画面に限定してよい。

### 9.3 管理画面

最低限：

* Creative Profile一覧・編集
* Creative Card一覧・編集・承認
* Creative Rule一覧・編集・承認
* evidence link確認
* generation一覧
* trace確認
* guard違反確認

既存の思想カード・判断規則管理UIを流用できる場合も、思想用データと創作用データを誤認しない表示にする。

## 10. 評価機能

創作モードでは、既存の理由一致A–D評価をそのまま使用しない。
最低限、以下の評価系を新設する。

### 10.1 Style Fidelity

語彙 / 文長 / 文末 / 語りの距離 / 時代語 / 地の文／会話 / リズム / 文体の持続

### 10.2 Narrative Grammar Fidelity

* 異常を自然な事実として扱っているか
* 中心象徴が維持されているか
* 時間・因果のずれが成立しているか
* 説明を抑制しているか
* 終結が前段と接続しているか
* 既存作品の焼き直しでないか

### 10.3 Originality／Safety

* 原文文字列一致 / プロット類似 / 特徴的表現の過剰再利用 / AI生成表示 / Guard違反

単一の総合点だけに集約せず、各軸を個別表示する。
評価者、評価日時、generation modeを保存する。

## 11. 比較実験に対応できる設計

後の論文・検証に備え、同一briefから以下の条件を比較できるようにする。

```text
B0：baseline
B1：rag_only
B2：cards_only
Proposed：assist
```

条件名を生成物本文には表示せず、評価者がブラインド判定できる仕組みを考慮する。

将来的には各条件を複数回生成し、以下を比較する。

* 文体評価 / 物語規則評価 / 反復安定性 / 原文類似 / 人間評価 / 生成コスト / latency

v0.1で統計分析までは不要だが、後から実験可能なデータを保存する。

## 12. DB・migration方針

原則：

* additive migration
* destructive migration禁止
* 既存データの意味変更禁止
* 既存enumへ値を追加する場合は影響範囲を確認
* rollback可能
* migration前後で既存テストを実行
* seedと本番データを分離
* 原典全文をmigration SQLへ埋め込まない

推奨候補：

```text
creative_profiles
creative_cards
creative_rules
creative_projects
creative_generations
creative_traces
creative_evaluations
creative_guard_results
```

ただし、既存のカード・規則基盤を安全に共用できる場合は重複を避けてよい。
その場合も、思想データと創作データを区別する明示的なdomain／scopeを必須とする。

例：`domain = judgment | creative`

既存テーブルへdomainを追加する方が危険な場合は、v0.1では別テーブルを選ぶこと。

## 13. API方針

推測で既存APIへ混ぜず、現行ルーティングを確認する。

概念上必要な操作：

```text
GET    creative profiles
GET    creative profile detail
POST   creative project
POST   creative generation
GET    creative generation
POST   creative generation regenerate
GET    creative trace
POST   creative evaluation

CRUD   creative cards
POST   creative card approve/reject
CRUD   creative rules
POST   creative rule approve/reject
```

生成処理はtimeout、retry、idempotencyを考慮する。
同一リクエストの多重送信で、複数generationが意図せず作られないようにする。

## 14. モデル・プロンプト方針

既存provider abstractionを維持する。
モデル名を処理コードへ直書きしない。
設定で切替可能にする。

役割分担の例：

* brief正規化：軽量モデル
* ルーティング：軽量モデル
* L3発火：軽量モデル
* outline：高性能モデル
* draft：高性能モデル
* revision：高性能モデルまたは軽量モデル
* judge：生成とは別プロンプト
* guard：機械検査＋LLM検査

各promptにはversionまたはhashを持たせ、traceへ保存する。

プロンプト中で以下を明示する。

* 原作者本人として名乗らない
* 原文を長く引用しない
* 出典のない有名句を作らない
* 創作規則を本文中で説明しない
* 象徴の意味を解説しない
* 指定profile外の特徴を混ぜない
* 使用カード・規則の内容を本文へ露出させない

## 15. テスト要件

### 15.1 Unit Tests

* creative profile scope
* approved cardのみ取得 / unapproved card除外
* rule_scopeの分離 / application_stageの分離
* shadowではL3が生成promptへ入らない / assistではapproved L3のみ入る
* profile外カードが混入しない
* evidence link保存 / trace保存
* guard文字列一致検査 / 誤認表現の検出
* generation mode保存 / prompt version保存

### 15.2 Integration Tests

LLMはmockして検証する。

* brief→profile→card→source→rule→outline→draft→guard→trace
* approved card 0件時の失敗
* shadowとassistの差
* Guard違反時の再生成 / 再生成上限到達時の安全側失敗
* DB transaction / idempotency
* generation失敗時にも監査ログが残る
* 既存思想モードへ影響しない

### 15.3 E2E

最低限のシナリオ：

1. 管理者が『夢十夜』profileを開く
2. approved creative cardを確認する
3. 「鏡」をmotifとして第十一夜を生成する
4. 作品を表示する
5. 使用カードを確認する
6. Creative Traceで発火・棄却規則を見る
7. Guard結果を見る
8. 同じbriefでshadow生成する
9. assist生成と比較する
10. 既存思想対話が従来どおり動作する

### 15.4 Regression

創作モード追加後も、既存の思想モードの全テストを緑にする。
創作モードのために既存テストを削除・skipしない。

## 16. 受入条件

以下をすべて満たした場合にv0.1完了とする。

* 既存思想モードの挙動が変わらない
* 既存テストがすべて通る
* 『夢十夜』creative profileが登録できる
* approvedカードのみを用いて生成できる
* approved規則のみをassist生成へ反映できる
* shadowでは規則判定を記録するが本文へ影響しない
* outlineと本文を段階生成する
* 使用した原典・カード・規則をtraceで確認できる
* 棄却規則もtraceで確認できる
* 原文類似検査が動く
* 違反時に再生成または安全側失敗になる
* AI生成物であることが常に表示される
* 原作者本人の未発表作・真作と誤認させない
* generation mode、model、prompt versionを保存する
* baseline／rag_only／cards_only／shadow／assistを区別できる
* 今後の比較評価に必要なデータが保存される
* 夏目漱石以外のprofileを追加できる構造になっている

## 17. 実装フェーズ

**T0：リポジトリ監査**
成果物：現行アーキテクチャ / DB schema / 関連ファイル一覧 / 再利用可能な処理 / 既存仕様との衝突 / リスク / 推奨実装方針
この段階ではコード変更を行わない。

**T1：正本仕様**
成果物：Creative Mode仕様書 / ER図またはschema diff / API diff / UI構成 / 生成sequence / Guard仕様 / trace仕様 / migration方針 / テスト計画 / タスク分割

**T2：DB・domain model** — additive migration / profile／card／rule／generation／trace / repository／service / migration test

**T3：管理機能** — profile管理 / creative card管理 / creative rule管理 / approval / evidence確認

**T4：生成pipeline** — brief / profile / card / retrieval / rule firing / outline / draft / revision / guard / trace

**T5：ユーザーUI** — 創作入力 / 作品表示 / 構成 / trace / guard / disclosure

**T6：評価・比較** — generation mode / baseline比較 / 評価保存 / blind condition対応の基礎

**T7：『夢十夜』初期profile** — 原典metadata / 初期カード / 初期規則 / evidence / sample prompts / test fixtures

**T8：ドキュメント同期** — architecture / schema / API / operations / tests / limitations / copyright／disclosure方針

## 18. 初回の回答で提出するもの

最初から実装を始めず、まず以下を提示してください。

1. リポジトリ監査結果
2. 現行Thinkerで再利用できる箇所
3. 変更が必要な箇所
4. 推奨schema
5. 既存テーブル共用案と別テーブル案の比較
6. 推奨案と理由
7. generation sequence
8. UI構成
9. migrationリスク
10. 実装タスクT0〜T8
11. 不明点・コードから確認できなかった点
12. MVPで実施しない事項

重大な不整合がなければ、その後、T0／T1を正本化してから実装へ進んでください。

## 19. v0.1で実施しないもの

次はスコープ外です。

* fine-tuning / 継続事前学習 / 漱石専用モデルの訓練
* 長編小説生成 / 複数章の長期一貫性
* 自動的な作家本人認定 / 「漱石が本当に書いた」とする真作判定
* 完全自動のカード承認 / 完全自動の規則承認
* 存命作家の無許可模倣
* 非公開・権利不明コーパスの取り込み
* Creative Traceを次の推論へ影響させる長期状態機構
* 創作論文用の統計的有意差検定
* 思想モードと創作モードの完全統合

## 20. 実装上の最終原則

* 作家の文章を模倣することと、作家の創作原理を構造化することを区別する
* 原作者・語り手・登場人物を混同しない
* 宣言的な創作特徴と条件付き創作操作を分離する
* 未承認のカード・規則をassist生成へ使用しない
* 発火だけでなく棄却も記録する
* 生成品質とシステム安全性を別に評価する
* 原文との類似を必ず検査する
* AI生成であることを隠さない
* PoCで確認していない能力を実現済みと表示しない
* 『夢十夜』に最適化しつつ、他作家へ追加可能な構造にする
* 既存思想モードの安定性を最優先する
