"""作家別ページのパーサ(C-T2c)。

作業中作品はCSVに載らないため、このページからのみ取得できる。
本文は取得せず、記録だけ残す(指示書§2.1)。
"""

from src.aozora import person_page

# 実ページの構造を写したfixture。作業中セクションは
# 「リンクが無い」「タイトルと括弧の間に全角空白が無い行がある」点が公開中と違う。
HTML = """
<div align="right">
［<a href="#sakuhin_list_1">公開中の作品</a>｜<a href="#sakuhin_list_2">作業中の作品</a>］
</div>

<h2><a name="sakuhin_list_1">公開中の作品</a></h2>
<ol>
<li><a href="../cards/000148/card799.html">夢十夜</a>　（新字新仮名、作品ID：799）　</li>
<li><a href="../cards/000148/card789.html">吾輩は猫である</a>　（新字新仮名、作品ID：789）　</li>
</ol>

<h2><a name="sakuhin_list_2">作業中の作品</a></h2>
→<a href="list_inp148_1.html">作業中　作家別作品一覧：夏目 漱石</a>
<ol>
<li>客観描写と印象描写　（新字新仮名、作品ID：4384）　</li>
<li>三四郎　（旧字旧仮名、作品ID：46611）　</li>
<li>余が『草枕』　作家と著作（新字新仮名、作品ID：60840）　</li>
</ol>

<h2>関連サイト</h2>
"""


def test_parses_only_in_progress_section():
    """公開中の作品を作業中として拾わないこと(混ざると本文を取りに行ってしまう)。"""
    entries = person_page.parse_in_progress(HTML)

    ids = [e["aozora_work_id"] for e in entries]
    assert ids == ["004384", "046611", "060840"]
    assert "000799" not in ids, "公開中の作品が混入してはいけない"


def test_pads_work_id_to_six_digits():
    """作品IDはCSV・URLと同じ6桁ゼロ埋めに揃える(照合できなくなるため)。"""
    entries = person_page.parse_in_progress(HTML)
    assert entries[0]["aozora_work_id"] == "004384"


def test_extracts_title_and_orthography():
    entries = person_page.parse_in_progress(HTML)
    assert entries[0] == {
        "aozora_work_id": "004384",
        "title": "客観描写と印象描写",
        "orthography": "新字新仮名",
    }


def test_handles_title_without_separator_before_paren():
    """タイトルと括弧の間に全角空白が無い行がある(実ページの『余が「草枕」』)。"""
    entries = person_page.parse_in_progress(HTML)
    kusamakura = next(e for e in entries if e["aozora_work_id"] == "060840")
    assert kusamakura["title"] == "余が『草枕』　作家と著作"
    assert kusamakura["orthography"] == "新字新仮名"


def test_returns_empty_when_no_in_progress_section():
    """作業中セクションが無い作家でも落ちない。"""
    html = '<h2><a name="sakuhin_list_1">公開中の作品</a></h2><ol><li>x</li></ol>'
    assert person_page.parse_in_progress(html) == []
