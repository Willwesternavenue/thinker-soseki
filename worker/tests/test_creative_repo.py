"""創作モード repository 層のテスト(T1設計書 §11 T2b)。

純粋関数はそのまま検証し、DBアクセスはローカルSupabaseに対する結合テストで
検証する(接続できない環境では skip。理由を明示する)。
"""

import pytest

from src.creative import repo

from tests.conftest import _new_job


def test_display_title_applies_profile_format():
    """表示題名は profile の display_title_format から組み立てる(誤認防止・仕様§5.1)。"""
    profile = {"display_title_format": "{title}（AI創作）"}
    assert repo.build_display_title(profile, "第十一夜") == "第十一夜（AI創作）"


def test_error_message_is_prefixed_with_failure_kind():
    """管理画面で失敗種別を絞り込めるよう、error_message は分類タグで始める(T1 §4)。"""
    msg = repo.format_error(repo.ERROR_INVARIANT, "承認済み創作カードが0枚です")
    assert msg.startswith("invariant_violation:")
    assert "承認済み創作カードが0枚です" in msg


def test_error_message_is_truncated_to_db_limit():
    """既存ジョブと同じく2000字に切り詰める(error_message列の運用に合わせる)。"""
    msg = repo.format_error(repo.ERROR_LLM, "あ" * 5000)
    assert len(msg) <= 2000


def test_fetch_approved_cards_excludes_unapproved(client, profile):
    """approved以外のカードは生成に使わせない(既存思想モードと同じ不変条件・仕様§3 L2)。"""
    for suffix, status in [("a", "approved"), ("d", "draft"), ("r", "rejected")]:
        client.table("creative_cards").insert(
            {
                "card_id": f"{profile}_{suffix}",
                "profile_id": profile,
                "card_type": "style",
                "title": f"カード{suffix}",
                "status": status,
            }
        ).execute()

    cards = repo.fetch_approved_cards(profile, client=client)

    assert [c["card_id"] for c in cards] == [f"{profile}_a"]


def test_require_approved_cards_raises_when_none(client, profile):
    """承認済みカード0枚では生成を続行しない(仕様§6.2 Step3の不変条件)。"""
    with pytest.raises(repo.CreativeInvariantError):
        repo.require_approved_cards(profile, client=client)


def _assert_no_pending(client):
    """claimは全ジョブ横断で動くため、他に残ったpendingがあると検証にならない。"""
    left = (
        client.table("creative_generations").select("job_id")
        .eq("status", "pending").execute().data
    )
    assert not left, (
        "他にpendingの創作ジョブが残っているためclaim順を検証できない。"
        "`supabase db reset` でローカルDBを初期化してから再実行すること。"
    )


def test_claim_next_generation_takes_oldest_pending_and_marks_running(client, profile):
    """既存ジョブと同じく created_at 昇順で1件取り、running にする(T1 §3.1)。"""
    _assert_no_pending(client)
    first = _new_job(client, profile, idempotency_key=f"k1_{profile}")
    _new_job(client, profile, idempotency_key=f"k2_{profile}")

    claimed = repo.claim_next_generation(client=client)

    assert claimed["job_id"] == first["job_id"]
    assert claimed["status"] == "running"


def test_claim_next_generation_returns_none_when_no_pending(client, profile):
    """pendingが無ければ None(呼び出し側はポーリングを続ける)。"""
    _assert_no_pending(client)
    _new_job(client, profile, status="succeeded", idempotency_key=f"k3_{profile}")
    assert repo.claim_next_generation(client=client) is None


def test_finish_generation_saves_result(client, profile):
    """完了時は本文・表示題名・outline を保存し succeeded にする(仕様§6.2 Step8)。"""
    job = _new_job(client, profile, idempotency_key=f"k4_{profile}")

    repo.finish_generation(
        job["job_id"],
        final_text="こんな夢を見た。",
        display_title="第十一夜（AI創作）",
        outline={"導入": "鏡の前に立つ"},
        client=client,
    )

    row = (
        client.table("creative_generations").select("*")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "succeeded"
    assert row["current_step"] == "done"
    assert row["final_text"] == "こんな夢を見た。"
    assert row["display_title"] == "第十一夜（AI創作）"
    assert row["outline"] == {"導入": "鏡の前に立つ"}


def test_fail_generation_records_tagged_reason(client, profile):
    """失敗時は分類タグ付きの理由を残す(管理画面での絞り込み用)。"""
    job = _new_job(client, profile, idempotency_key=f"k5_{profile}")

    repo.fail_generation(
        job["job_id"], repo.ERROR_INVARIANT, "承認済み創作カードが0枚です", client=client
    )

    row = (
        client.table("creative_generations").select("status, error_message")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "failed"
    assert row["error_message"].startswith("invariant_violation:")


def test_insert_trace_persists_audit_fields(client, profile):
    """使用カード・投入原典・guard結果・生成設定をtraceで確認できること(仕様§16)。"""
    job = _new_job(client, profile, idempotency_key=f"k6_{profile}")

    repo.insert_trace(
        job["job_id"],
        profile,
        used_card_ids=["cc_1"],
        injected_source_ids=["SRC_YUME_06"],
        guard_results={"similarity": {"passed": True}},
        model_ids={"draft": "claude-sonnet-5"},
        prompt_versions={"draft": "v1"},
        client=client,
    )

    row = (
        client.table("creative_traces").select("*")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["used_card_ids"] == ["cc_1"]
    assert row["injected_source_ids"] == ["SRC_YUME_06"]
    assert row["guard_results"] == {"similarity": {"passed": True}}
    assert row["prompt_versions"] == {"draft": "v1"}
    # L3は v0.1 では使わない。器だけ確保されていること
    assert row["fired_rule_ids"] == []


def test_reclaim_orphaned_returns_running_jobs_to_pending(client, profile):
    """worker再起動時に running のまま残ったジョブを拾い直す(既存ジョブと同じ挙動)。"""
    job = _new_job(client, profile, status="running", current_step="draft",
                   idempotency_key=f"k7_{profile}")

    repo.reclaim_orphaned_generations(client=client)

    row = (
        client.table("creative_generations").select("status, current_step")
        .eq("job_id", job["job_id"]).single().execute().data
    )
    assert row["status"] == "pending"
    assert row["current_step"] is None


def test_get_active_profile_returns_profile(client, profile):
    """生成対象のプロファイルを取得できる(仕様§6.2 Step2)。"""
    got = repo.get_active_profile(profile, client=client)
    assert got["profile_id"] == profile
    assert got["orthography_policy"] == "新字新仮名"


def test_get_active_profile_rejects_unknown_id(client, profile):
    """存在しないprofileでは別プロファイルへ自動fallbackせず失敗させる(仕様§6.2 Step2)。"""
    with pytest.raises(repo.CreativeInvariantError):
        repo.get_active_profile("cp_does_not_exist", client=client)


def test_get_active_profile_rejects_non_active_profile(client, profile):
    """draft/archived のプロファイルでは生成しない。"""
    client.table("creative_profiles").update({"status": "archived"}).eq(
        "profile_id", profile
    ).execute()
    with pytest.raises(repo.CreativeInvariantError):
        repo.get_active_profile(profile, client=client)
