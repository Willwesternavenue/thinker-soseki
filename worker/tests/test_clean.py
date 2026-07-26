"""話者正規化・verbatim導出・整形のテスト(仕様6.2)。"""

from src.steps.clean import clean_pages, normalize_speaker_line, page_for_offset


def test_self_label_is_normalized():
    """本人ラベル「漱石:」はそのまま残さず「本人発言:」に正規化する(仕様6.2重要ルール)。"""
    line, speaker = normalize_speaker_line("漱石:則天去私とは自然に従うことだ。")
    assert line.startswith("本人発言: ")
    assert "漱石" not in line
    assert speaker == "本人発言"


def test_interviewer_label_is_normalized():
    line, speaker = normalize_speaker_line("聞き手:絶対負とは何ですか?")
    assert line.startswith("質問者: ")
    assert speaker == "質問者"


def test_half_width_colon_also_normalized():
    line, speaker = normalize_speaker_line("漱石: これが答えだ。")
    assert speaker == "本人発言"


def test_full_width_colon_normalized():
    """ベンダー納品書き起こしは全角コロン「：」を使う。半角と同様に正規化する。"""
    line, speaker = normalize_speaker_line("漱石：これが答えだ。")
    assert speaker == "本人発言"
    assert line.startswith("本人発言: ")


def test_shikaisha_label_is_normalized():
    """司会者(役割表記の聞き手)も質問者に正規化する。"""
    line, speaker = normalize_speaker_line("司会者：菌について伺えますか。")
    assert speaker == "質問者"
    assert line.startswith("質問者: ")


def test_non_speaker_line_untouched():
    line, speaker = normalize_speaker_line("これはただの本文である。")
    assert speaker is None
    assert line == "これはただの本文である。"


def test_clean_pages_keeps_page_offsets():
    pages = ["1ページ目の本文。", "2ページ目の本文。"]
    result = clean_pages(pages)
    assert "社長" not in result.text
    assert len(result.page_offsets) == 2
    # 2ページ目の文字位置からページ番号を引ける
    second_page_start = result.page_offsets[1][0]
    assert page_for_offset(result.page_offsets, second_page_start) == 2
    assert page_for_offset(result.page_offsets, 0) == 1


def test_page_number_only_lines_removed():
    pages = ["本文の一行目。\n- 82 -\n本文の二行目。"]
    result = clean_pages(pages)
    assert "82" not in result.text


def test_broken_lines_joined():
    """文末記号で終わらないPDF由来の改行は結合される。"""
    pages = ["これは途中で\n切れた文である。"]
    result = clean_pages(pages)
    assert "これは途中で切れた文である。" in result.text


def test_chapter_heading_marked():
    pages = ["第三章 武士道と死\n本文である。"]
    result = clean_pages(pages)
    assert "# 第三章 武士道と死" in result.text
