"""青空文庫チャンカー(C-T3c)。

正本仕様: docs/CORPUS_T1_SPEC.md §7 / 上位指示 §8.6。

**固定文字数で切らない。** 優先順位は 章 → 節 → 段落 → 話者交代 → 意味段落 → token上限。
- 講演・評論: 主張／具体例／反論／例外／結論をできる限り別チャンクにする
- 小説: 語り手記述／人物発言／場面転換を識別できるようにする

既存の思想モード用チャンカー(CHUNKER_VERSION='v1')とは別系統にして、
既存チャンクを再生成しないようにする。
"""

import hashlib

# 分割ルールを変えたら上げる(既存の 'v1' とは別系統)
CHUNKER_VERSION = "aozora_v1"

# 1チャンクの目安。埋め込みと文脈量の両立で決める
DEFAULT_MAX_CHARS = 800

# 会話文の開始。小説では話者交代の境界として使う
_DIALOGUE_START = ("「", "『")

# 小説系のみ会話文で切る。講演・評論は主張の連続性を保つため切らない
_NOVEL_TYPES = ("novel", "short_story", "sketch")


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """(開始位置, 段落) の並び。位置は引用箇所の特定に使うので必ず保つ。"""
    out: list[tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        if line.strip():
            out.append((pos, line))
        pos += len(line) + 1
    return out


def _split_long_paragraph(pos: int, para: str, max_chars: int) -> list[tuple[int, str]]:
    """上限を大きく超える1段落を文末で分ける(§8.6 の「意味段落」)。

    講演・評論は改行が少なく1段落が数千字になることがある(『現代日本の開化』で実測
    最大2517字)。そのままでは埋め込みの粒度が粗くなりすぎるため、文の途中では
    切らずに文末で区切る。
    """
    if len(para) <= max_chars:
        return [(pos, para)]

    # 文末(。！？)の直後で区切る。閉じ括弧が続く場合はそこまで含める
    sentences: list[tuple[int, str]] = []
    start = 0
    i = 0
    while i < len(para):
        if para[i] in "。！？!?":
            end = i + 1
            while end < len(para) and para[end] in "」』）)":
                end += 1
            sentences.append((pos + start, para[start:end]))
            start = end
            i = end
        else:
            i += 1
    if start < len(para):
        sentences.append((pos + start, para[start:]))

    # 文を上限まで詰め直す
    out: list[tuple[int, str]] = []
    buf: list[tuple[int, str]] = []
    for s_pos, sentence in sentences:
        if buf and sum(len(s) for _, s in buf) + len(sentence) > max_chars:
            out.append((buf[0][0], "".join(s for _, s in buf)))
            buf = []
        buf.append((s_pos, sentence))
    if buf:
        out.append((buf[0][0], "".join(s for _, s in buf)))
    return out


def _is_dialogue(paragraph: str) -> bool:
    return paragraph.lstrip("　 ").startswith(_DIALOGUE_START)


def chunk_chapter(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    source_type: str = "essay",
) -> list[dict]:
    """章の本文をチャンクへ分ける。

    段落を跨いで詰めるが、**段落の途中では切らない**。
    小説では会話文を地の文と分けて、語り手と人物の発言を後段で区別できるようにする。
    """
    split_dialogue = source_type in _NOVEL_TYPES
    paragraphs = _paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[dict] = []
    buf: list[tuple[int, str]] = []
    state = {"kind": "body"}

    def flush() -> None:
        if not buf:
            return
        start = buf[0][0]
        end = buf[-1][0] + len(buf[-1][1])
        chunks.append({
            "text": "\n".join(p for _, p in buf),
            "char_start": start,
            "char_end": end,
            "paragraph_start": len(chunks),
            "chunk_type": state["kind"],
        })
        buf.clear()

    # 上限を大きく超える段落は、あらかじめ文末で分けておく(§8.6 意味段落)
    units: list[tuple[int, str]] = []
    for pos, para in paragraphs:
        units.extend(_split_long_paragraph(pos, para, max_chars))

    for pos, para in units:
        kind = "body"
        if split_dialogue:
            kind = "dialogue" if _is_dialogue(para) else "narration"

        if buf and kind != state["kind"]:
            # 話者交代。ここで切る
            flush()
        elif buf and sum(len(p) for _, p in buf) + len(para) > max_chars:
            # 上限超過。文の途中では切らない
            flush()

        state["kind"] = kind
        buf.append((pos, para))

    flush()
    return chunks


def chunk_document(
    source_id: str,
    chapters: list[dict],
    *,
    source_type: str = "essay",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    """文書全体を章ごとにチャンク化する。

    chunk_id は章・連番から決定的に作る。同じ入力なら常に同じIDになり、
    再取り込みしても差分が出ない(既存チャンカーと同じ規律)。
    """
    out: list[dict] = []
    for chapter_index, chapter in enumerate(chapters, start=1):
        pieces = chunk_chapter(
            chapter["text"], max_chars=max_chars, source_type=source_type
        )
        for piece_index, piece in enumerate(pieces, start=1):
            chunk_hash = hashlib.sha256(
                f"{CHUNKER_VERSION}:{piece['text']}".encode()
            ).hexdigest()
            out.append({
                **piece,
                "chunk_id": f"{source_id}_C{chapter_index:02d}_{piece_index:03d}",
                "source_id": source_id,
                "chapter_title": chapter.get("chapter_title"),
                "chunker_version": CHUNKER_VERSION,
                "chunk_hash": chunk_hash,
            })
    return out
