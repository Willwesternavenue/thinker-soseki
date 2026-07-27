# corpus snapshot（受入#18）

取り込みを再現できたかを照合するための基準スナップショット。

`phase_a.json` は Phase A 13資料（講演9 / 創作論3 / 夢十夜1 = 483チャンク）を
空のDBから取り込んだ結果。作成手順とその実測は
[docs/CORPUS_T1_SPEC.md §12.1・§12.2](../../docs/CORPUS_T1_SPEC.md) を参照。

## 照合

```bash
uv run python -m src.aozora.cli snapshot --compare snapshots/phase_a.json
```

digest が一致すれば、取り込みが基準どおりに再現できている。
一致しない場合は、文書の増減と、同じ文書の中身の変化（指紋の相違）が表示される。

## 更新するとき

**取り込み結果が意図して変わったときだけ**更新する。具体的には:

- `parse.PARSER_VERSION` / `chunk.CHUNKER_VERSION` を上げた
- タグ付け（`tag.py`）の規則を変えた
- Phase A の対象資料を増減した

いずれでもないのに digest が変わったなら、それは**取り込みが壊れた合図**であって
スナップショットを更新して合わせる場面ではない。

```bash
uv run python -m src.aozora.cli snapshot --out snapshots/phase_a.json
```

## 含めていないもの

- 本文そのもの（チャンクの hash を文書単位でまとめた `chunks_fingerprint` のみ）
- embedding（ベクトルは再生成のたびに一致するとは限らず、再現性の判定に使えない）
- 創作カード（LLM生成のため `card_id` も内容も毎回変わる。品質は
  `cli report` の「根拠チャンクが実在しない承認済み創作カード」で見る）
- 時刻・UUID（同じ内容でも変わるため、含めると照合に使えなくなる）
