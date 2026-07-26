"""青空文庫 Manifest Importer のテスト(C-T2b / 仕様 docs/CORPUS_T1_SPEC.md §1.2)。

canonical work の同定は、作品名の完全一致に依存させない。
実データの反例:「吾輩は猫である」(000789)と「吾輩ハ猫デアル」(000790)。
"""

import pytest

from src.aozora import manifest


def _row(work_id, title, reading, orthography="新字新仮名", **over):
    """青空文庫CSVの1行(必要な列だけ)。"""
    row = {
        "作品ID": work_id,
        "作品名": title,
        "作品名読み": reading,
        "文字遣い種別": orthography,
        "人物ID": "000148",
        "役割フラグ": "著者",
        "分類番号": "NDC 913",
        "初出": "",
        "図書カードURL": f"https://www.aozora.gr.jp/cards/000148/card{int(work_id)}.html",
        "テキストファイルURL": f"https://www.aozora.gr.jp/cards/000148/files/{int(work_id)}_ruby.zip",
        "テキストファイル符号化方式": "ShiftJIS",
        "テキストファイル文字集合": "JIS X 0208",
        "底本名1": "夏目漱石全集10",
        "底本出版社名1": "ちくま文庫、筑摩書房",
        "底本初版発行年1": "1988（昭和63）年7月26日",
        "入力に使用した版1": "1996（平成8）年7月15日第5刷",
        "校正に使用した版1": "",
        "底本の親本名1": "筑摩全集類聚版夏目漱石全集",
        "入力者": "野口英司",
        "校正者": "",
        "公開日": "1997-12-16",
        "最終更新日": "2013-07-17",
    }
    row.update(over)
    return row


# ── 正規化(§1.2 段2) ──


def test_normalize_title_absorbs_orthographic_variants():
    """新字新仮名と旧字旧仮名でタイトル表記が割れても同じ正規形になる。"""
    assert manifest.normalize_title("吾輩ハ猫デアル") == manifest.normalize_title("吾輩は猫である")


def test_normalize_title_applies_nfkc_and_strips_spaces():
    assert manifest.normalize_title("三四郎　") == manifest.normalize_title("三四郎")


# ── canonical work の同定(§1.2) ──


def test_group_works_merges_editions_by_reading():
    """作品名読みが一致すれば、タイトル文字列が違っても同一作品に束ねる(実データの反例)。"""
    rows = [
        _row("000789", "吾輩は猫である", "わがはいはねこである", "新字新仮名"),
        _row("000790", "吾輩ハ猫デアル", "わがはいはねこである", "旧字旧仮名"),
    ]

    groups = manifest.group_into_canonical_works(rows)

    assert len(groups) == 1
    g = groups[0]
    assert sorted(e["作品ID"] for e in g["editions"]) == ["000789", "000790"]
    # 両方の表記を保持し、どちらでも検索が当たるようにする
    assert set(g["title_variants"]) == {"吾輩は猫である", "吾輩ハ猫デアル"}
    assert g["match_method"] == "reading"


def test_group_works_keeps_distinct_works_separate():
    """読みが違う別作品は束ねない。"""
    rows = [
        _row("000799", "夢十夜", "ゆめじゅうや"),
        _row("000759", "現代日本の開化", "げんだいにほんのかいか"),
    ]
    assert len(manifest.group_into_canonical_works(rows)) == 2


def test_group_works_falls_back_to_normalized_title_when_reading_missing():
    """読みが欠けていても正規化タイトルで束ねられる(§1.2 段2)。"""
    rows = [
        _row("000789", "吾輩は猫である", ""),
        _row("000790", "吾輩ハ猫デアル", ""),
    ]

    groups = manifest.group_into_canonical_works(rows)

    assert len(groups) == 1
    assert groups[0]["match_method"] == "normalized_title"


def test_group_works_merges_wagahai_as_in_real_data():
    """実データの回帰テスト: 「吾輩は猫である」はタイトルも読みも表記が異なる。

    青空文庫の実データでは、旧字旧仮名版の読みが「わがはいハねこデアル」と
    カタカナ混じりで登録されている。そのため読み(段1)では束ねられず、
    正規化タイトル(段2)で統合される。統合はするが needs_review を立てて
    人手確認に回す(指示書§14.2 の必須ケース)。
    """
    rows = [
        _row("000789", "吾輩は猫である", "わがはいはねこである", "新字新仮名"),
        _row("000790", "吾輩ハ猫デアル", "わがはいハねこデアル", "旧字旧仮名",
             **{"テキストファイルURL": ""}),
    ]

    groups = manifest.group_into_canonical_works(rows)

    assert len(groups) == 1, "版違いが別作品に割れてはいけない"
    g = groups[0]
    assert g["match_method"] == "normalized_title"
    assert g["needs_review"] is True, "読みが割れているので人手確認へ回す"
    # 本文の無い旧字版は既定検索版に選ばない
    assert manifest.pick_primary_edition(g["editions"])["作品ID"] == "000789"


def test_group_works_flags_ambiguous_for_manual_review():
    """読みもタイトルも一致しないが同一作品の疑いがある場合は自動統合しない(§1.2 段4)。"""
    rows = [
        _row("000794", "三四郎", "さんしろう"),
        _row("058842", "三四郎", "さんしろお"),  # 読みが揺れている
    ]

    groups = manifest.group_into_canonical_works(rows)

    # タイトルが一致するので統合はするが、読みの不一致を記録して確認に回す
    assert len(groups) == 1
    assert groups[0]["match_method"] == "normalized_title"
    assert groups[0]["needs_review"] is True


# ── primary retrieval edition の選定(§1.3) ──


def test_primary_edition_prefers_shinji_shinkana():
    """既定の検索版は新字新仮名を優先する。"""
    rows = [
        _row("058842", "三四郎", "さんしろう", "新字旧仮名"),
        _row("000794", "三四郎", "さんしろう", "新字新仮名"),
    ]
    g = manifest.group_into_canonical_works(rows)[0]
    assert manifest.pick_primary_edition(g["editions"])["作品ID"] == "000794"


def test_primary_edition_skips_edition_without_text_file():
    """本文が取得できない版は既定の検索版にしない(000790のケース)。"""
    rows = [
        _row("000790", "吾輩ハ猫デアル", "わがはいはねこである", "新字新仮名",
             **{"テキストファイルURL": ""}),
        _row("000789", "吾輩は猫である", "わがはいはねこである", "旧字旧仮名"),
    ]
    g = manifest.group_into_canonical_works(rows)[0]
    # 表記の優先度では000790が上だが、本文が無いので000789を選ぶ
    assert manifest.pick_primary_edition(g["editions"])["作品ID"] == "000789"


def test_primary_edition_returns_none_when_no_text_available():
    """全版で本文が無ければ既定検索版は立てない。"""
    rows = [_row("000790", "吾輩ハ猫デアル", "わがはい", **{"テキストファイルURL": ""})]
    g = manifest.group_into_canonical_works(rows)[0]
    assert manifest.pick_primary_edition(g["editions"]) is None


# ── 行 → edition レコード(§2.1) ──


def test_build_edition_record_keeps_provenance():
    """底本・入力者・取得元などの由来情報を落とさない(指示書§2.4)。"""
    rec = manifest.build_edition_record(_row("000799", "夢十夜", "ゆめじゅうや"), "cw_x")

    assert rec["edition_id"] == "000799"
    assert rec["orthography"] == "新字新仮名"
    assert rec["work_status"] == "published"
    assert rec["text_charset"] == "JIS X 0208"
    assert rec["bottom_text"]["底本名"] == "夏目漱石全集10"
    assert rec["bottom_text"]["底本の親本名"] == "筑摩全集類聚版夏目漱石全集"
    assert rec["input_by"] == "野口英司"
    # 校正者は空の行がある(実データで113件中1件)。空文字ではなくNoneで保存する
    assert rec["proofread_by"] is None


def test_build_edition_record_handles_missing_text_url():
    rec = manifest.build_edition_record(
        _row("000790", "吾輩ハ猫デアル", "わがはい", **{"テキストファイルURL": ""}), "cw_x"
    )
    assert rec["text_file_url"] is None


# ── DBへの投入(C-T2b) ──


def test_import_manifest_writes_works_and_editions(clean_corpus, client):
    """CSV行から canonical_works / work_editions を投入する。"""
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    rows = [
        _row("000789", "吾輩は猫である", "わがはいはねこである", "新字新仮名"),
        _row("000790", "吾輩ハ猫デアル", "わがはいハねこデアル", "旧字旧仮名",
             **{"テキストファイルURL": ""}),
        _row("000799", "夢十夜", "ゆめじゅうや", "新字新仮名"),
    ]

    result = manifest.import_manifest(rows, person_id="natsume_soseki", client=client)

    assert result["works"] == 2
    assert result["editions"] == 3
    assert result["review_queued"] == 1  # 吾輩は猫である(読みが割れている)

    works = client.table("canonical_works").select("*").execute().data
    wagahai = next(w for w in works if "猫" in w["canonical_title"])
    # 両表記を保持していること(順序は照合順に依存するので集合で見る)
    assert set(wagahai["title_variants"]) == {"吾輩は猫である", "吾輩ハ猫デアル"}

    eds = (
        client.table("work_editions").select("*")
        .eq("canonical_work_id", wagahai["canonical_work_id"]).execute().data
    )
    assert len(eds) == 2
    primary = [e for e in eds if e["is_primary_retrieval_edition"]]
    assert [e["edition_id"] for e in primary] == ["000789"], "本文の無い版を既定にしない"
    # 由来情報が保存されていること
    assert primary[0]["bottom_text"]["底本名"] == "夏目漱石全集10"
    assert primary[0]["input_by"] == "野口英司"

    queue = client.table("canonical_work_review_queue").select("*").execute().data
    assert len(queue) == 1
    assert sorted(queue[0]["aozora_work_ids"]) == ["000789", "000790"]


def test_import_manifest_is_idempotent(clean_corpus, client):
    """再実行しても行が重複しない(取り込みは何度でも流せること)。"""
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    rows = [_row("000799", "夢十夜", "ゆめじゅうや")]

    manifest.import_manifest(rows, person_id="natsume_soseki", client=client)
    manifest.import_manifest(rows, person_id="natsume_soseki", client=client)

    assert len(client.table("canonical_works").select("canonical_work_id").execute().data) == 1
    assert len(client.table("work_editions").select("edition_id").execute().data) == 1


def test_import_manifest_records_in_progress_without_fetching(clean_corpus, client):
    """作業中作品は manifest にのみ記録し、editionを作らない(指示書§2.1)。"""
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()

    manifest.import_in_progress_entries(
        [{"aozora_work_id": "000000", "title": "三四郎", "orthography": "新字新仮名"}],
        person_id="natsume_soseki",
        source_page_url="https://www.aozora.gr.jp/index_pages/person148.html",
        client=client,
    )

    entries = client.table("aozora_manifest_entries").select("*").execute().data
    assert len(entries) == 1
    assert entries[0]["work_status"] == "in_progress"
    # 本文側(edition)は作らない
    assert client.table("work_editions").select("edition_id").execute().data == []
