"""検索ルーティングのテスト(C-T7 / 仕様 docs/CORPUS_T1_SPEC.md §5・§6)。

論理Index(8種)はフィルタのプリセットとして持つ。物理分割はしない。
"""

import pytest

from src.aozora import routing


# ── 論理Index のプリセット(仕様§5) ──


def test_core_thought_index_excludes_fiction_speakers():
    """思想の中核Indexは、作者の直接発言だけを引く。"""
    f = routing.INDEXES["author_thought_core"]

    assert f["corpus_roles"] == ["core_thought"]
    assert f["speaker_roles"] == ["author_direct"]


def test_character_judgment_index_targets_characters():
    """作中人物の発言は**小説の中**にある。

    ⚠️ `corpus_role='character_judgment'` だけを条件にすると、この Index は
    永久に空になる。corpus_role は文書単位の単一値で、取り込みは小説を
    narrative_reference に割り当てるため。誰の発言かはチャンクの speaker_role が
    持っている(仕様§5 の定義を実データに合わせて改めた)。
    """
    f = routing.INDEXES["character_judgment"]
    assert "narrative_reference" in f["corpus_roles"]
    # 明示的に character_judgment と付けた文書も拾う(人手で割り当てた場合)
    assert "character_judgment" in f["corpus_roles"]
    assert f["speaker_roles"] == ["character"]


def test_character_index_does_not_pick_up_narration():
    """語り手の文は人物の判断ではない。"""
    assert "narrator" not in routing.INDEXES["character_judgment"]["speaker_roles"]


def test_validation_only_index_is_not_used_for_generation():
    """検証専用コーパスはカード生成・回答の入力にしない(指示書§3.8)。"""
    assert routing.INDEXES["validation_only"]["generation_input"] is False
    assert routing.INDEXES["author_thought_core"]["generation_input"] is True


# ── 質問種別 → 検索順(仕様§6) ──


def test_thought_query_starts_from_core_thought():
    """思想質問は core_thought から。小説は比較・補助として最後(明示付き)。"""
    route = routing.route_for("thought")

    assert route[0]["index"] == "author_thought_core"
    assert [s["index"] for s in route] == [
        "author_thought_core", "author_thought_support",
        "creative_grammar", "narrative_reference",
    ]
    # 小説由来は「作者本人の発言ではない」と明示する必要がある
    assert route[-1]["requires_attribution_notice"] is True
    assert route[0]["requires_attribution_notice"] is False


def test_creative_query_starts_from_creative_grammar():
    """創作質問は creative_grammar から。core_thought は Bridge Rule 経由のみ。"""
    route = routing.route_for("creative")

    assert [s["index"] for s in route][:3] == [
        "creative_grammar", "narrative_reference", "style_reference",
    ]
    core = next(s for s in route if s["index"] == "author_thought_core")
    assert core["requires_bridge_rule"] is True, "思想を直接台詞化させない"


def test_character_query_uses_character_judgment_first():
    """人物質問は character_judgment から。作者思想は比較対象としてのみ。"""
    route = routing.route_for("character")

    assert route[0]["index"] == "character_judgment"
    core = next(s for s in route if s["index"] == "author_thought_core")
    assert core["comparison_only"] is True


def test_unknown_kind_falls_back_to_thought_route():
    assert routing.route_for("fact")[0]["index"] == "author_thought_core"


# ── 質問種別の判定(既存 QueryKind への追加。仕様§6) ──


@pytest.mark.parametrize(
    "query,expected",
    [
        ("漱石は近代化をどう考えたか", "thought"),
        ("『夢十夜』の第十一夜を書け", "creative"),
        ("代助は日本社会をどう考えたか", "character"),
        ("三四郎の美禰子はどんな人物か", "character"),
    ],
)
def test_detects_query_kind(query, expected):
    """人物質問を思想質問と区別する(作者と作中人物を混同しないため)。"""
    assert routing.detect_kind(query) == expected


def test_character_detection_requires_known_character():
    """既知の登場人物名が無ければ人物質問にしない(誤判定で作者思想を外すため)。"""
    assert routing.detect_kind("その人はどう考えたか") != "character"


# ── 拡張RPCの絞り込み(仕様§5.1。実DBで検証) ──


def _seed(client, source_id, *, corpus_role, genre, speaker_role, text, primary=True):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": f"cw_{source_id}", "person_id": "natsume_soseki",
        "canonical_title": source_id}).execute()
    client.table("work_editions").upsert({
        "edition_id": f"ed_{source_id}", "canonical_work_id": f"cw_{source_id}",
        "aozora_work_id": "000000", "orthography": "新字新仮名",
        "is_primary_retrieval_edition": primary}).execute()
    client.table("sources").upsert({
        "source_id": source_id, "person_id": "natsume_soseki", "title": source_id,
        "source_type": "essay", "edition_id": f"ed_{source_id}",
        "corpus_role": corpus_role, "document_genre": genre,
        "source_provider": "aozora"}).execute()
    client.table("source_chunks").upsert({
        "chunk_id": f"{source_id}_001", "source_id": source_id,
        "person_id": "natsume_soseki", "text": text,
        "chunker_version": "aozora_v1", "chunk_hash": f"h{source_id}",
        "speaker_role": speaker_role, "embedding": [0.1] * 1536}).execute()


def test_rpc_without_filters_returns_everything(clean_corpus, client):
    """既存の呼び出し(絞り込みなし)は従来どおり全件を返す。"""
    _seed(client, "SRC_LECTURE", corpus_role="core_thought", genre="lecture",
          speaker_role="author_direct", text="開化は内発的である。")
    _seed(client, "SRC_NOVEL", corpus_role="narrative_reference", genre="short_story",
          speaker_role="narrator", text="こんな夢を見た。")

    rows = client.rpc("match_source_chunks_all", {
        "query_embedding": [0.1] * 1536,
        "target_person_id": "natsume_soseki",
        "match_count": 10,
    }).execute().data

    assert {r["source_id"] for r in rows} == {"SRC_LECTURE", "SRC_NOVEL"}


def test_rpc_filters_by_corpus_role(clean_corpus, client):
    """corpus_role を指定すると小説が返らない(思想Indexへの混入防止)。"""
    _seed(client, "SRC_LECTURE", corpus_role="core_thought", genre="lecture",
          speaker_role="author_direct", text="開化は内発的である。")
    _seed(client, "SRC_NOVEL", corpus_role="narrative_reference", genre="short_story",
          speaker_role="narrator", text="こんな夢を見た。")

    rows = client.rpc("match_source_chunks_all", {
        "query_embedding": [0.1] * 1536,
        "target_person_id": "natsume_soseki",
        "match_count": 10,
        "target_corpus_roles": ["core_thought"],
        "target_speaker_roles": ["author_direct"],
    }).execute().data

    assert [r["source_id"] for r in rows] == ["SRC_LECTURE"]
    # 回答時に「本人の発言か否か」を示せるよう、役割も返る
    assert rows[0]["speaker_role"] == "author_direct"
    assert rows[0]["corpus_role"] == "core_thought"


def test_rpc_can_limit_to_primary_edition(clean_corpus, client):
    """既定検索版だけに絞れる(版違いで同じ段落を重複して返さない)。"""
    _seed(client, "SRC_NEW", corpus_role="core_thought", genre="lecture",
          speaker_role="author_direct", text="新字版の本文。", primary=True)
    _seed(client, "SRC_OLD", corpus_role="core_thought", genre="lecture",
          speaker_role="author_direct", text="旧字版の本文。", primary=False)

    rows = client.rpc("match_source_chunks_all", {
        "query_embedding": [0.1] * 1536,
        "target_person_id": "natsume_soseki",
        "match_count": 10,
        "primary_edition_only": True,
    }).execute().data

    assert [r["source_id"] for r in rows] == ["SRC_NEW"]
