"""創作モードの repository 層(T1設計書 §11 T2b)。

DBアクセスは既存 worker と同じ service_role クライアントで行う。
テストからローカルSupabaseを差し込めるよう、各関数は client を受け取れる
(省略時は既存の db.client())。
"""

from .. import db

# 失敗分類。error_message の先頭に付けて管理画面での絞り込みに使う(T1設計書 §4)
ERROR_INVARIANT = "invariant_violation"  # 承認済みカード0枚など、続行してはいけない状態
ERROR_GUARD = "guard_exhausted"  # 再生成上限に達しても Guard を通らなかった
ERROR_LLM = "llm_error"
ERROR_UNKNOWN = "unknown"

# 既存ジョブ(ingestion/distillation)の運用に合わせた error_message の上限
ERROR_MESSAGE_MAX = 2000


def format_error(kind: str, message: str) -> str:
    """error_message を「分類タグ: 本文」の形にし、DB運用の上限で切り詰める。"""
    return f"{kind}: {message}"[:ERROR_MESSAGE_MAX]


class CreativeInvariantError(RuntimeError):
    """続行してはいけない状態(承認済みカード0枚など)。ジョブは安全側で失敗させる。"""


def _c(client=None):
    return client or db.client()


def build_display_title(profile: dict, title: str) -> str:
    """表示題名を profile の display_title_format から組み立てる。"""
    return profile["display_title_format"].format(title=title)


def fetch_approved_cards(profile_id: str, *, client=None) -> list[dict]:
    """承認済みの創作カードのみを返す。未承認カードは生成に使わせない。"""
    return (
        _c(client)
        .table("creative_cards")
        .select("*")
        .eq("profile_id", profile_id)
        .eq("status", "approved")
        .order("card_type")
        .order("card_id")
        .execute()
        .data
    )


def require_approved_cards(profile_id: str, *, client=None) -> list[dict]:
    """承認済みカードを取得する。0枚なら不変条件違反として例外を送出する。"""
    cards = fetch_approved_cards(profile_id, client=client)
    if not cards:
        raise CreativeInvariantError(
            f"承認済み創作カードが0枚です(profile_id={profile_id})。"
            "管理画面でカードを承認してから生成してください。"
        )
    return cards


def get_active_profile(profile_id: str, *, client=None) -> dict:
    """生成に使えるプロファイルを取得する。

    指定が見つからない・利用可能でない場合に別プロファイルへ自動fallbackすると、
    別作家の特徴が混ざった生成物ができてしまう。曖昧なら失敗させる(仕様§6.2 Step2)。
    """
    rows = (
        _c(client)
        .table("creative_profiles")
        .select("*")
        .eq("profile_id", profile_id)
        .execute()
        .data
    )
    if not rows:
        raise CreativeInvariantError(
            f"creative_profile が見つかりません(profile_id={profile_id})"
        )
    profile = rows[0]
    if profile["status"] != "active":
        raise CreativeInvariantError(
            f"creative_profile が利用可能な状態ではありません"
            f"(profile_id={profile_id}, status={profile['status']})"
        )
    return profile


# ── 生成ジョブのライフサイクル ──
# claim は既存の ingestion_jobs / distillation_jobs と同じく非排他(単一worker前提)。
# 複数worker化する場合は update ... where status='pending' returning が必要
# (supabase/migrations/20260726000001_creative_mode.sql のテーブルコメント参照)。


def claim_next_generation(*, client=None) -> dict | None:
    """pending のジョブを古い順に1件取り、running にして返す。無ければ None。"""
    c = _c(client)
    rows = (
        c.table("creative_generations")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    job = rows[0]
    updated = (
        c.table("creative_generations")
        .update({"status": "running", "error_message": None})
        .eq("job_id", job["job_id"])
        .execute()
        .data
    )
    return updated[0] if updated else {**job, "status": "running"}


def set_generation_step(job_id: str, step: str, *, client=None) -> None:
    """進捗を記録する。UIはこれを見て生成中の表示を切り替える。"""
    _c(client).table("creative_generations").update(
        {"status": "running", "current_step": step}
    ).eq("job_id", job_id).execute()


def finish_generation(
    job_id: str,
    *,
    final_text: str,
    display_title: str,
    outline: dict | None = None,
    client=None,
) -> None:
    """生成結果を保存して succeeded にする。"""
    _c(client).table("creative_generations").update(
        {
            "status": "succeeded",
            "current_step": "done",
            "final_text": final_text,
            "display_title": display_title,
            "outline": outline,
            "error_message": None,
        }
    ).eq("job_id", job_id).execute()


def fail_generation(job_id: str, kind: str, message: str, *, client=None) -> None:
    """安全側で失敗させる。理由は分類タグ付きで残す。"""
    _c(client).table("creative_generations").update(
        {"status": "failed", "error_message": format_error(kind, message)}
    ).eq("job_id", job_id).execute()


def reclaim_orphaned_generations(*, client=None) -> None:
    """worker再起動時、running のまま残ったジョブを pending へ戻す(孤児回収)。"""
    _c(client).table("creative_generations").update(
        {"status": "pending", "current_step": None}
    ).eq("status", "running").execute()


def insert_trace(job_id: str, profile_id: str, *, client=None, **fields) -> None:
    """生成過程を creative_traces に残す。成功・失敗のどちらの終端でも必ず呼ぶ。"""
    _c(client).table("creative_traces").insert(
        {"job_id": job_id, "profile_id": profile_id, **fields}
    ).execute()
