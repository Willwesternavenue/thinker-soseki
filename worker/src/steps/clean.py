"""テキスト整形(仕様6.2)。LLMトークンは使わない。

- 文字化け・制御文字の除去、改行整理
- 章・節見出し検出(マーカー付与)
- ページ番号・文字位置の保持(page_offsets)
- タイムスタンプの保持形式整理
- 話者ラベル正規化:「社長:」はそのまま残さず「本人発言:」に正規化する。
  正規化結果から source_chunks.verbatim を導出する(仕様6.2重要ルール)
"""

import re
import unicodedata
from dataclasses import dataclass, field

# 本人を指す話者ラベル(正規化して「本人発言」に統一)
SELF_SPEAKER_LABELS = ("社長", "執行草舟", "執行", "先生")
SELF_SPEAKER_NORMALIZED = "本人発言"
# 相手側の話者ラベル(「質問者」に統一)。YouTube書き起こし・ベンダー納品では聞き手が
# 「女性」「男性」「司会者」等の属性・役割で表記されることがあるため含める。
OTHER_SPEAKER_LABELS = (
    "質問者", "聞き手", "インタビュアー", "司会", "司会者", "進行",
    "生徒", "Q", "女性", "男性",
)
OTHER_SPEAKER_NORMALIZED = "質問者"

_SPEAKER_RE = re.compile(r"^([\w一-龠ぁ-んァ-ヶA-Za-z]{1,10})[：:]\s*")
_TIMESTAMP_RE = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?$")
# 章:「第◯章」「第◯回」(対談・連載形式)の両方を認識
_CHAPTER_RE = re.compile(r"^(第[0-9一二三四五六七八九十百]+[章回])")
_SECTION_RE = re.compile(r"^(第[0-9一二三四五六七八九十百]+節|\d+[\.．]\d+)")
# 対談・インタビューの聞き手行(行頭のダッシュ類)。
# カタカナ長音「ー」(U+30FC)は除外(誤検出防止)。使うのは em/水平ダッシュのみ。
_DASH = "—―─━－"  # U+2014 U+2015 U+2500 U+2501 U+FF0D
_INTERVIEWER_RE = re.compile(rf"^[{_DASH}]{{1,3}}\s*(\S.*)$")
# 「小見出し—聞き手の質問」が同一行に繋がったケース(短い頭+em dash+長い質問)。
# 区切りは em dash(U+2014)のみに限定(カタカナ長音や語中ダッシュの誤検出を防ぐ)。
_SECTION_THEN_Q_RE = re.compile(r"^([^—。!?！？]{2,12})—(\S.{18,})$")
_SENTENCE_END = ("。", "!", "?", "!", "?", "」", "』", ")", ")")

# 対談・インタビュー形式の source_type(聞き手=ダッシュ、本人=無標識段落)
_INTERVIEW_SOURCE_TYPES = ("interview", "dialogue")


def _normalize_chapter_line(line: str) -> str:
    """章見出しを「# 第◯回 タイトル」に整形(タイトル内の分かち書き空白を詰める)。"""
    m = _CHAPTER_RE.match(line)
    prefix = m.group(1)
    rest = re.sub(r"\s+", "", line[m.end():].strip())
    return f"# {prefix} {rest}".rstrip()


def _looks_like_section(line: str) -> bool:
    """対談中の小見出し(短い名詞句・文末記号なし)を推定する。"""
    if len(line) < 2 or len(line) > 24:
        return False
    if line[-1] in "。!?！？、":
        return False
    return not (
        _INTERVIEWER_RE.match(line)
        or _SPEAKER_RE.match(line)
        or _CHAPTER_RE.match(line)
    )


@dataclass
class CleanResult:
    text: str
    # (クリーンテキスト内の開始文字位置, 元PDFページ番号) の昇順リスト
    page_offsets: list[tuple[int, int]] = field(default_factory=list)


def normalize_speaker_line(line: str) -> tuple[str, str | None]:
    """行頭の話者ラベルを正規化する。戻り値: (正規化後の行, 話者 or None)。"""
    m = _SPEAKER_RE.match(line)
    if not m:
        return line, None
    label = m.group(1)
    rest = line[m.end():]
    if label in SELF_SPEAKER_LABELS:
        return f"{SELF_SPEAKER_NORMALIZED}: {rest}", SELF_SPEAKER_NORMALIZED
    if label in OTHER_SPEAKER_LABELS:
        return f"{OTHER_SPEAKER_NORMALIZED}: {rest}", OTHER_SPEAKER_NORMALIZED
    return line, label


def _clean_line(line: str) -> str:
    # 制御文字除去・空白正規化(日本語本文はNFKCを全適用すると記号が変わるため最小限)
    line = "".join(ch for ch in line if unicodedata.category(ch) != "Cc")
    return line.strip()


def _mark_headings(line: str) -> str:
    if line.startswith("#"):
        return line  # docx抽出で既にマーカー付与済み
    if _CHAPTER_RE.match(line):
        return f"# {line}"
    if _SECTION_RE.match(line):
        return f"## {line}"
    return line


def _is_block_boundary(line: str) -> bool:
    return (
        not line
        or line.startswith("#")
        or bool(_TIMESTAMP_RE.match(line))
        or bool(_SPEAKER_RE.match(line))
    )


def clean_pages(pages: list[str], source_type: str | None = None) -> CleanResult:
    """ページ単位テキストを整形し、ページ番号→文字位置の対応を保持して結合する。

    source_type が interview/dialogue の場合、行頭ダッシュを聞き手(質問者)として
    正規化し、無標識段落を本人発言として扱う対談モードで整形する。
    """
    interview = source_type in _INTERVIEW_SOURCE_TYPES
    out_parts: list[str] = []
    page_offsets: list[tuple[int, int]] = []
    offset = 0

    for page_no, raw in enumerate(pages, start=1):
        cleaned = _clean_page_text(raw, interview=interview)
        page_offsets.append((offset, page_no))
        out_parts.append(cleaned)
        offset += len(cleaned) + 1  # 結合時の改行分

    return CleanResult(text="\n".join(out_parts), page_offsets=page_offsets)


def _clean_page_text(raw: str, interview: bool = False) -> str:
    lines = [_clean_line(l) for l in raw.splitlines()]

    processed: list[str] = []
    for line in lines:
        if not line:
            processed.append("")
            continue
        # ページ番号だけの行は落とす(ページはpage_offsetsで保持)
        if re.fullmatch(r"[-‐−ー\s]*\d+[-‐−ー\s]*", line):
            continue
        ts = _TIMESTAMP_RE.match(line)
        if ts:
            processed.append(f"[{ts.group(1)}]")
            continue
        # 章見出し(第◯章/第◯回)
        if _CHAPTER_RE.match(line):
            processed.append(_normalize_chapter_line(line))
            continue
        if interview:
            # 聞き手行(行頭ダッシュ)→ 質問者ラベルに正規化
            m = _INTERVIEWER_RE.match(line)
            if m:
                processed.append(f"{OTHER_SPEAKER_NORMALIZED}: {m.group(1).strip()}")
                continue
            # 「小見出し—聞き手の質問」が1行に繋がったケースを分離
            sq = _SECTION_THEN_Q_RE.match(line)
            if sq:
                processed.append("## " + re.sub(r"\s+", "", sq.group(1)))
                processed.append(f"{OTHER_SPEAKER_NORMALIZED}: {sq.group(2).strip()}")
                continue
            # 対談中の小見出し
            if (not processed or not processed[-1]) and _looks_like_section(line):
                processed.append("## " + re.sub(r"\s+", "", line))
                continue
        line, _speaker = normalize_speaker_line(line)
        line = _mark_headings(line)
        processed.append(line)

    # 不要改行の整理: 文末記号で終わらない行は次の行と結合(見出し・話者・タイムスタンプ境界を除く)
    joined: list[str] = []
    for line in processed:
        if (
            joined
            and joined[-1]
            and not joined[-1].endswith(_SENTENCE_END)
            and not _is_block_boundary(joined[-1])
            and not _is_block_boundary(line)
        ):
            joined[-1] += line
        else:
            joined.append(line)

    # 3連以上の空行を1つに
    text = "\n".join(joined)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def page_for_offset(page_offsets: list[tuple[int, int]], char_pos: int) -> int | None:
    """文字位置から元ページ番号を引く。"""
    page = None
    for start, page_no in page_offsets:
        if char_pos >= start:
            page = page_no
        else:
            break
    return page
