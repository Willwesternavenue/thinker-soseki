"""llm.call_json のタイムアウト処理。

2026-08-01: タイムアウト無しの呼び出しで検出層のハングが worker のパイプライン
全体を46分止めた。SDKのタイムアウトを明示し、かかった時に判別できるエラーへ
変換することを確認する。実APIは叩かず、SDKクライアントを差し替える。
"""

import anthropic
import pytest

from src import config, db, llm


class _FakeMessage:
    def __init__(self, text='{"ok": true}', stop_reason="end_turn"):
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5})()


class _FakeMessages:
    def __init__(self, *, raises=None, timeout_kwarg_seen=None):
        self._raises = raises
        self._timeout_kwarg_seen = timeout_kwarg_seen

    def create(self, **kwargs):
        if self._timeout_kwarg_seen is not None:
            self._timeout_kwarg_seen.append(kwargs.get("timeout"))
        if self._raises:
            raise self._raises
        return _FakeMessage()


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages


def _patch_client(monkeypatch, fake_messages):
    monkeypatch.setattr(llm, "_get_client", lambda: _FakeClient(fake_messages))
    monkeypatch.setattr(llm.db, "log_agent_run", lambda **kw: None)


def test_passes_the_configured_timeout_to_the_sdk(monkeypatch):
    """SDK既定はタイムアウト無しに近いので、明示的に渡していることを固定する。"""
    seen = []
    _patch_client(monkeypatch, _FakeMessages(timeout_kwarg_seen=seen))

    llm.call_json(agent_name="x", model="m", system="s", prompt="p", input_ref="r")

    assert seen == [config.LLM_TIMEOUT_SECONDS]


def test_timeout_raises_a_distinguishable_error(monkeypatch):
    """タイムアウトを判別できるエラーへ変換する（原因不明の例外にしない）。"""
    request = anthropic.APITimeoutError(request=object())
    _patch_client(monkeypatch, _FakeMessages(raises=request))

    with pytest.raises(llm.LLMTimeout, match=f"{config.LLM_TIMEOUT_SECONDS}秒"):
        llm.call_json(agent_name="creative_device_detect", model="m",
                      system="s", prompt="p", input_ref="r")


def test_timeout_is_recorded_in_agent_runs(monkeypatch):
    """ハングを見つけたときに agent_runs から追えるよう記録する。"""
    recorded = []
    _patch_client(monkeypatch, _FakeMessages(raises=anthropic.APITimeoutError(request=object())))
    monkeypatch.setattr(llm.db, "log_agent_run", lambda **kw: recorded.append(kw))

    with pytest.raises(llm.LLMTimeout):
        llm.call_json(agent_name="x", model="m", system="s", prompt="p", input_ref="r")

    assert recorded[0]["status"] == "error"
    assert f"{config.LLM_TIMEOUT_SECONDS}秒" in recorded[0]["output_json"]["error"]


def test_non_timeout_success_still_works(monkeypatch):
    """タイムアウト処理を足しても通常応答は変わらない。"""
    _patch_client(monkeypatch, _FakeMessages())

    result = llm.call_json(agent_name="x", model="m", system="s", prompt="p", input_ref="r")

    assert result == {"ok": True}


# ── 所要時間の記録（タイムアウトと対。レイテンシを体感でなく実測で議論する） ──


def test_records_duration_on_success(monkeypatch):
    recorded = []
    _patch_client(monkeypatch, _FakeMessages())
    monkeypatch.setattr(llm.db, "log_agent_run", lambda **kw: recorded.append(kw))

    llm.call_json(agent_name="x", model="m", system="s", prompt="p", input_ref="r")

    assert isinstance(recorded[0]["duration_ms"], int)
    assert recorded[0]["duration_ms"] >= 0


def test_records_duration_on_timeout_too(monkeypatch):
    """失敗時も所要時間を残す — ハングの傾向を事後にログから追うため。"""
    recorded = []
    _patch_client(monkeypatch, _FakeMessages(raises=anthropic.APITimeoutError(request=object())))
    monkeypatch.setattr(llm.db, "log_agent_run", lambda **kw: recorded.append(kw))

    with pytest.raises(llm.LLMTimeout):
        llm.call_json(agent_name="x", model="m", system="s", prompt="p", input_ref="r")

    assert isinstance(recorded[0]["duration_ms"], int)


def test_duration_ms_column_exists_on_the_real_table(client):
    """マイグレーション(20260801000001)適用の確認。実DBへ1行だけ書いて消す。"""
    db.log_agent_run(
        job_id=None, agent_name="test_llm_call_json_duration_check", model="m",
        input_ref="r", output_json={}, status="success", cost=0, duration_ms=42,
    )
    try:
        row = (
            client.table("agent_runs").select("duration_ms")
            .eq("agent_name", "test_llm_call_json_duration_check")
            .single().execute().data
        )
        assert row["duration_ms"] == 42
    finally:
        client.table("agent_runs").delete().eq(
            "agent_name", "test_llm_call_json_duration_check"
        ).execute()
