"""Retrieval シナリオテスト(C-T8 / 仕様 docs/CORPUS_T1_SPEC.md §11 の Retrieval 分類)。

既存の `test_aozora_routing.py` は**ルーティングの定義**（Indexのプリセットと検索順）を
検証している。こちらは**実際に取り込んだ本文を引いたときに何が返るか**を検証する。
「小説中の登場人物の発言を作者の思想として扱わない」は定義だけでは守れず、
実データを引いて初めて確かめられるため分ける。

必要な2資料（『現代日本の開化』= 講演、『夢十夜』= 小説）が無ければ**自分で取り込む**。
他のテストの `clean_corpus` がコーパスを消すため、既存データを前提にすると
フルスイートでは常に skip になり、テストとして働かない。
"""

import pytest

from src.aozora import cli, ingest, manifest, routing

PERSON_ID = "natsume_soseki"
KAIKA_EDITION = "000759"  # 現代日本の開化(講演 → core_thought)
SHASEI_EDITION = "000796"  # 写生文(文学論 → creative_grammar)
YUME_EDITION = "000799"  # 夢十夜(小説 → narrative_reference)
KAIKA = f"AOZORA_{KAIKA_EDITION}"
SHASEI = f"AOZORA_{SHASEI_EDITION}"
YUME = f"AOZORA_{YUME_EDITION}"

# 3つの corpus_role が揃う最小構成。思想・創作・小説の分離を確かめるのに要る
REQUIRED = {KAIKA: KAIKA_EDITION, SHASEI: SHASEI_EDITION, YUME: YUME_EDITION}

# Phase C (docs/CORPUS_T1_SPEC.md §12.6) で夢十夜に加えて投入された9長編。
# narrative_reference / character_judgment Index はこれらも含む。
PHASE_C_NOVEL_EDITIONS = {
    "056143",  # それから
    "000789",  # 吾輩は猫である
    "000776",  # 草枕
    "000794",  # 三四郎
    "000785",  # 門
    "000775",  # 行人
    "000773",  # こころ
    "000783",  # 道草
    "000782",  # 明暗
}
ALL_NOVEL_SOURCE_IDS = {YUME} | {f"AOZORA_{e}" for e in PHASE_C_NOVEL_EDITIONS}
SOREKARA = "AOZORA_056143"  # それから(代助の character_id が付く作品)


@pytest.fixture(scope="module")
def phase_a(client):
    """講演・創作論・小説を1件ずつ入れた状態を用意する。既にあれば取り込まない。"""
    present = {
        r["source_id"]
        for r in client.table("sources").select("source_id")
        .in_("source_id", list(REQUIRED)).execute().data or []
    }
    missing = {sid: eid for sid, eid in REQUIRED.items() if sid not in present}
    if not missing:
        return client

    try:
        # ingest_edition は work_editions の行を前提にするのでマニフェストを先に入れる
        manifest.import_manifest(cli._fetch_manifest_rows(), person_id=PERSON_ID,
                                 client=client)
        for edition_id in missing.values():
            ingest.ingest_edition(edition_id, client=client)
    except OSError as exc:  # 青空文庫/GitHubへ到達できない環境
        pytest.skip(f"原典を取得できないため skip: {exc}")
    return client


def _chunks(client, *, corpus_roles=None, speaker_roles=None, source_ids=None):
    """論理Indexのフィルタをそのまま SQL 相当の絞り込みに落として引く。"""
    q = client.table("source_chunks").select(
        "chunk_id,source_id,text,speaker_role,character_id,thought_eligibility,creative_eligibility"
    ).eq("person_id", PERSON_ID)
    if speaker_roles:
        q = q.in_("speaker_role", speaker_roles)
    if source_ids is not None:
        q = q.in_("source_id", source_ids)
    rows = q.execute().data or []
    if corpus_roles is None:
        return rows
    srcs = {
        s["source_id"]: s
        for s in client.table("sources")
        .select("source_id,corpus_role,document_genre")
        .eq("person_id", PERSON_ID).execute().data or []
    }
    return [r for r in rows if srcs.get(r["source_id"], {}).get("corpus_role") in corpus_roles]


def _index_chunks(client, index_name):
    idx = routing.INDEXES[index_name]
    return _chunks(client, corpus_roles=idx["corpus_roles"], speaker_roles=idx["speaker_roles"])


# ── シナリオ1: 近代化に関する思想質問 ──


def test_modernization_question_is_answered_from_lectures_not_fiction(phase_a):
    """『現代日本の開化』が引ける一方、小説本文は思想の中核Indexに入らない。"""
    core = _index_chunks(phase_a, "author_thought_core")

    assert any(c["source_id"] == KAIKA for c in core), "講演が思想Indexに入っていない"
    assert not any(c["source_id"] == YUME for c in core), "小説が思想Indexに混入している"


def test_fiction_speakers_never_enter_core_thought_index(phase_a):
    """語り手・登場人物の発言は、どの文書由来でも思想の中核Indexに入らない。"""
    core = _index_chunks(phase_a, "author_thought_core")

    assert {c["speaker_role"] for c in core} == {"author_direct"}


def test_thought_route_marks_fiction_as_requiring_attribution(phase_a):
    """思想質問でも小説は最後に補助として引くが、本人の発言でない旨の明示を伴う。"""
    route = routing.route_for(routing.detect_kind("漱石は近代化をどう考えたか"))
    fiction_step = next(s for s in route if s["index"] == "narrative_reference")

    assert fiction_step["requires_attribution_notice"] is True
    # ⚠️ phase_a は KAIKA/SHASEI/YUME の3資料しか自己修復しない(docstring 参照)ので、
    # フルスイート実行でコーパスが一度空にされた環境では YUME だけのことがある。
    # 「YUME は必ず入る」「小説以外・未知の作品は混入しない」の両方を、環境に依らず検証する
    novel_ids = {c["source_id"] for c in _index_chunks(phase_a, "narrative_reference")}
    assert YUME in novel_ids, "夢十夜が narrative_reference Index に入っていない"
    assert novel_ids <= ALL_NOVEL_SOURCE_IDS, (
        f"想定外の source が narrative_reference Index に混入: {novel_ids - ALL_NOVEL_SOURCE_IDS}"
    )


# ── シナリオ2: 第十一夜の生成 ──


def test_creative_generation_reaches_creative_grammar_and_the_original(phase_a):
    """創作質問は創作論と夢十夜本文の両方に届く。"""
    kind = routing.detect_kind("夢十夜の続きになる第十一夜を書いて")
    route = routing.route_for(kind)

    assert kind == "creative"
    assert [s["index"] for s in route][:2] == ["creative_grammar", "narrative_reference"]
    assert _index_chunks(phase_a, "creative_grammar"), "創作論が引けていない"
    assert _index_chunks(phase_a, "narrative_reference"), "夢十夜本文が引けていない"


def test_creative_route_does_not_take_thought_directly(phase_a):
    """思想を直接台詞化させない。core_thought は Bridge Rule 経由に限る。"""
    route = routing.route_for("creative")
    core = next(s for s in route if s["index"] == "author_thought_core")

    assert core["requires_bridge_rule"] is True


def test_dream_chunks_are_creative_eligible_but_not_thought_eligible(phase_a):
    """夢十夜は創作の材料にはなるが、思想の根拠にはならない。"""
    chunks = _chunks(phase_a, source_ids=[YUME])

    assert chunks, "夢十夜のチャンクが無い"
    assert {c["thought_eligibility"] for c in chunks} == {"excluded"}
    assert {c["creative_eligibility"] for c in chunks} == {"candidate"}


# ── シナリオ3: 直接の原典が無い現代的な質問 ──


def test_question_without_direct_source_finds_nothing_in_core_index(phase_a):
    """『生成AI』のような現代語は原典に無い。留保の判断材料として空を返せること。

    回答生成側で留保する（`abstention_reason` を trace に残す）ための前提を、
    データ側から確かめる。ここで何かがヒットするようだと、無関係な原典を
    根拠として提示してしまう。
    """
    core = _index_chunks(phase_a, "author_thought_core")

    # 対照: 原典にある語は引ける(空振りで通っていないことの確認)
    assert [c for c in core if "開化" in c["text"]]
    # そのうえで、現代語に対応する原典は無い
    assert not [c for c in core if "生成AI" in c["text"] or "人工知能" in c["text"]]


# ── シナリオ4: 登場人物の判断についての質問 ──


def test_character_index_finds_speech_inside_novels(phase_a):
    """作中人物の発言が引ける。

    ⚠️ 以前は `corpus_role='character_judgment'` だけを条件にしていたため、
    この Index は**構造上ずっと空**だった（取り込みは小説を narrative_reference に
    割り当てるので、その corpus_role が付く文書が存在しない）。
    Phase C で長編を入れても解消しない種類の欠落だった。
    """
    chunks = _index_chunks(phase_a, "character_judgment")

    assert chunks, "作中人物の発言が1件も引けていない"
    # Phase C 投入済みの環境では全長編の作中人物発言が引けるが、phase_a の自己修復のみ
    # (YUMEだけ)の環境でも壊れないよう、YUME必須+未知作品の非混入で検証する
    source_ids = {c["source_id"] for c in chunks}
    assert YUME in source_ids, "夢十夜の作中人物発言が引けていない"
    assert source_ids <= ALL_NOVEL_SOURCE_IDS, (
        f"想定外の source が character_judgment Index に混入: {source_ids - ALL_NOVEL_SOURCE_IDS}"
    )
    assert {c["speaker_role"] for c in chunks} == {"character"}


def test_character_index_excludes_the_narrator(phase_a):
    """語り手の文は人物の判断として引かない。"""
    chunk_ids = {c["chunk_id"] for c in _index_chunks(phase_a, "character_judgment")}
    narration = {
        c["chunk_id"]
        for c in _chunks(phase_a, source_ids=[YUME], speaker_roles=["narrator"])
    }

    assert narration, "語り手の文が無い(前提が崩れている)"
    assert not (chunk_ids & narration)


def test_character_speech_is_still_excluded_from_thought(phase_a):
    """人物ルートで引けるようにしても、思想の根拠にはならないままであること。"""
    character = _index_chunks(phase_a, "character_judgment")

    assert {c["thought_eligibility"] for c in character} == {"excluded"}
    core_ids = {c["chunk_id"] for c in _index_chunks(phase_a, "author_thought_core")}
    assert not ({c["chunk_id"] for c in character} & core_ids)


def test_named_characters_are_resolved_after_phase_c(phase_a):
    """代助（『それから』）のような固有名の人物は Phase C 投入後、発言まで絞り込める。

    docs/CORPUS_T1_SPEC.md §12.6: Pass2 の character_id 付与は「自作品の中でだけ」
    行われ、作品間の混線はゼロ。代助はそれからにしか現れない。

    ⚠️ phase_a は KAIKA/SHASEI/YUME の3資料しか自己修復しない(モジュール docstring
    参照)。それから(SOREKARA)が無い環境では Pass2 の character_id 付与も無いので、
    この検証自体が意味を持たない → skip する(元のテスト名 `..._await_phase_c` が
    持っていた「未投入なら待つ」という設計を、投入後の検証を保ったまま引き継ぐ)。
    """
    assert routing.detect_character("代助はなぜ働かないのか") == "daisuke"

    present = {
        r["source_id"]
        for r in phase_a.table("sources").select("source_id")
        .eq("source_id", SOREKARA).execute().data or []
    }
    if not present:
        pytest.skip(f"{SOREKARA}(それから)が未取り込みのため character_id 検証を skip")

    named = [
        c for c in _index_chunks(phase_a, "character_judgment") if c.get("character_id")
    ]
    assert named, "character_id を持つ作中人物発言が1件も無い"

    daisuke = [c for c in named if c["character_id"] == "daisuke"]
    assert daisuke, "代助の発言が引けていない"
    assert {c["source_id"] for c in daisuke} == {SOREKARA}, (
        "代助の発言が『それから』以外に混線している"
    )
