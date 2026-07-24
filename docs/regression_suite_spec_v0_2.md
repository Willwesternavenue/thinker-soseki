# 理由一致型 Regression Suite 仕様書 v0.2

「思想を再現できているか」を主観論争にしないための、再現度の操作的定義。

関連文書:
- L3 Judgment Rule データ仕様書 v0.2(格納装置)
- Thought Gap Interview 機能仕様書 v0.1(獲得装置)

## v0.1からの変更点(ピアレビュー反映)

1. スイートは **strict_eval モードで実行**する(L3失敗時に既存RAGへ落とさない)
2. dataset_split を三区分(development / validation / holdout)に拡張
3. リーク禁止の範囲を few-shot・検索インデックス(synthetic query)まで拡大
4. ケース結果は不変の `rule_version_id` を参照(規則更新後も当時の内容を再現できる)

## 1. 原則

### 1.1 正解は文章ではなく導出

本人が承認した回答文との文章一致率は測らない。同じ判断でも表現は無数にあるため。
評価対象は次の5点:

1. 結論が一致したか
2. 必要な区別(distinction)を通ったか
3. 適切な規則が発火したか
4. 禁止された推論をしていないか
5. 本人なら答えない場合に留保(abstention)できたか

これにより「結論だけでなく**理由まで本人か**」を測る。

### 1.2 再現度の定義

> 再現度 = **holdoutケース**(規則作成に使っていないケース)における、
> 結論・区別・規則発火・禁止推論・留保の総合一致率

思想モデル(judgment_rule_versions のスナップショット)ごとにスイートを実行し、
「v(n+1) は v(n) より執行草舟に近い」を測定可能な文にする。

### 1.3 strict_eval モードで実行する(必須)

スイート実行時、L3が規則選択・発火・導出に失敗した場合は**既存RAGへフォールバックせず、
失敗として記録する**(Judgment Rule仕様4.3)。

assist / production モードで測ると、ベースLLMの即興推論がL3の不足を隠し、
「L3の再現度」ではなく「LLMの器用さ」を測ることになる。
フォールバック痕跡(`l3_execution_status` / `fallback_reason`)も集計対象とする。

### 1.4 ホールドアウト規律(過学習の禁止)

予測失敗マイニングには過学習の罠がある:
**失敗ケースから規則を書き、そのケースでテストすれば必ず合格する。それは再現ではなく暗記である。**

dataset_split は三区分:

| split | 用途 |
|---|---|
| `development` | 規則の作成・修正・few-shotに自由に使える |
| `validation` | 開発中のチューニング確認(規則作成には使わない) |
| `holdout` | 公式の再現度算出のみ。他のいかなる用途にも使わない |

運用規則:

- 規則の作成・修正に使ったケースは `used_for_rule_authoring = true` を付け、`development` に移す
- **公式の再現度数値は holdout のみで算出する**
- holdoutケースを見て規則を直したくなったら、そのケースをdevelopmentに移してから直す(数値からは除外)
- **holdoutの質問・回答は次に使用してはならない**:
  規則の生成・修正 / 規則のexamples(few-shot) / 検索用synthetic query / インデックス文書
  (検索インデックス経由のリークは見落としやすい。論文化する場合は特に厳守)

### 1.5 規則IDとの共進化

`required_rule_ids` は規則が承認されて初めて記入できる。運用順序:

1. ケースは最初、**結論+必要な区別+禁止推論+留保要否**だけで作成する(規則ID欄は空)
2. 対応する規則が承認されたら、ケースに規則IDを後付けする
3. したがってスイートの評価軸は規則層の成熟に応じて段階的に厳しくなる
   (初期: 結論と区別のみ → 成熟後: 規則発火まで)

## 2. データ仕様

### 2.1 regression_cases テーブル(DDL案)

```sql
create table public.regression_cases (
  case_id text primary key,                        -- 例: CASE_001
  person_id text not null references public.personas(person_id),
  question text not null,
  category text,                                    -- 仕事/失敗/承認/競争/家族/孤独/老い/死/成功/努力 等

  -- 正解(導出ベース)
  approved_conclusions text[] not null default '{}',   -- 満たすべき結論(自然言語、複数可)
  required_distinctions text[] not null default '{}',  -- 通るべき区別
  forbidden_inferences text[] not null default '{}',   -- してはいけない推論
  required_rule_ids text[] not null default '{}',      -- 発火必須(承認後に後付け)
  acceptable_rule_ids text[] not null default '{}',    -- 発火してよい
  forbidden_rule_ids text[] not null default '{}',     -- 発火してはいけない
  expected_abstention boolean not null default false,  -- 本人なら答えない問い
  acceptable_abstention boolean not null default false, -- 留保も正解として許容

  expert_notes text,

  -- 出所と権威
  provenance text not null check (provenance in
    ('author_interview','supervisor','consultation_log')),
  authority_level real not null default 0.5,        -- 本人由来=1.0

  -- ホールドアウト規律(1.4)
  dataset_split text not null default 'holdout' check (dataset_split in
    ('development','validation','holdout')),
  used_for_rule_authoring boolean not null default false,

  status text not null default 'active' check (status in
    ('draft','active','deprecated')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.regression_runs (
  run_id uuid primary key default gen_random_uuid(),
  person_id text not null references public.personas(person_id),
  execution_mode text not null default 'strict_eval',  -- 公式runは strict_eval のみ
  rules_snapshot_note text,                         -- 実行時の judgment_rule_versions 状態
  case_results jsonb not null default '[]',         -- ケース別結果(3.1)。rule_version_id を記録
  metrics jsonb not null default '{}',              -- 集計(4章)
  created_at timestamptz not null default now()
);
```

- `required_rule_ids` 等は regression case 側の性質上、配列のままでよい
  (規則側の証拠・例と異なり、ケースは編集頻度が低く、承認ゲートで整合検査する)。
  運用で問題が出れば `judgment_rule_regression_cases` 中間テーブルへ移行する。

### 2.2 記入例

```json
{
  "case_id": "CASE_001",
  "question": "努力しても報われない人生に意味はあるか",
  "category": "努力",
  "approved_conclusions": ["結果の有無だけで生の価値を決めない"],
  "required_distinctions": ["報酬と価値を区別する"],
  "forbidden_inferences": ["敗北すれば自動的に価値が生まれる"],
  "required_rule_ids": ["JR_ABSOLUTE_NEGATIVE_001"],
  "acceptable_rule_ids": ["JR_HUMAN_BEAUTY_003"],
  "forbidden_rule_ids": ["JR_EFFICIENCY_FIRST_001"],
  "expected_abstention": false,
  "acceptable_abstention": false,
  "expert_notes": "励ますだけでは不十分。結果から価値を切り離す論理が必要",
  "provenance": "supervisor",
  "authority_level": 0.7,
  "dataset_split": "holdout",
  "used_for_rule_authoring": false
}
```

## 3. 判定方法

### 3.1 二段判定(既存Guardと同型: exact + judge)

Derivation が rule_version_id を引用するため、判定は機械層とLLM層に分かれる:

| 判定項目 | exact(機械) | judge(LLM/人間) |
|---|---|---|
| 規則発火 | L4 traceの発火rule_id(version解決済み)と required/forbidden の集合比較 | 引用した規則を**実際に適用しているか**(引用だけして無視していないか) |
| 結論一致 | — | approved_conclusions を満たすか |
| 区別 | — | required_distinctions を通っているか |
| 禁止推論 | forbidden_inferences の完全一致検出(可能な範囲) | 意味的な違反判定 |
| 留保 | expected_abstention と abstention 発動の比較 | 留保の仕方が本人らしいか |
| L3実行 | l3_execution_status(strict_evalでのfallback=不合格) | — |

exact層で確実に落ちるもの(forbidden_rule_ids の発火等)はLLM判定を待たず不合格とする。
case_results には使用した `rule_version_id` を保存し、規則が更新されても当時の判定を再現できるようにする。

### 3.2 人間評価: 理由一致4分類

監修者・本人による評価は次の四択を基本とする:

- **A. 結論も理由も近い**
- **B. 結論は近いが理由が違う** ← L3不足を発見する最重要データ
- **C. 理由の方向は近いが結論が違う**
- **D. 結論も理由も違う**

失敗タイプ分類(Judgment Rule仕様8.1)との対応:

| 評価 | 主な対応失敗タイプ |
|---|---|
| B | 判断規則・中間操作の不足 / 発火条件が広すぎる |
| C | priority規則・衝突解決の不足 / boundary・exceptionの不足 |
| D | ルーティング誤り(L2) or 規則の根本的欠落 |
| 回答すべきでなかった | abstention不足 |

### 3.3 評価画面への追加項目

既存の品質評価(admin/evaluations)に追加する:

**回答全体**
- 結論は本人らしいか
- 理由は本人らしいか(A/B/C/D)
- 本人ならこの問いをこのように捉えるか
- 一般的なLLMの価値観が混入していないか

**導出(L4 trace表示とセット)**
- 使用したJudgment Ruleは妥当か
- 必要な規則が欠けていないか
- 適用条件を満たしているか(matched_trigger_conditions / evidence_spans を表示)
- 例外規則を見落としていないか
- 規則の優先順位・衝突解決は妥当か

**TGI**
- 質問すべきGapを正しく検出したか
- 本人への質問で不足規則を特定できたか
- 回答から適切なL3候補を作れたか

## 4. 集計指標

runごとに metrics へ保存:

- `conclusion_match_rate`(holdout)
- `distinction_pass_rate`(holdout)
- `required_rule_fire_rate` / `forbidden_rule_fire_rate`(規則ID付きケースのみ)
- `forbidden_inference_rate`
- `abstention_accuracy`(答えるべき/留保すべきの正解率)
- `l3_fallback_rate`(strict_evalでのL3実行失敗率。L3カバレッジの指標)
- `reason_alignment_rate`(人間評価Aの割合。B率は改善余地の指標として併記)
- 上記のsplit別・カテゴリ別内訳

**注意**: holdoutケース数が少ない初期は数値が不安定。50件を超えるまでは
点推定ではなく傾向として読む(仕様として明記しておく)。

## 5. ケースの収集源

1. **本人インタビュー**(authority_level = 1.0): TGI Author Gap への本人回答から、
   (問い, 本人が理由まで承認した回答)ペアを起票
2. **監修者作成**: TGI仕様14.3の相談シナリオ50〜100件と統合してよい
   (question_required 等のTGI評価属性と、本仕様の導出属性を同一ケースに併記できる)
3. **実相談ログ**: 監修者評価で A 判定を得た実回答を、匿名化・同意区分を確認の上ケース化

いずれの収集源でも、起票時に dataset_split を決め、holdout に入れたものは
リーク禁止規則(1.4)の対象とする。

## 6. 実装順(MVP)

1. `regression_cases` / `regression_runs` テーブル(マイグレーション1本)
2. 評価画面に理由一致4分類(A/B/C/D)を追加 ← **最優先。規則層がなくても今日から集められる**
3. 初期ケース20〜30件を規則ID欄なしで作成(結論+区別+禁止推論のみ)。
   development / holdout の split をこの時点で確定する
4. ランナー実装: activeケースに対しパイプラインを **strict_eval** で実行し、
   exact判定+LLM judge判定 → run保存
5. judgment_rules の初期10〜20件が承認され次第、ケースに規則IDを後付けし、規則発火評価を有効化

## 7. MVPで実装しないもの

- 自動的なケース生成(LLMによる問いの量産)— 初期は人間が書く
- 統計的検定・信頼区間の自動計算
- 時期別(Temporal)再現度の分離測定 — バリアント(rule_family_id)運用開始後
