# C-T1: 漱石コーパス仕様 正本

> 上位指示: 「夏目漱石コーパス構築・思想／創作分離 実装指示書 v0.1」（[原本](received/CORPUS_SPEC_v0.1_received.pdf)）。
> 前段: [CORPUS_T0_AUDIT_AND_DESIGN.md](CORPUS_T0_AUDIT_AND_DESIGN.md)（C-T0 監査 + 設計提案、§17の18項目）。
> 本書は指示書 §16 の **T1「コーパス仕様正本」**の成果物であり、C-T2 以降の実装が従う正本。
>
> **発注者確定事項（2026-07-27）**: 承認状態の語は `draft` を維持 / 今回は青空文庫のみ（NDLはスコープ外）。
>
> ⚠️ フェーズ名は既存の創作モード T0〜T8 と衝突するため **C-T0〜C-T8** と表記する。

## 0. スコープ

| 対象 | 内容 |
|---|---|
| **含む** | 青空文庫 漱石113件のマニフェスト化 / canonical work・edition の正規化 / 本文取得・正規化 / 文書・チャンクのタグ付け / corpus_role による論理Index分離 / L2・L3候補の抽出導線 / evidence・provenance の保存 / 検索ルーティング / データ品質検査 |
| **含まない** | NDL資料（『文学論』『文学評論』）/ Phase C 小説10作品の全投入（C-T5 は Phase A 13資料に限定）/ 第十一夜の本生成（パイプラインは実装済み）/ 明暗続編 / 長編状態管理 / 推理小説 / fine-tuning |

---

## 1. source model

### 1.1 3層構造

```
canonical_work（作品）  1 ── n  work_edition（版）  1 ── 0..1  sources（取り込んだ文書）
                                                              1 ── n  source_chunks
```

- **canonical_work**: 版をまたぐ作品の同一性。実データでは 113行 → **106作品**（C-T2b で同定ロジックを実装して実測。素朴なタイトル一致では107だが、「吾輩は猫である」の統合により106になる）
- **work_edition**: 青空文庫の1エントリ（作品ID単位）。同一作品の新字新仮名版と旧字旧仮名版は別 edition
- **sources**: 実際に本文を取得・正規化した文書。`work_status=in_progress` の版は sources を作らない

### 1.2 canonical work の同定規則（実データに基づく）

⚠️ **作品名の完全一致で束ねてはいけない**。実データに次の反例がある。

| 作品ID | 作品名 | 作品名読み | 文字遣い | テキスト |
|---|---|---|---|---|
| 000789 | 吾輩**は**猫**である** | わがはいはねこである | 新字新仮名 | あり |
| 000790 | 吾輩**ハ**猫**デアル** | **わがはいハねこデアル** | 旧字旧仮名 | **なし** |

判定は次の順で行い、**どの段でも決まらなければ人手確認キューへ**（自動統合しない）。

| 段 | 判定 | 備考 |
|---|---|---|
| 1 | **`作品名読み` の一致** | 第一候補。ただし上記の反例は**読みもカタカナ混じりで一致しない** |
| 2 | 正規化タイトルの一致 | NFKC + 旧字→新字 + カタカナ→ひらがな。**実データの吾輩は猫であるはここで統合される** |
| 3 | 正規化本文の hash・段落対応 | 指示書 §8.7。本文取得後にのみ可能 |
| 4 | 人手確認 | `canonical_work_review_queue` へ |

`canonical_works.title_variants text[]` に全表記を保持し、検索はどの表記でも当たるようにする。

⚠️ **段2で統合した場合も、読みが割れていれば `canonical_work_review_queue` へ回す**。
統合はするが人手確認を必ず通す(自動統合しっぱなしにしない)。実データでは106作品中**1件**が該当。

### 1.3 primary retrieval edition

- 原則 **新字新仮名** を `is_primary_retrieval_edition=true`
- 実測(C-T2b): 106作品すべてに既定版が立った。内訳は 新字新仮名84 / 新字旧仮名13 / 旧字旧仮名9
  （新字新仮名版が無い作品では、次順の表記から本文のある版を選ぶ）
- 新字旧仮名15件・旧字旧仮名14件は**削除せず保存**し、既定の検索からは除外（版比較・校訂確認用）
- 1 canonical_work につき primary は**最大1件**（部分一意制約で担保）
- 000790（吾輩ハ猫デアル）のように**テキストファイルが無い版**は edition として登録し、
  `text_file_url = null` / `sources` を作らない

### 1.4 work_status

- CSV（`list_person_all_extended_utf8`）には**公開作品しか載らない**。作業中8件は
  作家別HTMLページからのみ取得できる
- `work_status=in_progress` は `aozora_manifest_entries` に記録するのみ。
  **本文取得・Index登録・L2/L3候補生成を行わない**（指示書 §2.1）
- 保存するのは 作品ID / 作品名 / 表記種別 / 作業中であること / 一覧取得日時 のみ

---

## 2. schema 定義（additive migration）

migration: `supabase/migrations/2026072800000X_corpus_layer.sql`（C-T2 で作成）

### 2.1 新規テーブル

```sql
-- 作品(版をまたぐ同一性)
create table public.canonical_works (
  canonical_work_id text primary key,           -- 例 cw_yumejuya
  person_id text not null references public.personas(person_id),
  canonical_title text not null,
  canonical_title_reading text,                 -- 同定の第一候補(§1.2)
  title_variants text[] not null default '{}',  -- 「吾輩は猫である」「吾輩ハ猫デアル」
  first_publication text,                       -- 初出(実データ充足率48%のため nullable)
  ndc text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 版(青空文庫の1エントリ)
create table public.work_editions (
  edition_id text primary key,                  -- 青空文庫の作品ID(例 000799)
  canonical_work_id text not null references public.canonical_works(canonical_work_id),
  aozora_work_id text not null,
  orthography text not null,                    -- 新字新仮名 / 新字旧仮名 / 旧字旧仮名
  work_status text not null default 'published'
    check (work_status in ('published', 'in_progress')),
  is_primary_retrieval_edition boolean not null default false,
  card_url text,
  text_file_url text,                           -- 無い版がある(000790)。nullable
  text_encoding text,                           -- CSV「テキストファイル符号化方式」
  text_charset text,                            -- CSV「テキストファイル文字集合」
  -- 底本・由来情報(指示書§2.4)。実データの充足率が項目ごとに違うため jsonb でまとめる
  bottom_text jsonb not null default '{}',
  input_by text, proofread_by text,             -- 校正者は1件欠落あり。nullable
  aozora_published_at date, aozora_updated_at date,
  copyright_status text not null default 'public_domain',
  license_note text,
  retrieved_at timestamptz, content_sha256 text, parser_version text,
  duplicate_of text references public.work_editions(edition_id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- 1作品につきprimaryは最大1件
create unique index work_editions_primary_unique
  on public.work_editions (canonical_work_id) where (is_primary_retrieval_edition);
create index work_editions_work_idx on public.work_editions (canonical_work_id, work_status);

-- 作業中を含む一覧の記録(本文は取らない。指示書§2.1)
create table public.aozora_manifest_entries (
  entry_id text primary key,
  person_id text not null references public.personas(person_id),
  aozora_work_id text not null,
  title text not null,
  orthography text,
  work_status text not null check (work_status in ('published', 'in_progress')),
  listed_at timestamptz not null default now(),
  source_page_url text
);

-- canonical work の自動同定が割れた場合の人手確認キュー(§1.2 段4)
create table public.canonical_work_review_queue (
  queue_id uuid primary key default gen_random_uuid(),
  person_id text not null references public.personas(person_id),
  aozora_work_ids text[] not null,
  reason text not null,
  status text not null default 'open' check (status in ('open', 'resolved', 'dismissed')),
  resolved_canonical_work_id text,
  created_at timestamptz not null default now()
);
```

### 2.2 既存テーブルへの追加（すべて nullable / default 付き = 既存挙動を変えない）

```sql
alter table public.sources
  add column edition_id text references public.work_editions(edition_id),
  add column corpus_role text check (corpus_role in (
    'core_thought','supporting_thought','creative_grammar','character_judgment',
    'narrative_reference','style_reference','biographical_context',
    'validation_only','excluded')),
  add column document_genre text check (document_genre in (
    'lecture','essay','criticism','literary_theory','preface','afterword','letter',
    'interview','memoir','travelogue','novel','short_story','sketch',
    'advertisement','announcement','other')),
  add column authority_level text check (authority_level in (
    'author_direct','author_contextual','fictional_indirect',
    'third_party','editorial','unknown')),
  add column source_provider text not null default 'manual_upload' check (source_provider in (
    'aozora','ndl','manual_upload','licensed_source')),
  add column corpus_metadata jsonb not null default '{}';
create index sources_corpus_role_idx on public.sources (person_id, corpus_role);

alter table public.source_chunks
  add column speaker_role text check (speaker_role in (
    'author_direct','narrator','character','quoted_person','interviewer','editor','unknown')),
  add column character_id text,
  add column addressee text,
  add column claim_type text check (claim_type in (
    'normative_claim','descriptive_observation','conceptual_distinction','priority_claim',
    'prohibition','exception','autobiographical_report','historical_report',
    'hypothetical_example','quotation','literary_analysis','fictional_statement',
    'meta_commentary')),
  add column assertion_status text check (assertion_status in (
    'asserted','attributed','hypothetical','questioned','ironic','ambiguous','rejected_by_author')),
  add column thought_eligibility text check (thought_eligibility in ('candidate','support','excluded')),
  add column creative_eligibility text check (creative_eligibility in ('candidate','support','excluded')),
  add column is_quotation boolean not null default false,
  add column is_hypothetical boolean not null default false,
  add column is_ironic boolean not null default false,
  add column tag_confidence numeric,
  add column classification_reason text,
  add column tag_review_status text not null default 'unreviewed'
    check (tag_review_status in ('unreviewed','auto_ok','needs_review','reviewed','corrected')),
  add column tag_reviewed_by text, add column tag_reviewed_at timestamptz,
  add column tagger_version text,
  add column chunk_metadata jsonb not null default '{}';  -- ruby / gaiji / 注記 / 原情報
create index source_chunks_role_idx on public.source_chunks (person_id, speaker_role, thought_eligibility);
```

⚠️ `thought_eligibility` / `creative_eligibility` の `candidate` は**チャンクの適格性**を表す語で、
カードの承認状態（`draft`）とは別概念。混同しないこと。

### 2.3 creative_cards への追加（C-T0 §3 の衝突1・2）

```sql
alter table public.creative_cards
  add column evidence_type text check (evidence_type in (
    'author_creative_theory','demonstrated_in_fiction','critic_interpretation'));
-- card_type に 'criticism' を追加(check制約の入替。既存データは無いため安全)
alter table public.creative_cards drop constraint creative_cards_card_type_check;
alter table public.creative_cards add constraint creative_cards_card_type_check
  check (card_type in ('style','narrative','motif','character','perspective','ending',
                       'criticism','prohibition','setting','dialogue','rhythm','theme',
                       'historical_language'));
```

### 2.4 反対証拠（指示書 §11.1 `counter_evidence_links`）

既存 `thought_evidence_links` は「支持する根拠」のみを扱う。反対証拠を別に持つ。

```sql
alter table public.thought_evidence_links
  add column link_polarity text not null default 'support'
    check (link_polarity in ('support', 'counter'));
```

既存行はすべて `support` になるため意味変更なし（additive）。

---

## 3. metadata定義

### 3.1 文書単位（指示書 §7.1）

指示書の約30項目を、**実カラム**（フィルタ・結合に使う）と **jsonb**（保存のみ）に振り分ける。

| 指示書の項目 | 格納先 |
|---|---|
| document_id / author_id / author_name | `sources.source_id` / `person_id`（既存） |
| canonical_work_id / canonical_title | `work_editions.canonical_work_id` 経由 |
| aozora_work_id / source_url / card_url / download_url | `work_editions` の実カラム |
| **corpus_role / document_genre / authority_level / source_provider** | `sources` の**実カラム**（索引あり） |
| orthography / edition_variant / work_status / is_primary_retrieval_edition / duplicate_of | `work_editions` の実カラム |
| bottom_text / parent_edition / input_by / proofread_by / copyright_status / license_note | `work_editions`（bottom_text は jsonb） |
| publication_date / first_publication / historical_period / temporal_phase / language | `canonical_works` + `sources.corpus_metadata` |
| retrieved_at / content_sha256 / parser_version | `work_editions` の実カラム（再現性のため） |

### 3.2 チャンク単位（指示書 §7.4）

| 指示書の項目 | 格納先 |
|---|---|
| chunk_id / document_id / chapter / char_start / char_end | 既存カラム |
| raw_text / normalized_text | 既存 `text` = normalized。raw は `chunk_metadata.raw_text` |
| **speaker_role / character_id / addressee / claim_type / assertion_status** | 実カラム（新規） |
| **thought_eligibility / creative_eligibility** | 実カラム（新規・索引あり） |
| is_quotation / is_hypothetical / is_ironic / confidence / classification_reason | 実カラム（新規） |
| review_status / reviewed_by / reviewed_at | 実カラム（新規、`tag_` 接頭辞） |
| quotation_source / ruby / gaiji / 注記 | `chunk_metadata` jsonb |
| embedding_version | 既存 `chunker_version` + 新規 `tagger_version` |

⚠️ 既存 `source_chunks.speaker`（話者ラベルの正規化結果）と新規 `speaker_role`（役割の分類）は
**別物**。前者は clean.py が付ける文字列、後者は §4 のタグ辞書に従う分類値。

---

## 4. tag辞書

| 分類 | 値 | 決め方 |
|---|---|---|
| **corpus_role**（9） | core_thought / supporting_thought / creative_grammar / character_judgment / narrative_reference / style_reference / biographical_context / validation_only / excluded | Pass1（ルール）+ Pass4（人手） |
| **document_genre**（16） | lecture / essay / criticism / literary_theory / preface / afterword / letter / interview / memoir / travelogue / novel / short_story / sketch / advertisement / announcement / other | ⚠️ **NDCだけで決めない**。実データでも NDC 914（随筆）が47件と最多で講演・評論が同居。タイトル・初出・本文冒頭・底本情報を併用 |
| **authority_level**（6） | author_direct / author_contextual / fictional_indirect / third_party / editorial / unknown | 文書単位。小説は fictional_indirect |
| **speaker_role**（7） | author_direct / narrator / character / quoted_person / interviewer / editor / unknown | チャンク単位。Pass2（LLM） |
| **claim_type**（13） | 指示書 §7.6 のまま | Pass2 |
| **assertion_status**（7） | 指示書 §7.7 のまま | Pass2 |
| **承認状態**（5） | **draft** / reviewing / approved / rejected / deprecated | ✅ 指示書 §11.3 の `candidate` は **`draft` と読み替える**（2026-07-27 確定） |
| 注記種別（7） | formatting_note / gaiji_note / editorial_note / original_text_note / ruby_note / emphasis_note / page_note | Parser（C-T3） |

### 4.1 corpus_role の初期割当（Phase A 13資料）

| corpus_role | 作品（青空文庫ID） |
|---|---|
| `core_thought` | 私の個人主義(772) / 現代日本の開化(759) / 中味と形式(788) / 模倣と独立(1747) / 道楽と職業(757) / 文芸の哲学的基礎(755) / 文芸と道徳(756) |
| `creative_grammar` | 創作家の態度(1102) / 写生文(796) / 作物の批評(793) / 高浜虚子著『鶏頭』序(2667) / 教育と文芸(778) |
| `narrative_reference` + `style_reference` | 夢十夜(799) |

13件すべて**公開・取得可能・新字新仮名**であることを CSV で個別確認済み（C-T0 §1.1）。

---

## 5. Index設計

**共通テーブル + `corpus_role` による厳格な metadata filter**（物理分割はしない）。
理由は C-T0 §9 のとおり（現行は単一 Postgres + pgvector で別 Collection の概念がない。
指示書 §10 も現行インフラを監査して決めてよいとしている）。**論理的な分離は要件どおり厳格に守る**。

論理Index（8種）は**フィルタのプリセット**としてコード側に定義する。

| 論理Index | フィルタ条件 |
|---|---|
| `author_thought_core_index` | `corpus_role='core_thought'` AND `speaker_role='author_direct'` AND `thought_eligibility!='excluded'` |
| `author_thought_support_index` | `corpus_role='supporting_thought'` AND `speaker_role='author_direct'` |
| `creative_grammar_index` | `corpus_role='creative_grammar'` |
| `character_judgment_index` | `corpus_role='character_judgment'` AND `speaker_role='character'`（+ `character_id` 絞り） |
| `narrative_reference_index` | `corpus_role='narrative_reference'`（+ canonical_work 絞り） |
| `style_reference_index` | `corpus_role='style_reference'` |
| `biographical_context_index` | `corpus_role='biographical_context'` |
| `validation_only_index` | `corpus_role='validation_only'`（カード生成の入力にしない。事後検証専用） |

**既定の検索は `is_primary_retrieval_edition=true` の版に限定**する（版違いで同じ段落を重複して返さない）。

### 5.1 既存RPCの拡張方針

```sql
create or replace function public.match_source_chunks_all(
  query_embedding extensions.vector(1536),
  target_person_id text default 'natsume_soseki',
  match_count int default 20,
  target_corpus_roles text[] default null,      -- 追加(null = 従来どおり全件)
  target_speaker_roles text[] default null,     -- 追加
  primary_edition_only boolean default false    -- 追加
) ...
```

**default 付きの追加パラメータ**にすることで、既存の呼び出し（思想モードのRAG）は
**無変更で従来どおり動く**。これが「既存Thinkerを壊さない」（指示書 §1.1）の担保。

---

## 6. routing設計

| 質問種別 | 検索順 | 禁止事項 |
|---|---|---|
| **思想** | core_thought → supporting_thought → creative_grammar（必要時のみ）→ fictional_indirect（比較・補助として**明示**） | 小説中の人物発言を主根拠にしない |
| **創作** | creative_grammar → narrative_reference（対象作品）→ style_reference → core_thought（**Bridge Rule を介する場合のみ**） | 思想チャンクを登場人物の台詞へそのまま注入しない |
| **人物** | character_judgment（対象人物）→ narrative_reference（対象作品）→ core_thought（**比較対象としてのみ**） | 人物の発言を作者思想として提示しない |

- 既存 `classify.ts` の `QueryKind` に**人物質問**の判定を追加する
- ⚠️ 既存 `QueryKind` の `"creative"` は「創作的な質問」の意味で、創作モードとは**別概念**
  （C-T0 §1.9-9）。routing 実装時に混同しない
- 小説を検索した場合、回答中で「作者本人の直接発言ではなく、作品内人物の発言または
  物語上の表現である」と区別できるように表示する（指示書 §10.1）

---

## 7. 生成sequence（コーパス投入）

```
[C-T2 Manifest]
  CSV取得 → 人物ID 000148 の113行抽出
  → canonical work 同定(§1.2: 読み → 正規化タイトル → hash → 人手キュー)
  → canonical_works / work_editions へ upsert
  → 作家別HTMLから作業中8件 → aozora_manifest_entries(本文は取らない)

[C-T3 Parser]  ※ work_status=published かつ text_file_url あり のみ
  GitHubミラーから zip 取得 → raw_bytes 保存 → CP932→UTF-8 → raw_text
  → ヘッダ/フッタ分離(底本情報は metadata へ) → ルビ・外字・注記の分離
  → normalized_text / display_text → content_sha256 記録
  → sources へ登録(corpus_role/document_genre/authority_level は Pass1 の候補値)
  → chunk 分割(章→節→段落→話者交代→意味段落→token上限、chunker_version='aozora_v1')

[C-T4 Tagger]
  Pass1 決定的metadata(ルール) → Pass2 LLM分類 → Pass3 機械的整合性検査
  → Pass4 人手レビューキュー(confidence低/皮肉/引用/仮定例/作者と人物が曖昧 等)

[C-T5 投入]  Phase A 13資料 → embed → 論理Index確認 → retrieval test
[C-T6 候補生成]  L2思想カード / L2創作カード / L3判断規則 / Bridge Rule 候補 → 人手承認
[C-T7 routing]  質問種別判定 → corpus_role フィルタ → inference trace → abstention
```

---

## 8. Guard仕様（コーパス層）

生成側の Creative Output Guard（実装済み・[T1_CREATIVE_MODE_DESIGN.md](T1_CREATIVE_MODE_DESIGN.md) §5）
とは別に、**コーパス投入時の品質ゲート**を設ける。

| 検査 | 閾値超過時の扱い |
|---|---|
| 文字化け率（CP932変換の失敗率） | 閾値超過なら **Index登録しない**（指示書 §8.2） |
| 注記の未知パターン率 | `needs_manual_review=true` |
| orphan chunk（sources に紐づかない） | 投入を中断 |
| evidence span が本文範囲外 | 該当カードを approved にしない |
| core_thought 内の fiction 混入率 | データ品質レポートに出す（指示書 §14.6） |
| speaker_role 未分類率 | 同上 |

閾値は**設定値として持ち、コードへ直書きしない**（創作モード Guard と同じ規律）。

---

## 9. trace仕様（§13 推論表示）

現代の未知質問に答える場合、次を区別して trace へ残す（指示書 §13）。

```
direct_source / retrieved_claim / activated_rule / rejected_rule /
system_inference / confidence / abstention_reason
```

- 既存 `answer_traces` に**追加列**として持たせる（別テーブルにしない。回答1件と1:1のため）
- **原典とAI外挿を分ける**。直接資料が無い現代質問では**留保を明示**する
- 目的は、滑らかなペルソナ回答で起こりがちな次の混同を防ぐこと:
  作者と登場人物 / 原典とAI推論 / 批判的チャンクへの検索偏り /
  文体の類似による思想的一致の代替 / 不確実な問いへの断定

---

## 10. migration方針

- **additive のみ**。既存テーブルへは nullable 列 or default 付き列の追加のみ
- RPCは `create or replace` + **default 付き新パラメータ**（既存呼び出しは無変更）
- `creative_cards.card_type` の check 入替は、**既存データが無い**ことを確認してから実行
- rollback: 追加列の drop + 新規テーブル4本の drop
- 適用前後に既存テスト（worker 107件 / frontend 43件）+ `supabase db reset` を通す
- **原典全文を migration SQL に埋め込まない**
- `chunker_version='aozora_v1'` として既存 `v1` と別系統にし、既存チャンクを再生成しない

---

## 11. テスト計画

指示書 §14 の5分類。既存方針（LLMは注入で差し替え、DBはローカルSupabase実DB、
接続不可なら理由付きskip）を踏襲する。

| 分類 | 必須ケース |
|---|---|
| **Parser Unit**（§14.1） | ヘッダ除去 / 奥付抽出 / SJIS→UTF-8 / ルビ / 外字注記 / 傍点・強調 / 章見出し / 会話文 / 語り手文 / 奥付の非embedding / raw保持 |
| **Dedup**（§14.2） | 三四郎・それから・門の版違い統合 / **吾輩は猫である（タイトル文字列が不一致のケース）** / 通常検索で同内容を重複表示しない / 版指定時は個別取得できる |
| **Role Classification**（§14.3） | 講演の作者直接発言 / 講演中の他者引用 / 小説の語り手 / 小説の登場人物 / 書簡 / インタビュー回答 / 序文 / 仮定例 / 皮肉 / 引用内引用 |
| **Retrieval**（§14.4） | 近代化質問（『現代日本の開化』優先・人物発言を主根拠にしない）/ 代助質問（character_judgment を取得し作者思想と区別）/ 生成AI質問（直接原典が無いことを表示・留保）/ 第十一夜生成（creative_grammar と夢十夜本文を取得） |
| **Approval**（§14.5） | draft はassistへ入らない / rejected は検索候補から除外 / approved のみ本番利用 / version変更時に過去traceが再現可能 / evidence切れのカードを検出 |
| **Data Quality**（§14.6） | 全公開作品マニフェスト取得 / 作業中は本文未取得 / orphan chunkなし / source URL欠落なし / content hash保存 / parser version保存 / 文字化け率 / 重複率 / speaker_role未分類率 / core thought内のfiction混入率 / evidence span整合性 |

---

## 12. タスク分割

| # | タスク | 主な成果物 | 依存 |
|---|---|---|---|
| C-T2a | migration（新規4テーブル + 既存への additive 列） | `2026072800000X_corpus_layer.sql` | C-T1 |
| C-T2b | Manifest Importer（CSV → canonical work同定 → editions） | `worker/src/aozora/manifest.py` | C-T2a |
| C-T2c | 作業中8件の記録（HTMLページ） | 同上 | C-T2b |
| C-T3a | 取得（GitHubミラー・zip・sha256） | `worker/src/aozora/fetch.py` | C-T2b |
| C-T3b | Parser/Normalizer（encoding/ruby/gaiji/notes/header/footer） | `worker/src/aozora/parse.py` | C-T3a |
| C-T3c | チャンク分割（`aozora_v1`） | `worker/src/aozora/chunk.py` | C-T3b |
| C-T4a | Pass1 決定的タグ + Pass3 整合性検査 | `worker/src/aozora/tag.py` | C-T3c |
| C-T4b | Pass2 LLM分類 + Pass4 レビューキュー | 同上 | C-T4a |
| C-T5 | Phase A 13資料の投入 + 論理Index + retrieval test | 実データ | C-T4b |
| C-T6 | L2/L3候補生成（思想/創作/規則/Bridge Rule） | | C-T5 |
| C-T7 | Router・Trace（人物質問判定・corpus_roleフィルタ・留保） | | C-T6 |
| C-T8 | テスト・ドキュメント・snapshot | | C-T7 |

**創作モードとの関係**: 生成パイプラインは T4c まで完成済み。創作モードの T6（夢十夜 profile 投入）は
**C-T5 に吸収**される。C-T5 完了時点で、実データを既存パイプラインに流せる。

---

## 13. 受入条件（指示書 §15 の20項目）

| # | 条件 | 対応するタスク |
|---|---|---|
| 1 | 公開作品一覧をマニフェスト化できる | C-T2b |
| 2 | 作業中作品を production corpus へ入れない | C-T2c |
| 3 | 版違いを canonical work へ統合できる | C-T2b（§1.2） |
| 4 | raw / normalized / display text を保存できる | C-T3b |
| 5 | 青空文庫の由来情報を保持できる | C-T2b（bottom_text） |
| 6 | 文書単位の genre / corpus role を付けられる | C-T4a |
| 7 | チャンク単位の speaker role を付けられる | C-T4b |
| 8 | 作者・語り手・登場人物・引用人物を区別できる | C-T4b |
| 9 | core thought Index へ小説人物の発言が混入しない | C-T5（§5） |
| 10 | 主要12資料を取り込める | C-T5（実際は13件） |
| 11 | 『文学論』『文学評論』を別providerとして管理できる | **スコープ外**。`source_provider` 列で将来対応可能な形にする |
| 12 | approved カード・規則のみ assist で使用する | 実装済み（創作モード） |
| 13 | L2/L3 の全項目に evidence と provenance がある | C-T6 |
| 14 | 原典とAI外挿を trace 上で区別できる | C-T7（§9） |
| 15 | 思想検索と創作検索のルーティングを分離できる | C-T7（§6） |
| 16 | 既存Thinkerのテストがすべて通る | 全タスクで維持 |
| 17 | migration が additive で rollback 可能 | C-T2a（§10） |
| 18 | corpus snapshot を再現できる | C-T8 |
| 19 | parser / embedding / prompt の version を保存できる | C-T3b・C-T4b |
| 20 | データ品質レポートを出力できる | C-T8（§8・§11） |

⚠️ **#11 は今回スコープ外**（発注者確定: 今回は青空文庫のみ）。`source_provider` を最初から
持たせることで、将来 NDL を追加する際に additive で済む形にしてある。

---

## 14. 最終原則（指示書 §18 の遵守確認）

本仕様が指示書 §18 の各原則をどう満たすかの対応表。

| 原則 | 本仕様での担保 |
|---|---|
| 一覧件数を独立作品数とみなさない | §1.1（113行 → 106作品・実測）/ §1.2 の同定規則 |
| 作品と版を分ける | §1.1 の3層構造 |
| NDCだけで文書種別を決めない | §4（実データで NDC 914 に講演・評論が同居） |
| 文書単位でなくチャンク単位でタグを付ける | §2.2 / §3.2 |
| 作者・語り手・人物・引用者を分ける | `speaker_role`（7値）/ §6 routing |
| 小説人物の発言を作者思想へ自動昇格させない | §5 論理Index / §6 禁止事項 / §11 Retrieval test |
| 原典とAIの外挿を分ける | §9 trace |
| 思想カードと創作カードを分ける | 既存 `thought_cards` / `creative_cards`（別テーブル） |
| 判断規則と創作規則を分ける | `judgment_rules` / `creative_rules`（後者は v0.2 で新設） |
| 両者の連携は Bridge Rule として明示する | `rule_scope=bridge_rule`（C-T6） |
| 未承認カード・規則を assist で使わない | 実装済み・テスト済み |
| 出典・底本・版・取得日・hash を保持する | §2.1 `work_editions` |
| 発火だけでなく棄却理由も記録する | §9 `rejected_rule` |
| 直接資料がない現代質問では留保する | §9 `abstention_reason` |
| 文体の似ている回答を思想的一致とみなさない | §5 `style_reference` を思想の根拠に使わない |
| 推理小説用の構造を今回の実装へ混ぜない | §0 スコープ外 |

---

## 次のアクション

**C-T2a（migration 作成）から実装に入る。** 本仕様に対する異論があれば C-T2a 着手前に指摘されたい。
