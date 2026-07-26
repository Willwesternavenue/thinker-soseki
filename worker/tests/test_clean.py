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


# --- 複合姓の区切り入りラベルの認識(upstream 23b8aab) --------------------------------
# 漱石は姓名に区切り文字を含まないため実害は無いが、カタカナ複合姓の人物を将来
# 扱うときに「話者ラベルとして認識されず本文扱いになる死にラベル」が再発するため、
# 上流に揃えて _SPEAKER_RE の文字クラスに =＝・ を含めてある。


def test_label_with_equals_separator_is_recognized():
    """複合姓の区切り「=」(半角)入りラベルを話者ラベルとして認識する。"""
    line, speaker = normalize_speaker_line("サン=テグジュペリ:テスト。")
    assert speaker == "サン=テグジュペリ"


def test_label_with_fullwidth_equals_separator_is_recognized():
    """複合姓の区切り「＝」(全角)入りラベルを話者ラベルとして認識する。"""
    line, speaker = normalize_speaker_line("サン＝テグジュペリ:テスト。")
    assert speaker == "サン＝テグジュペリ"


def test_label_with_nakaguro_separator_is_recognized():
    """複合姓の区切り「・」(中黒)入りラベルを話者ラベルとして認識する。"""
    line, speaker = normalize_speaker_line("サン・テグジュペリ:テスト。")
    assert speaker == "サン・テグジュペリ"


def test_unknown_label_with_separator_chars_untouched():
    """=を含む未知ラベルは認識するが正規化はしない(誤爆防止)。"""
    line, speaker = normalize_speaker_line("A=B:結果はこうなる。")
    assert line == "A=B:結果はこうなる。"
    assert speaker == "A=B"


# --- 日本語の既存挙動(回帰防止) ------------------------------------------------------


def test_japanese_chapter_title_spaces_are_squeezed():
    """日本語タイトルの分かち書き空白は従来どおり詰める(回帰防止)。"""
    pages = ["第三章 武士道 と 死\n本文である。"]
    result = clean_pages(pages)
    assert "# 第三章 武士道と死" in result.text


def test_japanese_lines_still_joined_without_space():
    """日本語の行結合はスペースを入れない(回帰防止)。"""
    pages = ["これは途中で\n切れた文である。"]
    result = clean_pages(pages)
    assert "これは途中で切れた文である。" in result.text
    assert "これは途中で 切れた文である。" not in result.text


def test_japanese_long_colon_line_not_treated_as_speaker():
    """日本語のコロン付き長い行は話者行扱いせず結合する(長さ上限緩和の回帰防止)。"""
    pages = ["これは非常に重要な論点である:則天去私とは\n自然に従うことである。"]
    result = clean_pages(pages)
    assert "これは非常に重要な論点である:則天去私とは自然に従うことである。" in result.text


# --- 欧文原典(英訳)対応(upstream da6c660) ------------------------------------------


def test_english_lines_joined_with_space():
    """欧文の折り返し行はスペースを挟んで結合する(単語が融合しない)。"""
    pages = [
        "The heart is not a\n"
        "possession of the\n"
        "self. It is a burden."
    ]
    result = clean_pages(pages)
    assert "The heart is not a possession of the self. It is a burden." in result.text
    assert "apossession" not in result.text
    assert "theself" not in result.text


def test_english_period_terminates_line():
    """欧文はピリオドを文末と認識し、次行を詰めて繋げない。"""
    pages = ["I was born a fool.\nLoneliness is the price."]
    result = clean_pages(pages)
    assert "fool.Loneliness" not in result.text
    assert "I was born a fool." in result.text
    assert "Loneliness is the price." in result.text


def test_english_chapter_heading_not_fused_with_body():
    """欧文の章見出しが直後の本文と融合しない(再現ケース)。"""
    pages = ["Chapter 3 The Heart\nThe heart is not a\npossession."]
    result = clean_pages(pages)
    assert "# Chapter 3 The Heart" in result.text
    assert "The HeartThe heart" not in result.text


def test_english_closing_quote_after_period_terminates_line():
    """引用符閉じ(ピリオド直後)も文末として扱う。"""
    pages = ['He wrote: "loneliness is the price."\nThat is the thesis.']
    result = clean_pages(pages)
    assert 'price."That' not in result.text


def test_hyphenation_is_rejoined():
    """行末ハイフンによる分綴はハイフンごと除去して連結する。"""
    pages = ["It is a repre-\nsentation of the age."]
    result = clean_pages(pages)
    assert "a representation of the age." in result.text
    assert "repre-sentation" not in result.text
    assert "repre- sentation" not in result.text


def test_hyphen_before_capital_keeps_hyphen():
    """次行が大文字始まりの場合は複合語とみなしハイフンを残す(誤爆防止)。"""
    pages = ["He studied Anglo-\nJapanese relations."]
    result = clean_pages(pages)
    assert "He studied Anglo-Japanese relations." in result.text


def test_elision_at_line_end_joined_without_space():
    """行末がアポストロフィ(エリジオン)の場合はスペースを入れずに連結する。"""
    pages = ["il est le véhicule de l'\nêtre au monde."]
    result = clean_pages(pages)
    assert "de l'être au monde." in result.text
    assert "de l' être" not in result.text


def test_english_paragraph_with_speaker_label():
    """英訳対談の実文相当: 話者ラベル正規化と行結合が両立する。"""
    pages = [
        "Soseki: The heart is not a possession\n"
        "of the self. It is a burden we carry\n"
        "until the end of our days.\n"
        "Interviewer: What do you mean?"
    ]
    result = clean_pages(pages)
    assert "本人発言: The heart is not a possession" in result.text
    # 話者行をまたがない本文の折り返しはスペースを挟んで結合される
    assert (
        "of the self. It is a burden we carry until the end of our days."
        in result.text
    )
    assert "質問者: What do you mean?" in result.text
    assert "Soseki" not in result.text


def test_latin_self_label_normalized():
    line, speaker = normalize_speaker_line("Soseki: The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_latin_self_label_with_space_before_colon_normalized():
    """欧文組版はコロンの前に空白を置くことがある。"""
    line, speaker = normalize_speaker_line("Soseki : The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_latin_self_label_with_nbsp_before_colon_normalized():
    """実文書ではコロン前が U+00A0 の非改行スペースのことがある。"""
    line, speaker = normalize_speaker_line("Soseki : The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_latin_self_label_with_narrow_nbsp_before_colon_normalized():
    """実文書ではコロン前が U+202F の狭い非改行スペースのことがある。"""
    line, speaker = normalize_speaker_line("Soseki : The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_latin_self_label_uppercase_normalized():
    """全大文字の書き起こしも大文字小文字を無視して照合する。"""
    line, speaker = normalize_speaker_line("SOSEKI: The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_latin_self_label_with_macron_normalized():
    """ヘボン式の長音記号つき表記も本人発言に正規化する。"""
    line, speaker = normalize_speaker_line("Sōseki: The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_latin_self_label_with_initial_normalized():
    line, speaker = normalize_speaker_line("N. Soseki: The heart.")
    assert speaker == "本人発言"
    assert line == "本人発言: The heart."


def test_english_interviewer_label_normalized():
    """11文字の Interviewer も長さ上限に掛からず正規化される。"""
    line, speaker = normalize_speaker_line("Interviewer: What do you mean?")
    assert speaker == "質問者"
    assert line == "質問者: What do you mean?"


def test_english_moderator_label_normalized():
    line, speaker = normalize_speaker_line("Moderator: Let us continue.")
    assert speaker == "質問者"
    assert line == "質問者: Let us continue."


def test_abbreviated_question_label_normalized():
    line, speaker = normalize_speaker_line("Q.: What?")
    assert speaker == "質問者"
    assert line == "質問者: What?"


def test_unknown_latin_label_untouched():
    """未知の欧文ラベルは正規化せずそのまま返す(誤爆防止)。"""
    line, speaker = normalize_speaker_line("Ogai: about Maihime.")
    assert line == "Ogai: about Maihime."
    assert speaker == "Ogai"


def test_english_chapter_heading_keeps_word_spacing():
    """欧文見出しの語間スペースを潰さない。"""
    pages = ["Chapter 3 The Heart\n本文."]
    result = clean_pages(pages)
    assert "# Chapter 3 The Heart" in result.text


def test_french_chapter_heading_with_roman_numeral():
    pages = ["Chapitre III Le coeur\nLe coeur est."]
    result = clean_pages(pages)
    assert "# Chapitre III Le coeur" in result.text


def test_part_heading_marked():
    pages = ["Part I\nThe heart is a burden."]
    result = clean_pages(pages)
    assert "# Part I" in result.text


def test_standalone_introduction_heading_marked():
    pages = ["Introduction\nThe heart is a burden."]
    result = clean_pages(pages)
    assert "# Introduction" in result.text


def test_introduction_word_in_sentence_is_not_heading():
    """本文中の Introduction 始まりの文は見出しにしない(誤爆防止)。"""
    pages = ["Introduction of the concept is difficult."]
    result = clean_pages(pages)
    assert not result.text.startswith("#")
