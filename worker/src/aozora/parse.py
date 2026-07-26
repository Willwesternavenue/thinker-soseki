"""青空文庫 Parser / Normalizer(C-T3)。

正本仕様: docs/CORPUS_T1_SPEC.md §7 / 上位指示 §8。

保存する本文は3形式(指示書§8.1):
- raw_text        : 文字コード変換後・注記を残した本文(校訂・原情報)
- normalized_text : ルビ・注記を除いた本文(検索・embedding用)
- display_text    : ルビを読みとして残した本文(ユーザー表示・引用用)

注記(［＃…］)は**単純削除しない**。種別に分類して記録する(指示書§8.4)。
"""

import hashlib
import re

# 本文の解釈が変わる修正をしたら上げる(既存の CHUNKER_VERSION='v1' とは別系統)
PARSER_VERSION = "aozora_v1"

# ヘッダ(記号説明)は罫線で囲まれている
_HEADER_RULE = re.compile(r"^-{10,}$", re.MULTILINE)
# フッタの開始。底本情報から後は本文ではない
_COLOPHON_START = re.compile(r"^底本[：:]", re.MULTILINE)

# ルビ: ｜で範囲が明示される場合と、直前の漢字列に付く場合がある
_RUBY_WITH_MARKER = re.compile(r"｜([^｜《》]+)《([^《》]+)》")
_RUBY_PLAIN = re.compile(r"([一-鿿々ヶ]+)《([^《》]+)》")
# 上記で拾えない《》。本文からは外し、読みだけ拾う
_RUBY_FALLBACK = re.compile(r"《([^《》]+)》")

_NOTE = re.compile(r"［＃[^］]*］")
# 見出しの注記から章題を取る
_HEADING_NOTE = re.compile(r"［＃「([^」]+)」は(?:大|中|小)見出し］")


def decode_aozora_bytes(raw: bytes, *, with_ratio: bool = False):
    """青空文庫のテキストをUTF-8文字列へ変換する。

    ⚠️ `shift_jis` ではなく **CP932** を使う。青空文庫の本文には機種依存文字が
    含まれ、shift_jis 指定では復号に失敗する(指示書§8.2)。
    変換できないバイトは置換し、その割合を返せるようにする(閾値超過は
    Index登録しない運用のため)。
    """
    text = raw.decode("cp932", errors="replace")
    if not with_ratio:
        return text
    ratio = text.count("�") / len(text) if text else 0.0
    return text, ratio


def _parse_colophon(text: str) -> dict:
    """奥付を「見出し：値」の辞書にする。継続行(全角空白始まり)は連結する。"""
    colophon: dict[str, str] = {}
    key = None
    for line in text.split("\n"):
        if not line.strip():
            continue
        m = re.match(r"^([^：:]{2,12})[：:](.*)$", line)
        if m and not line.startswith("　"):
            key = m.group(1).strip()
            colophon[key] = m.group(2).strip()
        elif key and line.startswith("　"):
            colophon[key] += " " + line.strip()
    return colophon


def split_document(text: str) -> dict:
    """タイトル・著者・本文・奥付に分ける(指示書§8.5)。

    青空文庫の記号説明と底本情報は本文チャンクへ混ぜない。ただし破棄せず
    metadata として保存する。
    """
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    title = lines[0].strip() if lines else ""
    author = lines[1].strip() if len(lines) > 1 else ""

    # ヘッダ(罫線で囲まれた記号説明)を落とす
    rules = list(_HEADER_RULE.finditer(text))
    if len(rules) >= 2:
        body_start = rules[1].end()
    else:
        # 記号説明が無いファイルもある。その場合は著者行の後ろから
        body_start = sum(len(x) + 1 for x in lines[:2])

    colophon_match = _COLOPHON_START.search(text)
    body_end = colophon_match.start() if colophon_match else len(text)
    colophon_text = text[body_end:] if colophon_match else ""

    return {
        "title": title,
        "author": author,
        "body": text[body_start:body_end].strip("\n"),
        "colophon": _parse_colophon(colophon_text),
        "colophon_raw": colophon_text.strip(),
    }


def extract_ruby(text: str) -> tuple[str, list[dict]]:
    """ルビを本文から外し、surface と reading の対で返す(指示書§8.3)。

    embedding用本文には**漢字を残し、読みは重複挿入しない**。
    """
    rubies: list[dict] = []

    def take(m: re.Match) -> str:
        rubies.append({"surface": m.group(1), "reading": m.group(2)})
        return m.group(1)

    out = _RUBY_WITH_MARKER.sub(take, text)
    out = _RUBY_PLAIN.sub(take, out)

    def take_rest(m: re.Match) -> str:
        rubies.append({"surface": "", "reading": m.group(1)})
        return ""

    return _RUBY_FALLBACK.sub(take_rest, out), rubies


def ruby_to_display(text: str) -> str:
    """表示用: ルビを（読み）の形で残す。"""
    out = _RUBY_WITH_MARKER.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", text)
    return _RUBY_PLAIN.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", out)


def classify_note(note: str) -> str:
    """入力者注を種別に分類する(指示書§8.4)。単純削除しないための分類。"""
    if "傍点" in note or "太字" in note or "強調" in note:
        return "emphasis_note"
    if "水準" in note or "Unicode" in note or "unicode" in note:
        return "gaiji_note"
    if any(k in note for k in ("字下げ", "見出し", "地から", "改丁", "改ページ", "字上げ")):
        return "formatting_note"
    if "ページ" in note:
        return "page_note"
    if "底本" in note or "ママ" in note:
        return "original_text_note"
    return "editorial_note"


def extract_notes(text: str) -> tuple[str, list[dict]]:
    """注記を本文から外し、種別付きで記録する。単純削除はしない。"""
    notes: list[dict] = []

    def take(m: re.Match) -> str:
        raw = m.group(0)
        notes.append({"raw": raw, "kind": classify_note(raw)})
        return ""

    return _NOTE.sub(take, text), notes


def split_chapters(body: str) -> list[dict]:
    """章(=一夜)に分ける(指示書§8.6)。

    固定文字数で切らない。青空文庫は見出しを注記で示すため、それを境界に使う。
    """
    headings = list(_HEADING_NOTE.finditer(body))
    if not headings:
        return [{"chapter_title": None, "text": body.strip()}]

    chapters: list[dict] = []
    for i, m in enumerate(headings):
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        text = body[start:end]
        # 次の見出し行の頭(字下げ注記 + 章題)が末尾に残るので落とす
        text = re.sub(
            r"［＃[^］]*］\s*" + re.escape(headings[i + 1].group(1)) + r"\s*$",
            "",
            text,
        ) if i + 1 < len(headings) else text
        chapters.append({"chapter_title": m.group(1), "text": text.strip("\n 　")})
    return chapters


def normalize_document(text: str) -> dict:
    """本文を3形式へ正規化し、由来情報を添えて返す(指示書§8.1)。"""
    doc = split_document(text)
    raw_body = doc["body"]

    display_text, _ = extract_notes(ruby_to_display(raw_body))
    no_ruby, rubies = extract_ruby(raw_body)
    normalized_text, notes = extract_notes(no_ruby)

    return {
        "title": doc["title"],
        "author": doc["author"],
        "raw_text": raw_body,
        "normalized_text": normalized_text.strip(),
        "display_text": display_text.strip(),
        "rubies": rubies,
        "notes": notes,
        "colophon": doc["colophon"],
        "colophon_raw": doc["colophon_raw"],
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "parser_version": PARSER_VERSION,
    }
