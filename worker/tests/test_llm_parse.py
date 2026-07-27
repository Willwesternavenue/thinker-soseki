"""llm._parse_json のテスト。

実LLMの応答は、長文の本文に改行を含むと JSON 文字列内へ生の制御文字を
出してくることがある。FakeLLMを使う単体テストでは再現しないため、
実運用で見つかった形をここで固定する。
"""

import json

import pytest

from src import llm


def test_parses_plain_json():
    assert llm._parse_json('{"text": "本文"}') == {"text": "本文"}


def test_parses_json_in_code_fence():
    assert llm._parse_json('```json\n{"text": "本文"}\n```') == {"text": "本文"}


def test_parses_json_with_preamble():
    assert llm._parse_json('以下が結果です。\n{"text": "本文"}') == {"text": "本文"}


def test_parses_body_containing_raw_newlines():
    """本文中の改行が生のままでもパースできること(実LLMで発生した形)。

    JSONの仕様上は不正だが、長文生成では頻繁に起きるため許容する。
    """
    raw = '{"text": "こんな夢を見た。\n　鏡の前に立つと、映った顔だけが老いていた。"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)  # 標準のパースは失敗する

    result = llm._parse_json(raw)

    assert result["text"].startswith("こんな夢を見た。")
    assert "鏡の前に立つと" in result["text"]


def test_parses_body_containing_tab():
    raw = '{"text": "行1\t行2"}'
    assert llm._parse_json(raw)["text"] == "行1\t行2"


# ── 応答の切り詰め検出(実運用で判明) ──


def test_truncated_response_raises_clear_error():
    """max_tokensで切れた応答は、分かりにくいJSONエラーではなく明確に報告する。

    実運用では outline 生成が既定の2048トークンで切れ、
    「Unterminated string」という原因の分からないエラーになっていた。
    """
    with pytest.raises(llm.LLMResponseTruncated, match="max_tokens"):
        llm.ensure_not_truncated("max_tokens", agent_name="creative_outline",
                                 max_tokens=2048)


def test_normal_stop_reason_passes():
    llm.ensure_not_truncated("end_turn", agent_name="x", max_tokens=2048)
