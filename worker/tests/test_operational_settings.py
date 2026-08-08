"""運用設定の回帰テスト。

⚠️ ここが守るのは**コードではなくデータ**である。

2026-08-03、管理画面でプロファイルを保存しただけで
`cp_yume_juya.default_generation_settings.rules` が `assist` から `off` へ戻り、
Bridge Rule 6件が黙って切れた。原因のコードは `557e651` で直したが、設定は
SQL・退避からの復元・今後追加される画面から再び失われうる。**失われても
エラーは出ない**ので、生成物を読むまで気づけない（引き継ぎ §2 3-b）。

意図的に設定を変えたときは下の期待値を更新すること。
**更新が必要になること自体が「設定を変えた」という記録になる。**

対象に選ぶ基準は「画面に入力欄が無く、消えても動き続けるもの」。
Guard 閾値のように正当に調整されうる値は対象にしない（誤検知になる）。
"""

import pytest

PROFILE_ID = "cp_yume_juya"

# 値が None のキーは「未設定であるべき」を表す。
EXPECTED_GENERATION_SETTINGS = {
    # ブリッジ注入。off だと思想カードが創作へ渡らない（引き継ぎ B-1）
    "rules": "assist",
    # 装置除外。自由創作でも装置が引き寄せることは対照実験で確認済みだが、
    # on にするかは未決（CREATIVE_PIPELINE_DESIGN §8）。
    # on にしたらここを "on" に変えること
    "device_exclusion": None,
}

# 両側のカードが approved でないと composeBridges が黙って落とす
EXPECTED_BRIDGE_COUNT = 6


def _profile(client):
    rows = (
        client.table("creative_profiles")
        .select("profile_id, status, default_generation_settings")
        .eq("profile_id", PROFILE_ID)
        .execute()
        .data
        or []
    )
    if not rows:
        pytest.skip(f"{PROFILE_ID} が無いDB（新規・テスト用）のため skip")
    return rows[0]


def test_generation_settings_match_expectation(client):
    """入力欄の無い設定が期待どおりであること（消えていないこと）。"""
    settings = _profile(client).get("default_generation_settings") or {}

    actual = {key: settings.get(key) for key in EXPECTED_GENERATION_SETTINGS}
    assert actual == EXPECTED_GENERATION_SETTINGS, (
        "プロファイルの運用設定が期待とずれている。意図的に変えたなら "
        "EXPECTED_GENERATION_SETTINGS を更新すること。"
        "意図していないなら、管理画面の保存や退避からの復元で消えた疑いがある"
    )


def test_profile_is_active(client):
    """active でないと創作依頼を受け付けない（画面から使えなくなる）。"""
    assert _profile(client)["status"] == "active"


def test_bridge_rules_are_readable_end_to_end(client):
    """ブリッジが「承認の鎖」ごと通っていること。

    片側のカードを取り消すと `composeBridges` は**黙ってその橋を落とす**。
    規則側の承認だけ見ても架かっているとは限らないので、鎖の全体を見る。
    """
    rules = (
        client.table("judgment_rules")
        .select("rule_id")
        .eq("rule_scope", "bridge_rule")
        .eq("lifecycle", "active")
        .execute()
        .data
        or []
    )
    if not rules:
        pytest.skip("bridge_rule が無いDB（新規・テスト用）のため skip")

    rule_ids = [r["rule_id"] for r in rules]
    versions = (
        client.table("judgment_rule_versions")
        .select("rule_id, content")
        .in_("rule_id", rule_ids)
        .eq("status", "approved")
        .execute()
        .data
        or []
    )

    thought_ids = {v["content"].get("source_thought_id") for v in versions}
    card_ids = {v["content"].get("target_creative_card_id") for v in versions}

    approved_thoughts = {
        r["thought_id"]
        for r in client.table("thought_cards")
        .select("thought_id")
        .in_("thought_id", sorted(i for i in thought_ids if i))
        .eq("status", "approved")
        .execute()
        .data
        or []
    }
    approved_cards = {
        r["card_id"]
        for r in client.table("creative_cards")
        .select("card_id")
        .in_("card_id", sorted(i for i in card_ids if i))
        .eq("status", "approved")
        .execute()
        .data
        or []
    }

    readable = [
        v
        for v in versions
        if v["content"].get("source_thought_id") in approved_thoughts
        and v["content"].get("target_creative_card_id") in approved_cards
    ]
    assert len(readable) == EXPECTED_BRIDGE_COUNT, (
        f"読み出せるブリッジが {len(readable)} 件（期待 {EXPECTED_BRIDGE_COUNT} 件）。"
        "両側のカードが approved でない橋は黙って落ちる。"
        "意図的に増減させたなら EXPECTED_BRIDGE_COUNT を更新すること"
    )
