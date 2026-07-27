"""corpus snapshot とデータ品質レポート(C-T8 / 仕様 docs/CORPUS_T1_SPEC.md §13 の #18・#20)。

- snapshot: 取り込みが再現できたかを digest で照合できること
- quality report: §14.6 の指標を機械的に判定できること

どちらもローカルSupabaseの実DBで検証する(集計は SQL/PostgREST 越しの挙動が本体のため)。
"""

from src.aozora import snapshot


def _seed_source(
    client,
    source_id,
    *,
    corpus_role="core_thought",
    genre="lecture",
    chunks=(("_001", "author_direct", "開化は内発的である。"),),
    content_sha256="a" * 64,
    parser_version="aozora_v1",
    source_url="https://www.aozora.gr.jp/cards/000148/card000000.html",
    thought_eligibility="candidate",
    embedding=True,
):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": f"cw_{source_id}",
        "person_id": "natsume_soseki",
        "canonical_title": source_id,
    }).execute()
    client.table("work_editions").upsert({
        "edition_id": f"ed_{source_id}",
        "canonical_work_id": f"cw_{source_id}",
        "aozora_work_id": source_id[-6:].rjust(6, "0"),
        "orthography": "新字新仮名",
        "is_primary_retrieval_edition": True,
        "content_sha256": content_sha256,
        "parser_version": parser_version,
    }).execute()
    client.table("sources").upsert({
        "source_id": source_id,
        "person_id": "natsume_soseki",
        "title": source_id,
        "source_type": "essay",
        "edition_id": f"ed_{source_id}",
        "corpus_role": corpus_role,
        "document_genre": genre,
        "source_provider": "aozora",
        "source_url": source_url,
    }).execute()
    for suffix, speaker_role, text in chunks:
        client.table("source_chunks").upsert({
            "chunk_id": f"{source_id}{suffix}",
            "source_id": source_id,
            "person_id": "natsume_soseki",
            "text": text,
            "chunker_version": "aozora_v1",
            "chunk_hash": f"h{source_id}{suffix}",
            "speaker_role": speaker_role,
            "thought_eligibility": thought_eligibility,
            "embedding": [0.1] * 1536 if embedding else None,
        }).execute()


# ── snapshot(受入#18: 取り込みを再現できたか照合できる) ──


def test_snapshot_is_deterministic(clean_corpus, client):
    """同じDB状態からは同じ digest が出る(時刻やUUIDを含めない)。"""
    _seed_source(client, "SRC_A")

    first = snapshot.build_snapshot(client=client)
    second = snapshot.build_snapshot(client=client)

    assert first["digest"] == second["digest"]
    assert first == second


def test_snapshot_digest_changes_when_text_changes(clean_corpus, client):
    """本文が変われば digest が変わる(取り込み内容の差分を検出できる)。"""
    _seed_source(client, "SRC_A")
    before = snapshot.build_snapshot(client=client)["digest"]

    client.table("source_chunks").update({"chunk_hash": "changed"}).eq(
        "chunk_id", "SRC_A_001"
    ).execute()

    assert snapshot.build_snapshot(client=client)["digest"] != before


def test_snapshot_does_not_change_on_reingest_of_same_content(clean_corpus, client):
    """同じ内容を入れ直しても digest は変わらない(取り込みの冪等性を照合できる)。"""
    _seed_source(client, "SRC_A")
    before = snapshot.build_snapshot(client=client)["digest"]

    _seed_source(client, "SRC_A")  # upsert なので同内容で入れ直し

    assert snapshot.build_snapshot(client=client)["digest"] == before


def test_snapshot_records_counts_and_versions(clean_corpus, client):
    _seed_source(client, "SRC_A")
    _seed_source(client, "SRC_B", corpus_role="narrative_reference", genre="short_story",
                 chunks=(("_001", "narrator", "こんな夢を見た。"),))

    snap = snapshot.build_snapshot(client=client)

    assert snap["counts"] == {
        "canonical_works": 2, "work_editions": 2, "sources": 2, "chunks": 2
    }
    assert snap["versions"]["parser"] == {"aozora_v1": 2}
    assert snap["versions"]["chunker"] == {"aozora_v1": 2}


def test_snapshot_lists_sources_in_stable_order(clean_corpus, client):
    """並び順に依存しない(PostgRESTの返却順が変わっても digest が揺れない)。"""
    _seed_source(client, "SRC_B")
    _seed_source(client, "SRC_A")

    snap = snapshot.build_snapshot(client=client)

    assert [s["source_id"] for s in snap["sources"]] == ["SRC_A", "SRC_B"]


def test_snapshot_keeps_edition_provenance(clean_corpus, client):
    """再現に必要な由来(hash・parser版)を残す(受入#19)。"""
    _seed_source(client, "SRC_A", content_sha256="b" * 64)

    edition = snapshot.build_snapshot(client=client)["editions"][0]

    assert edition["content_sha256"] == "b" * 64
    assert edition["parser_version"] == "aozora_v1"


# ── data quality report(受入#20 / 指示書§14.6) ──


def test_quality_report_passes_on_clean_corpus(clean_corpus, client):
    _seed_source(client, "SRC_A")

    report = snapshot.build_quality_report(client=client)

    assert report["passed"] is True, report["checks"]


def test_detects_garbled_text(clean_corpus, client):
    """文字化け(置換文字・〓)を検出する。"""
    _seed_source(client, "SRC_A", chunks=(("_001", "author_direct", "開化は�発的である。"),))

    check = _check(snapshot.build_quality_report(client=client), "garbling_ratio")

    assert check["passed"] is False
    assert check["value"] > 0


def test_detects_duplicate_chunks(clean_corpus, client):
    """同一 chunk_hash の重複を検出する。"""
    _seed_source(client, "SRC_A", chunks=(
        ("_001", "author_direct", "同じ本文。"),
        ("_002", "author_direct", "同じ本文。"),
    ))
    client.table("source_chunks").update({"chunk_hash": "hSRC_A_001"}).eq(
        "chunk_id", "SRC_A_002"
    ).execute()

    check = _check(snapshot.build_quality_report(client=client), "duplicate_ratio")

    assert check["passed"] is False


def test_detects_unclassified_speaker_role(clean_corpus, client):
    _seed_source(client, "SRC_A", chunks=(("_001", None, "誰の発言か不明。"),))

    check = _check(snapshot.build_quality_report(client=client), "unclassified_speaker_role_ratio")

    assert check["passed"] is False


def test_detects_fiction_mixed_into_core_thought(clean_corpus, client):
    """小説の本文が思想の中核Indexに入っていたら必ず落とす(最重要の不変条件)。"""
    _seed_source(client, "SRC_NOVEL", corpus_role="core_thought", genre="short_story",
                 chunks=(("_001", "author_direct", "こんな夢を見た。"),))

    check = _check(snapshot.build_quality_report(client=client), "fiction_in_core_thought")

    assert check["passed"] is False
    assert check["value"] == 1


def test_detects_missing_source_url(clean_corpus, client):
    _seed_source(client, "SRC_A", source_url=None)

    assert _check(snapshot.build_quality_report(client=client), "sources_without_url")["passed"] is False


def test_detects_missing_content_hash(clean_corpus, client):
    _seed_source(client, "SRC_A", content_sha256=None)

    assert _check(snapshot.build_quality_report(client=client), "editions_without_hash")["passed"] is False


def test_detects_missing_embedding(clean_corpus, client):
    _seed_source(client, "SRC_A", embedding=False)

    assert _check(snapshot.build_quality_report(client=client), "chunks_without_embedding")["passed"] is False


def test_detects_card_evidence_pointing_at_missing_chunk(clean_corpus, client, profile):
    """承認済みカードの根拠が実在しないことを検出する(§14.5 evidence span整合性)。"""
    _seed_source(client, "SRC_A")
    client.table("creative_cards").insert({
        "card_id": "cc_broken", "profile_id": profile, "card_type": "style",
        "title": "根拠が失われたカード", "status": "approved",
        "evidence_chunk_ids": ["SRC_A_001", "MISSING_CHUNK"],
    }).execute()

    check = _check(snapshot.build_quality_report(client=client), "cards_with_missing_evidence")

    assert check["passed"] is False
    assert "cc_broken" in check["detail"]


def test_ignores_unapproved_cards_with_broken_evidence(clean_corpus, client, profile):
    """draft のカードは生成に使われないので品質判定の対象外。"""
    _seed_source(client, "SRC_A")
    client.table("creative_cards").insert({
        "card_id": "cc_draft", "profile_id": profile, "card_type": "style",
        "title": "下書き", "status": "draft",
        "evidence_chunk_ids": ["MISSING_CHUNK"],
    }).execute()

    assert _check(snapshot.build_quality_report(client=client), "cards_with_missing_evidence")["passed"] is True


def test_report_lists_every_check_even_when_passing(clean_corpus, client):
    """通っている項目も残す(何を見たかが分かるレポートにするため)。"""
    _seed_source(client, "SRC_A")

    names = {c["name"] for c in snapshot.build_quality_report(client=client)["checks"]}

    assert names == set(snapshot.CHECK_NAMES)


def _check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


# ── snapshot の比較(取り込みを再現できたかの照合) ──


def _snap(sources):
    return {
        "counts": {"sources": len(sources), "chunks": sum(s["chunk_count"] for s in sources)},
        "sources": sources,
        "digest": "d" + str(sorted(s["chunks_fingerprint"] for s in sources)),
    }


def test_compare_reports_identical_snapshots():
    snap = _snap([{"source_id": "A", "chunk_count": 3, "chunks_fingerprint": "f1"}])

    diff = snapshot.compare_snapshots(snap, snap)

    assert diff["same"] is True
    assert diff["sources_added"] == []
    assert diff["sources_removed"] == []
    assert diff["sources_changed"] == []


def test_compare_detects_added_and_removed_sources():
    old = _snap([{"source_id": "A", "chunk_count": 1, "chunks_fingerprint": "f1"}])
    new = _snap([{"source_id": "B", "chunk_count": 1, "chunks_fingerprint": "f2"}])

    diff = snapshot.compare_snapshots(old, new)

    assert diff["same"] is False
    assert diff["sources_added"] == ["B"]
    assert diff["sources_removed"] == ["A"]


def test_compare_detects_changed_content_of_the_same_source():
    """同じ文書でも本文が変わったことを指紋で見分ける。"""
    old = _snap([{"source_id": "A", "chunk_count": 3, "chunks_fingerprint": "f1"}])
    new = _snap([{"source_id": "A", "chunk_count": 3, "chunks_fingerprint": "CHANGED"}])

    diff = snapshot.compare_snapshots(old, new)

    assert diff["same"] is False
    assert diff["sources_changed"] == ["A"]


def test_compare_reports_count_differences():
    old = _snap([{"source_id": "A", "chunk_count": 3, "chunks_fingerprint": "f1"}])
    new = _snap([{"source_id": "A", "chunk_count": 5, "chunks_fingerprint": "f2"}])

    assert snapshot.compare_snapshots(old, new)["counts"] == {
        "chunks": {"old": 3, "new": 5},
    }


def test_empty_corpus_does_not_divide_by_zero(clean_corpus, client):
    report = snapshot.build_quality_report(client=client)

    assert _check(report, "garbling_ratio")["value"] == 0
    assert report["passed"] is True
