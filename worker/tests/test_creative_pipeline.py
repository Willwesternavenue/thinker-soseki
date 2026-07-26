"""T4c: Guard統合・再生成フロー・成功保存のテスト(T1設計書 §5.3 / §11 T4c)。

LLMは注入して差し替え、DBはローカルSupabaseの実DBを使う。
"""

from src.creative import generate
from tests.conftest import _new_job
from tests.test_creative_generate import FakeLLM, _seed_corpus

# 原典をそのまま写した本文(Guardの原文類似検査に必ず掛かる)
COPIED_TEXT = (
    "こんな夢を見た。腕組をして枕元に坐っていると、仰向に寝た女が静かな声でもう死にますと云う。"
)
ORIGINAL_TEXT = "鏡の前に立つと、映った顔だけが老いていた。私は驚かなかった。"
OUTLINE = {
    "intro": "a", "anomaly": "b", "repetition_and_change": "c",
    "turn": "d", "ending": "e", "unexplained": "f",
}


def _approve_card(client, profile_id):
    client.table("creative_cards").insert(
        {"card_id": f"{profile_id}_n", "profile_id": profile_id,
         "card_type": "narrative", "title": "異常を自然な事実として扱う",
         "status": "approved"}
    ).execute()


def _person_id(client, profile_id):
    return (
        client.table("creative_profiles").select("person_id")
        .eq("profile_id", profile_id).single().execute().data["person_id"]
    )


def test_process_generation_succeeds_and_saves_work(client, profile):
    """Guardを通れば succeeded になり、本文・表示題名・outlineが保存される(仕様§6.2 Step8)。"""
    _seed_corpus(client, profile, _person_id(client, profile), {"第一夜": "こんな夢を見た。"})
    _approve_card(client, profile)
    job = _new_job(client, profile, idempotency_key=f"ok_{profile}")
    llm = FakeLLM(
        {"motif": "鏡", "length": 600, "constraints": []},
        OUTLINE,
        {"text": ORIGINAL_TEXT},
    )

    generate.process_generation(job, client=client, call_json=llm)

    row = (
        client.table("creative_generations")
        .select("status, current_step, final_text, display_title, outline")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "succeeded"
    assert row["current_step"] == "done"
    assert "鏡の前に立つと" in row["final_text"]
    # 誤認防止のため題名はprofileの書式で固定される(仕様§5.1)
    assert row["display_title"].endswith("（AI創作）")
    assert row["outline"]["anomaly"] == "b"

    trace = (
        client.table("creative_traces").select("guard_results, regeneration_count, model_ids")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert trace["guard_results"]["similarity"]["passed"] is True
    assert trace["regeneration_count"] == 0
    assert trace["model_ids"]["draft"] == generate.config.MODEL_CREATIVE_MAIN


def test_process_generation_regenerates_on_guard_violation(client, profile):
    """Guard違反なら違反理由を渡して再生成し、通れば成功する(仕様§8.1)。"""
    _seed_corpus(client, profile, _person_id(client, profile), {"第一夜": COPIED_TEXT})
    _approve_card(client, profile)
    job = _new_job(client, profile, idempotency_key=f"regen_{profile}")
    llm = FakeLLM(
        {"motif": "鏡", "length": 600, "constraints": []},
        OUTLINE,
        {"text": COPIED_TEXT},    # 1回目: 原典の写し → 違反
        {"text": ORIGINAL_TEXT},  # 2回目: 独自の本文 → 通る
    )

    generate.process_generation(job, client=client, call_json=llm)

    row = (
        client.table("creative_generations").select("status, final_text")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "succeeded"
    assert "鏡の前に立つと" in row["final_text"]
    # 再生成時のプロンプトに違反理由が入っていること(仕様§5.3)
    regen_prompt = llm.calls[-1]["prompt"]
    assert "類似" in regen_prompt or "原文" in regen_prompt

    trace = (
        client.table("creative_traces").select("regeneration_count")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert trace["regeneration_count"] == 1


def test_process_generation_fails_safely_when_regeneration_limit_reached(client, profile):
    """再生成上限に達したら安全側で失敗する(違反したまま公開しない。仕様§8.1)。"""
    _seed_corpus(client, profile, _person_id(client, profile), {"第一夜": COPIED_TEXT})
    _approve_card(client, profile)
    # 上限を1回に設定。閾値がprofileの設定から読まれること(コード直書きでないこと)の検証も兼ねる
    client.table("creative_profiles").update(
        {"default_generation_settings": {
            "guard": {"max_regenerations": 1, "lcs_threshold": 20,
                      "ngram_n": 10, "ngram_overlap_ratio_max": 0.05}}}
    ).eq("profile_id", profile).execute()
    job = _new_job(client, profile, idempotency_key=f"limit_{profile}")
    llm = FakeLLM(
        {"motif": "鏡", "length": 600, "constraints": []},
        OUTLINE,
        {"text": COPIED_TEXT}, {"text": COPIED_TEXT}, {"text": COPIED_TEXT},
    )

    generate.process_generation(job, client=client, call_json=llm)

    row = (
        client.table("creative_generations")
        .select("status, current_step, error_message, final_text")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "failed"
    assert row["current_step"] == "guard"
    assert row["error_message"].startswith("guard_exhausted:")
    assert row["final_text"] is None, "違反したまま本文を保存してはいけない"

    trace = (
        client.table("creative_traces").select("guard_results, regeneration_count")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert trace["guard_results"]["similarity"]["passed"] is False
    assert trace["regeneration_count"] == 1
