"""Document / Chunk Tagger(C-T4)。

正本仕様: docs/CORPUS_T1_SPEC.md §4・§11 / 上位指示 §9。

指示書の核心は「**小説中の登場人物の発言を漱石本人の思想として扱わない**」こと。
そのために4段で付ける:
  Pass1 決定的タグ(ここ) → Pass2 LLM分類 → Pass3 整合性検査 → Pass4 人手レビュー

⚠️ LLM分類だけで approved にしない(指示書§9 Pass4)。ここで出すのは候補値。
"""

import re

TAGGER_VERSION = "aozora_tag_v1"

# confidence がこれ未満なら人手確認へ回す
REVIEW_CONFIDENCE_THRESHOLD = 0.7

# 小説系。作者本人の直接発言として扱ってはいけない
_FICTION_GENRES = ("novel", "short_story", "sketch")

# タイトルから genre を推定する手掛かり。NDCだけでは決まらないため併用する(指示書§7.2)
_TITLE_HINTS = (
    ("序", "preface"),
    ("跋", "afterword"),
    ("書簡", "letter"),
    ("宛", "letter"),
    ("談話", "interview"),
    ("文学論", "literary_theory"),
    ("文学評論", "literary_theory"),
    ("批評", "criticism"),
)

# 講演として知られる作品(底本・初出から確認済み。Phase A のコア資料を含む)
_KNOWN_LECTURES = frozenset({
    "私の個人主義", "現代日本の開化", "中味と形式", "道楽と職業",
    "文芸の哲学的基礎", "文芸と道徳", "模倣と独立", "創作家の態度",
    "教育と文芸", "学者と名誉",
})
# 短編として扱う作品(長編小説と区別する)
_KNOWN_SHORT_STORIES = frozenset({"夢十夜", "永日小品", "琴のそら音", "一夜", "薤露行"})

# NDC の大分類 → genre の既定値
_NDC_DEFAULT = {
    "913": "novel",
    "914": "essay",
    "915": "travelogue",
    "916": "memoir",
    "901": "literary_theory",
    "911": "other",
}

# genre → corpus_role の既定値(最終確定は人手)
_GENRE_TO_ROLE = {
    "lecture": "core_thought",
    "essay": "supporting_thought",
    "criticism": "creative_grammar",
    "literary_theory": "creative_grammar",
    "preface": "creative_grammar",
    "afterword": "creative_grammar",
    "novel": "narrative_reference",
    "short_story": "narrative_reference",
    "sketch": "narrative_reference",
    "memoir": "biographical_context",
    "travelogue": "biographical_context",
    "letter": "supporting_thought",
    "interview": "supporting_thought",
}

# ブロック引用の判定(C-T4 の実データ検証で確定)。
# ⚠️ 実データの鉤括弧は大半が作品名・語句の参照(「坊ちゃん」「眉のような月」)で、
# 他者の引用ではない。「〜と云った」型を正規表現で狙うと作品名参照を誤検出する。
# そこで Pass1 では「引用が段落の大半を占める長いブロック」だけを機械的に拾い、
# 地の文に埋め込まれた引用の判断は Pass2(LLM)へ委ねる。
_BRACKETED_RE = re.compile(r"[「『]([^「」『』]+)[」』]")
# これ未満の鉤括弧は語句・作品名の参照とみなす
_QUOTATION_MIN_CHARS = 30
# 段落に占める引用の割合がこれ以上ならブロック引用とみなす
_QUOTATION_MIN_RATIO = 0.6


def infer_document_genre(*, title: str, ndc: str | None = None) -> str:
    """文書種別を推定する(Pass1)。

    ⚠️ **NDCだけで決めない**(指示書§7.2)。実データでも NDC 914(随筆)に
    講演・評論・随筆が同居しており、47件と最多を占める。
    タイトル・既知の講演一覧を併用する。
    """
    if title in _KNOWN_LECTURES:
        return "lecture"
    if title in _KNOWN_SHORT_STORIES:
        return "short_story"
    for hint, genre in _TITLE_HINTS:
        if hint in title:
            return genre

    code = re.search(r"\d{3}", ndc or "")
    return _NDC_DEFAULT.get(code.group(0) if code else "", "other")


def default_corpus_role(genre: str) -> str:
    """genre から corpus_role の既定値を出す。最終確定は人手(Pass4)。"""
    return _GENRE_TO_ROLE.get(genre, "supporting_thought")


def default_authority_level(genre: str) -> str:
    """小説を author_direct にしない。作者と作中人物の混同を防ぐ最初の関門。"""
    if genre in _FICTION_GENRES:
        return "fictional_indirect"
    if genre in ("memoir", "travelogue"):
        return "author_contextual"
    return "author_direct"


def _looks_like_quotation(text: str) -> bool:
    """段落の大半を占める長いブロック引用か。

    語句・作品名の参照(短い鉤括弧)や、地の文に埋め込まれた引用は False を返す。
    後者は Pass2(LLM)が判断する。
    """
    body = text.strip().strip("　 ")
    if not body:
        return False
    quoted = "".join(_BRACKETED_RE.findall(body))
    if len(quoted) < _QUOTATION_MIN_CHARS:
        return False
    return len(quoted) / len(body) >= _QUOTATION_MIN_RATIO


def deterministic_chunk_tags(chunk: dict, *, document_genre: str) -> dict:
    """チャンク単位の決定的タグ(Pass1)。

    小説では chunk_type(dialogue/narration)から speaker_role を決め、
    **どちらも思想の根拠にはしない**(登場人物も語り手も作者本人ではない)。
    """
    text = chunk.get("text", "")

    if document_genre in _FICTION_GENRES:
        speaker_role = (
            "character" if chunk.get("chunk_type") == "dialogue" else "narrator"
        )
        return {
            "speaker_role": speaker_role,
            "is_quotation": False,
            # 小説は作者の思想の直接根拠にしない(指示書§6 重要な禁止事項)
            "thought_eligibility": "excluded",
            # 創作の参照には使える(作風・物語構成の材料)
            "creative_eligibility": "candidate",
            "claim_type": "fictional_statement",
            "assertion_status": "attributed",
        }

    if _looks_like_quotation(text):
        return {
            "speaker_role": "quoted_person",
            "is_quotation": True,
            # 引用は作者の主張そのものではない。根拠の補助に留める
            "thought_eligibility": "support",
            "creative_eligibility": "support",
            "claim_type": "quotation",
            "assertion_status": "attributed",
        }

    return {
        "speaker_role": "author_direct",
        "is_quotation": False,
        "thought_eligibility": "candidate",
        "creative_eligibility": "support",
        "claim_type": None,  # Pass2(LLM)で決める
        "assertion_status": "asserted",
    }


def check_consistency(
    tags: dict, *, document_genre: str, corpus_role: str | None = None
) -> list[str]:
    """機械的整合性検査(Pass3。指示書§9)。

    LLMの分類結果と決定的metadataが矛盾していないかを見る。
    """
    issues: list[str] = []
    speaker_role = tags.get("speaker_role")
    eligibility = tags.get("thought_eligibility")

    if speaker_role == "character" and document_genre not in _FICTION_GENRES:
        issues.append(
            f"speaker_role=character だが document_genre={document_genre}(小説ではない)"
        )
    if tags.get("is_quotation") and eligibility == "candidate":
        issues.append("引用(is_quotation)なのに thought_eligibility=candidate になっている")
    if speaker_role == "author_direct" and document_genre in _FICTION_GENRES:
        issues.append(
            f"author_direct だが document_genre={document_genre}(小説の語り手・人物のはず)"
        )
    if corpus_role == "core_thought" and speaker_role in ("character", "narrator"):
        issues.append(
            f"corpus_role=core_thought に小説由来のチャンク"
            f"(speaker_role={speaker_role})が混入している"
        )
    if corpus_role == "core_thought" and document_genre in _FICTION_GENRES:
        issues.append(f"corpus_role=core_thought だが document_genre={document_genre}")
    return issues


def needs_review(tags: dict, issues: list[str]) -> bool:
    """人手レビューへ回すか(Pass4。指示書§9)。

    確信度が低い / 皮肉 / 仮定例 / 作者と人物の区別が曖昧 / 整合性違反。
    """
    if issues:
        return True
    if (tags.get("tag_confidence") or 0) < REVIEW_CONFIDENCE_THRESHOLD:
        return True
    if tags.get("assertion_status") in ("ironic", "ambiguous", "questioned"):
        return True
    if tags.get("is_hypothetical") or tags.get("is_ironic"):
        return True
    if tags.get("speaker_role") == "unknown":
        return True
    return False
