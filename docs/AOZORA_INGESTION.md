# 青空文庫からの原典取得手順（夏目漱石）

> 対象: 青空文庫 作家ID **000148 夏目漱石**（公開中 **113作品**。https://www.aozora.gr.jp/index_pages/person148.html ）
> 決定事項: 取得形式は**テキストファイル（ルビあり）zip 一択**（2026-07-26 ユーザー確認済み）。
> 本書の URL はすべて 2026-07-26 に到達確認済み（HTTP 200）。

## 1. 形式の選定（確定）

図書カードには3形式が並ぶが、以下の理由でルビありテキストを使う。

| 形式 | 判定 | 理由 |
|---|---|---|
| **テキストファイル(ルビあり) zip** | **採用** | 青空文庫の正本形式。注記法（`《ルビ》` `｜` `［＃...］`）が公式文書化されており機械処理の定番。**末尾に底本ブロックが定型で付く**ため、仕様（SPEC v0.2 §3 L1）が必須とする底本・正字法の記録をそのまま抽出できる。最小サイズ |
| エキスパンドブック .ebk | 除外 | 1990年代ボイジャー社の独自バイナリ。パーサ実質なし（死んだフォーマット） |
| XHTML | 除外 | テキスト版からの機械生成派生物。タグ剥がしの工程が増えるだけで情報は増えない |

## 2. 取得方法

### 2.1 一括取得（推奨ルート）

青空文庫サイトへの連続ダウンロードは避け（サーバ負荷への配慮）、以下の公式提供物を使う。

**手順A: 公式CSVインデックスで対象リストを作る**

```
https://www.aozora.gr.jp/index_pages/list_person_all_extended_utf8.zip
```

全公開作品の一覧CSV（UTF-8）。1作品1行で、人物ID / 作品ID / 作品名 / **文字遣い種別** / 図書カードURL / **テキストファイルURL** / 底本情報 / 入力者・校正者 等の列を持つ。
→ 人物ID `000148` でフィルタし、テキストファイルURL列（`…/cards/000148/files/{作品ID}_ruby_{リビジョン}.zip`）の一覧を得る。

**手順B: GitHub公式ミラーから実ファイルを取る**

青空文庫はサイト全体を GitHub にミラーしている: `https://github.com/aozorabunko/aozorabunko`

- 個別ファイルの raw 取得（確認済み例・夢十夜）:
  `https://raw.githubusercontent.com/aozorabunko/aozorabunko/master/cards/000148/files/799_ruby_6024.zip`
- まとめて取る場合はリポジトリ全体（数GB）を clone せず **sparse checkout** で漱石分のみ:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/aozorabunko/aozorabunko.git
cd aozorabunko && git sparse-checkout set cards/000148/files
```

CSVのURL列のファイル名部分を `cards/000148/files/` 配下と突合すれば、113作品のルビありテキストが揃う。

### 2.2 単品取得（v0.1 の夢十夜はこれで足りる）

図書カード `https://www.aozora.gr.jp/cards/000148/card{作品ID}.html` → ルビありテキストのリンク。
夢十夜（作品ID 799）: `https://www.aozora.gr.jp/cards/000148/files/799_ruby_6024.zip`

v0.1 のコーパスは『夢十夜』1作品のみなので単品取得で開始し、全作品拡張（semantic search 導入時）に 2.1 の一括ルートへ移行する。

## 3. 前処理（worker の ingestion に渡すまで）

zip 内の .txt は **Shift_JIS（実質 CP932）**。前処理は次の順:

1. unzip → CP932 → UTF-8 変換（`cp932` コーデック。`shift_jis` 指定だと機種依存文字で落ちることがある）
2. **ヘッダ分離**: 冒頭のタイトル・著者行と `----` 区切りの記号凡例ブロックを除去
3. **フッタ分離**: 末尾の「底本：…」以降のブロックを**削除せず metadata として抽出**（§4 の必須記録項目の供給源）
4. **注記除去**（本文を親文字だけにする）:
   - ルビ: `《...》` を除去、ルビ範囲開始 `｜` を除去
   - 入力注記: `［＃...］`（傍点・字下げ・改丁・図版等）を除去
   - アクセント分解などの特殊記法は夢十夜には出ないが、全作品拡張時は要対応
5. UTF-8 本文 + metadata JSON を出力 → 既存 ingestion（extract → clean → chunk → embed）へ

既存の extract は青空文庫注記に未対応のため、この前処理は**青空文庫用の取得・前処理スクリプトとして worker に追加**する（T1 設計書のタスク分割参照。ルビは生成コーパス・embedding 用途では不要のため保持しない）。

## 4. sources へ記録する metadata（夢十夜の確定値）

図書カード card799 から確認済み（2026-07-26）:

```json
{
  "author": "夏目漱石",
  "work_title": "夢十夜",
  "work_group": "夢十夜",
  "aozora_work_id": "799",
  "aozora_card_url": "https://www.aozora.gr.jp/cards/000148/card799.html",
  "orthography": "新字新仮名",
  "teihon": "夏目漱石全集10（ちくま文庫、筑摩書房）",
  "teihon_first_edition": "1988-07-26",
  "teihon_used_printing": "1996-07-15 第5刷",
  "oyahon": "筑摩全集類聚版夏目漱石全集（筑摩書房、1971〜1972年）",
  "aozora_input_by": "野口英司",
  "copyright_status": "public_domain",
  "language": "ja"
}
```

`orthography = 新字新仮名` が `creative_profiles.orthography_policy = 新字新仮名` と整合することを ingestion 時に検証する。

## 5. 注意事項

- **文字遣いの混在**: 漱石113作品には新字旧仮名等の版が混在し得る。全作品拡張時は CSV の「文字遣い種別」列で profile の orthography_policy と一致するものだけを対象にする（不一致テキストを同一コーパスに混ぜない）
- **底本の版まで記録**: 同一作品でも底本が異なれば本文が異なる。evidence の正本性のため、上記 metadata の底本・刷・親本を省略しない
- **クレジット**: パブリックドメインだが、青空文庫の慣行に従い出典（青空文庫・図書カードURL・入力者/校正者）を metadata に保持し、必要に応じ画面のクレジット表記に使えるようにする
- 一括取得は GitHub ミラー経由を既定とし、aozora.gr.jp への機械的な連続アクセスはしない
