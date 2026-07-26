"""青空文庫 ingestion のテスト(C-T5 / 仕様 docs/CORPUS_T1_SPEC.md §7)。

edition → 取得 → 正規化 → sources/source_chunks 投入 までを繋ぐ。
"""

import io
import zipfile

import pytest

from src.aozora import ingest
from tests.conftest import _new_job  # noqa: F401  (フィクスチャの都合で読み込む)

SAMPLE = """夢十夜
夏目漱石

-------------------------------------------------------
【テキスト中に現れる記号について】

《》：ルビ
-------------------------------------------------------

［＃５字下げ］第一夜［＃「第一夜」は中見出し］

　こんな夢を見た。
　腕組をして枕元に坐《すわ》っていると、女が云う。
「死んだら、埋めて下さい」
　自分は黙っていた。

底本：「夏目漱石全集10巻」ちくま文庫、筑摩書房
入力：野口英司
"""


def _zip_bytes(text: str, name: str = "yume.txt") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text.encode("cp932"))
    return buf.getvalue()


def test_extract_text_from_zip():
    """zipから本文テキストを取り出しUTF-8へ変換する。"""
    text, ratio = ingest.extract_text_from_zip(_zip_bytes(SAMPLE))

    assert "こんな夢を見た。" in text
    assert ratio == 0.0


def test_extract_text_rejects_zip_without_txt():
    with pytest.raises(ValueError, match="テキストファイル"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.md", b"x")
        ingest.extract_text_from_zip(buf.getvalue())


def test_mirror_url_uses_github_not_aozora():
    """一括取得はGitHubミラー経由にする(aozora.gr.jpへ機械的に連続アクセスしない)。"""
    url = ingest.mirror_url(
        "https://www.aozora.gr.jp/cards/000148/files/799_ruby_6024.zip"
    )
    assert url.startswith("https://raw.githubusercontent.com/aozorabunko/aozorabunko/")
    assert url.endswith("cards/000148/files/799_ruby_6024.zip")


# ── DBへの投入 ──


def _seed_edition(client, *, edition_id="000799", text_url="https://www.aozora.gr.jp/cards/000148/files/799_ruby_6024.zip",
                  work_status="published"):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": "cw_test", "person_id": "natsume_soseki",
        "canonical_title": "夢十夜", "canonical_title_reading": "ゆめじゅうや",
        "ndc": "NDC 913",
    }).execute()
    client.table("work_editions").upsert({
        "edition_id": edition_id, "canonical_work_id": "cw_test",
        "aozora_work_id": edition_id, "orthography": "新字新仮名",
        "work_status": work_status, "is_primary_retrieval_edition": True,
        "text_file_url": text_url,
    }).execute()


def test_ingest_edition_creates_source_and_chunks(clean_corpus, client):
    """本文を取得して sources と source_chunks を作る。"""
    _seed_edition(client)

    result = ingest.ingest_edition(
        "000799", client=client, fetch=lambda url: _zip_bytes(SAMPLE)
    )

    assert result["chunks"] > 0
    src = (
        client.table("sources").select("*")
        .eq("source_id", result["source_id"]).single().execute().data
    )
    # 文書単位のタグが付いていること
    assert src["source_provider"] == "aozora"
    assert src["document_genre"] == "short_story"
    assert src["corpus_role"] == "narrative_reference"
    assert src["authority_level"] == "fictional_indirect"
    assert src["edition_id"] == "000799"
    # 底本情報が保存されていること(指示書§2.4)
    assert "底本" in src["corpus_metadata"]["colophon"]


def test_ingest_edition_tags_chunks_by_speaker_role(clean_corpus, client):
    """小説の会話文と地の文が speaker_role で区別されること(指示書の核心)。"""
    _seed_edition(client)

    result = ingest.ingest_edition(
        "000799", client=client, fetch=lambda url: _zip_bytes(SAMPLE)
    )

    chunks = (
        client.table("source_chunks").select("*")
        .eq("source_id", result["source_id"]).execute().data
    )
    roles = {c["speaker_role"] for c in chunks}
    assert "character" in roles, "会話文が登場人物として区別されること"
    assert "narrator" in roles
    # 小説チャンクは思想の根拠にしない
    assert all(c["thought_eligibility"] == "excluded" for c in chunks)
    assert all(c["chunker_version"] == "aozora_v1" for c in chunks)


def test_ingest_edition_records_provenance(clean_corpus, client):
    """再現性のため hash と parser version を edition へ記録する。"""
    _seed_edition(client)

    ingest.ingest_edition("000799", client=client, fetch=lambda url: _zip_bytes(SAMPLE))

    ed = (
        client.table("work_editions").select("*")
        .eq("edition_id", "000799").single().execute().data
    )
    assert len(ed["content_sha256"]) == 64
    assert ed["parser_version"] == "aozora_v1"
    assert ed["retrieved_at"] is not None


def test_ingest_refuses_in_progress_edition(clean_corpus, client):
    """作業中の版は本文を取得しない(指示書§2.1)。"""
    _seed_edition(client, edition_id="046611", work_status="in_progress")

    with pytest.raises(ValueError, match="作業中"):
        ingest.ingest_edition("046611", client=client, fetch=lambda url: b"")


def test_ingest_refuses_edition_without_text_file(clean_corpus, client):
    """本文ファイルが無い版(000790)は取り込まない。"""
    _seed_edition(client, edition_id="000790", text_url=None)

    with pytest.raises(ValueError, match="テキストファイル"):
        ingest.ingest_edition("000790", client=client, fetch=lambda url: b"")


def test_ingest_is_idempotent(clean_corpus, client):
    """再取り込みしてもチャンクが重複しない。"""
    _seed_edition(client)
    fetch = lambda url: _zip_bytes(SAMPLE)  # noqa: E731

    first = ingest.ingest_edition("000799", client=client, fetch=fetch)
    second = ingest.ingest_edition("000799", client=client, fetch=fetch)

    assert first["source_id"] == second["source_id"]
    chunks = (
        client.table("source_chunks").select("chunk_id")
        .eq("source_id", first["source_id"]).execute().data
    )
    assert len(chunks) == first["chunks"]


def test_ingest_rejects_high_garbling_ratio(clean_corpus, client):
    """文字化け率が閾値を超えたらIndex登録しない(指示書§8.2)。

    ⚠️ CP932は多くのバイト列を有効な文字として解釈するため、テストには
    実際に置換文字が出る並び(0xf0 0xfd 0x80 0xff)を使う。
    """
    _seed_edition(client)
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("x.txt", b"\xf0\xfd\x80\xff" * 200)

    with pytest.raises(ValueError, match="文字化け"):
        ingest.ingest_edition(
            "000799", client=client, fetch=lambda url: broken.getvalue()
        )


# ── embedding(C-T5 後段) ──


def test_embed_pending_only_targets_aozora_chunks_without_embedding(clean_corpus, client):
    """embedding未生成の aozora チャンクだけを対象にする(既存チャンクを触らない)。"""
    _seed_edition(client)
    result = ingest.ingest_edition(
        "000799", client=client, fetch=lambda url: _zip_bytes(SAMPLE)
    )

    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.01] * 1536 for _ in texts]

    done = ingest.embed_pending_chunks(client=client, embed=fake_embed)

    assert done == result["chunks"]
    assert len(calls) == 1, "1バッチで処理されること"
    # 2回目は対象が無い(冪等)
    assert ingest.embed_pending_chunks(client=client, embed=fake_embed) == 0


def test_embed_pending_writes_vectors(clean_corpus, client):
    """生成したベクトルが source_chunks.embedding に入ること。"""
    _seed_edition(client)
    ingest.ingest_edition("000799", client=client, fetch=lambda url: _zip_bytes(SAMPLE))

    ingest.embed_pending_chunks(
        client=client, embed=lambda texts: [[0.02] * 1536 for _ in texts]
    )

    rows = (
        client.table("source_chunks").select("chunk_id, embedding")
        .eq("chunker_version", "aozora_v1").execute().data
    )
    assert rows
    assert all(r["embedding"] is not None for r in rows)


def test_embed_pending_skips_existing_thought_mode_chunks(clean_corpus, client):
    """既存の思想モード(chunker_version='v1')のチャンクは対象にしない。"""
    _seed_edition(client)
    ingest.ingest_edition("000799", client=client, fetch=lambda url: _zip_bytes(SAMPLE))
    client.table("source_chunks").insert({
        "chunk_id": "LEGACY_001", "source_id": "AOZORA_000799",
        "person_id": "natsume_soseki", "text": "既存形式のチャンク",
        "chunker_version": "v1", "chunk_hash": "legacy",
    }).execute()

    embedded = []

    def fake_embed(texts):
        embedded.extend(texts)
        return [[0.03] * 1536 for _ in texts]

    ingest.embed_pending_chunks(client=client, embed=fake_embed)

    assert "既存形式のチャンク" not in embedded
