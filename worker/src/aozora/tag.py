"""Document / Chunk Tagger(C-T4)。

正本仕様: docs/CORPUS_T1_SPEC.md §4・§11 / 上位指示 §9。

指示書の核心は「**小説中の登場人物の発言を漱石本人の思想として扱わない**」こと。
そのために4段で付ける:
  Pass1 決定的タグ(ここ) → Pass2 LLM分類 → Pass3 整合性検査 → Pass4 人手レビュー

⚠️ LLM分類だけで approved にしない(指示書§9 Pass4)。ここで出すのは候補値。
"""

import json
import re

from .. import config, llm

# Pass1(決定的タグ)だけを通した状態。取り込みはこれを付ける。
# ⚠️ 取り込み時点で TAGGER_VERSION を付けてはいけない。「分類済み」と見分けが
# 付かなくなり、Pass2 が永久に走らないチャンクができる。
PASS1_VERSION = "aozora_tag_v1_pass1"
# Pass1+Pass2 まで通した状態。retag が付ける。
# v3: character_id の割当を追加(作品の人物一覧から選ぶ。C-T6後続)
TAGGER_VERSION = "aozora_tag_v3"

# confidence がこれ未満なら人手確認へ回す
REVIEW_CONFIDENCE_THRESHOLD = 0.7

# 小説系。作者本人の直接発言として扱ってはいけない
FICTION_GENRES = ("novel", "short_story", "sketch")
_FICTION_GENRES = FICTION_GENRES  # 後方互換(既存の内部参照)

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

# 既知の長編小説。⚠️ NDCは欠落しうる(実データで三四郎の NDC が空 → genre=other →
# supporting_thought に落ち、小説本文210チャンクが author_direct/candidate になった)。
# 表題で確定させ、NDC任せにしない。人物辞書(characters.json)に載る作品は
# 必ずここに含めること(テストが検証する)。
_KNOWN_NOVELS = frozenset({
    "吾輩は猫である", "坊っちゃん", "草枕", "虞美人草", "三四郎", "それから",
    "門", "彼岸過迄", "行人", "こころ", "道草", "明暗", "二百十日", "野分", "坑夫",
})

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
    if title in _KNOWN_NOVELS:
        return "novel"
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


# ── Pass2: LLM分類(候補値を出すだけ。安全側の決定は覆せない) ──

SPEAKER_ROLES = frozenset({
    "author_direct", "narrator", "character", "quoted_person",
    "interviewer", "editor", "unknown",
})
# 小説では作者本人の発言はあり得ない。LLMがどう答えてもこの2値に閉じる
FICTION_SPEAKER_ROLES = frozenset({"narrator", "character"})
_FICTION_SPEAKER_ROLES = FICTION_SPEAKER_ROLES

CLAIM_TYPES = frozenset({
    "normative_claim", "descriptive_observation", "conceptual_distinction",
    "priority_claim", "prohibition", "exception", "autobiographical_report",
    "historical_report", "hypothetical_example", "quotation",
    "literary_analysis", "fictional_statement", "meta_commentary",
})
ASSERTION_STATUSES = frozenset({
    "asserted", "attributed", "hypothetical", "questioned",
    "ironic", "ambiguous", "rejected_by_author",
})

# 適格性の強さ。Pass1 の値が上限で、Pass2 は下げることしかできない
_ELIGIBILITY_RANK = {"candidate": 2, "support": 1, "excluded": 0}

# 1回のプロンプトに詰め込むチャンク数。多すぎると max_tokens で打ち切られ、
# その文書のPass2が丸ごと失われる(llm.ensure_not_truncated が例外を投げる)
PASS2_BATCH_SIZE = 20

_PASS2_SYSTEM = (
    "あなたは文献の分類器である。夏目漱石の著作のチャンクに、"
    "誰の発言か・どういう主張かのタグを付ける。"
    "最も重要な原則: **小説中の登場人物や語り手の言葉を、作者本人の主張として扱わない**。"
)

_PASS2_PROMPT = """文書種別: {document_genre}
コーパス上の役割: {corpus_role}

以下の各チャンクを分類せよ。

speaker_role（誰の言葉か）:
- author_direct: 著者本人が自分の考えとして述べている
- narrator: 小説の語り手（**小説にのみ現れる**）
- character: 小説の登場人物（**小説にのみ現れる**）
- quoted_person: 著者が引用した他者の発言
- interviewer / editor / unknown

⚠️ narrator と character は文書種別が novel / short_story / sketch の場合にだけ使う。
講演・評論・随筆では、語っているのは著者本人である。講演で著者が自分を演出していても
それは author_direct であって character ではない。他者の言葉を引いているなら
quoted_person を使う。

claim_type（主張の型）:
normative_claim（べきだ）/ descriptive_observation（事実の観察）/
conceptual_distinction（概念の区別）/ priority_claim（優先順位）/
prohibition（禁止）/ exception（例外）/ autobiographical_report（自分の経験）/
historical_report（歴史の記述）/ hypothetical_example（仮定の例）/
quotation（引用）/ literary_analysis（作品の分析）/
fictional_statement（作中の言明）/ meta_commentary（話の進め方への言及）

assertion_status（どう述べているか）:
asserted（主張している）/ attributed（他者に帰属）/ hypothetical（仮定）/
questioned（問いかけ）/ ironic（皮肉）/ ambiguous（曖昧）/
rejected_by_author（著者が否定している）

thought_eligibility（著者の思想の根拠に使えるか）:
candidate（主たる根拠になる）/ support（補助）/ excluded（使わない）

判断の指針:
- 鉤括弧があっても、作品名や語句の参照なら引用ではない
  （例:「坊ちゃん」でもご覧になったのでしょう）
- 地の文に埋め込まれた他者の発言は quotation として拾う
- 皮肉・仮定・問いかけを主張と取り違えない
- 迷う場合は confidence を下げる。無理に断定しない
{character_section}
チャンク:
{chunks}

出力形式（JSONのみ。全チャンクを必ず含める）:
{{"chunks": [{{"chunk_id": "...", "speaker_role": "...", "claim_type": "...",
"assertion_status": "...", "thought_eligibility": "...", "is_quotation": true,
"is_hypothetical": false, "is_ironic": false,
"character_id": "一覧のID または null", "confidence": 0.0,
"reason": "判断の根拠を一文で"}}]}}"""


def _valid(value, allowed, fallback=None):
    return value if value in allowed else fallback


def merge_pass2(
    pass1: dict, llm_result: dict, *, document_genre: str,
    character_ids: frozenset = frozenset(),
) -> dict:
    """Pass1 の決定的タグに Pass2 の分類を重ねる。

    ⚠️ Pass2 は**安全側の決定を覆せない**。具体的には:
      - 小説のチャンクを author_direct にできない（作者と作中人物の混同を防ぐ最後の砦）
      - thought_eligibility を Pass1 より上げられない（下げることはできる）
    LLMがどれだけ自信を持って別の値を返しても、ここで閉じる。

    未知の値は Pass1 の値へ戻し、確信度を 0 にしてレビューへ回す
    （握りつぶすと「分類済みの誤り」になり、後から見つけられない）。
    """
    merged = dict(pass1)
    invalid = False
    coerced: list[str] = []
    is_fiction = document_genre in _FICTION_GENRES

    # speaker_role
    role = _valid(llm_result.get("speaker_role"), SPEAKER_ROLES)
    if role is None and llm_result.get("speaker_role") is not None:
        invalid = True
    if role is not None:
        if is_fiction and role not in FICTION_SPEAKER_ROLES:
            # 小説で author_direct 等を返してきたら Pass1 の判定を保つ
            coerced.append(f"{role}→{pass1['speaker_role']}(小説)")
            role = pass1["speaker_role"]
        elif not is_fiction and role in FICTION_SPEAKER_ROLES:
            # 講演・評論に「登場人物」「語り手」はいない。実データで LLM が講演者を
            # character と分類する傾向が出たが、これは本文についての情報ではなく
            # カテゴリの誤り。Pass3 でレビューへ回すとキューが埋まって使えなくなる
            fixed = "quoted_person" if llm_result.get("is_quotation") else pass1["speaker_role"]
            coerced.append(f"{role}→{fixed}({document_genre}に登場人物はいない)")
            role = fixed
        merged["speaker_role"] = role

    # claim_type / assertion_status
    for key, allowed in (("claim_type", CLAIM_TYPES),
                         ("assertion_status", ASSERTION_STATUSES)):
        raw = llm_result.get(key)
        if raw is None:
            continue
        value = _valid(raw, allowed)
        if value is None:
            invalid = True
            continue
        merged[key] = value

    # thought / creative eligibility: Pass1 が上限
    for key in ("thought_eligibility", "creative_eligibility"):
        raw = llm_result.get(key)
        if raw not in _ELIGIBILITY_RANK:
            if raw is not None:
                invalid = True
            continue
        if _ELIGIBILITY_RANK[raw] < _ELIGIBILITY_RANK[pass1[key]]:
            merged[key] = raw

    # 小説は何があっても思想の根拠にしない
    if is_fiction:
        merged["thought_eligibility"] = "excluded"

    for flag in ("is_quotation", "is_hypothetical", "is_ironic"):
        if isinstance(llm_result.get(flag), bool):
            merged[flag] = llm_result[flag]

    # character_id: 辞書(作品の人物一覧)が語彙、LLMは割当だけ。
    # 一覧の外のID・人物発言でないチャンクへの付与は捨てる。捨てた時点で
    # 誤帰属は起きないので、レビュー行きにはしない(キューを溢れさせない)
    raw_cid = llm_result.get("character_id")
    cid = raw_cid if raw_cid in character_ids else None
    if merged["speaker_role"] != "character":
        cid = None
    if raw_cid and cid is None:
        coerced.append(f"character_id={raw_cid}を破棄(一覧に無い/人物の発言でない)")
    merged["character_id"] = cid

    confidence = llm_result.get("confidence")
    merged["tag_confidence"] = (
        0.0 if invalid or not isinstance(confidence, (int, float)) else float(confidence)
    )
    reason = llm_result.get("reason")
    # 直した事実は残す。黙って直すと後から追えない
    notes = [*coerced, *( ["分類結果に不正な値があったため確認が必要"] if invalid else [])]
    merged["classification_reason"] = "; ".join([*notes, *( [reason] if reason else [])]) or None
    return merged


def _character_section(characters: list[dict] | None) -> str:
    """作品の人物一覧をプロンプト片にする。一覧が無ければ空(IDの指示もしない)。"""
    if not characters:
        return ""
    lines = "\n".join(
        f"- {c['character_id']}: {'、'.join(c['names'])}" for c in characters
    )
    return (
        "\n## この作品の登場人物（character_id の一覧）\n"
        f"{lines}\n\n"
        "speaker_role が character のチャンクには、発言者を上の一覧から選び\n"
        "character_id に入れよ。一覧に無い人物・判断できない場合は null。\n"
        "一覧の外のIDを作らない。\n"
    )


def classify_chunks(
    chunks: list[dict], *, document_genre: str, corpus_role: str | None,
    characters: list[dict] | None = None,
    call_json=None, job_id: str | None = None,
) -> dict[str, dict]:
    """Pass2。チャンクをまとめてLLMに分類させ、chunk_id → タグ を返す。

    LLMが返さなかったチャンク・LLM呼び出しが落ちた場合は、Pass1 の結果を残して
    確信度0（= レビュー行き）にする。**分類できなかったものを分類済みにしない**。
    """
    call = call_json or llm.call_json
    character_ids = frozenset(c["character_id"] for c in characters or [])
    pass1_by_id = {
        ck["chunk_id"]: deterministic_chunk_tags(ck, document_genre=document_genre)
        for ck in chunks
    }
    result = {
        cid: merge_pass2({**tags}, {}, document_genre=document_genre,
                         character_ids=character_ids)
        for cid, tags in pass1_by_id.items()
    }

    for start in range(0, len(chunks), PASS2_BATCH_SIZE):
        batch = chunks[start : start + PASS2_BATCH_SIZE]
        payload = json.dumps(
            [{"chunk_id": ck["chunk_id"], "text": ck.get("text", "")} for ck in batch],
            ensure_ascii=False,
        )
        # プロンプトの組み立ては try の外。ここでの失敗はコードの不具合であって
        # 「LLMが答えられなかった」ではない。握りつぶすと全チャンクが静かに
        # 未分類のまま通ってしまう
        # 引数の組み立ては try の外。ここでの失敗はコードの不具合であって
        # 「LLMが答えられなかった」ではない。try の中に入れると、設定名の書き間違い
        # ひとつで全チャンクが静かに未分類のまま通ってしまう
        kwargs = {
            "agent_name": "aozora_tag_pass2",
            "model": config.MODEL_LIGHT_DISTILL,
            "system": _PASS2_SYSTEM,
            "prompt": _PASS2_PROMPT.format(
                document_genre=document_genre,
                corpus_role=corpus_role or "(未設定)",
                character_section=_character_section(characters),
                chunks=payload,
            ),
            "input_ref": batch[0]["chunk_id"],
            "job_id": job_id,
            "max_tokens": 8192,
        }
        try:
            response = call(**kwargs)
        except Exception:  # noqa: BLE001 - 分類できなかった事実を残して続ける
            continue

        for item in (response or {}).get("chunks") or []:
            cid = item.get("chunk_id")
            # 存在しない chunk_id は捨てる(他のチャンクへ混ぜない)
            if cid not in pass1_by_id:
                continue
            result[cid] = merge_pass2(
                {**pass1_by_id[cid]}, item, document_genre=document_genre,
                character_ids=character_ids,
            )

    return result


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
