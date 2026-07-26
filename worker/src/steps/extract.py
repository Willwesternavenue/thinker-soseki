"""テキスト抽出(仕様6.2)。PDF / Word / TXT / YouTube標準書き起こしに対応。

戻り値はページ単位のリスト。ページ概念がない形式は1要素になる。
スキャンPDF / OCRが必要なPDFは対象外(抽出結果が空なら失敗にする)。
"""

import io

from docx import Document
from pypdf import PdfReader


def extract_pages(file_bytes: bytes, file_type: str) -> list[str]:
    """ファイルからページごとのテキストを抽出する。

    file_type: 'pdf' / 'docx' / 'txt'
    """
    if file_type == "pdf":
        return _extract_pdf(file_bytes)
    if file_type == "docx":
        return [_extract_docx(file_bytes)]
    if file_type == "txt":
        return [file_bytes.decode("utf-8", errors="replace")]
    raise ValueError(f"未対応のファイル形式: {file_type}")


def _extract_pdf(file_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = [(page.extract_text() or "") for page in reader.pages]
    if not any(p.strip() for p in pages):
        raise ValueError(
            "PDFからテキストを抽出できませんでした。スキャンPDF / OCRが必要なPDFはMVP対象外です(仕様1.3)"
        )
    return pages


def _extract_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    parts: list[str] = []
    for para in doc.paragraphs:
        style = (para.style.name or "") if para.style else ""
        text = para.text
        # 見出しスタイルはチャンカーの章検出のためにマーカーを付ける
        if style.startswith("Heading") and text.strip():
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            parts.append(f"{'#' * int(level)} {text}")
        else:
            parts.append(text)
    return "\n".join(parts)


def guess_file_type(file_name: str) -> str:
    lower = file_name.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".docx") or lower.endswith(".doc"):
        return "docx"
    if lower.endswith(".txt"):
        return "txt"
    raise ValueError(f"未対応の拡張子: {file_name}")
