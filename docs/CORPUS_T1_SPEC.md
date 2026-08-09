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

### 4.1 引用判定の分担（C-T4 の実データ検証で確定）

⚠️ **Pass1（決定的タグ）で「〜と云った」型の言い回しから引用を検出してはいけない。**

実データを調べたところ、漱石の講演・評論に現れる鉤括弧は**大半が作品名・語句の参照**だった。

| 実データの例 | 種別 |
|---|---|
| 私の書いた「坊ちゃん」でもご覧になったのでしょう | 作品名の参照 |
| 「現代日本の開化」と云う題で御話を致します | 作品名の参照 |
| 「眉のような月」と云う叙述 | 語句の参照 |

言い回しで狙うとこれらを引用と誤判定する。そこで Pass1 は
**「引用が段落の 60% 以上を占め、かつ30字以上」のブロック引用のみ**を機械的に拾い、
地の文に埋め込まれた引用の判断は **Pass2（LLM）に委ねる**。

実測: Phase A の講演3資料（『私の個人主義』『現代日本の開化』『創作家の態度』）には
Pass1 が拾うブロック引用は**0件**（引用占有率の最大は 0.51）。漱石は引用を地の文へ
埋め込む書き方をするため、この corpus では引用判定は実質 Pass2 の担当になる。

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
| `character_judgment_index` | `corpus_role IN ('character_judgment','narrative_reference')` AND `speaker_role='character'`（+ `character_id` 絞り）⚠️ **2026-07-27 改訂**。詳細は §5.2 |
| `narrative_reference_index` | `corpus_role='narrative_reference'`（+ canonical_work 絞り） |
| `style_reference_index` | `corpus_role='style_reference'` |
| `biographical_context_index` | `corpus_role='biographical_context'` |
| `validation_only_index` | `corpus_role='validation_only'`（カード生成の入力にしない。事後検証専用） |

**既定の検索は `is_primary_retrieval_edition=true` の版に限定**する（版違いで同じ段落を重複して返さない）。

### 5.2 ⚠️ character_judgment_index の定義を実データに合わせて改めた（2026-07-27）

当初の定義 `corpus_role='character_judgment'` は、**構造上ずっと空になる**。

- `corpus_role` は `sources` の**単一カラム**（文書単位）
- 取り込み（`tag._GENRE_TO_ROLE`）は novel / short_story / sketch を
  `narrative_reference` に割り当てる
- したがって `corpus_role='character_judgment'` が付く文書は生まれない

実測（Phase A）: 仕様どおりの条件で **0件** / 実際に作中人物の発言があるチャンクは
`narrative_reference` + `character` に **34件**。

**Phase C で長編10作品を入れても解消しない**種類の欠落だった。1万チャンクを
取り込んでから気づくと、役割の付け直しが同規模になる。

作中人物の判断は小説の中にあり、**誰の発言かはチャンクの `speaker_role` が持っている**。
文書の役割（narrative_reference）と発話者の役割（character）は別の軸なので、
Index の条件も両方を見る形に改めた。`character_judgment` を残してあるのは、
人手で明示的に割り当てた文書も拾うため。

⚠️ 同種の注意: 仕様§4.1 は夢十夜に `narrative_reference` + `style_reference` の
2つを割り当てているが、`corpus_role` は単一値なので**両方は持てない**。
実装は `narrative_reference` のみ。`style_reference_index` も現状は空。

### 5.3 character_id（誰の発言か）— 辞書が語彙・Pass2 が割当（2026-07-28）

Phase C で「代助はなぜ働かないのか」に**代助の発言だけ**を根拠として使うための仕組み。

**役割分担**（どちらか一方では成立しない）:

| 担当 | 役割 | 理由 |
|---|---|---|
| **辞書** `worker/src/aozora/characters.json` | 使ってよい `character_id` と表記を定める | LLM の自由生成に任せるとIDが揺れ（daisuke / 長井代助）、質問側の検出と結合できない |
| **Pass2（LLM）** | チャンクの発言者を一覧から選ぶ | 辞書は「誰がいるか」しか知らず「誰が言ったか」を判定できない。話者帰属は文脈読解 |

制約（`tag.merge_pass2` で閉じる）:
- 一覧の外のIDは捨てる（レビュー行きにはしない。捨てた時点で誤帰属は起きない）
- `speaker_role != character` のチャンクには付けない
- **一覧は作品スコープ**で渡す。全作品ぶんを渡すと『三四郎』のチャンクに代助が付く混線が起きる
- 人手の修正（`tag-review --set character_id=...`）も同じ一覧で検証する

辞書はルーティングの人物検出（`routing.detect_character` / frontend `detectCharacter`）と
**同一の出所**。frontend 側の複製は `src/lib/rag/characters.json` で、テストが同期を検証する。

⚠️ 「先生」（こころ）のような一般名詞と衝突する呼称は辞書に入れない。検出は部分一致
なので、無関係な質問を人物質問に誤判定する。

⚠️ 併せて `tagger_version` を v3 に上げた際、**人手レビュー済み（reviewed / corrected）の
チャンクは再分類の対象から外す**ようにした。LLM の再実行が人の判断を黙って覆すと、
レビューという関門の意味が無くなる。

---

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

### 11.1 ⚠️ worker の pytest はローカルのコーパスを消す

`worker/tests/conftest.py` の `clean_corpus` fixture が sources / source_chunks /
creative_* を FK 順に truncate するため、**`uv run pytest` を流すとローカルの取り込み済み
データと承認済みカードが消える**。テストの独立性のために必要な挙動なので変えない。

UI を触る前にテストを流したら、下記で作り直す（`gen-cards` と `embed` は課金あり）:

```bash
uv run python -m src.aozora.cli manifest && \
uv run python -m src.aozora.cli ingest-phase-a && \
uv run python -m src.aozora.cli embed && \
uv run python -m src.aozora.cli create-profile && \
uv run python -m src.aozora.cli gen-cards
```

カードは再生成のたびに `card_id` が変わるので、承認は作り直しのたびに必要になる。

---

## 12. タスク分割

| # | タスク | 主な成果物 | 依存 |
|---|---|---|---|
| C-T2a | **完了** migration（新規4テーブル + 既存への additive 列） | `20260727000001_corpus_layer.sql` | C-T1 |
| C-T2b | **完了** Manifest Importer（106作品 / 113版 / 要確認1件） | `worker/src/aozora/manifest.py` | C-T2a |
| C-T2c | **完了** 作業中8件の記録（HTMLページ） | `worker/src/aozora/person_page.py` | C-T2b |
| C-T3a | **完了** 取得（GitHubミラー・zip・sha256） | `worker/src/aozora/ingest.py` | C-T2b |
| C-T3b | **完了** Parser/Normalizer（3形式・ルビ・注記・奥付） | `worker/src/aozora/parse.py` | C-T3a |
| C-T3c | **完了** チャンク分割（`aozora_v1`・話者交代・意味段落） | `worker/src/aozora/chunk.py` | C-T3b |
| C-T4a | **完了** Pass1 決定的タグ + Pass3 整合性検査 + Pass4 レビュー判定 | `worker/src/aozora/tag.py` | C-T3c |
| C-T4b | **完了 2026-07-27** Pass2 LLM分類 + Pass4 レビューキュー。実測は §12.4 | `worker/src/aozora/tag.py` + `retag.py` | C-T4a |
| C-T5 | **完了 2026-07-27** Phase A 13資料の投入 + 論理Index検証 | 実データ 483チャンク | C-T4a |
| C-T6 | **完了 2026-07-27** L2思想カード / L3判断規則 / Bridge Rule + 代表質問。実測は §12.5 | `worker/src/aozora/gen_thought_cards.py` + `gen_rules.py` | C-T5 |
| C-T7 | **worker側まで完了 2026-07-27** 拡張RPC・論理Index・質問種別ルーティング・trace列。frontend への配線は UI(T5)と同時 | `worker/src/aozora/routing.py` + `20260727000002_corpus_routing.sql` | C-T6 |
| C-T8 | **完了 2026-07-27** snapshot（受入#18）+ データ品質レポート（受入#20）+ Retrieval シナリオテスト。実測は §12.2 | `worker/src/aozora/snapshot.py` | C-T7 |

**創作モードとの関係**: 生成パイプラインは T4c まで完成済み。創作モードの T6（夢十夜 profile 投入）は
**C-T5 に吸収**される。C-T5 完了時点で、実データを既存パイプラインに流せる。

---

## 12.1 C-T5 の実測結果（2026-07-27）

CLI（`worker/src/aozora/cli.py`）だけで、空のDBから以下まで再現できる。

```bash
uv run python -m src.aozora.cli manifest        # CSV 113行 → 作品106 / 版113 / 要確認1
uv run python -m src.aozora.cli in-progress     # 作業中8件を記録(本文は取らない)
uv run python -m src.aozora.cli ingest-phase-a  # Phase A 13資料 → 483チャンク
uv run python -m src.aozora.cli embed           # embedding 483件(実測12秒)
uv run python -m src.aozora.cli report          # 状態とデータ品質(§12.2)
uv run python -m src.aozora.cli snapshot --out snapshot.json  # 再現性の照合用(§12.2)
```

⚠️ `embed` には OpenAI の実キーが要る（`text-embedding-3-small` / 1536次元）。
対象は `chunker_version='aozora_v1'` かつ embedding が null のものだけで、
既存の思想モード（`v1`）のチャンクには触らない。

| 指標 | 実測 |
|---|---|
| 文書 | 13件（lecture 9 / literary_theory 1 / criticism 1 / preface 1 / short_story 1） |
| チャンク | 483件（文字化け率は全件 0.0000） |
| corpus_role | core_thought 9 / creative_grammar 3 / narrative_reference 1 |
| speaker_role | author_direct 408 / narrator 47 / character 28 |
| **author_thought_core_index** | **377件・うち小説由来 0件** |
| narrative_reference_index | 75件（全件が思想の根拠から除外） |
| tag_review_status | auto_ok 483（整合性違反 0件） |
| orphan chunk | 0件 |
| 未解決の同定キュー | 1件（吾輩は猫である） |

**指示書§14.6「core thought内のfiction混入率」= 0** を実データで満たしている。

### 12.2 retrieval test の実測（指示書 §14.4）

483チャンクに実 embedding を付けた状態で、論理Index によるフィルタが効くことを確認した。

| ケース | 結果 |
|---|---|
| **思想質問**「近代化と開化について、内発的か外発的か」を `core_thought` + `author_direct` で検索 | 『現代日本の開化』の該当箇所が上位を占める（「内発的ででもあるかのごとき顔をして」等） |
| **創作質問** を `creative_grammar` で検索 | 『写生文』の作風論が返る |
| 同じく `narrative_reference` で検索 | 夢十夜の第一夜・第三夜が `speaker_role=narrator` で返る |
| **「夢の中で女が死ぬ話」を `core_thought` で検索** | 10件すべて講演。**小説由来 0件** |

最後のケースが指示書の核心の裏付けになる。夢十夜そのものを狙った質問でも、
思想Index には小説が1件も入らない。

---

### 12.3 C-T6（創作カード候補）の実測

```bash
uv run python -m src.aozora.cli create-profile  # 『夢十夜』profile
uv run python -m src.aozora.cli gen-cards       # カード候補(必ず draft)
```

実LLM（claude-sonnet-5）で **17件の draft カード**が生成された（所要 約1分45秒）。
根拠が最低件数（2件）に満たない4件は自動で捨てられた。

| evidence_type | 件数 | 例 |
|---|---|---|
| `demonstrated_in_fiction`（小説本文での実演） | 10 | 各夜は「こんな夢を見た」という同一の導入句で開始する / 超自然の発生原理を説明しない |
| `author_creative_theory`（漱石自身の創作論） | 7 | 評価軸は単一でなく複数の条項を用意する / 大人が小児を視るように、描く対象と距離を保つ |

**根拠の接地を実DBで検証**した。全カードの `evidence_chunk_ids` が実在チャンクを指し、
LLM による捏造は0件。例:

- 「各夜は『こんな夢を見た』という同一の導入句で開始する」→ 第一夜・第二夜・第三夜・
  第五夜・第七夜の冒頭チャンク5件（すべて実際に「こんな夢を見た。」で始まる）
- 「専門領域を越えて他分野を排斥しない」→『作物の批評』の該当2チャンク

⚠️ 生成物はすべて `status='draft'`。**approved は人間が管理画面で行う**（指示書§9 Pass4）。
現時点で approved は0件のため、この profile ではまだ生成できない（不変条件が働く）。

---

### 12.4 通し確認: 承認 →「第十一夜」の生成（2026-07-27）

CLI で承認した13枚のカードを使い、生成パイプラインが最後まで通ることを実データで確認した。

```bash
uv run python -m src.aozora.cli gen-cards            # 候補(draft)
uv run python -m src.aozora.cli show-card <card_id>  # 根拠原文を見て確認
uv run python -m src.aozora.cli approve <card_id>... # 承認(根拠の実在を検証)
```

結果: `status=succeeded` / `display_title=鏡（AI創作）` / **Guard passed（再生成1回）**。

| 検査 | 結果 |
|---|---|
| 原文類似 | passed（最長一致 **7字** / n-gram重複 **0.0**） |
| 誤認防止 | passed |
| 禁止事項（承認済み prohibition カード） | passed |

承認済みカードが本文に効いていることを確認できる:
「こんな夢を見た」で開始 / 異常を疑わず受容（「鏡というのはそういうものらしい」）/
反復による時間経過（「あと十日」「あと九日」）/ 泣く対象を泣かずに叙述（「涙とは呼ばなかった。
ただ濡れて見えた」）/ **結末を明示せず途切れさせる**（鏡に何が映ったかは書かない）。

#### 実LLMでのみ表面化した不具合3件（いずれも修正済み）

FakeLLM を使う単体テストでは再現せず、実データ通しで初めて出た。

| 症状 | 原因 | 対処 |
|---|---|---|
| `Invalid control character` で draft が失敗 | 長文の本文に含まれる改行を、LLMがJSON文字列へ生のまま出力する | `json.loads(..., strict=False)`。既存の蒸留にも効く |
| `Unterminated string` で outline が失敗 | 応答が `max_tokens` で切れ、壊れたJSONになっていた | 切り詰めを `stop_reason` で検出し、明確なエラーにする。outline の上限も引き上げ |
| Guard が通らず安全側失敗が続く | judge にカードの**タイトルしか渡していなかった**ため、「語り手の気づき」を「仕組みの説明」と過検出 | カードの `positive_patterns` / `negative_patterns`（＝境界）を judge へ渡す。判定の原則も明記し `guard_judge` を v2 へ |

3件目は Guard が**正しく安全側に倒れた**うえでの調整であり、違反したまま公開されたわけではない。

---

### 12.5 C-T7（Router）の実測

拡張RPC（`target_corpus_roles` / `target_speaker_roles` / `primary_edition_only`、
すべて default 付き）と論理Index 8種のプリセットを実装し、実データで検証した。

| 質問 | 判定 | 検索順の実際 |
|---|---|---|
| 漱石は近代化をどう考えたか | `thought` | core → support → creative_grammar → narrative_reference（**明示付き**） |
| 『夢十夜』の第十一夜を書け | `creative` | creative_grammar → narrative_reference → style_reference → core_thought（**Bridge Rule 経由のみ**） |
| 代助は日本社会をどう考えたか | `character` | character_judgment（明示付き）→ narrative_reference（明示付き）→ core_thought（**比較対象のみ**） |

- 「夢の中で女が死ぬ話」を思想ルート第1段で引いても **小説由来 0件**
- 人物質問は登場人物名（代助 → `daisuke`）で判定。**名前が挙がらない質問は人物質問にしない**
  （誤判定すると作者の思想を主根拠から外してしまうため）
- 『それから』は Phase C（未投入）のため character_judgment は0件だが、
  ルーティング自体は正しく人物ルートへ分岐する

#### ⚠️ `create or replace` の落とし穴（実DBで判明）

RPC にパラメータを足すと `create or replace` は**置換ではなく多重定義**になる。
旧シグネチャが残ると PostgREST が候補を選べず、**既存の3引数呼び出し**（思想モードの
現行RAG）が `PGRST203: Could not choose the best candidate function` で落ちる。

→ 新版を作る前に **旧シグネチャを明示的に `drop function`** すること。
migration にその手順を入れてある。「default 付きで足せば後方互換」は
PostgreSQL の関数解決では**成り立たない**。

---

## 12.2 C-T8 の実測結果（2026-07-27）

### snapshot による再現性の照合（受入#18）

```bash
uv run python -m src.aozora.cli snapshot --out snapshot.json      # 保存
uv run python -m src.aozora.cli snapshot --compare snapshot.json  # 照合
```

snapshot は**決定的**でなければ照合に使えないため、時刻・UUID・DBの返却順といった
「同じ内容でも変わる値」を一切含めない。本文そのものも載せず、チャンクの hash を
文書単位でまとめた指紋（`chunks_fingerprint`）にしている。

実測（空のDBから `manifest` → `in-progress` → `ingest-phase-a` を2回）:

| 回 | digest | 件数 |
|---|---|---|
| 1回目 | `3fa5b42ff303535c...` | works 106 / editions 113 / sources 13 / chunks 483 |
| 2回目（全テストでDBを空にした後） | `3fa5b42ff303535c...` | 同上 |

**一致**。取り込みは空のDBから再現できる。

### データ品質レポート（受入#20 / 指示書§14.6）

`uv run python -m src.aozora.cli report` が10項目を判定する。Phase A 13資料での実測:

| 項目 | 実測 | 判定 |
|---|---|---|
| 文字化けを含むチャンクの割合 | 0.000% | OK |
| chunk_hash が重複するチャンクの割合 | 0.000% | OK |
| speaker_role 未分類の割合 | 0.000% | OK |
| 思想の中核Indexに入っている小説由来チャンク | 0件 | OK |
| source_url が無い文書 | 0件 | OK |
| content_sha256 が無い版 | 0件 | OK |
| parser_version が無い版 | 0件 | OK |
| embedding 未生成のチャンク | 0件 | OK |
| 根拠チャンクが実在しない承認済み創作カード | 0件 | OK |
| 未解決の作品同定キュー | 1件（000789 / 000790） | **要対応** |

最後の1件は §1.2 の既知の案件（吾輩は猫である / 吾輩ハ猫デアル の読みが割れる）。
人が判断するために積んであるものなので、レポートが NG を出し続けるのは正しい。

### 小説混入に対する防御が3層あることを実データで確認

「小説中の登場人物の発言を作者の思想として扱わない」は、独立した3つの条件で守られている。
実データで1つずつ壊して確かめた:

| 壊した層 | 結果 |
|---|---|
| `corpus_role` のみ（夢十夜を core_thought に変更） | **混入しない**。`speaker_role`（narrator/character）と `thought_eligibility=excluded` が残るため |
| `corpus_role` + `thought_eligibility` | 品質レポートが **75件を検出**（NG） |

思想の中核Index の実体は `corpus_role=core_thought` かつ `speaker_role=author_direct` かつ
`thought_eligibility≠excluded`。1層の設定ミスでは混入に至らない。

### Retrieval シナリオテスト（§11）

`tests/test_aozora_retrieval_scenarios.py` を追加。既存の `test_aozora_routing.py` が
**ルーティングの定義**を検証するのに対し、こちらは**実データを引いた結果**を検証する。

| §11 の要求 | 状態 |
|---|---|
| 近代化質問（講演優先・人物発言を主根拠にしない） | 実装・検証済み |
| 第十一夜生成（creative_grammar と夢十夜本文を取得） | 実装・検証済み |
| 生成AI質問（直接原典が無い） | 中核Indexが空を返すことまで検証済み。留保の**表示**は frontend 配線時 |
| 代助質問（character_judgment） | Index の定義を直し（§5.2）、夢十夜の作中人物の発言34件が引けるようになった。**固有名の人物**（代助・三四郎）は Phase C の取り込みと `character_id` の実装待ち |

---

## 12.7 Bridge Rule の frontend 配線（2026-07-28）

創作依頼（チャットの `queryKind=creative`）における**思想の唯一の経路**を配線した。
実装: `frontend/src/lib/rag/bridges.ts` + pipeline / context への注入。

### 設計判断: 思想は「チャンク検索」ではなく「橋が運ぶ主張」として入る

仕様§6 の「core_thought は Bridge Rule を介する場合のみ」を、
**core_thought チャンクの類似検索を解禁する形にはしなかった**。類似検索は
どのチャンクが入るかを制御できず、思想の文言がそのまま創作の文脈に流れ込む。

代わりに、承認済みの橋が持つ対応（思想カードの主張 → 創作カードの書き方）を
【思想と書き方の対応】として注入する。冒頭に「思想はそのまま書かず、対応が示す
『書き方』としてだけ作品へ現す」を明示し、橋ごとの禁止事項
（既定:「思想の文言を登場人物の台詞としてそのまま言わせない」）を伴わせる。

### 承認の鎖は読み出し時にも検証する

橋が架かる条件: 規則が active ＋ 版が approved ＋ **元の思想カードが approved
＋ 先の創作カードが approved**。どれか1つでも欠ければ橋は黙って落ちる。
承認後にカード側だけ取り消された場合に効く（worker 側 approve_rule と同じ規律）。

実測でこの検証が先に働いた: E2E 初回は橋0本 — 過去の復元サイクルで版の承認が
失われ draft に戻っていたためで、draft の橋は正しく架からなかった。

### E2E（承認4本で確認）

「短い夢の話をもうひとつ書いてください」→ `creative` ルート、trace に橋4本。
生成物には「名を持たない語り手（自分）」「因果連鎖に支配された時間感覚の体感化」
（日没と明転の反復・目覚めた後の時間の歪み）が**書き方として**現れ、
思想の文言は一切台詞化されなかった。

橋が発火した事実は `answer_traces.retrieval_route.bridge_rules` に残る（受入#14）。

⚠️ 未配線の残り: 創作モード（`/creative` の作品生成、worker 側）への Bridge Rule
注入。プロファイルの `"rules": "off"` を切り替える設計から必要。

---

## 12.6 Phase C（小説コーパス）の投入（2026-07-28 完了）

指示書 §6 の優先10作品: 吾輩は猫である / 草枕 / 三四郎 / それから / 門 / 行人 /
こころ / 道草 / 夢十夜（Phase A で投入済み）/ 明暗。
⚠️ 坊っちゃん・虞美人草・彼岸過迄は**リストに入っていない**。

### 取り込み結果（9作品・すべて化け率 0.0000）

| 作品 | edition | chunks |
|---|---|---|
| それから | 056143 | 747 |
| 吾輩は猫である | 000789 | 659 |
| 草枕 | 000776 | 288 |
| 三四郎 | 000794 | 849 |
| 門 | 000785 | 652 |
| 行人 | 000775 | 1,398 |
| こころ | 000773 | 719 |
| 道草 | 000783 | 1,452 |
| 明暗 | 000782 | 2,905 |

合計 9,669チャンク（Phase A と合わせ 10,152）。

### ⚠️ NDC欠落で小説が思想Indexに入りかけた（1作品ずつ確かめる進め方が捕捉）

三四郎は **CSV の NDC が空**。genre 推定が `other` に落ち、`supporting_thought` +
`author_direct` + `thought_eligibility=candidate` で取り込まれた — つまり
**小説本文210チャンクが作者の思想の根拠候補になっていた**。genre 起点の混入検査
（fiction_in_core_thought）は genre 自体の誤りを見えない。

対処（3層）:
1. `tag._KNOWN_NOVELS` — 既知の長編は表題で genre を確定させ、NDC任せにしない
2. テストで「人物辞書に載る作品は必ず小説判定になる」ことを固定
3. 品質レポートに `known_novel_misclassified`（表題起点の検査）を追加

再取り込み後の三四郎は novel / narrative_reference / 849チャンク
（other 時代は講演用チャンカーに流れて241だった。会話分割も直った）。

### 『それから』での character_id 実測（先行検証）

Pass2 適用 747件 / レビュー要 33件（4.4%）。会話314件のうち **170件に `daisuke`**、
複数話者の応酬・辞書未登録の話者（三千代・平岡・梅子）は null（意図どおり）。

実チャット「代助はなぜ働かないのですか」: character ルート発火
（`character_id: daisuke`）、`direct_source_ids = それから`、留保なし。
回答は作中の職業論の場面に基づき、**回答自身が**「あくまで作品の中の話として…
俺自身がそれを正しいと主張しているわけじゃない」と帰属を区別した。
Phase C 前は同じ質問に留保しか返せなかった。

### 完了時の実測（2026-07-28）

| 指標 | 実測 |
|---|---|
| 文書 / チャンク | 22 / **10,152**（Pass2・embedding 全件適用） |
| speaker_role | author_direct 408 / narrator 5,372 / character 4,372 |
| character_id | 10人全員が**自作品の中でだけ**付与。津田350 / 健三303 / お延267 / それから=daisuke 178 / 門=sosuke 108 / 猫=迷亭84・苦沙弥49・寒月49 / 三四郎=美禰子81・三四郎67。**作品間の混線ゼロ** |
| レビュー要 | 951件（9.4%） |
| 品質レポート | 既知の同定キュー1件（000789/000790）以外すべて OK |
| snapshot 基準 | `snapshots/phase_c.json`（phase_a.json は歴史的基準として残置） |

E2E: 「津田とお延の関係は」→ character ルート（`tsuda`）、`direct_source_ids=明暗`。
「代助はなぜ働かないのですか」→ 作中の職業論に基づく回答（§12.6 冒頭）。

### ⚠️ PostgREST の1000行上限を3箇所で踏んだ

PostgREST は1リクエスト最大1000行しか返さない。1回の execute で全件が来る前提の
コードは、データが1000件を超えた時点で**黙って切り詰められる**:

1. `retag` — 9,669件中1000件で「完了」した
2. `snapshot` / 品質レポート — counts が1000になり、**検査も先頭1000件のみ**だった
3. `cli report` — 思想Index が 376件のところ 214件と表示された

`paged.fetch_all`（ページング必須ヘルパー）へ集約した。gen_thought_cards /
gen_creative_cards の取得も同時に直した（創作対象は約9,700件で次回実行時に発症するはずだった）。
**新しく全件取得を書くときは必ず paged.py を使うこと。**

### 重複検査は短文を対象外にした

Phase C で重複33件が出たが、すべて「何ですって」のような短い台詞の正当な繰り返しと
章番号だった。検査の目的は取り込みミス（同じ段落の二重投入）の検出なので、
30字未満のチャンクを対象外にした（`DUPLICATE_MIN_CHARS`）。

### 呼称が衝突する人物の扱い（2026-07-28 追補）

こころ・行人・草枕を人物辞書に追加した。鍵は**検出とタグ付けを別の関心事として分けた**こと:

| 呼称 | タグ付け(names) | 質問検出(detect_names) |
|---|---|---|
| K | そのまま | **境界一致**（前後が英数字なら不一致。OK / KPI / 4K に反応しない） |
| 先生 | そのまま（Pass2 は作品スコープの一覧から選ぶので衝突しない） | **複合語のみ**（「こころの先生」）。「漱石先生はどう考える？」を人物ルートに入れないため |
| 一郎・二郎・お直・那美 | そのまま | そのまま（既存の「津田」と同水準の許容） |

辞書更新後の付け直しには `retag --source <source_id> --force` を使う（全作品の
再実行をせずに済む）。force でも人手レビュー済み（reviewed / corrected）は上書きしない。

再タグ付け後の実測（16人 / 9作品・作品間の混線ゼロ）:
津田350 / 健三303 / お延267 / 代助178 / 一郎159 / 二郎155 / 宗助108 /
**こころの先生100** / 迷亭84 / 美禰子81 / 三四郎67 / 苦沙弥49 / 寒月49 /
お直48 / 那美45 / **K 12**。

E2E: 「**K**はなぜ自殺したのだと思いますか」→ 境界一致で `k` を検出、
character ルートで『こころ』を直接根拠に回答（先生がKの死因を考え直す場面・
Kの気質と神経衰弱に言及）。回答自身が「作中の書き方としては」と帰属を保った。

### 既知の残課題

- 明暗の章番号（字下げされた漢数字）が一部チャンクとして残る（例:「　十九」）。
  検索品質への影響は軽微。チャンカー修正は snapshot の指紋が全部変わるため、
  次に chunker_version を上げる機会にまとめて行う

---

## 12.5 C-T6（L2/L3候補生成）の実測（2026-07-27）

```bash
uv run python -m src.aozora.cli gen-thought-cards            # 思想カード候補(draft)
uv run python -m src.aozora.cli approve-thought <card_id>    # 承認
uv run python -m src.aozora.cli gen-questions                # 代表質問(ルーティング用)
uv run python -m src.aozora.cli gen-rules                    # 判断規則 + Bridge Rule
uv run python -m src.aozora.cli approve-rule <rule_id>
```

### 承認の関門を迂回させない鎖

| 段 | 入力 | 制約 |
|---|---|---|
| 思想カード | `author_thought_core` Index のチャンクのみ | 3条件（corpus_role / speaker_role / thought_eligibility）を**すべて**満たすもの。LLM が Index 外のIDを根拠に挙げたら捨てる |
| 判断規則 | **承認済み**思想カード | 原典から直接作らない。カード承認という人手の関門を規則が迂回できてしまうため |
| Bridge Rule | **両側が承認済み**の思想カード + 創作カード | 片側でも未承認なら橋を架けない |

承認時にも再検証する。思想カードは「根拠が今も思想Indexに居るか」、規則は
「元の思想カードが承認済みのままか」を見る。取り込み直しやタグの修正で
根拠が小説側へ移ることがあるため。

### Bridge Rule の禁止事項は LLM 任せにしない

仕様§6 の禁止（思想チャンクを登場人物の台詞へそのまま注入しない）は、
LLM の出力に関わらず必ず `forbidden_inferences` へ入れる。実測でも
LLM 由来の禁止2件に加えて既定の1件が入っている。

### ⚠️ 全チャンクを1プロンプトに詰めると1資料しか入らない

最初の実装は思想Index 377チャンクをまとめて渡していた。文字数上限（12000字）で
**9資料中1つの冒頭しか入らず、カード9枚すべてが『文芸の哲学的基礎』由来**になった。
資料ごとに分けて呼ぶよう直した結果、8資料すべてから出るようになった。

### 実測

| 対象 | 実測 |
|---|---|
| 思想カード | 57枚（全 draft → 12枚を承認）。8資料すべてから出ている |
| 代表質問 | 113件（承認済み12枚ぶん） |
| 判断規則 | 23件（`distinction` / `priority` / `value_transformation` / `boundary` / `temporal_override`） |
| Bridge Rule | 6件（思想カード → 創作カードの対応） |

⚠️ `gen-rules` は実行のたびに新しい候補を提案する（LLM が別の `rule_family_id` を出すため）。
既存と同名のものはスキップするが、**回を重ねると候補が積み上がる**。承認して使うものを
選び、残りは却下する運用が要る。

### 思想対話が動くようになった

C-T6 の前は `thought_cards` 0件・`fallback_card_id` 未設定で、`thought` /
`life_advice` に分類された質問は既存の不変条件で 500 になっていた。

| 質問 | routing_method | 選ばれたカード |
|---|---|---|
| 近代化についてどう考えますか | （代表質問の生成前）フォールバック | 傍観者の観察は対象と同化できない |
| 文学は科学のように進歩するものでしょうか | `llm_classifier`・フォールバック**不使用** | 文学は科学と異なり一本道に発達しない |

⚠️ **代表質問（`thought_questions`）が無いと Thought Router は当てられない**。
無くても回答は返るが、すべてフォールバックカードへ流れる。`gen-questions` は
思想カードを承認したら必ず流すこと。

### 承認リンク由来の根拠がコーパスタグを落としていた（修正済み）

`fetchLinkedEvidence` が `speaker_role` / `corpus_role` を取っておらず、
承認リンク経由の根拠だけ帰属が判定できない状態だった（trace でも `unclassified`
として数えられていた）。列を追加して解消。

---

## 12.4 C-T4b（Pass2 LLM分類 / Pass4 レビューキュー）の実測（2026-07-27）

```bash
uv run python -m src.aozora.cli retag         # Pass2 を未適用チャンクへ
uv run python -m src.aozora.cli review-tags   # Pass4 レビュー待ち（確信度の低い順）
uv run python -m src.aozora.cli tag-review <chunk_id> --by me --set speaker_role=quoted_person
```

Pass2 は**取り込みとは別のステップ**にした。取り込みを LLM 無しで再実行できる状態に
保ちたい（§12.2 の再現性検証で使う）のと、分類だけをやり直せるようにするため。

### Pass2 は安全側の決定を覆せない

`tag.merge_pass2` が上限を掛ける。LLM がどれだけ自信を持って別の値を返しても:

- 小説のチャンクを `author_direct` にできない（`narrator` / `character` に閉じる）
- `thought_eligibility` を Pass1 より**上げられない**（下げることはできる）
- 小説は何があっても `thought_eligibility=excluded`
- 未知の値は Pass1 へ戻し、確信度0でレビューへ回す（握りつぶすと「分類済みの誤り」になる）

人手の修正（`tag-review --set`）にも同じ制限が掛かる。レビューは「LLMの誤りを直す」
ためのもので、作者と作中人物の区別そのものを覆す手段ではない。

### ⚠️ LLM は講演者を「登場人物」と分類する

実データで出た systematic な誤り。講演で著者が自分を演出している箇所を
`speaker_role=character` と分類してくる。Pass3 の整合性検査は拾えるが、
**40件中26件がレビュー行き**になりキューが使えなくなった。

これは本文についての情報ではなく**カテゴリの誤り**なので、レビューへ回さず
`merge_pass2` で直す（非小説に `narrator` / `character` は存在しない。
引用だと言っているなら `quoted_person`、それ以外は Pass1 の値へ）。直した事実は
`classification_reason` に残す。プロンプト側にも明記した。

| | レビュー行き |
|---|---|
| 修正前 | 40件中 **26件** |
| 修正後 | 40件中 **9件**（残りはすべて確信度 0.85〜0.89 の妥当なもの） |

### Phase A 483チャンクへの適用結果

| 指標 | 実測 |
|---|---|
| 分類済み | 483件（auto_ok 404 / needs_review 79 = 16%） |
| speaker_role | author_direct 408 / narrator 39 / character 36 |
| claim_type | descriptive_observation 114 / conceptual_distinction 96 / normative_claim 72 / fictional_statement 70 / literary_analysis 50 / autobiographical_report 40 ほか |
| **小説チャンクの thought_eligibility** | **75件すべて excluded**（受入#9 を維持） |

Pass1 は会話文の判定を段落単位でしか見ないため narrator 47 / character 28 だった。
Pass2 が地の文に埋め込まれた会話を8件拾い、39 / 36 になった（受入#7・#8）。

### ⚠️ snapshot に Pass2 の結果を含めてはいけない

Pass2 は LLM なので非決定的。`speaker_role` などを snapshot の指紋に含めると、
**同じ取り込みでも digest が揺れて #18 の再現性検証が使えなくなる**。
指紋は `chunk_id + chunk_hash`（本文とその分割）に限定した。
タグの状態は品質レポートと Pass4 レビューキューで見る。

実測: `ingest-phase-a` → snapshot 保存 → `retag`（483件分類）→ snapshot 照合 で**一致**。

---

## 12.3 frontend への routing / trace 配線の実測（2026-07-27）

`frontend/src/lib/rag/corpus-routing.ts` を追加し、回答パイプラインへ配線した。
⚠️ `INDEXES` / `ROUTES` は worker 側 `worker/src/aozora/routing.py` と対。
片方だけ変えると、生成と回答で別の規則が働く。

### 既存の検索を置き換えない

コーパス層より前に投入した原典は `corpus_role` が null で、絞り込むと
**丸ごと落ちる**。無絞り込み検索は残したまま、ルート絞り込み検索を**足して**統合する。
`attributionFor` も role 未設定のチャンクには注記を付けない（既存の挙動を変えない）。

### 小説を作者の思想として出さないための3点

1. **順序**: 思想質問では小説由来を作者の直接発言より後ろへ下げる（`rankByRoute`）。
   ベクトル検索は文体の似た小説をよく引くため、順序をそのまま使うと
   **文体の一致が思想の一致として提示される**（指示書§18）。
2. **明示**: 誰の発言かを原典本文と同じ場所に `⚠️` 付きで書く。別枠の注意書きにすると
   生成時に滑る（指示書§10.1）。
3. **留保**: 直接の原典が無ければ `abstention_reason` を残し、プロンプトにも入れる。

### ⚠️ 関連度の閾値が無いと留保は一度も発火しない

ベクトル検索は関連が無くても常に上位N件を返す。ヒットの有無だけで判定すると
**現代の質問にも必ず「原典がある」ことになる**。実測（Phase A 483チャンク /
text-embedding-3-small）:

| 質問 | 最上位の類似度 |
|---|---|
| 現代日本の開化について | 0.68 |
| 生成AIをどう思うか | 0.35 |
| 暗号資産の確定申告 | 0.24 |
| ブロックチェーンの規制について | 0.21 |

原典にある話題と現代語の間がはっきり空くので、その間に閾値を置いた。
`hasDirectSource` と `directSourceIds` は**同じ条件**で数える。
片方だけ緩いと、留保を出しているのに trace には原典が並ぶ矛盾した記録になる。

⚠️ **2026-08-09 に `MIN_DIRECT_SOURCE_SCORE` を 0.45 → 0.40 へ変更した。**
上の較正は483チャンク時点のもので、10,152チャンクでは分布が変わっていた。

| 質問 | 最高スコア | 原典の被覆 |
|---|---|---|
| 明治維新は日本を前に進めたか | 0.445 | あり(維新17・開化32件) |
| 森鴎外の作品について | 0.443 | **なし**(0件) |
| スマートフォンについて | 0.329 | なし |

被覆あり(0.445)と被覆なし(0.443)は **0.002 差**で、閾値では分離できない。
その判別は**主題語が原典に実在するか**の検査が担う（`decideAbstention` の
`subject` 引数）。閾値の役割は「明らかに無関係なものを落とす」ことだけに縮小した。

⚠️ また、判定に使う `score` は **`origin === "vector"` のものに限る**。
承認リンク由来は `strength` の変換値(0.6/0.8/1.0)、全文検索は固定 0.5 で、
**どちらも質問との関連度ではない**。混ぜると閾値を必ず超え、カードが1枚でも
紐づけば「原典あり」が常に真になる。

⚠️ **§12.3 の「既知の制約」は解消していない。** 上表の検証は 2026-08-09 に
`thought` / `person_or_work` の実チャットで取り直したが、較正は実測3点に
合わせたものでしかない。コーパスを増やしたら測り直すこと。

### 実チャットでの確認

| 質問 | route | trace | 回答 |
|---|---|---|---|
| 『夢十夜』はどんな作品ですか | thought | 小説5チャンクが `attributed_chunk_ids`、`direct_source_ids` は講演2件 | 「作中の語り手が語ることと、俺自身の考えを重ねて話すつもりはない」と自ら区別した |
| 暗号資産の確定申告は | thought | `abstention_reason` 発火 | 「答えられることが何もない」と述べ専門家へ誘導 |
| 半導体の輸出規制について書いた文章はありますか | thought | `abstention_reason` 発火・`direct_source_ids` は空 | 同上 |
| 三四郎という人物はどういう人ですか | **character**（`character_id: sanshiro`） | `indexes` が character ルート、`abstention_reason` 発火 | 「細かく具体的に語れるだけの材料が手元にない」と述べ、作品そのものに当たるよう案内 |

最後の1件は Phase C（長編小説）が未取り込みのため。**取り込み前に人物像を捏造しない**
ことが確認できた形。Phase C 完了後は `character_judgment` が引けるようになる。

### 既知の制約

- 承認済み思想カードが0枚・フォールバック未設定の現状では、`thought` / `life_advice`
  に分類された質問は**既存の不変条件で 500 になる**（本配線とは無関係の既存仕様）。
  上表の検証は `fact` / `person_or_work` に分類される聞き方で行った。
- ~~創作ルートの Bridge Rule 配線は未了~~ → **配線済み（2026-07-28）**。詳細は §12.7。

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
| 7 | チャンク単位の speaker role を付けられる | **完了**（§12.4。483件・未分類0%） |
| 8 | 作者・語り手・登場人物・引用人物を区別できる | **完了**（§12.4。author_direct 408 / narrator 39 / character 36） |
| 9 | core thought Index へ小説人物の発言が混入しない | C-T5（§5） |
| 10 | 主要12資料を取り込める | C-T5（実際は13件） |
| 11 | 『文学論』『文学評論』を別providerとして管理できる | **スコープ外**。`source_provider` 列で将来対応可能な形にする |
| 12 | approved カード・規則のみ assist で使用する | 実装済み（創作モード） |
| 13 | L2/L3 の全項目に evidence と provenance がある | **完了**（§12.5。思想カードは `thought_evidence_links`、規則は `judgment_rule_evidence` にカードと原典チャンクの両方） |
| 14 | 原典とAI外挿を trace 上で区別できる | **完了**（§12.3。`frontend/src/lib/rag/corpus-routing.ts` + pipeline 配線） |
| 15 | 思想検索と創作検索のルーティングを分離できる | **完了**（§12.3） |
| 16 | 既存Thinkerのテストがすべて通る | 全タスクで維持 |
| 17 | migration が additive で rollback 可能 | C-T2a（§10） |
| 18 | corpus snapshot を再現できる | **C-T8 完了**（§12.2。空のDBから2回取り込んで digest 一致） |
| 19 | parser / embedding / prompt の version を保存できる | **完了**（`parser_version` / `chunker_version` / `tagger_version`） |
| 20 | データ品質レポートを出力できる | **C-T8 完了**（§12.2。10項目・`cli report`） |

⚠️ **#11 は今回スコープ外**（発注者確定: 今回は青空文庫のみ）。`source_provider` を最初から
持たせることで、将来 NDL を追加する際に additive で済む形にしてある。

### C-T8 時点で残っている項目（2026-07-27）

20項目のうち、まだ満たしていないものと理由:

| # | 条件 | 残っている理由 |
|---|---|---|

#9（小説人物の発言が思想Indexへ混入しない）は §12.2 のとおり、実データで3層の防御を
1つずつ壊して確認済み。

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
