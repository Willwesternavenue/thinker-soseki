"""テキスト整形(仕様6.2)。LLMトークンは使わない。

- 文字化け・制御文字の除去、改行整理
- 章・節見出し検出(マーカー付与)
- ページ番号・文字位置の保持(page_offsets)
- タイムスタンプの保持形式整理
- 話者ラベル正規化:「漱石:」等の本人ラベルはそのまま残さず「本人発言:」に正規化する。
  正規化結果から source_chunks.verbatim を導出する(仕様6.2重要ルール)
"""

import re
import unicodedata
from dataclasses import dataclass, field

# 本人を指す話者ラベル(正規化して「本人発言」に統一)。
# 対談・インタビュー原典で本人の発話に付く表記を列挙する。表記ゆれ(区切り文字)を含める。
# ※主たる原典が単著(モノローグ)なら話者ラベルは現れず、この設定は使われない。
SELF_SPEAKER_LABELS = (
    "夏目漱石", "漱石", "夏目", "先生",
    # 英訳原典の表記(照合は大文字小文字・空白を無視するので代表形のみでよい)。
    # ※「Natsume Soseki」のように語中に空白を含む形は _SPEAKER_RE が拾わない
    #   (欧文の平文を話者行と誤判定しないためラベルに空白を許していない)ので、
    #   単一トークンの表記のみを列挙する。
    "Soseki", "Sōseki", "Natsume", "N. Soseki",
)
SELF_SPEAKER_NORMALIZED = "本人発言"
# 相手側の話者ラベル(「質問者」に統一)。YouTube書き起こし・ベンダー納品では聞き手が
# 「女性」「男性」「司会者」等の属性・役割で表記されることがあるため含める。
OTHER_SPEAKER_LABELS = (
    "質問者", "聞き手", "インタビュアー", "司会", "司会者", "進行",
    "生徒", "Q", "女性", "男性",
    # 英語の対談/インタビュー表記(人物固有ではない汎用の聞き手表記)
    "Interviewer", "Moderator", "Question", "Q.",
)
OTHER_SPEAKER_NORMALIZED = "質問者"

# 日本語(かな・漢字・CJK約物・全角形)を1文字でも含むかの判定に使う。
# 欧文と日本語で行結合規則・見出し整形規則を切り替えるための「行のスクリプト判定」。
_JA_CHAR_RE = re.compile(
    r"[　-ヿ㐀-䶿一-鿿豈-﫿！-ﾟ]"
)

# コロン前に置かれうる空白。フランス語組版は「Nom : …」とコロンの前に空白を入れ、
# 実文書では半角空白のほか U+00A0 / U+202F の非改行スペースが使われる。
_COLON_SPACE = "   "

# 話者ラベル。=＝・- は複合姓の区切り(カタカナ複合姓「サン=テグジュペリ」や
# 欧文のハイフン姓)、「.」は略記(「Q.」)を拾うために含める。空白は含めない
# (欧文の平文「There are three things:」等を話者行と誤判定しないため)。ただし
# 「N. Soseki」形式のみイニシャル接頭辞として例外的に許容する。
# 長さ上限は Interviewer(11) 等の欧文ラベルを通すため 20 に拡げた。
_SPEAKER_RE = re.compile(
    rf"^((?:[A-Za-z]\.[{_COLON_SPACE}]*)?[\w=＝・.\-]{{1,20}})[{_COLON_SPACE}]*[：:]\s*"
)


def _has_japanese(text: str) -> bool:
    """行が日本語文字を含むか(欧文/日本語で整形規則を切り替えるための判定)。"""
    return bool(_JA_CHAR_RE.search(text))


# ラベルの長さ上限。欧文は Interviewer(11) やハイフン入り複合姓のため 20 まで許すが、日本語ラベルは
# 従来どおり10文字までに留める(「これは重要な論点である:…」のようなコロン付き本文を
# 話者行と誤判定して結合が止まるのを防ぐ)。
_JA_LABEL_MAX = 10
_LATIN_LABEL_MAX = 20


def _speaker_match(line: str) -> re.Match | None:
    """行頭の話者ラベルに一致すれば Match を返す(スクリプト別の長さ上限つき)。"""
    m = _SPEAKER_RE.match(line)
    if not m:
        return None
    label = m.group(1)
    limit = _JA_LABEL_MAX if _has_japanese(label) else _LATIN_LABEL_MAX
    return m if len(label) <= limit else None


def _label_key(label: str) -> str:
    """話者ラベルの照合キー。空白除去+casefold で表記ゆれ・全大文字を吸収する。"""
    return re.sub(rf"[{_COLON_SPACE}\s]+", "", label).casefold()


_SELF_LABEL_KEYS = frozenset(_label_key(label) for label in SELF_SPEAKER_LABELS)
_OTHER_LABEL_KEYS = frozenset(_label_key(label) for label in OTHER_SPEAKER_LABELS)
_TIMESTAMP_RE = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?$")
# 章:「第◯章」「第◯回」(対談・連載形式)に加え、欧文原典の "Chapter 3" /
# "Chapitre III"(ローマ数字)/ "Part I" を認識する。
# Introduction 等の単独見出し語は「行全体がその語」の場合のみ章扱いにする
# (本文の "Introduction of the concept is…" を見出しと誤判定しないため)。
# ローマ数字は大文字限定。IGNORECASE を全体に掛けると "Part civil" の
# "civil" が [ivxlcdm]+ に一致してしまうため、キーワードだけを (?i:…) にする。
_CHAPTER_RE = re.compile(
    r"^(第[0-9一二三四五六七八九十百]+[章回]"
    rf"|(?i:chapitre|chapter|partie|part|livre|book)[{_COLON_SPACE}]+(?:\d+|[IVXLCDM]+)\b\.?"
    r"|(?i:introduction|conclusion|préface|preface|avant-propos|foreword"
    r"|prologue|épilogue|epilogue)(?=$)"
    r")"
)
_SECTION_RE = re.compile(
    r"^(第[0-9一二三四五六七八九十百]+節|\d+[\.．]\d+"
    rf"|(?i:section)[{_COLON_SPACE}]+(?:\d+|[IVXLCDM]+)\b)"
)
# 対談・インタビューの聞き手行(行頭のダッシュ類)。
# カタカナ長音「ー」(U+30FC)は除外(誤検出防止)。使うのは em/水平ダッシュのみ。
_DASH = "—―─━－"  # U+2014 U+2015 U+2500 U+2501 U+FF0D
_INTERVIEWER_RE = re.compile(rf"^[{_DASH}]{{1,3}}\s*(\S.*)$")
# 「小見出し—聞き手の質問」が同一行に繋がったケース(短い頭+em dash+長い質問)。
# 区切りは em dash(U+2014)のみに限定(カタカナ長音や語中ダッシュの誤検出を防ぐ)。
_SECTION_THEN_Q_RE = re.compile(r"^([^—。!?！？]{2,12})—(\S.{18,})$")
_SENTENCE_END = ("。", "!", "?", "!", "?", "」", "』", ")", ")")
# 欧文の文末記号。半角ピリオドを含めないと欧文の文末が判定できない。
_LATIN_SENTENCE_END = (".", "!", "?", ")", "»", "…")
# 引用符閉じは「直前が文末記号のとき」だけ文末とみなす。無条件に文末扱いすると
# フランス語のエリジオン(行末が "l'" 等)を文末と誤判定して行が結合されなくなる。
_LATIN_CLOSING_QUOTES = ('"', "'", "”", "’")
# フランス語のエリジオン(l' d' qu' 等)。行末がこれなら次行と直結する。
_ELISION_MARKS = ("'", "’")
# 欧文PDFの分綴(行末ハイフン)。直前が文字であることを要求して
# 「--」や記号直後のハイフンを除外する。
_HYPHEN_BREAK_RE = re.compile(r"(?<=[^\W\d_])-$")

# 対談・インタビュー形式の source_type(聞き手=ダッシュ、本人=無標識段落)
_INTERVIEW_SOURCE_TYPES = ("interview", "dialogue")


def _squeeze_title(text: str) -> str:
    """見出しタイトルの空白整理。

    日本語は分かち書き空白を詰める(従来挙動)。欧文は語間スペースが意味を持つため
    連続空白を1個に畳むだけにする(全除去すると "Chapter 3 The Body" が壊れる)。
    """
    text = text.strip()
    if _has_japanese(text):
        return re.sub(r"\s+", "", text)
    return re.sub(r"\s+", " ", text)


def _ends_sentence(line: str) -> bool:
    """行が文末で終わっているか(日本語/欧文で記号セットを切り替える)。"""
    if _has_japanese(line):
        return line.endswith(_SENTENCE_END)
    if line.endswith(_LATIN_SENTENCE_END):
        return True
    if line.endswith(_LATIN_CLOSING_QUOTES):
        return len(line) >= 2 and line[-2] in ".!?"
    return False


def _join_broken_lines(prev: str, nxt: str) -> str:
    """PDF由来の折り返し行を結合する(日本語は詰め、欧文はスペース区切り)。"""
    if _has_japanese(prev) or _has_japanese(nxt):
        return prev + nxt
    if _HYPHEN_BREAK_RE.search(prev):
        # 分綴なら次行は小文字始まり。ハイフンごと除去して連結する。
        # 次行が大文字始まりなら複合固有名詞(Anglo-\nJapanese)とみなしハイフンを残す。
        # なお well-known 等の複合語が行末ハイフンで折り返された場合は
        # 分綴と区別できず "wellknown" に潰れる(辞書なしでは判別不能)。
        if nxt[:1].islower():
            return prev[:-1] + nxt
        return prev + nxt
    if prev.endswith(_ELISION_MARKS):
        # フランス語のエリジオン("de l'" + "être")は語が連続するのでスペースを挟まない
        return prev + nxt
    return f"{prev} {nxt}"


def _normalize_chapter_line(line: str) -> str:
    """章見出しを「# 第◯回 タイトル」に整形(タイトル内の分かち書き空白を詰める)。"""
    m = _CHAPTER_RE.match(line)
    prefix = m.group(1)
    rest = _squeeze_title(line[m.end():])
    return f"# {prefix} {rest}".rstrip()


def _looks_like_section(line: str) -> bool:
    """対談中の小見出し(短い名詞句・文末記号なし)を推定する。"""
    if len(line) < 2 or len(line) > 24:
        return False
    if line[-1] in "。!?！？、":
        return False
    return not (
        _INTERVIEWER_RE.match(line)
        or _speaker_match(line)
        or _CHAPTER_RE.match(line)
    )


@dataclass
class CleanResult:
    text: str
    # (クリーンテキスト内の開始文字位置, 元PDFページ番号) の昇順リスト
    page_offsets: list[tuple[int, int]] = field(default_factory=list)


def normalize_speaker_line(line: str) -> tuple[str, str | None]:
    """行頭の話者ラベルを正規化する。戻り値: (正規化後の行, 話者 or None)。"""
    m = _speaker_match(line)
    if not m:
        return line, None
    label = m.group(1)
    rest = line[m.end():]
    key = _label_key(label)
    if key in _SELF_LABEL_KEYS:
        return f"{SELF_SPEAKER_NORMALIZED}: {rest}", SELF_SPEAKER_NORMALIZED
    if key in _OTHER_LABEL_KEYS:
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
        or bool(_speaker_match(line))
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
                processed.append("## " + _squeeze_title(sq.group(1)))
                processed.append(f"{OTHER_SPEAKER_NORMALIZED}: {sq.group(2).strip()}")
                continue
            # 対談中の小見出し
            if (not processed or not processed[-1]) and _looks_like_section(line):
                processed.append("## " + _squeeze_title(line))
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
            and not _ends_sentence(joined[-1])
            and not _is_block_boundary(joined[-1])
            and not _is_block_boundary(line)
        ):
            joined[-1] = _join_broken_lines(joined[-1], line)
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
