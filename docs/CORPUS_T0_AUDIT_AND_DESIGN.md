# 漱石コーパス構築・思想／創作分離: T0監査 + 設計提案

> 上位指示: 「Thinker 夏目漱石コーパス構築・思想／創作分離 実装指示書 v0.1」（2026-07-27 受領）。
> 本書は同指示書 **§17「Claudeの最初の回答で提出するもの」18項目**への回答であり、
> **設計提案（発注者未承認）**。§17 の指示どおり、承認までは production migration・大量取り込みを行わない。
>
> 検証済みの実データ: 青空文庫公式CSV（`list_person_all_extended_utf8.zip`、2026-07-27 取得）の
> 人物ID 000148 全113行を実際に解析した結果に基づく。推測ではない。

---

## ⚠️ 用語の衝突について（最初に読む）

本指示書の **T0〜T8** は、既存の [T1_CREATIVE_MODE_DESIGN.md](T1_CREATIVE_MODE_DESIGN.md) の T0〜T8 とは**別物**。
混同すると進捗管理が壊れるため、本書では新指示書のフェーズを **C-T0〜C-T8**（Corpus）と表記する。

| | 既存（創作モード） | 本指示書（コーパス） |
|---|---|---|
| T0 | maurice監査（完了） | C-T0: コーパス観点の監査（本書） |
| T4 | 生成パイプライン（完了） | C-T4: Document/Chunk Tagger |
| T6 | 夢十夜 profile 投入（未着手） | C-T5: Phase A コーパス投入 |

**創作モードの T4c まで（生成パイプライン）は実装・検証済み**で、本指示書はその下層にある
L1コーパスの作り直しを求めている。既存実装との衝突は §3 に列挙する。

---

## 1. 現行Thinkerのリポジトリ監査結果

詳細は [T0_AUDIT_REPORT.md](T0_AUDIT_REPORT.md)（maurice `b92e60f` 実地監査）を正とする。本書では
コーパス観点の要点のみ再掲する。

- **L1**: `sources`（source_id text PK / person_id / title / source_type / author / language /
  priority / status / source_url）+ `source_chunks`（chunk_id / source_id / person_id /
  chapter_id / chapter_title / section_title / char_start / char_end / chunk_type / **speaker** /
  **verbatim** / question / answer / text / summary / related_thought_ids / evidence_roles /
  chunker_version / chunk_hash / embedding vector(1536) / status）
- **L2**: `thought_cards`（approved部分一意 `(person_id, thought_id)`）/ `thought_questions` /
  `thought_evidence_links` / `concept_aliases`
- **L3**: `judgment_rules` + `judgment_rule_versions/evidence/examples/reviews`
- **L4**: `answer_traces`（+`l3_shadow`）
- **創作モード（実装済み）**: `creative_profiles` / `creative_cards` / `creative_generations` /
  `creative_traces`（migration `20260726000001_creative_mode.sql`）
- 検索: pgvector + PGroonga。RPC 5関数（すべて `target_person_id text default 'natsume_soseki'`）
- ingestion: worker の `extract → clean → chunk → embed → distill_light`（`CHUNKER_VERSION='v1'`）
- 承認: `status` 5値 + approved のみ生成利用（既存不変条件）
- RLS: 全テーブル有効・ポリシー0本（deny-all、service_role のみ）

### 1.1 実データで確認した事実（青空文庫CSV 113行）

| 項目 | 実測値 |
|---|---|
| 漱石の公開作品行数 | **113**（指示書§2 の記載と一致） |
| canonical work（作品名の完全一致） | 107 |
| **canonical work（§1.2 の同定規則を適用・C-T2b で実測）** | **106** |
| 複数版を持つ作品 | **7件**（それから / 三四郎 / 京に着ける夕 / 変な音 / 子規の画 / 門 / **吾輩は猫である**） |
| 文字遣い | 新字新仮名 **84** / 新字旧仮名 **15** / 旧字旧仮名 **14** |
| テキストファイルURLあり | **112 / 113** |
| 作業中（CSV外・HTMLページのみ） | **8件** |

Phase A のコア資料**13件（思想7 + 創作5 + 夢十夜）はすべて公開・取得可能・新字新仮名**であることを
CSVで個別に確認済み。C-T5 は取得面のリスクなしに着手できる。

底本メタデータの充足率（113件中）: 底本名 113 / 出版社 113 / 初版発行年 112 / 入力に使用した版 105 /
**校正に使用した版 66** / 底本の親本 53 / 入力者 113 / **校正者 112** / **初出 54**。
→ 校正・親本・初出は欠落があるため **nullable 必須**。「必ず入る」前提の設計にしてはいけない。

---

## 2. 現行L1/L2/L3/L4との対応

| 指示書の概念 | 現行の受け皿 | 判定 |
|---|---|---|
| 原典文書 | `sources` | 流用可。ただし document metadata が約30項目不足（§4） |
| チャンク | `source_chunks` | 流用可。`speaker`/`verbatim` はあるが `speaker_role`/`claim_type` 等が不足 |
| canonical_work / edition | **無し** | 新設が必要（§5） |
| corpus_role | **無し** | 新設が必要。現行 `sources.priority`(core/important/support/style/archive) は別概念 |
| 思想カード | `thought_cards` | 流用可 |
| 創作カード | `creative_cards`（実装済み） | 流用可。`criticism` 型と `evidence_type` が不足（§3） |
| 判断規則 | `judgment_rules` | 流用可 |
| 創作規則 / Bridge Rule | **無し**（創作モードv0.1で延期中） | schema 準備のみ |
| trace | `answer_traces` / `creative_traces` | 流用可。§13 の推論表示項目は追加が必要 |
| 承認状態 | 各テーブルの `status` | 流用可（§3 に用語差あり） |

---

## 3. 既存実装との衝突（重要・要判断）

創作モードは既に T4c まで実装済みのため、以下は**新規実装ではなく変更**になる。

| # | 衝突 | 現行 | 指示書 | 推奨 |
|---|---|---|---|---|
| 1 | 創作カード種別 | 12値（style/narrative/motif/character/ending/prohibition/setting/dialogue/perspective/rhythm/theme/historical_language） | 8値。うち **`criticism` が現行に無い** | **`criticism` を追加**（additive。check制約の入替） |
| 2 | 創作カードの根拠種別 | 無し | `evidence_type`: author_creative_theory / demonstrated_in_fiction / critic_interpretation | **列を追加**。「創作論由来」と「小説本文由来」を区別できないと §11.2 を満たせない |
| 3 | 承認状態の語 | `draft`/reviewing/approved/rejected/deprecated | **`candidate`**/reviewing/approved/rejected/deprecated | ✅ **`draft` を維持**（2026-07-27 発注者確認済み）。指示書の `candidate` は `draft` と読み替える。check制約の変更不要 |
| 4 | 原典スコープの指定 | `creative_profiles.source_scope = {"source_ids": [...]}` | canonical_work / edition / corpus_role で指定 | **後方互換で拡張**（`source_ids` を残しつつ `corpus_roles` / `edition_ids` を追加） |
| 5 | 原典投入の単位 | 章（chapter_title）単位で全文投入 | corpus_role 別 Index + routing | v0.1 の簡略化として維持し、C-T7 で routing へ移行 |

**衝突3（draft vs candidate）は発注者判断が必要**。他は additive で吸収できる。

---

## 4. 追加が必要なschema

### 4.1 方針

- 既存テーブルには **nullable 列の追加のみ**（additive）。既存の insert/select は影響を受けない
- 検索でフィルタする列（`corpus_role` / `speaker_role` 等）は**実カラム**にして索引を張る。
  別テーブルに逃がすと vector 検索RPCに join が必要になり、既存の思想モードRPCを触ることになる
- 長い尾（底本・入力者・注記など約25項目）は **jsonb 1列**に収める

### 4.2 新規テーブル

```sql
-- 作品(版をまたぐ同一性)。113行 → 107作品 + 6作品の版違いを束ねる
canonical_works(
  canonical_work_id text pk, person_id fk, canonical_title text,
  title_variants text[],        -- 「吾輩は猫である」「吾輩ハ猫デアル」等
  first_publication text, ndc text, created_at, updated_at)

-- 版(青空文庫の1エントリ = 1版)
work_editions(
  edition_id text pk,           -- 青空文庫の作品ID(例 000799)
  canonical_work_id fk, aozora_work_id text,
  orthography text,             -- 新字新仮名 / 新字旧仮名 / 旧字旧仮名
  work_status text check (published|in_progress),
  is_primary_retrieval_edition boolean not null default false,
  card_url, text_file_url, text_charset, text_encoding,
  bottom_text jsonb,            -- 底本名/出版社/初版発行年/入力に使用した版/校正に使用した版/親本
  input_by, proofread_by, aozora_published_at, aozora_updated_at,
  copyright_status, license_note, retrieved_at, content_sha256, parser_version,
  duplicate_of text,            -- 重複検出時の代表版
  created_at, updated_at)
```

### 4.3 既存テーブルへの追加（すべて nullable）

```sql
alter table sources
  add column edition_id text references work_editions(edition_id),
  add column corpus_role text check (corpus_role in (
    'core_thought','supporting_thought','creative_grammar','character_judgment',
    'narrative_reference','style_reference','biographical_context',
    'validation_only','excluded')),
  add column document_genre text,      -- lecture/essay/criticism/literary_theory/preface/... (16値)
  add column authority_level text,     -- author_direct/author_contextual/fictional_indirect/third_party/editorial/unknown
  add column source_provider text,     -- aozora/ndl/manual_upload/licensed_source
  add column corpus_metadata jsonb not null default '{}';

alter table source_chunks
  add column speaker_role text,        -- author_direct/narrator/character/quoted_person/interviewer/editor/unknown
  add column character_id text,
  add column addressee text,
  add column claim_type text,          -- normative_claim/descriptive_observation/... (13値)
  add column assertion_status text,    -- asserted/attributed/hypothetical/questioned/ironic/ambiguous/rejected_by_author
  add column thought_eligibility text,
  add column creative_eligibility text,
  add column is_quotation boolean not null default false,
  add column is_hypothetical boolean not null default false,
  add column is_ironic boolean not null default false,
  add column tag_confidence numeric,
  add column classification_reason text,
  add column review_status text not null default 'unreviewed',
  add column reviewed_by text, add column reviewed_at timestamptz,
  add column chunk_metadata jsonb not null default '{}';  -- ruby/gaiji/注記等
```

### 4.4 creative_cards への追加（§3の衝突1・2）

```sql
alter table creative_cards
  add column evidence_type text check (evidence_type in
    ('author_creative_theory','demonstrated_in_fiction','critic_interpretation'));
-- card_type に 'criticism' を追加(check制約の入替)
```

### 4.5 マニフェスト（作業中作品の記録・§2.1）

CSVには**公開作品しか載らない**ため、作業中8件は作家別HTMLページからのみ取得できる。
本文取得・Index登録・L2/L3候補生成は行わず、記録のみ行う。

```sql
aozora_manifest_entries(
  entry_id text pk, person_id, aozora_work_id, title, orthography,
  work_status text, listed_at timestamptz, source_page_url)
```

---

## 5. canonical work / edition の管理案

### 5.1 実データが示す設計上の落とし穴（重要）

指示書 §14.2 は「『吾輩は猫である』の版違いを統合」を要求している。しかし実データは:

| 作品ID | 作品名 | 文字遣い | テキスト |
|---|---|---|---|
| 000789 | **吾輩は猫である** | 新字新仮名 | あり |
| 000790 | **吾輩ハ猫デアル** | 旧字旧仮名 | **なし** |

**作品名の文字列が一致しない**（新字新仮名版と旧字旧仮名版でタイトル自体の表記が違う）。
そのため CSV の作品名で束ねると、この2つは別作品として数えられる（実際、私の集計でも
「複数版を持つ作品6件」に吾輩は猫であるは入っていない）。

→ **canonical work のグルーピングを作品名の完全一致に依存させてはいけない**。

推奨する判定順:

1. **作品名読み（`作品名読み`列）の一致** — 「わがはいはねこである」で両版が一致する
2. 正規化タイトル（NFKC + カタカナ→ひらがな + 旧字→新字の簡易写像）の一致
3. 正規化後本文hash・段落対応（指示書§8.7）
4. 上記でも割れる場合は**人手確認キューへ**（自動統合しない）

`canonical_works.title_variants` に両表記を保持し、検索はどちらでも当たるようにする。

### 5.2 primary retrieval edition

- 原則 **新字新仮名**を `is_primary_retrieval_edition=true`（84件が該当）
- 旧字旧仮名・新字旧仮名は削除せず保存し、既定の検索からは除外（版比較・校訂確認用）
- 000790（吾輩ハ猫デアル）は**テキストファイルが存在しない**ため、edition として登録するが
  本文取得はスキップし `text_file_url=null` を記録する

---

## 6. 青空文庫 Importer の設計（C-T2）

```
worker/src/aozora/
  manifest.py   -- CSV取得 → 漱石113行抽出 → canonical_work/edition へ正規化
                   + 作家別HTMLから作業中8件を manifest_entries へ(本文は取らない)
  fetch.py      -- edition の text_file_url から zip 取得(GitHubミラー既定)
  parse.py      -- 文字コード判定/変換・ルビ・外字注記・ヘッダフッタ分離(C-T3)
```

- 一括取得は **GitHub 公式ミラー**（`aozorabunko/aozorabunko`）の sparse checkout を既定とし、
  `aozora.gr.jp` への機械的な連続アクセスはしない（[AOZORA_INGESTION.md](AOZORA_INGESTION.md) §2）
- 取得ごとに `content_sha256` / `retrieved_at` / `parser_version` を記録（§15-19）
- `work_status=in_progress` は本文取得・Index登録・L2/L3候補生成を**行わない**（§2.1）

---

## 7. parser / normalizer の設計（C-T3）

指示書 §8.1 の4形式を保存する。

| 形式 | 内容 | 用途 |
|---|---|---|
| `raw_bytes` | 取得した原ファイル（zip解凍後のSJISバイト列） | 再現性・検証 |
| `raw_text` | UTF-8変換後・注記を残した本文 | 校訂・原情報 |
| `normalized_text` | 注記除去・ルビ分離後 | 検索・embedding |
| `display_text` | ユーザー表示・引用用 | UI |

- **文字コード**: CSVの `テキストファイル符号化方式=ShiftJIS` / `文字集合=JIS X 0208` を参照しつつ、
  ヘッダ依存にせず判定する。実処理は **CP932** で変換（`shift_jis` 指定は機種依存文字で落ちる）。
  変換エラー・文字化け率を記録し、閾値超過は Index 登録しない（§8.2）
- **ルビ**: `《...》` を `surface_text` / `reading` に分離。embedding 用本文には**漢字を残し、
  読みは重複挿入しない**（§8.3）
- **外字・注記**: `［＃...］` を単純削除せず、7種（formatting/gaiji/editorial/original_text/
  ruby/emphasis/page）に分類して `chunk_metadata` へ保存（§8.4）
- **ヘッダ・フッタ**: 青空文庫の説明・入力者・底本情報・表記についてを本文チャンクへ混ぜない。
  ただし **metadata へは保存**（§8.5）
- **チャンク分割**: 固定文字数で切らない。優先順位 章 → 節 → 段落 → 話者交代 → 意味段落 → token上限（§8.6）
  - 講演・評論: 主張／具体例／反論／例外／結論をできる限り別チャンクに
  - 小説: 語り手記述／人物発言／人物行動／場面転換を識別可能に
- ⚠️ 現行 `CHUNKER_VERSION='v1'` は既存の思想モードで使用中。青空文庫用は
  **`chunker_version='aozora_v1'` として別系統**にし、既存チャンクを再生成しない

---

## 8. document / chunk tags の確定案

- **document_genre**（16値）: lecture / essay / criticism / literary_theory / preface / afterword /
  letter / interview / memoir / travelogue / novel / short_story / sketch / advertisement /
  announcement / other
- **corpus_role**（9値）: §4.3 のとおり
- **authority_level**（6値）/ **speaker_role**（7値）/ **claim_type**（13値）/
  **assertion_status**（7値）: 指示書 §7.2〜7.7 のまま採用

⚠️ **NDCだけで genre を決めない**（§7.2）。実データでも NDC 914（随筆）が47件と最多で、
講演・評論・随筆が同じ分類に同居している。タイトル・初出・本文冒頭・底本情報を併用する。

---

## 9. Index分離案

**推奨: 共通テーブル + `corpus_role` による厳格な metadata filter**（物理分割はしない）。

理由: 現行インフラは Supabase の単一 Postgres 上の `source_chunks.embedding`（pgvector）であり、
別 Vector Collection という概念がない。物理分割は移行コストが大きく、指示書 §10 も
「現行インフラを監査して決めてよい」としている。**論理的な分離は必須要件どおり厳格に守る**。

実装:
- `source_chunks.corpus_role`（sources から継承）+ `speaker_role` に複合索引
- 既存RPC（`match_source_chunks_all` 等）に **`target_corpus_roles text[] default null`** を
  追加した新版を `create or replace` で定義。**default null = 既存挙動**なので既存呼び出しは無変更
- 論理Index名（`author_thought_core_index` 等8種）は**フィルタのプリセット定義**として
  コード側に持つ

---

## 10. query routing 案

| 質問種別 | 検索順（指示書§10） |
|---|---|
| 思想 | core_thought → supporting_thought → creative_grammar（必要時）→ fictional_indirect（明示） |
| 創作 | creative_grammar → narrative_reference（対象作品）→ style_reference → core_thought（Bridge Rule経由のみ） |
| 人物 | character_judgment（対象人物）→ narrative_reference（対象作品）→ core_thought（比較のみ） |

- 既存の `classify.ts` は8種の `QueryKind` を返す。ここに人物質問（`character`）の判定を追加する
- ⚠️ 既存 `QueryKind` には既に `"creative"` があるが、これは「創作的な質問」の意味で
  創作モードとは別概念（T0監査 §1.9-9）。routing 実装時に混同しないこと
- **思想チャンクを登場人物の台詞へそのまま注入しない**（§10.2）ことを生成側の制約に加える

---

## 11. L2 / L3 抽出フロー

```
Pass1 決定的metadata（ルールベース: 作品ID/タイトル/版/表記/公開状態/初出/底本/provider/genre候補/role候補）
  ↓
Pass2 LLM分類（speaker_role/claim_type/assertion_status/thought_eligibility/
      creative_eligibility/confidence/classification_reason）
  ↓
Pass3 機械的整合性検査（§9 の5例。例: speaker_role=character なのに genre=lecture 等）
  ↓
Pass4 人手レビュー（confidence低/作者と人物の区別が曖昧/皮肉/引用/仮定例/逸話/
      規則候補/Bridge Rule候補/反対証拠あり/時期で主張が変わる）
```

- **L2思想カード**: `author_direct` かつ `thought_eligibility=candidate` のチャンクのみから作る
- **L2創作カード**: `creative_grammar` 由来を `evidence_type=author_creative_theory`、
  小説本文由来を `demonstrated_in_fiction` として区別して保存
- **LLM分類だけで `approved` にしない**（§9 Pass4）。既存の蒸留機構
  （`gen_cards.py`: MIN_EVIDENCE_CHUNKS=2・既存カードskip・draft生成）の規律をそのまま使う
- **反対証拠 `counter_evidence_links`** は現行 `thought_evidence_links` に無い概念 → 追加が必要

---

## 12. approval フロー

既存の承認規律をそのまま使う（新規発明しない）:

- 状態: draft(=candidate) → reviewing → approved / rejected / deprecated
- **approved 以外を assist 生成に使わない**（既存の不変条件。実装済み・テスト済み）
- **Bridge Rule は自動承認しない**（§12.2）
- 承認時に evidence の実在（chunk_id が本文範囲内か）を検証する（§9 Pass3・§14.6）

---

## 13. migration方針

- **additive のみ**。既存テーブルへは nullable 列の追加のみで、既存データの意味を変えない
- RPCは `create or replace` + **default付き新パラメータ**（既存呼び出しは無変更で動く）
- rollback: 追加列の drop と新規テーブルの drop で復帰可能
- 適用前後に既存テスト（worker 107件 / frontend 43件）+ `supabase db reset` を通す
- **原典全文を migration SQL に埋め込まない**（§12）
- ⚠️ `source_chunks` は既存の思想モードRAGが参照する。列追加は安全だが、
  **`chunker_version` を分ける**ことで既存チャンクの再生成を避ける（§7）

---

## 14. C-T0〜C-T8 の詳細タスク

| # | タスク | 依存 | 状態 |
|---|---|---|---|
| C-T0 | リポジトリ監査 | — | **本書で完了** |
| C-T1 | コーパス仕様正本（source model / canonical work / metadata定義 / tag辞書 / Index / routing / approval / ER図 / sequence） | C-T0承認 | 未 |
| C-T2 | 青空文庫 Manifest Importer（CSV+HTML → canonical_work/edition/manifest） | C-T1 | 未 |
| C-T3 | Parser・Normalizer（encoding/ruby/gaiji/notes/header/footer/chapter/paragraph/dialogue/raw保存/hash） | C-T2 | 未 |
| C-T4 | Document/Chunk Tagger（Pass1〜4 + review queue + tag versioning） | C-T3 | 未 |
| C-T5 | Phase A コーパス投入（core_thought 7 + creative_grammar 5 + 夢十夜、Index作成、retrieval test） | C-T4 | 未 |
| C-T6 | L2/L3候補生成（思想カード/創作カード/判断規則/Bridge Rule/evidence/counter evidence/承認UI） | C-T5 | 未 |
| C-T7 | Router・Trace（thought/character/creative query、source role filter、inference trace、abstention、Output Guard連携） | C-T6 | 未 |
| C-T8 | テスト・ドキュメント・snapshot | C-T7 | 未 |

**創作モード側との関係**: 既存 T4c まで（生成パイプライン）は完成済み。創作モードの T6
（夢十夜 profile 投入）は、本指示書の **C-T5 に吸収**される。C-T5 完了時点で、
既に動いている生成パイプラインに実データを流せる。

---

## 15. テスト計画

指示書 §14 の5分類をそのまま採用する。既存のテスト方針（LLMは注入で差し替え、DBはローカル
Supabase実DB、接続不可なら理由付きskip）を踏襲する。

- **Parser Unit**（§14.1・11項目）: ヘッダ除去/奥付抽出/SJIS→UTF-8/ルビ/外字/傍点/章見出し/
  会話文/語り手文/奥付の非embedding/raw保持
- **Dedup**（§14.2）: 三四郎・それから・門・**吾輩は猫である**の版違い統合、
  通常検索で同内容を重複表示しない、版指定時は個別取得できる
  - ⚠️ 吾輩は猫であるは**タイトル文字列が一致しない**ケース（§5.1）。必ずテストに入れる
- **Role Classification**（§14.3・10項目）: 講演の作者直接発言/講演中の他者引用/小説の語り手/
  小説の登場人物/書簡/インタビュー回答/序文/仮定例/皮肉/引用内引用
- **Retrieval**（§14.4・4ケース）: 近代化質問 / 代助質問 / 生成AI質問（留保） / 第十一夜生成
- **Approval**（§14.5）/ **Data Quality**（§14.6・12指標）

---

## 16. 発注者判断（2026-07-27 確定）

1. ✅ **承認状態の語は `draft` を維持**（発注者確認済み）。指示書 §11.3 の `candidate` は
   `draft` と読み替える。既存 `thought_cards` / `creative_cards` と用語が統一され、
   check制約の変更も不要になる。読み替え表は C-T1 の tag辞書に明記する
2. ✅ **今回は青空文庫のみ**（発注者確認済み）。NDL資料（『文学論』『文学評論』全編）は
   スコープ外とし、C-T5 は青空文庫だけで完結させる。ただし将来の追加に備え、
   `sources.source_provider`（aozora / ndl / manual_upload / licensed_source）は
   最初から持たせ、provider 切替が additive で済む形にしておく

### 残る確認事項（実装を止めるものではない）

3. **Phase C（小説10作品）の投入時期**: C-T5（Phase A 13資料）の後に別フェーズとする前提で進める
4. **`chunker_version` の分離**（§7）: `aozora_v1` として既存 `v1` と別系統にする前提で進める
5. **推理小説用の構想**（§0）: 「別資料として管理し、今回のschema/Indexへ先回りして混ぜない」
   を厳守する。関連する要望が出ても本実装には入れない

## 17. 実コードから確認できなかった事項

1. **NDL のデジタル資料の実際の取得可否**（§16-2 と同件）。Web上の書誌情報は存在するが、
   全文テキストとして機械取得できるかは未検証
2. **青空文庫の注記記法の網羅性**: 夢十夜（1作品）では確認できるが、113作品全体で
   どの注記パターンが出現するかは実データを解析するまで不明。C-T3 で全作品の注記を
   機械的に洗い出してから網羅範囲を確定する
3. **旧字旧仮名版のパーサ挙動**: 現行 parser は新字新仮名しか想定していない。
   14件の旧字旧仮名版で外字・異体字がどれだけ出るかは未検証
4. **本番Supabaseの容量**: 113作品全文 + embedding の規模見積りは、実際の chunk 数が
   出るまで不明。無料枠での運用可否は C-T5 の実測後に判断

## 18. 今回実装しない事項

指示書 §0 の対象外に加え、本設計でも以下は行わない。

- 『夢十夜』第十一夜の**本生成**（※パイプライン自体は実装済み。ここでは**コーパスを作るだけ**）
- 『明暗』の続編生成 / 長編状態管理 / 推理小説生成 / Mystery Truth Graph
- fine-tuning / 継続事前学習 / 作家専用モデルの訓練
- NDL資料の取り込み（§16-2 の確認後に別途判断）
- Phase C（小説10作品）の全投入（C-T5 は Phase A の13資料に限定）
- 物理的な Vector Collection 分割（§9 のとおり論理分離で代替）

---

## 次のアクション

**§17 の指示に従い、本設計の承認をもって C-T1（コーパス仕様正本）へ進む。**
承認までは production migration・大量取り込みを開始しない。

特に §16 の 1（承認状態の語）と 2（NDL資料の扱い）は、C-T1 の内容が変わるため
先に判断をいただきたい。
