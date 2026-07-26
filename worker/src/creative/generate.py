"""創作生成パイプライン(T1設計書 §3.2)。

App Hosting のリクエスト上限5分を避けるため、生成は worker のジョブとして走る。
current_step を逐次更新し、UI はそれをポーリングして進捗を表示する。
"""

import json
from dataclasses import dataclass, field

from .. import config, llm
from . import guard, prompts, repo

# v0.1 で本文生成へ投入する章の上限。夢十夜は10篇の小コーパスなので、
# 関連する一夜の全文を入れる方が semantic search より単純で強い(仕様§6.2 Step4)
MAX_INJECTED_CHAPTERS = 2


@dataclass
class GenerationContext:
    """Step1〜4 で揃えた、本文生成に必要な材料。"""

    job: dict
    profile: dict
    brief: dict
    cards: list[dict]
    source_text: str
    injected_source_ids: list[str] = field(default_factory=list)
    injected_chunk_ids: list[str] = field(default_factory=list)
    selected_chapters: list[str] = field(default_factory=list)


def normalize_brief(
    brief_raw: dict, *, job_id: str | None = None, call_json=None
) -> dict:
    """Step1: ユーザーの自由記述を構造化briefにする(軽量モデル)。"""
    call = call_json or llm.call_json
    return call(
        agent_name="creative_brief",
        model=config.MODEL_CREATIVE_LIGHT,
        system=prompts.BRIEF_SYSTEM,
        prompt=prompts.BRIEF_PROMPT.format(
            brief_raw=json.dumps(brief_raw, ensure_ascii=False, indent=2)
        ),
        input_ref=f"creative_generation:{job_id}",
    )


def group_chunks_by_chapter(chunks: list[dict]) -> dict[str, dict]:
    """章(=一夜)ごとに本文とchunk_idをまとめる。

    夢十夜は10篇の小コーパスなので、v0.1では semantic search ではなく
    関連する一夜の全文をそのまま投入する(仕様§6.2 Step4)。
    """
    grouped: dict[str, dict] = {}
    for chunk in chunks:
        key = chunk.get("chapter_title") or "(章なし)"
        entry = grouped.setdefault(key, {"text": "", "chunk_ids": []})
        entry["text"] = f"{entry['text']}\n{chunk['text']}" if entry["text"] else chunk["text"]
        entry["chunk_ids"].append(chunk["chunk_id"])
    return grouped


def select_chapters(
    grouped: dict[str, dict],
    brief: dict,
    *,
    max_count: int,
    job_id: str | None = None,
    call_json=None,
) -> list[str]:
    """投入する章を選ぶ。候補が上限以下ならそのまま全部使う(LLMを呼ばない)。"""
    candidates = list(grouped)
    if len(candidates) <= max_count:
        return candidates

    call = call_json or llm.call_json
    result = call(
        agent_name="creative_source_select",
        model=config.MODEL_CREATIVE_LIGHT,
        system=prompts.SOURCES_SYSTEM,
        prompt=prompts.SOURCES_PROMPT.format(
            motif=brief.get("motif") or "(指定なし)",
            situation=brief.get("situation") or "(指定なし)",
            emotional_target=brief.get("emotional_target") or "(指定なし)",
            candidates="\n".join(f"- {name}" for name in candidates),
            max_count=max_count,
        ),
        input_ref=f"creative_generation:{job_id}",
    )
    # 候補に無い名称は捨てる(存在しない原典を投入しないため)
    selected = [name for name in result.get("selected") or [] if name in grouped]
    return selected[:max_count] or candidates[:max_count]


def fetch_scope_chunks(profile: dict, *, client=None) -> list[dict]:
    """profile の source_scope が指す原典のチャンクを取得する。

    source_scope は creative_profiles の既存カラム。既存の sources /
    source_chunks に列を足さずに済むよう、ここで対象を解決する。
    """
    scope = profile.get("source_scope") or {}
    source_ids = scope.get("source_ids") or []
    if not source_ids:
        raise repo.CreativeInvariantError(
            f"creative_profile の source_scope に source_ids がありません"
            f"(profile_id={profile['profile_id']})。原典を投入して紐づけてください。"
        )
    c = client or repo.db.client()
    return (
        c.table("source_chunks")
        .select("chunk_id, source_id, chapter_title, text")
        .in_("source_id", source_ids)
        .eq("status", "active")
        .order("chunk_id")
        .execute()
        .data
    )


def build_source_context(
    profile: dict, brief: dict, *, job_id=None, client=None, call_json=None
) -> dict:
    """Step4: 投入する原典コンテキストを組み立てる。"""
    chunks = fetch_scope_chunks(profile, client=client)
    if not chunks:
        raise repo.CreativeInvariantError(
            f"投入できる原典チャンクがありません(profile_id={profile['profile_id']})"
        )
    grouped = group_chunks_by_chapter(chunks)
    selected = select_chapters(
        grouped,
        brief,
        max_count=MAX_INJECTED_CHAPTERS,
        job_id=job_id,
        call_json=call_json,
    )
    by_chunk = {c["chunk_id"]: c for c in chunks}
    chunk_ids = [cid for name in selected for cid in grouped[name]["chunk_ids"]]
    return {
        "text": "\n\n".join(f"## {name}\n{grouped[name]['text']}" for name in selected),
        "chapters": selected,
        "chunk_ids": chunk_ids,
        "source_ids": sorted({by_chunk[cid]["source_id"] for cid in chunk_ids}),
    }


def _format_cards(cards: list[dict]) -> str:
    """承認済みカードをプロンプトへ埋め込む形にする。"""
    if not cards:
        return "(なし)"
    lines = []
    for c in cards:
        patterns = c.get("positive_patterns") or []
        line = f"- [{c['card_type']}] {c['title']}"
        if patterns:
            line += f": {'; '.join(patterns)}"
        lines.append(line)
    return "\n".join(lines)


def _format_constraints(brief: dict) -> str:
    return "、".join(brief.get("constraints") or []) or "(なし)"


def build_outline(ctx: "GenerationContext", *, job_id=None, call_json=None) -> dict:
    """Step5: 全文の前に内部outlineを作る(高性能モデル。仕様§6.2 Step6)。"""
    call = call_json or llm.call_json
    return call(
        agent_name="creative_outline",
        model=config.MODEL_CREATIVE_MAIN,
        system=prompts.OUTLINE_SYSTEM,
        prompt=prompts.OUTLINE_PROMPT.format(
            motif=ctx.brief.get("motif") or "(指定なし)",
            situation=ctx.brief.get("situation") or "(指定なし)",
            emotional_target=ctx.brief.get("emotional_target") or "(指定なし)",
            constraints=_format_constraints(ctx.brief),
            cards=_format_cards(ctx.cards),
            source_excerpt=ctx.source_text,
        ),
        input_ref=f"creative_generation:{job_id}",
    )


def build_draft(
    ctx: "GenerationContext",
    outline: dict,
    *,
    job_id=None,
    call_json=None,
    violations: list[str] | None = None,
) -> str:
    """Step6: outline・承認済みカード・原典から本文を生成する(高性能モデル)。

    violations が渡された場合は、直前のGuard違反を修正させる再生成として扱う
    (仕様§5.3)。
    """
    call = call_json or llm.call_json
    retry_note = ""
    if violations:
        retry_note = "\n\n## 前回の問題点(必ず修正すること)\n" + "\n".join(
            f"- {v}" for v in violations
        )
    result = call(
        agent_name="creative_draft",
        model=config.MODEL_CREATIVE_MAIN,
        system=prompts.DRAFT_SYSTEM,
        prompt=prompts.DRAFT_PROMPT.format(
            motif=ctx.brief.get("motif") or "(指定なし)",
            length=ctx.brief.get("length") or "指定なし",
            orthography_policy=ctx.profile["orthography_policy"],
            constraints=_format_constraints(ctx.brief),
            intro=outline.get("intro", ""),
            anomaly=outline.get("anomaly", ""),
            repetition_and_change=outline.get("repetition_and_change", ""),
            turn=outline.get("turn", ""),
            ending=outline.get("ending", ""),
            unexplained=outline.get("unexplained", ""),
            cards=_format_cards(ctx.cards),
            source_excerpt=ctx.source_text,
        )
        + retry_note,
        input_ref=f"creative_generation:{job_id}",
        max_tokens=4096,  # 短編1500字クラスでも既定2048では不足しうるため引き上げ
    )
    return result["text"]


def prepare_generation(job: dict, *, client=None, call_json=None) -> GenerationContext:
    """Step1〜4: brief正規化 → profile検証 → 承認済みカード → 原典投入。

    ここで揃えた材料を Step5(outline)以降が使う。
    """
    job_id = job["job_id"]

    # 不変条件(profile・承認済みカード)を先に確かめる。
    # 仕様§6.2 の並びは brief が先だが、失敗が確定しているジョブでLLMを呼ぶと
    # 無駄に課金されるため、安価で決定的な検証を先に実行する。
    repo.set_generation_step(job_id, "profile", client=client)
    profile = repo.get_active_profile(job["profile_id"], client=client)

    repo.set_generation_step(job_id, "cards", client=client)
    cards = repo.require_approved_cards(job["profile_id"], client=client)

    repo.set_generation_step(job_id, "brief", client=client)
    brief = normalize_brief(job["brief_raw"], job_id=job_id, call_json=call_json)
    repo.save_brief_normalized(job_id, brief, client=client)

    repo.set_generation_step(job_id, "sources", client=client)
    sources = build_source_context(
        profile, brief, job_id=job_id, client=client, call_json=call_json
    )

    return GenerationContext(
        job=job,
        profile=profile,
        brief=brief,
        cards=cards,
        source_text=sources["text"],
        injected_source_ids=sources["source_ids"],
        injected_chunk_ids=sources["chunk_ids"],
        selected_chapters=sources["chapters"],
    )


class GuardExhaustedError(RuntimeError):
    """再生成上限に達してもGuardを通らなかった。安全側で失敗させる(仕様§8.1)。"""

    def __init__(self, message: str, guard_results: dict, regeneration_count: int):
        super().__init__(message)
        self.guard_results = guard_results
        self.regeneration_count = regeneration_count


def _guard_settings(profile: dict) -> dict:
    """Guardの閾値を profile の設定から読む(コードへ直書きしない。仕様§8.1)。"""
    settings = (profile.get("default_generation_settings") or {}).get("guard") or {}
    return {**guard.DEFAULT_GUARD_SETTINGS, **settings}


def process_generation(job: dict, *, client=None, call_json=None) -> None:
    """1件の生成ジョブを処理する(Step1〜8)。失敗しても必ず trace を残す(仕様§15.2)。"""
    job_id = job["job_id"]
    ctx = None
    reached_outline_draft = False
    guard_results: dict = {}
    regeneration_count = 0
    try:
        ctx = prepare_generation(job, client=client, call_json=call_json)

        repo.set_generation_step(job_id, "outline", client=client)
        outline = build_outline(ctx, job_id=job_id, call_json=call_json)

        repo.set_generation_step(job_id, "draft", client=client)
        draft = build_draft(ctx, outline, job_id=job_id, call_json=call_json)
        reached_outline_draft = True

        # Step7: Guard。違反なら理由を渡して再生成し、上限で安全側に失敗する(仕様§5.3)
        settings = _guard_settings(ctx.profile)
        max_regenerations = settings["max_regenerations"]
        sources = fetch_scope_chunks(ctx.profile, client=client)
        while True:
            repo.set_generation_step(job_id, "guard", client=client)
            guard_results = guard.run_guard(
                draft,
                sources=sources,
                cards=ctx.cards,
                settings=settings,
                job_id=job_id,
                call_json=call_json,
            )
            if guard_results["passed"]:
                break
            if regeneration_count >= max_regenerations:
                raise GuardExhaustedError(
                    f"再生成{regeneration_count}回でもGuardを通らなかった: "
                    + " / ".join(guard_results["violations"]),
                    guard_results,
                    regeneration_count,
                )
            regeneration_count += 1
            repo.set_generation_step(job_id, "draft", client=client)
            draft = build_draft(
                ctx, outline, job_id=job_id, call_json=call_json,
                violations=guard_results["violations"],
            )

        # Step8: 保存
        repo.set_generation_step(job_id, "save", client=client)
        display_title = repo.build_display_title(
            ctx.profile, ctx.brief.get("motif") or "無題"
        )
        repo.finish_generation(
            job_id,
            final_text=draft,
            display_title=display_title,
            outline=outline,
            client=client,
        )
        repo.insert_trace(
            job_id,
            job["profile_id"],
            used_card_ids=[c["card_id"] for c in ctx.cards],
            injected_source_ids=ctx.injected_source_ids,
            injected_chunk_ids=ctx.injected_chunk_ids,
            model_ids=_model_ids(ctx, reached_outline_draft, guarded=True),
            prompt_versions=dict(prompts.PROMPT_VERSIONS),
            guard_results=guard_results,
            regeneration_count=regeneration_count,
            client=client,
        )
    except repo.CreativeInvariantError as exc:
        _finish_failed(job, repo.ERROR_INVARIANT, str(exc), ctx, reached_outline_draft,
                       client=client)
    except GuardExhaustedError as exc:
        _finish_failed(job, repo.ERROR_GUARD, str(exc), ctx, reached_outline_draft,
                       guard_results=exc.guard_results,
                       regeneration_count=exc.regeneration_count, client=client)
    except Exception as exc:  # noqa: BLE001 - 監査記録を残してから失敗させる
        _finish_failed(job, repo.ERROR_LLM, str(exc), ctx, reached_outline_draft,
                       client=client)


def _model_ids(ctx, reached_outline_draft: bool, *, guarded: bool = False) -> dict:
    """どのステップまで到達したかを反映したモデル一覧(trace用)。"""
    model_ids: dict[str, str] = {}
    if ctx is not None:
        model_ids["brief"] = config.MODEL_CREATIVE_LIGHT
        model_ids["sources"] = config.MODEL_CREATIVE_LIGHT
    if reached_outline_draft:
        model_ids["outline"] = config.MODEL_CREATIVE_MAIN
        model_ids["draft"] = config.MODEL_CREATIVE_MAIN
    if guarded:
        model_ids["guard_judge"] = config.MODEL_CREATIVE_LIGHT
    return model_ids


def _finish_failed(
    job, kind, message, ctx, reached_outline_draft=False, *,
    guard_results=None, regeneration_count=0, client=None,
) -> None:
    """安全側で失敗させる。違反したまま本文は保存せず、guard結果はtraceに残す。"""
    repo.fail_generation(job["job_id"], kind, message, client=client)
    repo.insert_trace(
        job["job_id"],
        job["profile_id"],
        used_card_ids=[c["card_id"] for c in ctx.cards] if ctx else [],
        injected_source_ids=ctx.injected_source_ids if ctx else [],
        injected_chunk_ids=ctx.injected_chunk_ids if ctx else [],
        model_ids=_model_ids(ctx, reached_outline_draft, guarded=bool(guard_results)),
        prompt_versions=dict(prompts.PROMPT_VERSIONS),
        guard_results=guard_results or {},
        regeneration_count=regeneration_count,
        client=client,
    )
