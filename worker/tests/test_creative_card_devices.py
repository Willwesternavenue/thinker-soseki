"""カードの装置/作風判定(移植テスト)と、続編生成の除外規則。

最重要は2つ。
1. 判定はカード本体に書かない — 人手承認済みの資産を再判定で書き換えない
2. brief の constraints が明示要求した装置は除外を免除する。続編は非対称な
   借用で、枠の装置(冒頭の定型句)は継承し、内側の装置(全知の子供)は禁じる

LLM呼び出しは注入して差し替える(実APIを叩かない)。
"""

from src.creative import card_devices as cd


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {}


_CARD = {
    "card_id": "cc_x", "card_type": "narrative",
    "title": "反復する事象を数えさせて時間の異常な長さを体感させる",
    "summary": "…", "positive_patterns": ["赤い日を数える"],
}


# ── 移植テスト ──


def test_classifies_a_portable_card():
    llm = FakeLLM({"verdict": "portable", "reason": "どの作家でも成立する",
                   "named_images": []})

    row = cd.classify_card(_CARD, call_json=llm)

    assert row["verdict"] == cd.PORTABLE
    assert row["card_id"] == "cc_x"
    assert "別の作家" in llm.calls[0]["prompt"], "移植テストとして聞く"


def test_unknown_verdict_falls_back_to_device_bound():
    """作風を1枚落とす方が、装置を1枚通すより安い。"""
    llm = FakeLLM({"verdict": "たぶん作風", "reason": "…"})

    assert cd.classify_card(_CARD, call_json=llm)["verdict"] == cd.DEVICE_BOUND


def test_keeps_the_reason_for_human_audit():
    llm = FakeLLM({"verdict": "device_bound", "reason": "夢十夜の像を運ぶ",
                   "named_images": ["赤い日", "数える行為"]})

    row = cd.classify_card(_CARD, call_json=llm)

    assert row["reason"] == "夢十夜の像を運ぶ"
    assert row["named_images"] == ["赤い日", "数える行為"]


# ── 二段フィルタ ──


_CATALOG = {
    "meta": {"work_title": "夢十夜", "catalog_version": "v2"},
    "chapters": [
        {"chapter_title": "第一夜", "devices": [
            {"device_id": "d_kei", "role": "central", "chapter_title": "第一夜",
             "name": "偽の計数と真の徴による百年成就の反転", "description": "…",
             "evidence_chunk_ids": ["C01_006", "C01_007"]},
            {"device_id": "d_hitomi", "role": "incidental", "chapter_title": "第一夜",
             "name": "瞳に映る像", "description": "…",
             "evidence_chunk_ids": ["C01_001"]},
        ]},
    ],
}


def test_stage1_is_exhaustive_over_central_devices():
    """段1は総当たり。根拠チャンクで絞ると収束先が別章のカードを取り逃がす。"""
    assert [d["device_id"] for d in cd.all_central_devices(_CATALOG)] == ["d_kei"]


def test_chunk_overlap_is_demoted_to_an_annotation():
    """由来章は注記として残すが、候補の絞り込みには使わない。"""
    card = {"card_id": "cc_a", "evidence_chunk_ids": ["C01_006"]}
    unrelated = {"card_id": "cc_b", "evidence_chunk_ids": ["C99_000"]}

    assert cd.origin_chapters(card, _CATALOG) == ["第一夜"]
    assert cd.origin_chapters(unrelated, _CATALOG) == []


def test_generalization_counts_as_device_bound():
    """「数えさせる」は言い換えではなく一成分の一般化。ここを除外に含める。"""
    llm = FakeLLM({"verdict": "generalization", "reason": "赤い日を数える場面へ収束する"})

    judgment = cd.judge_reconvergence(
        {"card_id": "cc_a", "title": "反復する事象を数えさせる"},
        _CATALOG["chapters"][0]["devices"][0], work_title="夢十夜", call_json=llm,
    )

    assert judgment["verdict"] == cd.GENERALIZATION
    assert cd.GENERALIZATION in cd.EXCLUDING_VERDICTS
    assert "収束" in llm.calls[0]["prompt"], "抽象度でなく収束先を問う"


def test_unrelated_judgment_keeps_the_card():
    llm = FakeLLM({"verdict": "unrelated", "reason": "この装置とは無関係"})

    judgment = cd.judge_reconvergence(
        {"card_id": "cc_b", "title": "語り手を「自分」に固定する"},
        _CATALOG["chapters"][0]["devices"][0], work_title="夢十夜", call_json=llm,
    )

    assert judgment["verdict"] not in cd.EXCLUDING_VERDICTS


def test_unknown_reconvergence_verdict_falls_back_to_excluding():
    llm = FakeLLM({"verdict": "よくわからない"})

    judgment = cd.judge_reconvergence(
        {"card_id": "cc_c", "title": "…"},
        _CATALOG["chapters"][0]["devices"][0], work_title="夢十夜", call_json=llm,
    )

    assert judgment["verdict"] in cd.EXCLUDING_VERDICTS


# ── 極性の門（否定形は装置を運べない） ──


def test_polarity_gate_is_not_syntactic():
    """否定語の有無で判定しない。judge に極性を問う形にしてある。"""
    llm = FakeLLM({"polarity": "forbids_content", "reason": "何も足さない"})

    got = cd.judge_polarity({"card_id": "cc_x", "title": "説明を与えずに終える"},
                            call_json=llm)

    assert got["polarity"] == cd.FORBIDS_CONTENT
    assert "否定語があるかで判定しない" in llm.calls[0]["prompt"]


def test_unknown_polarity_goes_through_the_gate():
    """不明なら装置判定にかける側へ倒す（門を素通りさせない）。"""
    llm = FakeLLM({"polarity": "なんとも"})

    assert cd.judge_polarity({"card_id": "x", "title": "…"},
                             call_json=llm)["polarity"] == cd.REQUIRES_CONTENT


# ── brief 免除の意味照合 ──


def test_semantic_match_exempts_a_reworded_constraint():
    """字面が違う同義の制約でも免除する（「説明で締めず情景で閉じる」など）。"""
    classification = {"cards": [
        _row("cc_end", cd.DEVICE_BOUND, "説明を与えずに宙吊りのまま終える"),
    ]}
    llm = FakeLLM({"requested": True, "reason": "同じことを求めている"})

    result = cd.resolve_exclusions(
        classification, brief_constraints=["説明で締めず情景で閉じる"], call_json=llm
    )

    assert result["excluded_card_ids"] == []
    assert result["exempted"][0]["exempted_by"] == "説明で締めず情景で閉じる"


def test_semantic_match_does_not_exempt_an_unrelated_constraint():
    classification = {"cards": [_row("cc_count", cd.DEVICE_BOUND, "数えさせる")]}
    llm = FakeLLM({"requested": False, "reason": "無関係"})

    result = cd.resolve_exclusions(
        classification, brief_constraints=["説明で締めず情景で閉じる"], call_json=llm
    )

    assert result["excluded_card_ids"] == ["cc_count"]


# ── 除外規則と brief 免除 ──


def _row(card_id, verdict, title="", images=()):
    return {"card_id": card_id, "verdict": verdict, "title": title,
            "reason": "", "named_images": list(images)}


def test_device_bound_cards_are_excluded():
    classification = {"cards": [
        _row("cc_dev", cd.DEVICE_BOUND, "反復する事象を数えさせる"),
        _row("cc_style", cd.PORTABLE, "距離のある描写"),
    ]}

    result = cd.resolve_exclusions(classification, brief_constraints=[])

    assert result["excluded_card_ids"] == ["cc_dev"]
    assert [r["card_id"] for r in result["kept"]] == ["cc_style"]


def test_brief_constraint_exempts_a_device_card():
    """「こんな夢を見た」は装置だが、依頼が明示要求しているので残す。"""
    classification = {"cards": [
        _row("cc_open", cd.DEVICE_BOUND, "「こんな夢を見た」という定型句で語りを開始する"),
    ]}

    result = cd.resolve_exclusions(
        classification, brief_constraints=["「こんな夢を見た」で始める"]
    )

    assert result["excluded_card_ids"] == []
    assert result["exempted"][0]["exempted_by"] == "「こんな夢を見た」で始める"


def test_unrelated_constraint_does_not_exempt():
    classification = {"cards": [
        _row("cc_count", cd.DEVICE_BOUND, "反復する事象を数えさせる"),
    ]}

    result = cd.resolve_exclusions(
        classification, brief_constraints=["「こんな夢を見た」で始める"]
    )

    assert result["excluded_card_ids"] == ["cc_count"]


def test_exemption_matches_against_named_images_too():
    """題の言い回しが違っても、名指しされた像で拾う。"""
    classification = {"cards": [
        _row("cc_open", cd.DEVICE_BOUND, "定型句で語りを開始する",
             images=["こんな夢を見た"]),
    ]}

    result = cd.resolve_exclusions(
        classification, brief_constraints=["「こんな夢を見た」で始める"]
    )

    assert result["excluded_card_ids"] == []


def test_portable_cards_are_never_excluded_even_without_constraints():
    classification = {"cards": [_row("cc_style", cd.PORTABLE, "距離のある描写")]}

    assert cd.resolve_exclusions(classification)["excluded_card_ids"] == []


# ── 置き場所 ──


def test_classification_is_stored_per_profile_outside_the_cards():
    """カード本体(承認済み資産)を書き換えない置き方にする。"""
    path = cd.classification_path("cp_yume_juya")

    assert path.name == "cp_yume_juya.json"
    assert path.parent.name == "card_classifications"
