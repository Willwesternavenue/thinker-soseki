"""原典ファイルを取り込む(admin UIの uploadSource と同じ処理をCLIで)。

Storageの originals バケットへアップロード → sources を連番採番で作成 →
ingestion_jobs(pending)を作成。あとは常駐Workerが
extract→clean→chunk→embed→distill_light を実行する。

  # 単体(タイトル明示)
  uv run python -m src.ingest_source --file "/path/transcript.txt" --title "動画タイトル"

  # ディレクトリ一括(ヘッダーの「動画名：〜」からタイトル自動抽出、無ければファイル名)
  uv run python -m src.ingest_source --dir "/path/to/vendor_txts" --priority core
  uv run python -m src.ingest_source --dir "/path/to/vendor_txts" --dry-run   # 登録せず抽出結果だけ表示

txtファイルの先頭数行に「動画名：〜」「動画名: 〜」があればタイトル、URL行があれば
source_url に自動格納する(ベンダー納品書き起こしの標準ヘッダー。無いファイルもある)。
対談形式(社長:/司会者: 等のラベル)はQAペア、話者ラベル無しは本人発言モノローグにチャンク化。
"""

import argparse
import re
import sys
from pathlib import Path

from . import db

PERSON_ID = "x_shigyo"

ID_PREFIX = {
    "book": "BOOK",
    "video_transcript": "VIDEO",
    "interview": "INTV",
    "dialogue": "DLG",
    "lecture": "LECT",
    "article": "ART",
    "essay": "ESSAY",
    "profile": "PROF",
    "document": "DOC",
    "other": "OTH",
}

CONTENT_TYPES = {
    "txt": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

# ヘッダー抽出(全角/半角コロン両対応)。先頭の数行のみ見る。
_TITLE_RE = re.compile(r"^\s*動画名\s*[：:]\s*(.+?)\s*$")
_URL_RE = re.compile(r"https?://\S+")
_WRAP_RE = re.compile(r"^[【「『（(]\s*(.+?)\s*[】」』）)]$")
_HEADER_SCAN_LINES = 6


def guess_file_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".docx") or lower.endswith(".doc"):
        return "docx"
    if lower.endswith(".txt"):
        return "txt"
    raise ValueError(f"未対応の拡張子です: {name}(PDF / Word / TXT のみ)")


def parse_header(file_path: Path) -> tuple[str | None, str | None]:
    """txtファイル先頭から「動画名：〜」のタイトルとURLを抽出する。無ければNone。

    タイトルが全体を【】「」等で括っている場合は外側の括弧のみ外す。
    txt以外(docx等)はここでは読めないため (None, None) を返す。
    """
    if file_path.suffix.lower() != ".txt":
        return None, None
    try:
        head = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()[
            :_HEADER_SCAN_LINES
        ]
    except OSError:
        return None, None
    title: str | None = None
    url: str | None = None
    for line in head:
        line = line.lstrip("﻿").strip()
        if title is None:
            m = _TITLE_RE.match(line)
            if m:
                t = m.group(1).strip()
                wrap = _WRAP_RE.match(t)
                title = wrap.group(1).strip() if wrap else t
                continue
        if url is None:
            u = _URL_RE.search(line)
            if u:
                url = u.group(0)
    return title, url


def next_source_id(sb, prefix: str) -> str:
    res = (
        sb.table("sources")
        .select("source_id")
        .like("source_id", f"{prefix}\\_%")
        .order("source_id", desc=True)
        .limit(1)
        .execute()
    )
    last = 0
    if res.data:
        try:
            last = int(res.data[0]["source_id"].split("_")[-1])
        except ValueError:
            last = 0
    return f"{prefix}_{last + 1:03d}"


def ingest_one(
    sb,
    file_path: Path,
    title: str,
    source_type: str,
    priority: str,
    author: str | None,
    source_url: str | None = None,
) -> str:
    file_type = guess_file_type(file_path.name)
    ext = file_path.suffix.lstrip(".").lower() or "bin"
    prefix = ID_PREFIX.get(source_type, "DOC")
    source_id = next_source_id(sb, prefix)

    # Storageキーは日本語・スペース不可のため固定名にする
    storage_path = f"{source_id}/original.{ext}"
    data = file_path.read_bytes()
    sb.storage.from_("originals").upload(
        storage_path,
        data,
        {"content-type": CONTENT_TYPES.get(ext, "application/octet-stream"), "upsert": "true"},
    )

    sb.table("sources").insert(
        {
            "source_id": source_id,
            "person_id": PERSON_ID,
            "title": title,
            "source_type": source_type,
            "author": author,
            "file_type": file_type,
            "priority": priority,
            "status": "raw",
            "original_file_path": storage_path,
            "source_url": source_url,
        }
    ).execute()

    sb.table("ingestion_jobs").insert(
        {"source_id": source_id, "status": "pending"}
    ).execute()

    return source_id


def _resolve_title(explicit: str | None, header_title: str | None, path: Path) -> str:
    """タイトル優先順位: 明示指定 > ヘッダー動画名 > ファイル名。"""
    if explicit:
        return explicit
    if header_title:
        return header_title
    # ファイル名フォールバック: 先頭の連番プレフィックス(例 174_)は除く
    stem = re.sub(r"^\d+[_\s]+", "", path.stem)
    return stem or path.stem


def _collect_files(args) -> list[Path]:
    files: list[Path] = []
    for f in args.file or []:
        files.append(Path(f))
    if args.dir:
        d = Path(args.dir)
        if not d.is_dir():
            sys.exit(f"ディレクトリが見つかりません: {args.dir}")
        found: list[Path] = []
        for pat in ("*.txt", "*.docx", "*.pdf"):
            found.extend(d.glob(pat))
        files.extend(sorted(found))
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", help="取り込むファイル(複数可)")
    ap.add_argument("--dir", help="ディレクトリ内の *.txt/*.docx/*.pdf を一括取り込み")
    ap.add_argument(
        "--title",
        action="append",
        help="タイトル(--fileと同順。省略時はヘッダー動画名→ファイル名)",
    )
    ap.add_argument("--type", default="video_transcript", help="source_type(既定: video_transcript)")
    ap.add_argument("--priority", default="important", help="core / important / support(既定: important)")
    ap.add_argument("--author", default="執行草舟")
    ap.add_argument("--dry-run", action="store_true", help="登録せず抽出結果だけ表示")
    args = ap.parse_args()

    files = _collect_files(args)
    if not files:
        sys.exit("--file または --dir を指定してください")

    titles = args.title or []
    if titles and (args.dir or len(titles) != len(files)):
        sys.exit("--title は --file と同数のときのみ使えます(--dirとは併用不可)")

    for p in files:
        if not p.exists():
            sys.exit(f"ファイルが見つかりません: {p}")

    sb = None if args.dry_run else db.client()
    registered = 0
    for i, p in enumerate(files):
        header_title, url = parse_header(p)
        explicit = titles[i] if titles else None
        title = _resolve_title(explicit, header_title, p)
        src = "指定" if explicit else ("ヘッダー" if header_title else "ファイル名")
        if args.dry_run:
            print(f"[dry] {p.name}\n      title[{src}]={title!r}  url={url or '—'}")
            continue
        sid = ingest_one(sb, p, title, args.type, args.priority, args.author, url)
        registered += 1
        print(f"取り込み登録: {sid}  ←  {p.name}\n      title[{src}]={title!r}  url={url or '—'}")

    if args.dry_run:
        print(f"\n[dry-run] {len(files)}件を確認(登録なし)。")
    else:
        print(f"\n{registered}件をpendingで登録。常駐Workerが順次処理します(/admin/jobs で進捗確認)。")


if __name__ == "__main__":
    main()
