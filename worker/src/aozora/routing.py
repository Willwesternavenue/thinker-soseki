"""検索ルーティング(C-T7)。

正本仕様: docs/CORPUS_T1_SPEC.md §5・§6 / 上位指示 §10。

論理Index は**物理分割しない**。corpus_role / speaker_role の絞り込みを
プリセットとして持ち、拡張したRPCへ渡す(既存RPCは default 付きの追加
パラメータなので、思想モードの既存呼び出しは無変更で動く)。

ルーティングの目的は、指示書§18 の次を守ること:
- 小説人物の発言を作者思想へ自動昇格させない
- 思想チャンクを登場人物の台詞へそのまま注入しない
- 文体の似ている回答を思想的一致とみなさない
"""

# 論理Index(8種)。物理的なCollectionではなく、絞り込みのプリセット。
# generation_input=False のものはカード生成・回答の入力にしない(指示書§3.8)。
INDEXES: dict[str, dict] = {
    "author_thought_core": {
        "corpus_roles": ["core_thought"],
        "speaker_roles": ["author_direct"],
        "generation_input": True,
    },
    "author_thought_support": {
        "corpus_roles": ["supporting_thought"],
        "speaker_roles": ["author_direct"],
        "generation_input": True,
    },
    "creative_grammar": {
        "corpus_roles": ["creative_grammar"],
        "speaker_roles": None,
        "generation_input": True,
    },
    "character_judgment": {
        "corpus_roles": ["character_judgment"],
        "speaker_roles": ["character"],
        "generation_input": True,
    },
    "narrative_reference": {
        "corpus_roles": ["narrative_reference"],
        "speaker_roles": None,
        "generation_input": True,
    },
    "style_reference": {
        "corpus_roles": ["style_reference"],
        "speaker_roles": None,
        "generation_input": True,
    },
    "biographical_context": {
        "corpus_roles": ["biographical_context"],
        "speaker_roles": None,
        "generation_input": True,
    },
    "validation_only": {
        "corpus_roles": ["validation_only"],
        "speaker_roles": None,
        # カード生成の入力にはせず、作ったあとの検証にだけ使う
        "generation_input": False,
    },
}


def _step(index: str, **flags) -> dict:
    return {
        "index": index,
        # 小説由来を出すときは「作者本人の発言ではない」と明示する(指示書§10.1)
        "requires_attribution_notice": flags.get("attribution", False),
        # 思想を創作へ持ち込むのは Bridge Rule を介する場合のみ(指示書§12.2)
        "requires_bridge_rule": flags.get("bridge", False),
        # 比較対象としてのみ使う(主根拠にしない)
        "comparison_only": flags.get("comparison", False),
        **INDEXES[index],
    }


# 質問種別ごとの検索順(仕様§6)
ROUTES: dict[str, list[dict]] = {
    # 思想: 本人の直接発言から。小説は比較・補助として最後に、明示付きで
    "thought": [
        _step("author_thought_core"),
        _step("author_thought_support"),
        _step("creative_grammar"),
        _step("narrative_reference", attribution=True),
    ],
    # 創作: 作風の論から。思想は Bridge Rule を介する場合のみ
    "creative": [
        _step("creative_grammar"),
        _step("narrative_reference"),
        _step("style_reference"),
        _step("author_thought_core", bridge=True),
    ],
    # 人物: 作中人物の判断から。作者思想は比較対象としてのみ
    "character": [
        _step("character_judgment", attribution=True),
        _step("narrative_reference", attribution=True),
        _step("author_thought_core", comparison=True),
    ],
}

# 既知の登場人物。人物質問の判定に使う。
# ⚠️ 名前が挙がらない質問を人物質問にしない(誤判定すると作者の思想を主根拠から外す)。
KNOWN_CHARACTERS = {
    "代助": "daisuke",        # それから
    "美禰子": "mineko",       # 三四郎
    "三四郎": "sanshiro",
    "宗助": "sosuke",         # 門
    "健三": "kenzo",          # 道草
    "津田": "tsuda",          # 明暗
    "お延": "onobu",          # 明暗
    "苦沙弥": "kushami",      # 吾輩は猫である
    "迷亭": "meitei",
    "寒月": "kangetsu",
}

# 創作依頼の手掛かり
_CREATIVE_HINTS = ("書け", "書いて", "作れ", "作って", "生成して", "第十一夜", "新作")


def route_for(query_kind: str) -> list[dict]:
    """質問種別に対応する検索順を返す。未知の種別は思想ルートに倒す。"""
    return ROUTES.get(query_kind, ROUTES["thought"])


def detect_character(query: str) -> str | None:
    """質問に含まれる既知の登場人物を返す。"""
    for name, character_id in KNOWN_CHARACTERS.items():
        if name in query:
            return character_id
    return None


def detect_kind(query: str) -> str:
    """質問種別を判定する(既存 QueryKind への追加ぶん)。

    人物質問を思想質問と区別するのが目的。作者と作中人物の混同を防ぐ。
    """
    if any(hint in query for hint in _CREATIVE_HINTS):
        return "creative"
    if detect_character(query):
        return "character"
    return "thought"
