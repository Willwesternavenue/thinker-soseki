"""ジョブランナー(仕様4.3 / 6章)。

ingestion_jobs をポーリングし、以下のステップを順次実行する:
  extract → clean → chunk → embed → distill_light

- 各ステップで ingestion_jobs.current_step を更新
- 失敗時は status='failed' + error_message(管理画面から status='pending' に戻して再実行)
- chunk_hash による差分検出で、再実行時は変更のあったチャンクのみ再embedding・再蒸留
"""

import os
import sys
import threading
import time
import traceback

from . import db
from .config import CHUNKER_VERSION
from .creative import generate as creative_generate
from .creative import repo as creative_repo
from .steps import chunk as chunk_step
from .steps import clean as clean_step
from .steps import distill_light
from .steps import embed as embed_step
from .steps import extract as extract_step

POLL_INTERVAL_SEC = 5
HEARTBEAT_INTERVAL_SEC = 10
WORKER_NAME = "ingestion"

# 処理ステップの正式な並び(UIの進捗表示 N/5 に使う)
STEPS = ["extract", "clean", "chunk", "embed", "distill_light"]

# heartbeatの現在状態(バックグラウンドスレッドが定期的にDBへ書き込む)
_hb_state: dict[str, str | None] = {
    "status": "idle",
    "current_job_id": None,
    "current_source_id": None,
}
_hb_lock = threading.Lock()


def _set_heartbeat_state(status: str, job_id: str | None = None, source_id: str | None = None) -> None:
    with _hb_lock:
        _hb_state["status"] = status
        _hb_state["current_job_id"] = job_id
        _hb_state["current_source_id"] = source_id


def _write_heartbeat() -> None:
    with _hb_lock:
        payload = {
            "worker_name": WORKER_NAME,
            "status": _hb_state["status"],
            "current_job_id": _hb_state["current_job_id"],
            "current_source_id": _hb_state["current_source_id"],
            "last_seen_at": "now()",
        }
    try:
        db.client().table("worker_heartbeats").upsert(payload).execute()
    except Exception as exc:  # heartbeat失敗はジョブ処理を止めない
        print(f"heartbeat失敗(無視): {exc}")


def _heartbeat_loop() -> None:
    """処理中も含めて一定間隔でheartbeatを書き続けるデーモンスレッド。"""
    while True:
        _write_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL_SEC)


def _reclaim_orphaned_jobs() -> None:
    """起動時に、前回のクラッシュ/停止で running のまま取り残されたジョブを pending に戻す。

    Workerは単一プロセス想定なので、起動時点の running は全て孤児。放置すると
    UIで「処理中のまま経過時間だけ増える」ゾンビになるため回収する。
    """
    for table in ("ingestion_jobs", "distillation_jobs", "creative_generations"):
        res = (
            db.client().table(table)
            .update({"status": "pending", "current_step": None})
            .eq("status", "running").execute()
        )
        if res.data:
            print(f"起動時回収: {table} の取り残し {len(res.data)}件を pending に戻した")


def run_creative_once() -> bool:
    """pendingの創作生成ジョブを1件処理する。処理した場合 True。

    claimは既存ジョブと同じく非排他(単一worker前提)。
    """
    job = creative_repo.claim_next_generation()
    if not job:
        return False
    _set_heartbeat_state("processing", job_id=job["job_id"])
    _write_heartbeat()
    creative_generate.process_generation(job)
    return True


def run_forever() -> None:
    print(
        f"worker[{WORKER_NAME}]: ingestion_jobs / distillation_jobs / "
        "creative_generations のポーリングを開始"
    )
    _reclaim_orphaned_jobs()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    _write_heartbeat()
    while True:
        try:
            did = run_once() or run_distillation_once() or run_creative_once()
        except Exception as exc:
            # ポーリング/処理中の一過性エラー(DBのstatement timeout・接続断など)で
            # Workerプロセスごと落とさない。ログして少し待ち、ループを継続する。
            traceback.print_exc()
            print(f"worker: 一過性エラーを無視して継続: {exc}")
            _set_heartbeat_state("idle")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        if not did:
            _set_heartbeat_state("idle")
            time.sleep(POLL_INTERVAL_SEC)


def run_once() -> bool:
    """pendingジョブを1件処理する。処理した場合 True。"""
    jobs = (
        db.client()
        .table("ingestion_jobs")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(1)
        .execute()
    )
    if not jobs.data:
        return False
    job = jobs.data[0]
    process_job(job)
    return True


def _set_step(job_id: str, step: str, source_id: str | None = None) -> None:
    print(f"job {job_id}: {step}")
    # ステップ更新のたびにheartbeat状態も更新(バックグラウンドスレッドがDBへ反映)
    _set_heartbeat_state("processing", job_id=job_id, source_id=source_id)
    _write_heartbeat()
    db.client().table("ingestion_jobs").update(
        {"status": "running", "current_step": step, "error_message": None}
    ).eq("job_id", job_id).execute()


def process_job(job: dict) -> None:
    job_id = job["job_id"]
    source_id = job["source_id"]
    try:
        source = (
            db.client().table("sources").select("*").eq("source_id", source_id)
            .single().execute().data
        )
        persona = (
            db.client().table("personas").select("*")
            .eq("person_id", source["person_id"]).single().execute().data
        )

        # 1. 抽出
        _set_step(job_id, "extract", source_id)
        raw = db.client().storage.from_("originals").download(source["original_file_path"])
        file_type = source["file_type"] or extract_step.guess_file_type(
            source["original_file_path"]
        )
        pages = extract_step.extract_pages(raw, file_type)

        # 2. 整形(話者正規化・ページ/文字位置保持)
        _set_step(job_id, "clean", source_id)
        cleaned = clean_step.clean_pages(pages, source["source_type"])
        clean_path = f"{source_id}.txt"
        db.client().storage.from_("clean_texts").upload(
            clean_path, cleaned.text.encode("utf-8"),
            {"content-type": "text/plain; charset=utf-8", "upsert": "true"},
        )
        db.client().table("sources").update(
            {"clean_text_path": clean_path, "status": "cleaned"}
        ).eq("source_id", source_id).execute()

        # 3. チャンク化(決定的。chunk_hashで差分検出)
        _set_step(job_id, "chunk", source_id)
        chunks = chunk_step.chunk_source(
            source_id, cleaned.text, source["source_type"], cleaned.page_offsets
        )
        if not chunks:
            raise RuntimeError("チャンクが1件も生成されませんでした")

        existing = (
            db.client().table("source_chunks")
            .select("chunk_id, chunk_hash")
            .eq("source_id", source_id)
            .execute()
        )
        existing_hashes = {r["chunk_id"]: r["chunk_hash"] for r in existing.data}
        changed_ids = {
            c.chunk_id for c in chunks
            if existing_hashes.get(c.chunk_id) != c.chunk_hash
        }

        rows = []
        for c in chunks:
            rows.append(
                {
                    "chunk_id": c.chunk_id,
                    "source_id": source_id,
                    "person_id": source["person_id"],
                    "chapter_id": c.chapter_id,
                    "chapter_title": c.chapter_title,
                    "section_title": c.section_title,
                    "source_page": c.source_page,
                    "char_start": c.char_start,
                    "char_end": c.char_end,
                    "locator_note": (
                        f"PDF上の{c.source_page}ページ。書籍実ページとは異なる可能性あり。"
                        if c.source_page is not None and file_type == "pdf" else None
                    ),
                    "chunk_type": c.chunk_type,
                    "speaker": c.speaker,
                    "verbatim": c.verbatim,
                    "question": c.question,
                    "answer": c.answer,
                    "timestamp_start": c.timestamp_start,
                    "timestamp_end": c.timestamp_end,
                    "text": c.text,
                    "chunker_version": c.chunker_version,
                    "chunk_hash": c.chunk_hash,
                    "status": "active",
                }
            )
        # embedding / related_thought_ids は含めない(既存値・派生列を保持)
        db.client().table("source_chunks").upsert(rows).execute()

        # 今回のチャンク一覧に存在しない旧チャンクは検索対象外にする
        current_ids = [c.chunk_id for c in chunks]
        stale = [cid for cid in existing_hashes if cid not in set(current_ids)]
        if stale:
            db.update_in("source_chunks", {"status": "superseded"}, "chunk_id", stale)

        db.client().table("sources").update({"status": "chunked"}).eq(
            "source_id", source_id
        ).execute()

        # 4. Embedding(新規・変更チャンクのみ)
        _set_step(job_id, "embed", source_id)
        need_embed = (
            db.client().table("source_chunks")
            .select("chunk_id, text")
            .eq("source_id", source_id)
            .eq("status", "active")
            .is_("embedding", "null")
            .execute()
        ).data
        need_embed_ids = {r["chunk_id"] for r in need_embed} | changed_ids
        targets = [c for c in chunks if c.chunk_id in need_embed_ids]
        if targets:
            vectors = embed_step.embed_texts([c.text for c in targets])
            for c, vec in zip(targets, vectors):
                db.client().table("source_chunks").update({"embedding": vec}).eq(
                    "chunk_id", c.chunk_id
                ).execute()
        db.client().table("sources").update({"status": "embedded"}).eq(
            "source_id", source_id
        ).execute()

        # 5. 軽蒸留(蒸留未実施 or 変更のあったチャンクのみ)
        _set_step(job_id, "distill_light", source_id)
        distilled = db.select_in(
            "chunk_distillations", "chunk_id", "chunk_id", current_ids
        )
        distilled_ids = {r["chunk_id"] for r in distilled}
        to_distill = [
            {"chunk_id": c.chunk_id, "chapter_title": c.chapter_title, "text": c.text}
            for c in chunks
            if c.chunk_id in changed_ids or c.chunk_id not in distilled_ids
        ]
        if to_distill:
            known_cards = (
                db.client().table("thought_cards")
                .select("thought_id")
                .eq("person_id", source["person_id"])
                .execute()
            ).data
            # 概念エイリアスの正規IDもヒントに含め、チャンク間で思想IDが揺れないようにする
            known_concepts = (
                db.client().table("concept_aliases")
                .select("concept_id, canonical_label")
                .eq("person_id", source["person_id"])
                .execute()
            ).data
            known_ids = sorted(
                {r["thought_id"] for r in known_cards}
                | {f"{r['concept_id']} = {r['canonical_label']}" for r in known_concepts}
            )
            # 保存は distill_chunks 内で行う(部分失敗時も成功分を残すため)
            distill_light.distill_chunks(
                to_distill,
                person_name=persona["display_name"],
                source_title=source["title"],
                source_type=source["source_type"],
                known_thought_ids=known_ids,
                job_id=job_id,
            )

        db.client().table("sources").update({"status": "distilled"}).eq(
            "source_id", source_id
        ).execute()
        db.client().table("ingestion_jobs").update(
            {"status": "succeeded", "current_step": "done"}
        ).eq("job_id", job_id).execute()
        print(f"job {job_id}: 完了({len(chunks)}チャンク、蒸留{len(to_distill)}件)")

    except Exception as exc:
        traceback.print_exc()
        db.client().table("ingestion_jobs").update(
            {"status": "failed", "error_message": str(exc)[:2000]}
        ).eq("job_id", job_id).execute()


# ── 蒸留→カード生成ジョブ(管理画面のボタンから投入される) ──────────

def run_distillation_once() -> bool:
    """pendingの distillation_jobs を1件処理する。処理した場合 True。"""
    jobs = (
        db.client()
        .table("distillation_jobs")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(1)
        .execute()
    )
    if not jobs.data:
        return False
    process_distillation_job(jobs.data[0])
    return True


def _set_distill_step(job_id: str, step: str) -> None:
    print(f"distill job {job_id}: {step}")
    _set_heartbeat_state("processing", job_id=job_id, source_id=step)
    _write_heartbeat()
    db.client().table("distillation_jobs").update(
        {"status": "running", "current_step": step, "error_message": None}
    ).eq("job_id", job_id).execute()


def process_distillation_job(job: dict) -> None:
    from .steps import distill_heavy, distill_source, gen_cards, gen_questions

    job_id = job["job_id"]
    person_id = job["person_id"]
    kind = job.get("kind", "all")
    result: dict = {}
    try:
        if kind in ("all", "heavy"):
            _set_distill_step(job_id, "heavy_distill")
            result["heavy"] = distill_heavy.run(person_id=person_id)

        if kind == "all":
            _set_distill_step(job_id, "source_distill")
            sources = (
                db.client().table("sources").select("source_id")
                .eq("person_id", person_id).eq("status", "distilled").execute()
            ).data
            result["sources"] = 0
            for s in sources:
                distill_source.run(s["source_id"])
                result["sources"] += 1

        if kind in ("all", "cards"):
            _set_distill_step(job_id, "card_generation")
            result["cards"] = len(gen_cards.run(person_id=person_id))

        if kind in ("all", "cards", "questions"):
            _set_distill_step(job_id, "question_generation")
            result["questions"] = gen_questions.run_for_all_cards(person_id=person_id)

        db.client().table("distillation_jobs").update(
            {"status": "succeeded", "current_step": "done", "result": result}
        ).eq("job_id", job_id).execute()
        print(f"distill job {job_id}: 完了 {result}")
    except Exception as exc:
        traceback.print_exc()
        db.client().table("distillation_jobs").update(
            {"status": "failed", "error_message": str(exc)[:2000], "result": result}
        ).eq("job_id", job_id).execute()


def _start_health_server(port: int) -> None:
    """Cloud Run用の最小ヘルスサーバ(仕様外・インフラ都合)。

    Cloud Runのサービスは $PORT でHTTPを受ける必要があるため、ポーリングループとは
    別スレッドで200を返すだけのサーバを立てる。ローカル実行(PORT未設定)では起動しない。
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.serverの規約)
            with _hb_lock:
                status = _hb_state.get("status", "unknown")
            body = f'{{"worker": "{WORKER_NAME}", "status": "{status}"}}'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # アクセスログは不要
            pass

    server = HTTPServer(("0.0.0.0", port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"health server: :{port}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once() or run_distillation_once()
        _set_heartbeat_state("idle")
        _write_heartbeat()
    else:
        port = os.environ.get("PORT")
        if port:
            _start_health_server(int(port))
        run_forever()
