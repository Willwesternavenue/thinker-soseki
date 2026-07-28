"""Document / Chunk Tagger のテスト(C-T4 / 仕様 docs/CORPUS_T1_SPEC.md §4・§11)。

指示書の核心: 小説中の登場人物の発言を漱石本人の思想として扱わない。
Pass1(決定的) → Pass2(LLM) → Pass3(整合性検査) → Pass4(レビューキュー)。
"""

import pytest

from src.aozora import tag


# ── Pass1: 決定的タグ(ルールベース。指示書§9 Pass1) ──


@pytest.mark.parametrize(
    "title,ndc,expected",
    [
        ("私の個人主義", "NDC 914", "lecture"),      # 講演として知られる
        ("夢十夜", "NDC 913", "short_story"),
        ("三四郎", "NDC 913", "novel"),
        ("高浜虚子著『鶏頭』序", "NDC 914", "preface"),
        ("鈴木三重吉宛書簡", "NDC 915", "letter"),
    ],
)
def test_infer_document_genre_uses_more_than_ndc(title, ndc, expected):
    """NDCだけでgenreを決めない(実データでもNDC914に講演・評論・随筆が同居する)。"""
    assert tag.infer_document_genre(title=title, ndc=ndc) == expected


def test_infer_genre_falls_back_to_essay_for_unknown_ndc914():
    """判断材料が無ければ随筆に倒す(NDC 914 の多数派)。"""
    assert tag.infer_document_genre(title="正体不明の文章", ndc="NDC 914") == "essay"


@pytest.mark.parametrize(
    "genre,expected_role",
    [
        ("lecture", "core_thought"),
        ("criticism", "creative_grammar"),
        ("literary_theory", "creative_grammar"),
        ("novel", "narrative_reference"),
        ("short_story", "narrative_reference"),
        ("memoir", "biographical_context"),
        ("letter", "supporting_thought"),
    ],
)
def test_default_corpus_role_from_genre(genre, expected_role):
    """genreから corpus_role の既定値を出す(最終確定は人手)。"""
    assert tag.default_corpus_role(genre) == expected_role


@pytest.mark.parametrize(
    "genre,expected",
    [
        ("lecture", "author_direct"),
        ("essay", "author_direct"),
        ("novel", "fictional_indirect"),      # 小説は本人の直接発言ではない
        ("short_story", "fictional_indirect"),
        ("letter", "author_direct"),
    ],
)
def test_authority_level_marks_fiction_as_indirect(genre, expected):
    """小説を author_direct にしない(作者と作中人物の混同を防ぐ最初の関門)。"""
    assert tag.default_authority_level(genre) == expected


# ── Pass1: チャンク単位の決定的タグ ──


def test_dialogue_chunk_in_novel_is_character_speech():
    """小説の会話文チャンクは登場人物の発言として扱う。"""
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "dialogue", "text": "「死んだら、埋めて下さい」"},
        document_genre="short_story",
    )

    assert result["speaker_role"] == "character"
    assert result["thought_eligibility"] == "excluded", "人物発言を思想の根拠にしない"
    assert result["creative_eligibility"] == "candidate", "創作の参照には使える"


def test_narration_chunk_in_novel_is_narrator_not_author():
    """小説の地の文は語り手であって作者本人ではない。"""
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "narration", "text": "こんな夢を見た。"},
        document_genre="short_story",
    )

    assert result["speaker_role"] == "narrator"
    assert result["thought_eligibility"] == "excluded"


def test_lecture_chunk_is_author_direct():
    """講演の本文は作者の直接発言。思想カードの候補になる。"""
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "body", "text": "西洋の開化は内発的である。"},
        document_genre="lecture",
    )

    assert result["speaker_role"] == "author_direct"
    assert result["thought_eligibility"] == "candidate"


def test_quotation_in_lecture_is_not_author_direct():
    """講演中の他者引用を作者の主張にしない(指示書§14.3)。

    ⚠️ 判定は「〜と云った」という言い回しではなく、引用が段落の大半を占めるかで行う。
    実データでは鉤括弧の大半が作品名参照であり、言い回しでは誤検出するため
    (下の実データ回帰テスト群を参照)。
    """
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "body",
         "text": "「人生は棒ほど願って針ほど叶うと申しますが、これは古人の言い残した言葉であります」"},
        document_genre="lecture",
    )

    assert result["is_quotation"] is True
    assert result["speaker_role"] == "quoted_person"
    assert result["thought_eligibility"] == "support", "引用は根拠の補助に留める"


# ── Pass3: 機械的整合性検査(指示書§9 Pass3) ──


def test_consistency_flags_character_speech_in_lecture():
    """speaker_role=character なのに document_genre=lecture は矛盾。"""
    issues = tag.check_consistency(
        {"speaker_role": "character", "thought_eligibility": "candidate"},
        document_genre="lecture",
    )
    assert any("character" in i for i in issues)


def test_consistency_flags_quotation_used_as_core_thought():
    """引用なのに thought_eligibility=candidate は矛盾。"""
    issues = tag.check_consistency(
        {"speaker_role": "author_direct", "thought_eligibility": "candidate",
         "is_quotation": True},
        document_genre="lecture",
    )
    assert any("引用" in i for i in issues)


def test_consistency_flags_fiction_in_core_thought():
    """小説由来なのに core_thought は矛盾(指示書§14.6 の混入率指標)。"""
    issues = tag.check_consistency(
        {"speaker_role": "character", "thought_eligibility": "candidate"},
        document_genre="novel",
        corpus_role="core_thought",
    )
    assert issues


def test_consistency_passes_for_valid_combination():
    assert tag.check_consistency(
        {"speaker_role": "author_direct", "thought_eligibility": "candidate",
         "is_quotation": False},
        document_genre="lecture",
        corpus_role="core_thought",
    ) == []


# ── Pass4: レビューキュー(指示書§9 Pass4) ──


def test_needs_review_when_confidence_is_low():
    assert tag.needs_review({"tag_confidence": 0.4}, []) is True


def test_needs_review_when_ironic_or_hypothetical():
    """皮肉・仮定例は人手確認へ回す。"""
    assert tag.needs_review({"tag_confidence": 0.95, "assertion_status": "ironic"}, []) is True
    assert tag.needs_review({"tag_confidence": 0.95, "is_hypothetical": True}, []) is True


def test_needs_review_when_consistency_issue_found():
    assert tag.needs_review({"tag_confidence": 0.99}, ["矛盾あり"]) is True


def test_no_review_needed_for_clean_high_confidence_tag():
    result = tag.needs_review(
        {"tag_confidence": 0.95, "assertion_status": "asserted",
         "speaker_role": "author_direct"},
        [],
    )
    assert result is False


# ── 引用判定の実データ回帰テスト(C-T4 検証で判明) ──
#
# 実データの鉤括弧は大半が「作品名・語句の参照」で、他者の引用ではない。
# 正規表現で「〜と云った」型を狙うと作品名参照を誤検出するため、
# Pass1では「長いブロック引用」だけを拾い、判断が要るものはPass2(LLM)へ回す。


@pytest.mark.parametrize(
    "text",
    [
        "　おおかた私の書いた「坊ちゃん」でもご覧になったのでしょう。",
        "　「現代日本の開化」と云う題で御話を致します。",
        "　「眉のような月」と云う叙述を考えてみると分ります。",
    ],
)
def test_short_bracket_is_not_treated_as_quotation(text):
    """作品名・語句の参照を引用と誤判定しない(実データで頻出する)。"""
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "body", "text": text}, document_genre="lecture"
    )
    assert result["is_quotation"] is False
    assert result["speaker_role"] == "author_direct"


def test_long_block_quotation_is_detected():
    """段落の大半を占める長い鉤括弧はブロック引用として扱う(実データの例)。"""
    text = (
        "「真正の象徴は明らかにまた直接に、無限をあらわしている。"
        "無限は象徴によって有限と合体する。眼に見えるようになる。"
        "あたかも達せらるるかのごとくに見える」"
    )
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "body", "text": text}, document_genre="lecture"
    )

    assert result["is_quotation"] is True
    assert result["speaker_role"] == "quoted_person"
    assert result["thought_eligibility"] == "support", "引用を作者の主張にしない"


def test_paragraph_with_incidental_long_quote_is_left_to_llm():
    """地の文が主で引用が一部の段落は Pass1 で断定せず author_direct のままにする。"""
    text = (
        "　私はこの点について長く考えてきたのであるが、ある人は"
        "「真正の象徴は無限をあらわしている」と述べている。"
        "しかし私はその見方には必ずしも同意しないのである。さらに言えば別の見方もある。"
    )
    result = tag.deterministic_chunk_tags(
        {"chunk_type": "body", "text": text}, document_genre="lecture"
    )
    assert result["is_quotation"] is False


# ── 既知の長編小説(NDC欠落への防御) ──


def test_known_novel_with_missing_ndc_is_still_a_novel():
    """NDCが空でも既知の小説は novel と判定する。

    実データで三四郎の NDC が空で genre=other → supporting_thought に落ち、
    **小説本文210チャンクが author_direct/candidate になった**(Phase C 実測)。
    NDCは欠落しうるので、既知の長編は表題で確定させる。
    """
    assert tag.infer_document_genre(title="三四郎", ndc=None) == "novel"
    assert tag.infer_document_genre(title="三四郎", ndc="") == "novel"


def test_all_dictionary_works_have_a_fiction_genre():
    """人物辞書に載る作品は必ず小説として判定される。

    辞書に人物を足す = その作品の発言を character として扱う前提を置くこと。
    genre 判定が小説でないと、その前提ごと崩れる(author_direct になる)。
    """
    from src.aozora import characters

    works = {entry["work"] for entry in characters._DATA.values()}
    for work in works:
        genre = tag.infer_document_genre(title=work, ndc=None)
        assert genre in tag.FICTION_GENRES, f"{work} が {genre} になっている"
