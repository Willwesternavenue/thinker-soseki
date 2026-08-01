"""llm.call_json のタイムアウト処理。

2026-08-01: タイムアウト無しの呼び出しで検出層のハングが worker のパイプライン
全体を46分止めた。SDKのタイムアウトを明示し、かかった時に判別できるエラーへ
変換することを確認する。実APIは叩かず、SDKクライアントを差し替える。
"""

import anthropic
import pytest

from src import config, llm


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
