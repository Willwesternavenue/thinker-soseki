"""作家別ページ(person<人物ID>.html)のパーサ(C-T2c)。

公式CSVには**公開作品しか載らない**ため、作業中作品はこのページからのみ取得できる。
作業中は本文取得・Index登録・L2/L3候補生成を行わず、記録だけ残す(指示書§2.1)。

作業中セクションは公開中と体裁が違う:
- 図書カードへのリンクが無い(まだカードが無いため)
- タイトルと括弧の間の全角空白が無い行がある(例「余が『草枕』　作家と著作（新字新仮名…」)
"""

import re

# 作業中セクションの開始位置。この見出し以降〜次の <h2> までを対象にする
_IN_PROGRESS_ANCHOR = re.compile(r'<h2>\s*<a\s+name="sakuhin_list_2"')
_NEXT_H2 = re.compile(r"<h2>")

# 1件の行。タイトルは「（…作品ID：n）」の直前までを取る。
_ENTRY = re.compile(
    r"<li>\s*(?P<title>.*?)\s*[（(]\s*(?P<orthography>[^、，]+?)\s*[、，]\s*"
    r"作品ID\s*[：:]\s*(?P<work_id>\d+)\s*[）)]",
    re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG.sub("", text)


def parse_in_progress(html: str) -> list[dict]:
    """作業中セクションから作品を取り出す。

    公開中の作品を拾うと本文取得へ流れてしまうため、セクションを厳密に
    切り出してから解析する。
    """
    start = _IN_PROGRESS_ANCHOR.search(html)
    if not start:
        return []
    rest = html[start.end():]
    # 次の <h2>(「関連サイト」等)までが作業中セクション
    end = _NEXT_H2.search(rest)
    section = rest[: end.start()] if end else rest

    entries: list[dict] = []
    for m in _ENTRY.finditer(section):
        title = _strip_tags(m.group("title")).strip().strip("　")
        entries.append({
            # CSV・URLと突合できるよう6桁ゼロ埋めに揃える
            "aozora_work_id": m.group("work_id").zfill(6),
            "title": title,
            "orthography": m.group("orthography").strip(),
        })
    return entries
