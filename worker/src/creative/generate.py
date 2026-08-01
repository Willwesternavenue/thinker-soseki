"""創作生成パイプライン(T1設計書 §3.2)。

App Hosting のリクエスト上限5分を避けるため、生成は worker のジョブとして走る。
current_step を逐次更新し、UI はそれをポーリングして進捗を表示する。
"""

import json
from dataclasses import dataclass, field

from .. import config, llm
from . import bridges as bridges_mod
from . import card_devices, device_catalog, guard, premises as premises_mod, prompts, repo

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
    # 承認済み Bridge Rule。思想が創作へ入る唯一の経路(仕様§6)。
    # rules モードが off なら空のまま
    bridges: list[dict] = field(default_factory=list)
    rules_mode: str = bridges_mod.DEFAULT_RULES_MODE
    # 装置カード除外の結果（タスク条件付きの制御。既定は未適用）
    device_exclusion: dict = field(default_factory=lambda: {"applied": False})
    # outline 段の装置検査の結果（多層防御の最終段）
    device_check: dict = field(default_factory=lambda: {"passed": None})
    # 十一夜目の中心前提（3案から抽選）。装置の出現率そのものを下げる機構
    premise: dict = field(default_factory=dict)


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


def _format_premise(premise: dict) -> str:
    """採用した中心前提をプロンプトへ入れる形にする。"""
    if not premise:
        return "(指定なし。依頼から自分で立てる)"
    image = premise.get("image")
    return premise["premise"] + (f"\n中心イメージ: {image}" if image else "")


def _format_constraints(brief: dict) -> str:
    return "、".join(brief.get("constraints") or []) or "(なし)"


def assert_exclusions_hold(ctx: "GenerationContext") -> None:
    """プロンプト組み立ての最終段で、除外が**全経路**に効いているかを確かめる。

    ⚠️ 除外はカード取得の一箇所でしか効いておらず、プロンプトへ合流する他の経路
    (bridge / 原典注入 / 今後の前提3案) は独立にコンテンツを運べる。実測で
    bridge がこの穴を通した — 除外した計数カードの要約「太陽の出没を何度も
    数えさせ、数えきれなくなる」が br_76de88e279c6 経由で outline に入っていた。

    個別の経路を塞ぐだけでは次の経路が増えたときに同じ穴が開くので、合流点で
    まとめて止める。**プロンプトへ材料を運ぶ経路を増やしたら、ここに追加する。**
    """
    excluded = set((ctx.device_exclusion or {}).get("excluded_card_ids") or [])
    if not excluded:
        return
    leaked = [c["card_id"] for c in ctx.cards if c.get("card_id") in excluded]
    leaked += [
        b["technique_card_id"] for b in ctx.bridges
        if b.get("technique_card_id") in excluded
    ]
    if leaked:
        raise repo.CreativeInvariantError(
            "除外した創作カードがプロンプトへ合流しています"
            f"（経路の塞ぎ漏れ）: {sorted(set(leaked))}"
        )


def build_outline(
    ctx: "GenerationContext", *, job_id=None, call_json=None, avoid=None
) -> dict:
    """Step5: 全文の前に内部outlineを作る(高性能モデル。仕様§6.2 Step6)。

    avoid は装置検査で捕まった再現の指摘。作り直しのときだけ渡す。
    """
    assert_exclusions_hold(ctx)
    call = call_json or llm.call_json
    avoid_note = ""
    if avoid:
        avoid_note = (
            "\n\n## 前回の構成で原作をなぞってしまった点(必ず避ける)\n"
            + "\n".join(f"- {a}" for a in avoid)
            + "\n原作のどの一夜とも異なる、十一夜目にしかない前提を立てること。"
        )
    return call(
        agent_name="creative_outline",
        model=config.MODEL_CREATIVE_MAIN,
        system=prompts.OUTLINE_SYSTEM,
        prompt=prompts.OUTLINE_PROMPT.format(
            motif=ctx.brief.get("motif") or "(指定なし)",
            situation=ctx.brief.get("situation") or "(指定なし)",
            emotional_target=ctx.brief.get("emotional_target") or "(指定なし)",
            constraints=_format_constraints(ctx.brief),
            premise=_format_premise(ctx.premise),
            cards=_format_cards(ctx.cards),
            # assist のときだけ橋をプロンプトへ入れる。shadow は trace にのみ残す
            # (仕様§6: 思想が創作へ入る経路は承認済みの橋だけ)
            bridges=(
                bridges_mod.render_bridge_section(ctx.bridges)
                if ctx.rules_mode == "assist"
                else ""
            ),
            source_excerpt=ctx.source_text,
        ) + avoid_note,
        input_ref=f"creative_generation:{job_id}",
        # 承認済みカードが増えると構成の記述も長くなる。カード13枚で 4096 でも
        # 打ち切られたため引き上げた(実運用で確認)。切り詰めは llm 側が明示的に
        # 検出するので、足りなければエラーで分かる
        max_tokens=8192,
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


def device_exclusion_enabled(profile: dict) -> bool:
    """装置カードの除外を効かせるか。

    ⚠️ これは**誤りの修正ではなく、タスク条件付きの制御**である。
    装置カード（第一夜の計数、第三夜の全知の子供）は『夢十夜』の**続編**では
    汚染源だが、「夢十夜風の別作品を書く」という依頼では素材になりうる。
    続編・パスティーシュ系のプロファイルで on、自由創作では off。

    既定は off（明示的に有効化したプロファイルだけが効く）。
    """
    settings = profile.get("default_generation_settings") or {}
    return (settings.get("device_exclusion") or "off") == "on"


def apply_device_exclusion(
    cards: list[dict], profile: dict, brief: dict, *, call_json=None
) -> tuple[list[dict], dict]:
    """続編生成で装置カードを落とす。判定は screening JSON（カード本体は不変）。

    brief の constraints が明示要求しているカードは除外を免除する
    （続編は非対称な借用: 枠の装置は継承し、内側の装置は禁じる）。
    """
    screening = card_devices.load_classification_for_generation(profile["profile_id"])
    if not screening:
        return cards, {"applied": False, "reason": "screening が無い"}

    resolved = card_devices.resolve_exclusions(
        screening,
        brief_constraints=brief.get("constraints") or [],
        call_json=call_json,
    )
    excluded = set(resolved["excluded_card_ids"])
    kept = [c for c in cards if c["card_id"] not in excluded]
    return kept, {
        "applied": True,
        "excluded_card_ids": sorted(excluded),
        "exempted_card_ids": [r["card_id"] for r in resolved["exempted"]],
    }


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

    # 承認済み Bridge Rule。off なら読まない。進捗ステップは足していない —
    # LLM を呼ばない即時のDB読みで、独立した段にすると進捗表示が行き来する
    mode = bridges_mod.rules_mode(profile)

    repo.set_generation_step(job_id, "brief", client=client)
    brief = normalize_brief(job["brief_raw"], job_id=job_id, call_json=call_json)
    repo.save_brief_normalized(job_id, brief, client=client)

    # 装置カードの除外は brief の constraints を見るので brief 正規化の後
    device_exclusion = {"applied": False}
    if device_exclusion_enabled(profile):
        cards, device_exclusion = apply_device_exclusion(
            cards, profile, brief, call_json=call_json
        )

    # 橋は除外の結果を渡してから読む（対応先が除外カードの橋は架けない）
    bridges = (
        bridges_mod.fetch_bridges(
            profile["person_id"], client=client,
            excluded_card_ids=set(device_exclusion.get("excluded_card_ids") or []),
        )
        if mode != "off"
        else []
    )

    # 中心前提の抽選。装置除外が on のとき（続編・パスティーシュ系）だけ効かせる
    premise = {}
    if device_exclusion_enabled(profile):
        catalog = device_catalog.load_catalog(_scope_source_id(profile))
        if catalog:
            resolved = premises_mod.resolve_premise(
                brief, catalog,
                past=premises_mod.load_past_premises(job["profile_id"], client=client),
                work_title=catalog.get("meta", {}).get("work_title") or "",
                job_id=job_id, call_json=call_json,
            )
            premise = resolved["premise"]
            # 採用前提は brief_normalized に残す（次回の照合リストになる）。
            # 落ちた前提と理由・抽選の候補集合も残す — 「どんな前提が装置判定で
            # 落ちるか」の分布は前提生成プロンプトの次の改善材料になり、
            # (iii)の照合リストの品質確認にもなる
            brief = {**brief, "premise": premise,
                     "premise_attempts": resolved["attempts"]}
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
        bridges=bridges,
        rules_mode=mode,
        device_exclusion=device_exclusion,
        premise=premise,
    )


# outline 段の装置検査は**監視モード**（ゲートしない）。
#
# 判定の重心は前提レベルへ移した。前提が装置でないことは採用時点で確認済みなので、
# outline 段で同じ判定を3試行かけて落とすのは二重チェックであり、実測では
# 偽陽性で正常な生成を殺す側に働いた — 影が体から離れる outline を第七夜
# 「船からの投身と着水前後悔」と判定して3試行焼き切るなど、装置の成分が1つも
# 一致しないのに抽象度の高い共通点（不可逆な離別・反復による消耗）で反応した。
# 第十夜と第七夜は汚染下でも偽陽性の常連で、この judge の癖と考えられる。
#
# ゲートを戻すなら、成分一致の要求を厳しくして偽陽性率を測ってから。
def observe_outline_devices(
    ctx: "GenerationContext", outline: dict, *, job_id=None, call_json=None
) -> dict:
    """outline に現れた装置を**記録するだけ**。作り直しも失敗判定もしない。"""
    if not device_exclusion_enabled(ctx.profile):
        return outline
    catalog = device_catalog.load_catalog(_scope_source_id(ctx.profile))
    if not catalog:
        return outline

    matches = device_catalog.detect_devices(
        device_catalog.outline_text(outline), catalog, call_json=call_json
    )
    verdict = device_catalog.verdict_for_matches(matches)
    ctx.device_check = {
        "gated": False,
        "passed": verdict["passed"],
        "reasons": verdict["reasons"],
        "outline": outline,
    }
    return outline


def _scope_source_id(profile: dict) -> str:
    ids = (profile.get("source_scope") or {}).get("source_ids") or []
    return ids[0] if ids else ""


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
        # 多層防御の最終段: どの経路から入っても装置は草稿に現れる。
        # カード選別（注入前）を通り抜けたものをここで捕まえ、outline を作り直す
        outline = observe_outline_devices(
            ctx, outline, job_id=job_id, call_json=call_json
        )

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

        # 本文への装置検査は**監視モード**。検出しても作り直さず失敗にもしない。
        # ゲートしなければ生成挙動に影響しないので一変数原則を保ったまま、
        # draft 段で装置が入るか否かのデータが貯まる（outline 起源説の検証）。
        # ゲート化するかは監視データを見てから決める
        body_check = _observe_body_devices(ctx, draft, call_json=call_json)

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
            guard_results={**guard_results, "device_check": ctx.device_check,
                           "body_device_check": body_check},
            regeneration_count=regeneration_count,
            **_rule_trace_fields(ctx),
            client=client,
        )
    except premises_mod.PremiseExhaustedError as exc:
        _finish_failed(job, repo.ERROR_INVARIANT, str(exc), ctx, reached_outline_draft,
                       client=client)
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


def _observe_body_devices(ctx, draft: str, *, call_json=None) -> dict:
    """完成本文に装置が現れているかを**記録するだけ**（監視モード）。

    outline 段で止める設計なので、ここは対策ではなく計測。実測は全数が
    outline 起源だが、draft 段で入る経路は原理上まだ開いている。ゲート化の
    可否は、この記録が貯まってから判断する。acceptance の (a) 判定の自動化も
    兼ねる。
    """
    if not device_exclusion_enabled(ctx.profile):
        return {"observed": False}
    catalog = device_catalog.load_catalog(_scope_source_id(ctx.profile))
    if not catalog:
        return {"observed": False}
    matches = device_catalog.detect_devices(draft, catalog, call_json=call_json)
    return {
        "observed": True,
        "gated": False,
        "detected": [
            {"device_id": m["device_id"], "chapter_title": m.get("chapter_title"),
             "name": m.get("name"), "quote": m.get("detect_quote")}
            for m in matches
        ],
    }


def _rule_trace_fields(ctx) -> dict:
    """trace に残す規則の記録(仕様§14 の発火規則欄)。

    `fired_rule_ids` は**実際に出力へ影響した橋**だけにする。shadow は
    プロンプトへ入れていないので発火扱いにせず `rule_decisions` に留める
    （監査で「使われた」と読み違えないため）。
    """
    if ctx is None:
        return {}
    rule_ids = [b["rule_id"] for b in ctx.bridges]
    decisions: dict = {"mode": ctx.rules_mode}
    if ctx.rules_mode == "shadow":
        decisions["would_fire"] = rule_ids
    return {
        "fired_rule_ids": rule_ids if ctx.rules_mode == "assist" else [],
        "rule_decisions": decisions,
    }


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
        guard_results={**(guard_results or {}),
                       "device_check": ctx.device_check if ctx else {}},
        regeneration_count=regeneration_count,
        **_rule_trace_fields(ctx),
        client=client,
    )
