# L3 Judgment Rule データ仕様書 v0.2

> 本文中の人物名は初代人物(執行草舟)時代の記述。仕様自体は人物に依存しない設計として読むこと。

執行草舟RAGを「過去の発言からの補間」から「承認可能な判断文法の未知の問いへの適用」へ進化させるための、
判断規則(Judgment Rule)層のデータ仕様。

関連文書:
- Thought Gap Interview 機能仕様書 v0.1(獲得エンジン)
- 理由一致型 Regression Suite 仕様書 v0.2(評価装置)

## v0.1からの変更点(ピアレビュー反映)

1. provenance を「出所(origin)」「モデルビュー」「承認状態」の三軸に分離
2. 証拠・例・概念参照・衝突・レビューを中間テーブル化(配列FKの廃止)
3. 規則のidentityと不変バージョンを分離。回答トレースは rule_version_id を参照
4. 発火理由とユーザー発言の根拠箇所をL4に保存
5. Hard Negative / Boundary / Counterexample / Adversarial を正式なテストデータ化
6. 規則衝突時の実行順序をシステム側メタルールとして定義
7. rule_scope(judgment / dialogue / response_policy)を追加
8. operation に共通エンベロープ(premises / derived_claims / required_distinctions)を必須化
9. Corpus/Self-Declared乖離は属性ではなく規則バリアント(rule_family_id)で表現
10. 実行モード(shadow / assist / strict_eval / production)とフォールバック痕跡
11. 評価リーク防止(dataset_split。few-shot・検索インデックスへのholdout混入禁止)
12. L2/L3境界の原則、承認ゲートの明文化

## 1. 位置づけ

### 1.1 四層モデル

| 層 | 内容 | 役割 | 現状 |
|---|---|---|---|
| L1 原典 | 著書・講演・対談・本人回答 | 証拠 | ✅ `sources` / `source_chunks` |
| L2 概念 | Thought Card・定義・関係 | 思想の部品 | ✅ `thought_cards` |
| **L3 判断文法** | 判断規則・優先順位・転換操作・例外・留保 | **どう考えるか** | ❌ 本仕様で新設 |
| L4 Thought State | 今回の問いで発火した概念と規則 | 今回どう考えたか | ❌ 実行時状態(`answer_traces` 拡張) |

回答時フロー:

```text
質問
↓
L2から関連概念を選択(既存ルーター)
↓
L3から適用可能な判断規則を選択・発火判定
↓
L4 Thought Stateを構成
↓
Derivation(判断骨格の導出。規則の連鎖として保存)
↓
Expression(文体・語彙による文章化。既存回答生成+Guard)
```

### 1.2 L4とSession Thought Stateの区別

- **L4 Thought State** = 思想家の判断の実行状態(発火した概念・規則・衝突解決)。毎ターン再構成し、
  `answer_traces` に保存する。「どう考えたか」の監査対象。
- **Session Thought State**(TGI仕様9章)= 相談者についての理解状態(user_definitions / user_values)。
  セッション累積。

両者は独立に保持し、混同しない。

### 1.3 三軸の分離: 出所・モデルビュー・承認状態

v0.1の `provenance` 単一enumは意味の異なる状態を混ぜていたため、三軸に分離する。

| 軸 | 意味 | 保持場所 |
|---|---|---|
| **出所(origin)** | どこから抽出されたか | `judgment_rule_evidence.origin_type` + 規則の `creation_method` |
| **モデルビュー** | どの思想モデルに属するか(corpus / current_author / integrated) | 規則バリアント(1.4) |
| **承認状態** | 承認・統合されたか | `judgment_rule_versions.status` + `judgment_rule_reviews` |

「原典にも繰り返し現れ、本人も現在承認し、編集者も統合規則として承認している」規則は普通に存在する。
三軸なら表現できる。

### 1.4 Corpus/Self-Declared乖離は規則バリアントで表現する

「2010年の原典ではAを優先、2026年の本人説明ではBを優先」の場合、同一規則の属性を書き換えるのではなく、
**同じ rule_family_id に属する別バリアント**として持つ。

```text
JR_COMPETITION_001_HISTORICAL   (variant_type = 'historical')
JR_COMPETITION_001_CURRENT      (variant_type = 'current_author')
```

これにより「現在の執行草舟」「特定時期の執行草舟」「統合された公認モデル」を用途に応じて切り替えられる。
乖離の検出自体は Author Thought Gap として起票する(本人への質問候補)。

## 2. データモデル全体像

```text
judgment_rules                 規則のidentityと現在状態
judgment_rule_versions         不変のバージョン本体(承認後は書き換えない)
judgment_rule_concepts         L2概念との接続
judgment_rule_evidence         原典・本人回答・反証
judgment_rule_examples         positive / hard_negative / boundary / counterexample / adversarial
judgment_rule_conflicts        規則間衝突と解決
judgment_rule_reviews          本人・編集者の承認履歴(スコープ別)
judgment_rule_search_documents 検索用の複数表現(Phase 2)
judgment_rule_embeddings       モデル別Embedding(Phase 2)
```

**MVPは次の5テーブルで開始する**:
`judgment_rules` / `judgment_rule_versions` / `judgment_rule_evidence` /
`judgment_rule_examples` / `judgment_rule_reviews`

conflicts はMVP初期(10〜20規則)ではペア数が少ないため versions の content 内に暫定保持してよいが、
Phase 2で必ずテーブル化する。search_documents / embeddings は初期規則数では不要
(approved全規則のtrigger_conditionsをL3選択プロンプトへ直接入れられる)。

## 3. スキーマ

### 3.1 judgment_rules(identity)

```sql
create table public.judgment_rules (
  rule_id text primary key,                        -- 例: JR_ABSOLUTE_NEGATIVE_001
  person_id text not null references public.personas(person_id),
  rule_family_id text not null,                    -- バリアントの束ね(単独ならrule_idと同値)
  variant_type text not null default 'integrated' check (variant_type in (
    'historical','current_author','integrated','alternative_interpretation')),
  title text not null,
  rule_scope text not null check (rule_scope in (
    'judgment',          -- 判断文法(value_transformation / distinction / priority 等)
    'dialogue',          -- 対話戦略(question_rule)
    'response_policy'    -- 回答方針(abstention / prohibition)
  )),
  rule_type text not null check (rule_type in (
    'value_transformation','distinction','priority','contradiction_hold',
    'boundary','exception','prohibition','question_rule','abstention',
    'temporal_override')),

  -- ライフサイクル(承認状態はバージョン側)
  lifecycle text not null default 'active' check (lifecycle in
    ('active','deprecated','merged','split')),

  -- 生成系譜(予測失敗マイニングの追跡に必須)
  creation_method text not null check (creation_method in (
    'manual','corpus_extraction','prediction_failure',
    'tgi_author_interview','regression_analysis','rule_split','rule_merge')),
  source_gap_id text,                              -- TGI Author Gap(soft reference)
  source_failure_case_id text,                     -- 予測失敗ケース(soft reference)
  created_by text,
  creation_rationale text,

  valid_from date,                                 -- 時期有効性(temporal系以外は通常null)
  valid_to date,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

### 3.2 judgment_rule_versions(不変スナップショット)

承認済みバージョンは原則として書き換えず、変更は新バージョンを作る。
**answer_traces は rule_version_id を参照する**(半年後に規則がv4になっても、当時使った内容を再現できる)。

```sql
create table public.judgment_rule_versions (
  rule_version_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id),
  version int not null,
  status text not null default 'draft' check (status in
    ('draft','reviewing','approved','rejected','deprecated')),
  content jsonb not null,                          -- 3.3のエンベロープ形式
  change_reason text,
  created_at timestamptz not null default now(),
  unique (rule_id, version)
);
```

### 3.3 content の共通エンベロープ

「その他はdescription」方式は運用開始後にJSONが発散するため廃止。
**全rule_type共通で次を必須**とし、タイプごとの追加フィールドを許す。
TypeScript側でZod等によりrule_type別に検証する。

```json
{
  "schema_version": "1.0",
  "trigger_conditions": ["対象が失敗・敗北・不成功によって自己価値を否定している"],
  "premises": ["相談者が外部評価と自己価値を同一視している"],
  "action": {
    "from": "外部結果による価値評価",
    "to": "結果と存在価値を分離した評価"
  },
  "derived_claims": ["敗北はそのまま自己価値の否定を意味しない"],
  "required_distinctions": ["結果と価値", "敗北と無価値"],
  "exceptions": ["具体的な責任回避や加害を、敗北の美として正当化する場合"],
  "forbidden_inferences": ["敗北すれば自動的に価値が生まれる", "努力や現実的改善は不要である"],
  "requires_rule_ids": [],
  "blocks_rule_ids": [],
  "next_rule_candidates": ["JR_HUMAN_BEAUTY_002"],
  "default_priority": 50,
  "explanation": "編集者向けの自然言語説明"
}
```

- `action` の形はrule_typeごとに定義(value_transformation: from/to、distinction: between/criterion、
  priority: higher/lower/condition、contradiction_hold: left/right/stance、abstention: response_style)
- `premises` → `action` → `derived_claims` により、**Derivationを文章ではなく規則の連鎖として保存**できる:

```text
JR_001(「結果」と「価値」を分離)
↓ derived_claims が次の規則の premises を満たす
JR_008(敗北の中の美へ再評価)
```

- `default_priority`(旧priority): 人物の判断に常に通用する単一順位は存在しないため、
  あくまで最後のタイブレーカー。主たる衝突解決は judgment_rule_conflicts(3.7)

### 3.4 judgment_rule_concepts(L2接続)

```sql
create table public.judgment_rule_concepts (
  rule_id text not null references public.judgment_rules(rule_id),
  thought_id text not null,   -- soft reference(注記参照)
  role text not null check (role in ('input','output','context','excluded')),
  primary key (rule_id, thought_id, role)
);
```

> **実装注記**: 現行スキーマでは `thought_cards` のPKは `card_id` であり、`thought_id` に一意制約が
> ないためFKを張れない。MVPでは soft reference とし、将来L2正規化(conceptsマスタテーブル)時に
> FK化する。それまでは整合性チェックをバッチ(または承認ゲート)で行う。

### 3.5 judgment_rule_evidence(証拠。反証を含む)

```sql
create table public.judgment_rule_evidence (
  evidence_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id),
  chunk_id text references public.source_chunks(chunk_id),
  author_answer_id text,       -- 本人回答(TGI 12.4)。テーブル未実装のためsoft reference
  evidence_role text not null check (evidence_role in (
    'supports','contradicts','defines','bounds','exception','historical')),
  origin_type text not null check (origin_type in (
    'corpus_inferred','author_declared','mixed','prediction_failure','tgi_interview')),
  note text,
  created_at timestamptz not null default now()
);
```

**`contradicts` が重要**: 規則に都合のよい根拠だけでなく、反証となる原典も保存する。
反証が蓄積した規則は再レビュー対象になる。

### 3.6 judgment_rule_examples(発火テストデータ)

単純なpositive/negativeでは不足する。**意味的に近いが発火してはいけないHard Negative**が最重要。

```sql
create table public.judgment_rule_examples (
  example_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id),
  example_type text not null check (example_type in (
    'positive',        -- 明確に発火すべき
    'hard_negative',   -- 話題は近いが発火すべきでない
    'boundary',        -- 条件次第で発火が変わる
    'counterexample',  -- 規則の例外を示す
    'adversarial'      -- 誤適用を誘う入力
  )),
  input_text text not null,
  expected_activation boolean,
  expected_reason text,
  dataset_split text not null default 'development' check (dataset_split in
    ('development','validation','holdout')),
  status text not null default 'draft' check (status in
    ('draft','approved','deprecated')),
  created_at timestamptz not null default now()
);
```

例(JR_ABSOLUTE_NEGATIVE_001):

| type | 入力 | 発火 |
|---|---|---|
| positive | 努力しても報われず、自分には価値がないと思います | ✅ |
| hard_negative | 試合に負けた原因を分析して、次に勝つ方法を考えたい | ❌(「敗北」を含むが自己価値否定ではない) |
| adversarial | 自分の不注意で人に損害を与えましたが、敗北には美があるので責任を取らなくてもよいでしょうか | ❌+exception/forbidden_inferences発火 |

### 3.7 judgment_rule_conflicts(規則間衝突)

conflicts_with jsonb は「A側とB側の不一致」「二重登録」「衝突自体の承認状態・根拠・時期依存を持てない」
問題があるため、テーブル化する(MVP初期はcontent内暫定保持可、2章参照)。

```sql
create table public.judgment_rule_conflicts (
  conflict_id uuid primary key default gen_random_uuid(),
  left_rule_id text not null references public.judgment_rules(rule_id),
  right_rule_id text not null references public.judgment_rules(rule_id),
  resolution_type text not null check (resolution_type in (
    'left_wins','right_wins','context_dependent','hold_both','unresolved')),
  resolution_condition text,
  rationale text,
  status text not null default 'draft',
  author_gap_id text,          -- 未解決ペアはAuthor Gap質問候補として自動起票
  valid_from date,
  valid_to date,
  created_at timestamptz not null default now(),
  -- 二重登録防止: rule_idの辞書順で left < right に正規化して登録する(運用規約)
  unique (left_rule_id, right_rule_id)
);
```

`unresolved` / `context_dependent` のペアは、TGIの本人質問
(「AとBが衝突する場合、どちらを優先しますか」= ペア単位の強制選択)として管理画面に出す。

### 3.8 judgment_rule_reviews(レビュー履歴)

単一のauthor_validation JSONでは「編集者が承認→本人が一部修正→後日見解変更→時期解釈への異議」
という現実の履歴を表現できない。スコープ別のレビュー履歴として持つ。

```sql
create table public.judgment_rule_reviews (
  review_id uuid primary key default gen_random_uuid(),
  rule_version_id uuid not null references public.judgment_rule_versions(rule_version_id),
  reviewer_id text,
  reviewer_role text not null check (reviewer_role in (
    'author','editor','researcher','system_evaluator')),
  verdict text not null check (verdict in (
    'approved','approved_with_changes','rejected','uncertain')),
  review_scope text not null check (review_scope in (
    'meaning',             -- 意味
    'reasoning',           -- 理由・導出
    'boundary',            -- 適用範囲
    'historical_validity', -- 過去の思想として妥当か
    'current_validity',    -- 現在の思想として妥当か
    'wording'              -- 言い回し
  )),
  note text,
  created_at timestamptz not null default now()
);
```

これにより「**結論は承認されたが理由は未承認**」「現在の思想としては承認するが過去の思想としては否定」
を表現できる。

### 3.9 judgment_rule_search_documents / embeddings(Phase 2)

固定次元ベクトルを本体に置かず、検索表現とEmbeddingを分離する。
同じ規則でも複数の検索表現(タイトル/発火条件/想定質問/positive example/判断操作/派生クエリ)を持てる。

```sql
create table public.judgment_rule_search_documents (
  search_document_id uuid primary key default gen_random_uuid(),
  rule_id text not null references public.judgment_rules(rule_id),
  document_type text not null check (document_type in (
    'canonical','trigger','example','synthetic_query','derived_claim','editor_alias')),
  search_text text not null,
  approval_status text not null default 'draft',
  generated_by text,
  generation_version text,
  created_at timestamptz not null default now()
);

create table public.judgment_rule_embeddings (
  search_document_id uuid not null references public.judgment_rule_search_documents(search_document_id),
  embedding_model text not null,
  embedding_version text not null,
  embedding extensions.vector(1536),
  content_hash text not null,
  indexed_at timestamptz not null default now(),
  primary key (search_document_id, embedding_model, embedding_version)
);
```

Embeddingモデル変更・synthetic query追加・インデックス再構築を、規則本体に触れずに行える。
**MVPでは不要**: 初期規則10〜20件なら、approved全規則のtrigger_conditionsをL3選択プロンプトへ直接入れる。

## 4. 実行エンジン仕様

### 4.1 競合解決のメタルール(システム側)

複数の規則が同時に発火した場合の優先順。
**これは執行思想そのものではなく、L3実行エンジンの規則**であり、人物固有規則と明確に区別する。

```text
1. 安全上の強制ルール(システム安全フロー)
2. abstention
3. 明示的な prohibition
4. temporal_override
5. exception
6. boundary
7. より具体的な trigger を持つ規則
8. 承認済みの pairwise conflict resolution(judgment_rule_conflicts)
9. default_priority
10. 未解決の場合は矛盾を保持し、回答内で不確実性を明示する
```

10は contradiction_hold の思想とも整合する: 解決できない衝突を無理に潰さない。

### 4.2 発火判定の根拠保存(L4 / answer_traces拡張)

「LLMが発火判定する」だけでは監査できない。発火・棄却の両方について根拠を保存する。

```json
{
  "rule_version_id": "…",
  "activation_decision": "fired",
  "matched_trigger_conditions": ["価値判断が外部評価に一元化されている"],
  "evidence_spans": [
    {"message_id": "msg_003", "quote": "上司に認められないなら自分には価値がありません"}
  ],
  "rejected_conditions": [],
  "selection_reason": "外部評価と自己価値の同一視が明示されたため"
}
```

重要なのはスコアではなく、**どの条件に合致したか / ユーザー発言のどこを根拠にしたか /
なぜ似た規則を選ばなかったか**。これがないとL3を作っても「LLMがなんとなく選んだ」状態に戻る。
候補に挙がったが発火しなかった規則も `activation_decision: "rejected"` として記録する。

### 4.3 実行モードとフォールバック痕跡

フェイルオープンは正しいが、静かに戻すと研究評価が成立しない。モードと痕跡を持つ。

| mode | 動作 |
|---|---|
| `shadow` | L3を実行するが回答には使わない(ログのみ) |
| `assist` | L3を使い、失敗時は既存RAGへ戻る |
| `strict_eval` | L3失敗時は回答を生成せず失敗として記録 |
| `production` | ユーザー体験を優先してフェイルオープン |

フォールバック時は必ず痕跡を残す:

```json
{
  "l3_execution_status": "fallback",
  "fallback_reason": "no_approved_rule_matched",
  "candidate_rule_ids": [],
  "response_pipeline": "legacy_rag"
}
```

**Regression Suiteは strict_eval で実行する**。assist/productionで測ると、
既存LLMの即興推論がL3の不足を隠す。

## 5. L2とL3の境界

編集者が「これはThought Cardの関係かJudgment Ruleか」で迷わないための原則。

**L2へ置くもの** — 文脈に依存せず成立する、概念の静的な関係
- 絶対負は敗北と関連する
- 美と生の価値は執行思想上で近接する

**L3へ置くもの** — 条件が満たされた場合に、認識・価値・結論を変換する実行可能な関係
- 外的敗北によって自己価値を否定している場合、勝敗と存在価値を分離して捉え直す

> **L2 = 何と何が関係するか。L3 = いつ、何を、どう変換するか。**

## 6. 評価リークの防止

本人インタビュー回答や回帰テスト事例を、そのまま規則のpositive exampleや検索インデックスへ入れると、
ホールドアウト評価が成立しなくなる。

- examples / regression cases に `dataset_split`(development / validation / holdout)を持つ
  (規則本体ではなくデータ側に持たせる)
- **holdout の質問・回答は次に使用してはならない**:
  規則の生成・修正 / few-shot例(positive_examples等) / 検索用synthetic query / インデックス文書
- 論文化する場合はこの規律が特に重要

詳細は Regression Suite 仕様書 v0.2 の共進化・ホールドアウト規律を参照。

## 7. 承認ゲート(Approvedの最低条件)

規則ごとの品質ばらつきを防ぐ。最初の10〜20規則はこの基準で丁寧に作る。

- [ ] タイトルと規則タイプ・スコープが定義済み
- [ ] 発火条件が1件以上
- [ ] derived_claims が1件以上
- [ ] positive example 2件以上
- [ ] hard_negative 2件以上
- [ ] boundary example 1件以上
- [ ] 原典根拠2件以上、または本人回答1件以上(evidence)
- [ ] forbidden_inference が検討済み(「なし」の明示も可)
- [ ] 編集者レビュー済み(reviews に記録)
- [ ] 発火ユニットテスト合格(examples 全件で期待どおり)
- [ ] 少なくとも1件の Regression Case と紐付く
- [ ] 他のapproved規則との衝突が検査済み

## 8. 予測失敗マイニング(中心ループ)

### 8.1 失敗タイプ → 不足規則の対応

| 失敗タイプ | 不足している規則 |
|---|---|
| 結論が違う | `priority` 規則の不足 |
| 結論は同じだが理由が違う | 判断規則または中間操作(`value_transformation` / `distinction`)の不足 |
| 思想は近いが適用範囲が違う | `boundary` / `exception` の不足 |
| 何にでも同じ思想を当てる | 発火条件が広すぎる(trigger_conditions / hard_negative の追加) |
| 本人なら答えない問いに回答した | `abstention` の不足 |
| 時期によって回答が違う | `temporal_override` またはバリアント分離の不足 |

### 8.2 ループ

```text
未知質問
↓
現行モデルの回答(strict_evalで測定)
↓
本人・監修者評価(理由一致4分類)
↓
失敗タイプを分類(8.1)
↓
不足しているL3規則を推定
↓
TGIで本人に質問(衝突はペア単位の強制選択で聞く)
↓
規則候補を作成(creation_method='prediction_failure'、source_failure_case_id記録)
↓
承認ゲート(7章)
↓
回帰テスト(holdoutで測定。該当ケースはdevelopmentへ移動済み)
```

## 9. TGI / Author Gap との接続

TGIが検出した Author Gap は、次のいずれが必要かを分類して起票する:

- 新しい概念カード(L2)
- 概念間エッジ(L2関係)
- Judgment Rule(L3)
- 例外規則(L3 `exception` / `boundary`)
- 本人の留保規則(L3 `abstention`)
- 衝突解決(judgment_rule_conflicts の unresolved ペア)
- バリアント分離(Corpus/Self-Declared乖離)

## 10. 初期構築(MVP)

1. 5テーブルのマイグレーション:
   `judgment_rules` / `judgment_rule_versions` / `judgment_rule_evidence` /
   `judgment_rule_examples` / `judgment_rule_reviews`
2. 最重要思想から判断規則を**10〜20件**抽出(絶対負・勝敗・生の価値・美・愛・死・競争)。
   承認ゲート(7章)を満たして approved にする
3. 発火ユニットテストランナー(examples全件 → 期待どおり発火/非発火か)
4. shadow モードでL3選択・発火判定を既存パイプラインに並走させ、L4痕跡のみ記録
5. 管理画面: 規則一覧・バージョン・レビュー(カード管理画面と同型でよい)

## 11. MVPで実装しないもの

- 規則の自動学習・重み最適化
- temporal_override の本格実行(スキーマのみ)
- 形式的な矛盾解決(SATソルバ等)
- 全Thought Graphとの完全統合
- 複数思想家に共通する汎用オントロジー
- 自動的な規則分割・統合
- search_documents / embeddings(Phase 2。初期規則数では不要)
- conflicts テーブル(Phase 2。初期はcontent内暫定保持可)

ただし**バージョン固定・証拠・Hard Negative・発火根拠・レビュー履歴はMVPから省かない**。
これらは後付けが難しい。
