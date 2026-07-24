"""ルールベースチャンク化(仕様6.3)。LLMは使わない。

- 書籍: 章 → 節 → 意味段落 → 600〜1,500字程度
- 動画・対談: 質問 + 回答 のQA単位
- 同じテキスト・同じchunker_versionなら同じchunk_idになる(決定的)
- chunk_hash(sha256)で再処理時の差分検出
"""

import hashlib
import re
from dataclasses import dataclass, field

from ..config import CHUNKER_VERSION
from .clean import (
    OTHER_SPEAKER_NORMALIZED,
    SELF_SPEAKER_NORMALIZED,
    page_for_offset,
)

TARGET_MIN = 600
TARGET_MAX = 1500
MERGE_MIN = 200  # これ未満のチャンクは前のチャンクへ結合

_TS_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]$")
_SPEAKER_LINE_RE = re.compile(
    rf"^({SELF_SPEAKER_NORMALIZED}|{OTHER_SPEAKER_NORMALIZED}): ?(.*)$"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。!?!?])")

# 短いQA単位で区切る書き起こし系(講義・動画)
QA_SOURCE_TYPES = {"video_transcript", "lecture"}
# 長文対談・インタビュー(聞き手の質問+本人の長い答え)
INTERVIEW_SOURCE_TYPES = {"interview", "dialogue"}


@dataclass
class Chunk:
    chunk_id: str
    text: str
    chunk_type: str  # body / qa_pair / monologue
    chapter_id: str | None = None
    chapter_title: str | None = None
    section_title: str | None = None
    char_start: int = 0
    char_end: int = 0
    source_page: int | None = None
    speaker: str | None = None
    verbatim: bool = False
    question: str | None = None
    answer: str | None = None
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    chunker_version: str = CHUNKER_VERSION
    chunk_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.chunk_hash:
            self.chunk_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def chunk_source(
    source_id: str,
    clean_text: str,
    source_type: str,
    page_offsets: list[tuple[int, int]] | None = None,
) -> list[Chunk]:
    if source_type in INTERVIEW_SOURCE_TYPES:
        chunks = _chunk_interview(source_id, clean_text, page_offsets or [])
        if chunks:
            return chunks
        # 聞き手が検出できなければ独白(本人発言)として書籍モードで分割
        return _chunk_book(
            source_id, clean_text, page_offsets or [],
            chunk_type="monologue", speaker=SELF_SPEAKER_NORMALIZED,
        )
    if source_type in QA_SOURCE_TYPES:
        chunks = _chunk_qa(source_id, clean_text)
        if chunks:
            return chunks
        # 話者ラベルの無い書き起こし(独白)は本人発言のモノローグとして扱う
        return _chunk_book(
            source_id, clean_text, page_offsets or [],
            chunk_type="monologue", speaker=SELF_SPEAKER_NORMALIZED,
        )
    return _chunk_book(source_id, clean_text, page_offsets or [])


# ── 書籍モード ──────────────────────────────────────────────

@dataclass
class _Segment:
    """章・節見出しで区切られた本文のまとまり。"""
    chapter_no: int
    chapter_title: str | None
    section_title: str | None
    char_start: int
    lines: list[str] = field(default_factory=list)


def _chunk_book(
    source_id: str,
    text: str,
    page_offsets: list[tuple[int, int]],
    chunk_type: str = "body",
    speaker: str | None = None,
) -> list[Chunk]:
    # 1. 章・節見出しでセグメントに分割(文字位置を保持)
    segments: list[_Segment] = []
    current = _Segment(chapter_no=0, chapter_title=None, section_title=None, char_start=0)
    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        stripped = line.strip()
        if stripped.startswith("# "):
            segments.append(current)
            current = _Segment(
                chapter_no=current.chapter_no + 1,
                chapter_title=stripped[2:].strip(),
                section_title=None,
                char_start=pos,
            )
        elif stripped.startswith("## "):
            segments.append(current)
            current = _Segment(
                chapter_no=current.chapter_no,
                chapter_title=current.chapter_title,
                section_title=stripped[3:].strip(),
                char_start=pos,
            )
        else:
            current.lines.append(stripped)
    segments.append(current)

    # 2. セグメントごとに段落を600〜1500字へ詰める(章内で連番)
    chunks: list[Chunk] = []
    seq_by_chapter: dict[int, int] = {}
    for seg in segments:
        body = "\n".join(seg.lines).strip()
        if not body:
            continue
        for rel_start, part in _pack(body):
            seq = seq_by_chapter.get(seg.chapter_no, 0) + 1
            seq_by_chapter[seg.chapter_no] = seq
            char_start = seg.char_start + rel_start
            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}_CH{seg.chapter_no:02d}_{seq:03d}",
                    text=part,
                    chunk_type=chunk_type,
                    chapter_id=f"{source_id}_CH{seg.chapter_no:02d}",
                    chapter_title=seg.chapter_title,
                    section_title=seg.section_title,
                    char_start=char_start,
                    char_end=char_start + len(part),
                    source_page=page_for_offset(page_offsets, char_start),
                    speaker=speaker,
                    # 書籍本文は原則、本人の著述そのもの(仕様5.3)
                    verbatim=True,
                )
            )

    return _merge_small(chunks)


def _pack(body: str) -> list[tuple[int, str]]:
    """段落を600〜1500字に詰める。長すぎる段落は文境界で分割する。

    戻り値: (セグメント先頭からの相対文字位置(近似), チャンクテキスト)
    """
    paragraphs = [p.strip().replace("\n", "") for p in body.split("\n\n")]
    paragraphs = [p for p in paragraphs if p]

    # 段落を最大1500字以下の断片(piece)に正規化
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= TARGET_MAX:
            pieces.append(para)
            continue
        buf = ""
        for sentence in _SENTENCE_SPLIT_RE.split(para):
            if buf and len(buf) + len(sentence) > TARGET_MAX:
                pieces.append(buf)
                buf = sentence
            else:
                buf += sentence
        if buf:
            pieces.append(buf)

    # 断片を600字以上になるまで結合
    packed: list[tuple[int, str]] = []
    offset = 0
    buf = ""
    buf_start = 0
    for piece in pieces:
        if buf and len(buf) + len(piece) > TARGET_MAX:
            packed.append((buf_start, buf))
            offset = buf_start + len(buf) + 1
            buf = ""
        if not buf:
            buf_start = offset
            buf = piece
        else:
            buf += "\n" + piece
        offset = buf_start + len(buf) + 1
        if len(buf) >= TARGET_MIN:
            packed.append((buf_start, buf))
            buf = ""
            buf_start = offset
    if buf:
        packed.append((buf_start, buf))
    return packed


def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    """短すぎるチャンクを前のチャンク(同一章)へ結合する。"""
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and len(chunk.text) < MERGE_MIN
            and merged[-1].chapter_id == chunk.chapter_id
            and merged[-1].chunk_type == chunk.chunk_type
        ):
            prev = merged[-1]
            prev.text = prev.text + "\n" + chunk.text
            prev.char_end = chunk.char_end
            prev.chunk_hash = hashlib.sha256(prev.text.encode("utf-8")).hexdigest()
        else:
            merged.append(chunk)
    return merged


# ── 対談・インタビューモード(長文の問答) ──────────────────

_INTERVIEW_Q_RE = re.compile(rf"^{OTHER_SPEAKER_NORMALIZED}: ?(.*)$")


def _chunk_interview(
    source_id: str,
    text: str,
    page_offsets: list[tuple[int, int]],
) -> list[Chunk]:
    """聞き手の質問(質問者:)+ 本人の長い答え(無標識段落)から章単位で分割する。

    本人の答えを600〜1,500字に詰め、直前の質問を question(文脈)として保持する。
    本人発言部分のみを text とするため verbatim=true(引用可能の土台)になる。
    """
    chapter_no = 0
    chapter_title: str | None = None
    section_title: str | None = None
    current_q: str | None = None
    answer_lines: list[str] = []
    answer_start = 0
    chunks: list[Chunk] = []
    seq_by_chapter: dict[int, int] = {}

    def flush() -> None:
        nonlocal answer_lines
        body = "\n".join(answer_lines).strip()
        answer_lines = []
        if not body:
            return
        for rel_start, part in _pack(body):
            seq = seq_by_chapter.get(chapter_no, 0) + 1
            seq_by_chapter[chapter_no] = seq
            char_start = answer_start + rel_start
            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}_R{chapter_no:02d}_{seq:03d}",
                    text=part,
                    chunk_type="interview_answer",
                    chapter_id=f"{source_id}_R{chapter_no:02d}",
                    chapter_title=chapter_title,
                    section_title=section_title,
                    char_start=char_start,
                    char_end=char_start + len(part),
                    source_page=page_for_offset(page_offsets, char_start),
                    speaker=SELF_SPEAKER_NORMALIZED,
                    verbatim=True,  # 本人の答えのみ。聞き手の質問はquestion(文脈)に分離
                    question=current_q,
                )
            )

    pos = 0
    saw_interviewer = False
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        stripped = line.strip()
        if stripped.startswith("# "):
            flush()
            chapter_no += 1
            chapter_title = stripped[2:].strip()
            section_title = None
            current_q = None
            continue
        if stripped.startswith("## "):
            flush()
            section_title = stripped[3:].strip()
            continue
        q = _INTERVIEW_Q_RE.match(stripped)
        if q:
            flush()
            current_q = q.group(1).strip()
            saw_interviewer = True
            continue
        if not stripped:
            continue
        if not answer_lines:
            answer_start = line_start
        answer_lines.append(stripped)
    flush()

    # 聞き手が一度も現れない=対談形式ではない。呼び出し側で書籍モードにフォールバック
    if not saw_interviewer:
        return []
    # 短い答え(相槌・短答)は同一章の前チャンクへ結合し、細切れを防ぐ
    return _merge_small(chunks)


# ── QAモード(動画・講義) ──────────────────────────────────

def _chunk_qa(source_id: str, text: str) -> list[Chunk]:
    """「質問者:」「本人発言:」の話者交代からQAペアを組み立てる。"""
    turns: list[tuple[str, str, str | None, int]] = []  # (speaker, text, timestamp, char_start)
    current_ts: str | None = None

    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        stripped = line.strip()
        ts = _TS_RE.match(stripped)
        if ts:
            current_ts = ts.group(1)
            continue
        m = _SPEAKER_LINE_RE.match(stripped)
        if m:
            turns.append((m.group(1), m.group(2), current_ts, line_start))
            current_ts = None
        elif turns and stripped:
            # 話者行の続き
            speaker, prev_text, prev_ts, start = turns[-1]
            turns[-1] = (speaker, prev_text + stripped, prev_ts, start)

    if not any(s == SELF_SPEAKER_NORMALIZED for s, *_ in turns):
        return []

    chunks: list[Chunk] = []
    seq = 0
    i = 0
    while i < len(turns):
        speaker, turn_text, ts, start = turns[i]
        if speaker == OTHER_SPEAKER_NORMALIZED:
            question_parts = [turn_text]
            ts_start = ts
            j = i + 1
            while j < len(turns) and turns[j][0] == OTHER_SPEAKER_NORMALIZED:
                question_parts.append(turns[j][1])
                j += 1
            answer_parts = []
            ts_end = None
            while j < len(turns) and turns[j][0] == SELF_SPEAKER_NORMALIZED:
                answer_parts.append(turns[j][1])
                ts_end = turns[j][2] or ts_end
                j += 1
            if answer_parts:
                seq += 1
                question = " ".join(question_parts)
                answer = " ".join(answer_parts)
                chunks.append(
                    Chunk(
                        chunk_id=f"{source_id}_QA_{seq:03d}",
                        text=f"{OTHER_SPEAKER_NORMALIZED}: {question}\n{SELF_SPEAKER_NORMALIZED}: {answer}",
                        chunk_type="qa_pair",
                        char_start=start,
                        char_end=turns[j - 1][3] + len(turns[j - 1][1]),
                        speaker=SELF_SPEAKER_NORMALIZED,
                        # 回答部分は本人発言そのもの(仕様5.3)
                        verbatim=True,
                        question=question,
                        answer=answer,
                        timestamp_start=ts_start,
                        timestamp_end=ts_end,
                    )
                )
                i = j
                continue
        elif speaker == SELF_SPEAKER_NORMALIZED:
            # 質問が先行しない本人の語り
            seq += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}_QA_{seq:03d}",
                    text=f"{SELF_SPEAKER_NORMALIZED}: {turn_text}",
                    chunk_type="monologue",
                    char_start=start,
                    char_end=start + len(turn_text),
                    speaker=SELF_SPEAKER_NORMALIZED,
                    verbatim=True,
                    timestamp_start=ts,
                )
            )
        else:
            # 相手発言のみで本人回答が続かない → 補助情報として保持(verbatim=false)
            seq += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}_QA_{seq:03d}",
                    text=f"{speaker}: {turn_text}",
                    chunk_type="other_speaker",
                    char_start=start,
                    char_end=start + len(turn_text),
                    speaker=speaker,
                    verbatim=False,
                    timestamp_start=ts,
                )
            )
        i += 1

    return chunks
