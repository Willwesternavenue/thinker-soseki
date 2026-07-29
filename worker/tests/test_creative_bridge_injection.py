"""Bridge Rule を outline 段へ注入する(引き継ぎ B-1 / 仕様§6)。

注入位置は outline 段だけ。draft 段は Guard の再生成と絡むため入れない
（再生成のたびに橋を混ぜ直すと、何が本文へ効いたのか trace から辿れなくなる）。

LLM呼び出しは注入して差し替える（実APIを叩かない）。
"""

from src.creative import bridges, generate


class FakeLLM:
    """call_json の差し替え。呼び出し内容を記録する。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {}


_OUTLINE = {
    "intro": "河原に立つ場面から始まる", "anomaly": "子供が大人の口調で断定する",
    "repetition_and_change": "同じ問いが繰り返される",
    "turn": "背負っていたものの正体が分かる", "ending": "夜が明けきらずに終わる",
    "unexplained": "子供が何者かは説明しない",
}

_BRIDGE = {
    "rule_id": "br_test01",
    "title": "物我の区別が溶ける場としての夢の宣言",
    "thought_id": "butsuga_gocchaku",
    "thought_title": "物我合着",
    "thought_claim": "物と我の区別は便宜にすぎない",
    "technique_card_id": "cc_test01",
    "technique_title": "夢の宣言",
    "technique_summary": "「こんな夢を見た」で始める",
    "rationale": "夢の枠が物我の境を溶かす",
    "forbidden": [bridges.DEFAULT_BRIDGE_PROHIBITION],
}


def _ctx(*, rules_mode, bridge_list):
    return generate.GenerationContext(
        job={"job_id": "job-1"},
        profile={"orthography_policy": "新字新仮名", "person_id": "natsume_soseki"},
        brief={"motif": "河原", "situation": "夢の中", "emotional_target": "不安",
               "length": 1200, "constraints": []},
        cards=[{"card_type": "narrative", "title": "異常を自然な事実として扱う",
                "positive_patterns": ["夢の内部では異常も当然の事実として扱う"]}],
        source_text="こんな夢を見た。",
        bridges=bridge_list,
        rules_mode=rules_mode,
    )


# ── プロンプトへ入るか ──


def test_assist_injects_the_bridge_into_the_outline_prompt():
    llm = FakeLLM(_OUTLINE)

    generate.build_outline(_ctx(rules_mode="assist", bridge_list=[_BRIDGE]),
                          job_id="job-1", call_json=llm)

    prompt = llm.calls[0]["prompt"]
    assert "物我合着" in prompt
    assert "夢の宣言" in prompt
    # ⚠️ 台詞化の禁止は必ずプロンプトへ現れる（仕様§6）
    assert bridges.DEFAULT_BRIDGE_PROHIBITION in prompt


def test_shadow_does_not_inject_into_the_prompt():
    """shadow は観察用。プロンプトへ入れない（trace にだけ残す）。"""
    llm = FakeLLM(_OUTLINE)

    generate.build_outline(_ctx(rules_mode="shadow", bridge_list=[_BRIDGE]),
                          job_id="job-1", call_json=llm)

    assert "物我合着" not in llm.calls[0]["prompt"]


def test_off_does_not_inject_into_the_prompt():
    llm = FakeLLM(_OUTLINE)

    generate.build_outline(_ctx(rules_mode="off", bridge_list=[]),
                          job_id="job-1", call_json=llm)

    prompt = llm.calls[0]["prompt"]
    assert "思想と書き方の対応" not in prompt
    # 橋が無くても他の材料は入っている（節が消えるだけ）
    assert "こんな夢を見た。" in prompt


def test_draft_never_receives_the_bridge():
    """draft 段には入れない（Guard の再生成と絡ませない）。"""
    llm = FakeLLM({"text": "こんな夢を見た。"})

    generate.build_draft(_ctx(rules_mode="assist", bridge_list=[_BRIDGE]),
                         _OUTLINE, job_id="job-1", call_json=llm)

    assert "物我合着" not in llm.calls[0]["prompt"]


# ── trace に残るか ──


def test_assist_records_fired_rule_ids():
    fields = generate._rule_trace_fields(
        _ctx(rules_mode="assist", bridge_list=[_BRIDGE])
    )

    assert fields["fired_rule_ids"] == ["br_test01"]
    assert fields["rule_decisions"] == {"mode": "assist"}


def test_shadow_is_not_recorded_as_fired():
    """shadow で発火扱いにすると、監査で「本文へ効いた」と読み違える。"""
    fields = generate._rule_trace_fields(
        _ctx(rules_mode="shadow", bridge_list=[_BRIDGE])
    )

    assert fields["fired_rule_ids"] == []
    assert fields["rule_decisions"] == {"mode": "shadow", "would_fire": ["br_test01"]}


def test_off_records_an_empty_firing():
    fields = generate._rule_trace_fields(_ctx(rules_mode="off", bridge_list=[]))

    assert fields["fired_rule_ids"] == []
    assert fields["rule_decisions"] == {"mode": "off"}


def test_no_context_records_nothing():
    """prepare 前に失敗したジョブでも trace の書き込みが落ちない。"""
    assert generate._rule_trace_fields(None) == {}


# ── モードの既定 ──


def test_prepare_does_not_read_bridges_when_off(clean_corpus, client, profile):
    """off なら橋を読みに行かない（DBアクセスごと省く）。"""
    ctx = _ctx(rules_mode="off", bridge_list=[])

    assert ctx.bridges == []
    assert bridges.rules_mode({"default_generation_settings": {"rules": "off"}}) == "off"
