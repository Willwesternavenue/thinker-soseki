"""創作生成パイプライン Step1〜4 のテスト(T1設計書 §11 T4a)。

LLM呼び出しは注入して差し替える(実APIを叩かない)。DBはローカルSupabaseの
実DBを使う(接続できない環境では skip。test_creative_repo.py と同じ方針)。
"""

from src.creative import generate
from tests.conftest import _new_job


class FakeLLM:
    """call_json の差し替え。呼び出し内容を記録し、用意した応答を順に返す。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {}


def test_normalize_brief_structures_user_input():
    """ユーザー入力を motif/situation 等の構造化briefにする(仕様§6.2 Step1)。"""
    llm = FakeLLM(
        {
            "motif": "鏡",
            "situation": "鏡の中の自分が一日ずつ年を取る",
            "emotional_target": "不安、静かな恐怖",
            "period": "明治",
            "length": 1500,
            "constraints": [],
        }
    )
    brief_raw = {"モチーフ": "鏡", "状況": "鏡の中の自分が年を取る", "文字数": 1500}

    brief = generate.normalize_brief(brief_raw, job_id="job-1", call_json=llm)

    assert brief["motif"] == "鏡"
    assert brief["length"] == 1500
    # 軽量モデルで処理する(仕様§14 の役割分担)
    assert llm.calls[0]["model"] == generate.config.MODEL_CREATIVE_LIGHT


def test_group_chunks_by_chapter_keeps_document_order():
    """一夜(章)単位で全文投入するため、章ごとに本文をまとめる(仕様§6.2 Step4)。"""
    chunks = [
        {"chunk_id": "c1", "chapter_title": "第一夜", "text": "こんな夢を見た。"},
        {"chunk_id": "c2", "chapter_title": "第一夜", "text": "腕組をして枕元に。"},
        {"chunk_id": "c3", "chapter_title": "第三夜", "text": "六つになる子供を。"},
    ]

    grouped = generate.group_chunks_by_chapter(chunks)

    assert list(grouped) == ["第一夜", "第三夜"]
    assert grouped["第一夜"]["text"] == "こんな夢を見た。\n腕組をして枕元に。"
    assert grouped["第一夜"]["chunk_ids"] == ["c1", "c2"]


def test_select_chapters_uses_light_model_and_rejects_unknown_names():
    """LLMが候補外の章名を返しても採用しない(存在しない原典を投入しないため)。"""
    llm = FakeLLM({"selected": ["第一夜", "第十一夜"], "reason": "鏡に関連"})
    # 候補が上限を超えるときだけLLMで絞る
    grouped = {"第一夜": {"text": "a", "chunk_ids": ["c1"]},
               "第三夜": {"text": "b", "chunk_ids": ["c3"]},
               "第五夜": {"text": "c", "chunk_ids": ["c5"]}}

    selected = generate.select_chapters(
        grouped, {"motif": "鏡"}, max_count=2, job_id="job-1", call_json=llm
    )

    assert selected == ["第一夜"]  # 候補に無い「第十一夜」は捨てる
    assert llm.calls[0]["model"] == generate.config.MODEL_CREATIVE_LIGHT


def test_select_chapters_skips_llm_when_corpus_is_small():
    """候補が上限以下なら全文投入で足りる。LLMを呼ばない(小コーパスの現実に合わせる)。"""
    llm = FakeLLM()
    grouped = {"第一夜": {"text": "a", "chunk_ids": ["c1"]}}

    selected = generate.select_chapters(
        grouped, {"motif": "鏡"}, max_count=2, job_id="job-1", call_json=llm
    )

    assert selected == ["第一夜"]
    assert llm.calls == []


def _seed_corpus(client, profile_id, person_id, chapters):
    """profileのsource_scopeが指す原典と章を用意する。"""
    source_id = f"SRC_{profile_id}"
    client.table("sources").insert(
        {"source_id": source_id, "person_id": person_id, "title": "夢十夜",
         "source_type": "book", "author": "夏目漱石"}
    ).execute()
    for i, (chapter, text) in enumerate(chapters.items()):
        client.table("source_chunks").insert(
            {"chunk_id": f"{source_id}_{i:02d}", "source_id": source_id,
             "person_id": person_id, "chapter_title": chapter, "text": text,
             "chunker_version": "v1", "chunk_hash": f"h{i}", "verbatim": True}
        ).execute()
    client.table("creative_profiles").update(
        {"source_scope": {"source_ids": [source_id]}}
    ).eq("profile_id", profile_id).execute()
    return source_id


def test_prepare_generation_runs_steps_and_records_progress(client, profile):
    """Step1〜4を順に実行し、current_step を進める(UIの進捗表示の根拠)。"""
    person_id = (
        client.table("creative_profiles").select("person_id")
        .eq("profile_id", profile).single().execute().data["person_id"]
    )
    source_id = _seed_corpus(client, profile, person_id, {"第一夜": "こんな夢を見た。"})
    client.table("creative_cards").insert(
        {"card_id": f"{profile}_ok", "profile_id": profile, "card_type": "narrative",
         "title": "異常を自然な事実として扱う", "status": "approved"}
    ).execute()
    job = _new_job(client, profile, idempotency_key=f"prep_{profile}")
    llm = FakeLLM({"motif": "鏡", "situation": "", "emotional_target": "",
                   "period": None, "length": 1500, "constraints": []})

    ctx = generate.prepare_generation(job, client=client, call_json=llm)

    assert ctx.brief["motif"] == "鏡"
    assert ctx.profile["profile_id"] == profile
    assert [c["card_id"] for c in ctx.cards] == [f"{profile}_ok"]
    assert source_id in ctx.injected_source_ids
    assert "こんな夢を見た。" in ctx.source_text
    row = (
        client.table("creative_generations").select("current_step, brief_normalized")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["current_step"] == "sources"
    assert row["brief_normalized"]["motif"] == "鏡"


def test_process_generation_fails_safely_when_no_approved_cards(client, profile):
    """承認済みカード0枚なら安全側で失敗し、失敗でもtraceを残す(仕様§15.2)。"""
    person_id = (
        client.table("creative_profiles").select("person_id")
        .eq("profile_id", profile).single().execute().data["person_id"]
    )
    _seed_corpus(client, profile, person_id, {"第一夜": "こんな夢を見た。"})
    job = _new_job(client, profile, idempotency_key=f"noc_{profile}")
    llm = FakeLLM({"motif": "鏡", "length": 1500, "constraints": []})

    generate.process_generation(job, client=client, call_json=llm)

    row = (
        client.table("creative_generations").select("status, error_message")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "failed"
    assert row["error_message"].startswith("invariant_violation:")
    traces = (
        client.table("creative_traces").select("*")
        .eq("job_id", job["job_id"]).execute().data
    )
    assert len(traces) == 1, "失敗した生成でも監査記録を残すこと"


def test_run_creative_once_processes_pending_job(client, profile, monkeypatch):
    """workerのポーリングが創作ジョブを拾う(T1 §3.1)。"""
    from src import main

    person_id = (
        client.table("creative_profiles").select("person_id")
        .eq("profile_id", profile).single().execute().data["person_id"]
    )
    _seed_corpus(client, profile, person_id, {"第一夜": "こんな夢を見た。"})
    job = _new_job(client, profile, idempotency_key=f"poll_{profile}")

    processed = []
    # main.creative_repo は差し替え対象と同じモジュールなので、
    # 差し替え前の実装を捕まえてからテスト用クライアントを束縛する
    original_claim = main.creative_repo.claim_next_generation
    monkeypatch.setattr(main.creative_generate, "process_generation",
                        lambda j, **kw: processed.append(j["job_id"]))
    monkeypatch.setattr(main.creative_repo, "claim_next_generation",
                        lambda **kw: original_claim(client=client))

    assert main.run_creative_once() is True
    assert processed == [job["job_id"]]


def test_run_creative_once_returns_false_when_idle(monkeypatch):
    """pendingが無ければ False を返し、ポーリングを続ける。"""
    from src import main

    monkeypatch.setattr(main.creative_repo, "claim_next_generation", lambda **kw: None)
    assert main.run_creative_once() is False


def test_invariants_are_checked_before_calling_llm(client, profile):
    """カード0枚のような失敗確定ジョブでLLMを呼ばない(無駄な課金を避ける)。"""
    person_id = (
        client.table("creative_profiles").select("person_id")
        .eq("profile_id", profile).single().execute().data["person_id"]
    )
    _seed_corpus(client, profile, person_id, {"第一夜": "こんな夢を見た。"})
    job = _new_job(client, profile, idempotency_key=f"nollm_{profile}")
    llm = FakeLLM({"motif": "鏡"})

    generate.process_generation(job, client=client, call_json=llm)

    assert llm.calls == [], "承認済みカードが無いと分かる前にLLMを呼んではいけない"
