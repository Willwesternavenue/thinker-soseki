"""青空文庫 Manifest Importer(C-T2b)。

公式CSV(list_person_all_extended_utf8)から対象人物の行を取り出し、
canonical work / edition へ正規化する。正本仕様: docs/CORPUS_T1_SPEC.md §1。

⚠️ canonical work の同定を作品名の完全一致に依存させてはいけない。
実データの反例:「吾輩は猫である」(000789 新字新仮名)と
「吾輩ハ猫デアル」(000790 旧字旧仮名)はタイトル文字列自体が異なる。
"""

import hashlib
import re
import unicodedata

from .. import db

# 既定の検索版に選ぶ優先順(指示書§2.3: 読みやすい新字新仮名を優先)
ORTHOGRAPHY_PRIORITY = ("新字新仮名", "新字旧仮名", "旧字旧仮名")

# 旧字体 → 新字体の簡易写像。タイトルの表記ゆれを吸収するためだけに使う
# (本文の正規化には使わない。本文は原表記を保持する)
_KYUJI_TO_SHINJI = str.maketrans({
    "藝": "芸", "體": "体", "學": "学", "國": "国", "會": "会", "來": "来",
    "圓": "円", "廣": "広", "戀": "恋", "櫻": "桜", "澤": "沢", "眞": "真",
    "祕": "秘", "續": "続", "萬": "万", "號": "号", "變": "変", "驅": "駆",
})

_SPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """タイトル比較用の正規形。

    NFKC → 空白除去 → 旧字を新字へ → カタカナをひらがなへ。
    「吾輩ハ猫デアル」と「吾輩は猫である」を同じ形にするのが目的。
    """
    t = unicodedata.normalize("NFKC", title or "")
    t = _SPACE_RE.sub("", t).translate(_KYUJI_TO_SHINJI)
    # カタカナ → ひらがな(送り仮名の表記差を吸収)
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in t)


def normalize_reading(reading: str) -> str:
    """読み比較用の正規形。NFKC + 空白除去 + 長音のゆれを吸収。"""
    r = unicodedata.normalize("NFKC", reading or "")
    return _SPACE_RE.sub("", r).replace("ー", "")


def _reading_key(row: dict) -> str:
    return normalize_reading(row.get("作品名読み", ""))


def group_into_canonical_works(rows: list[dict]) -> list[dict]:
    """CSVの行を canonical work 単位へ束ねる(§1.2)。

    段1(読みの一致)と段2(正規化タイトルの一致)の両方で寄せる。
    「タイトルは同じだが読みが揺れている」場合は統合したうえで
    needs_review を立て、人手で確認できるようにする(自動統合しっぱなしにしない)。
    """
    by_title: dict[str, list[dict]] = {}
    for row in rows:
        by_title.setdefault(normalize_title(row.get("作品名", "")), []).append(row)

    works: list[dict] = []
    for title_key, editions in by_title.items():
        readings = {_reading_key(r) for r in editions}
        readings.discard("")
        # 読みが2種類以上に割れている = 同定根拠が弱いので確認へ回す
        needs_review = len(readings) > 1
        # 全版に読みがあり、かつ1つに揃っていれば「読みで同定できた」
        method = (
            "reading"
            if len(readings) == 1 and all(_reading_key(r) for r in editions)
            else "normalized_title"
        )
        works.append({
            "canonical_title": editions[0].get("作品名", ""),
            "canonical_title_reading": editions[0].get("作品名読み") or None,
            "title_variants": sorted({r.get("作品名", "") for r in editions}),
            "normalized_title": title_key,
            "editions": editions,
            "match_method": method,
            "needs_review": needs_review,
        })
    return works


def pick_primary_edition(editions: list[dict]) -> dict | None:
    """既定の検索に使う版を選ぶ(§1.3)。

    新字新仮名を優先するが、**本文が取得できない版は選ばない**
    (000790 吾輩ハ猫デアル はテキストファイルが存在しない)。
    """
    usable = [e for e in editions if e.get("テキストファイルURL")]
    if not usable:
        return None

    def rank(e: dict) -> int:
        orth = e.get("文字遣い種別", "")
        return (
            ORTHOGRAPHY_PRIORITY.index(orth)
            if orth in ORTHOGRAPHY_PRIORITY
            else len(ORTHOGRAPHY_PRIORITY)
        )

    return sorted(usable, key=lambda e: (rank(e), e.get("作品ID", "")))[0]


def _blank_to_none(value: str | None) -> str | None:
    """CSVの空文字はNULLとして保存する(項目ごとに充足率が違うため)。"""
    return value or None


def build_edition_record(row: dict, canonical_work_id: str) -> dict:
    """CSVの1行を work_editions の行へ変換する。

    底本・入力者・校正者などの由来情報を落とさない(指示書§2.4)。
    """
    bottom = {
        "底本名": row.get("底本名1"),
        "底本出版社名": row.get("底本出版社名1"),
        "底本初版発行年": row.get("底本初版発行年1"),
        "入力に使用した版": row.get("入力に使用した版1"),
        "校正に使用した版": row.get("校正に使用した版1"),
        "底本の親本名": row.get("底本の親本名1"),
        "底本の親本出版社名": row.get("底本の親本出版社名1"),
        "底本の親本初版発行年": row.get("底本の親本初版発行年1"),
    }
    return {
        "edition_id": row["作品ID"],
        "canonical_work_id": canonical_work_id,
        "aozora_work_id": row["作品ID"],
        "orthography": row.get("文字遣い種別") or "unknown",
        "work_status": "published",  # CSVには公開作品しか載らない(§1.4)
        "card_url": _blank_to_none(row.get("図書カードURL")),
        "text_file_url": _blank_to_none(row.get("テキストファイルURL")),
        "text_encoding": _blank_to_none(row.get("テキストファイル符号化方式")),
        "text_charset": _blank_to_none(row.get("テキストファイル文字集合")),
        "bottom_text": {k: v for k, v in bottom.items() if v},
        "input_by": _blank_to_none(row.get("入力者")),
        "proofread_by": _blank_to_none(row.get("校正者")),
        "aozora_published_at": _blank_to_none(row.get("公開日")),
        "aozora_updated_at": _blank_to_none(row.get("最終更新日")),
        "copyright_status": "public_domain",
    }


def _canonical_work_id(person_id: str, normalized_title: str) -> str:
    """再実行しても同じIDになるよう、正規化タイトルから決定的に作る(冪等性のため)。"""
    digest = hashlib.sha256(f"{person_id}:{normalized_title}".encode()).hexdigest()[:12]
    return f"cw_{digest}"


def import_manifest(rows: list[dict], *, person_id: str, client=None) -> dict:
    """CSV行を canonical_works / work_editions へ投入する(C-T2b)。

    upsert で書くため何度実行しても行は重複しない。
    同定根拠が弱い作品(読みが割れている等)は canonical_work_review_queue へ回す。
    """
    c = client or db.client()
    works = group_into_canonical_works(rows)
    review_queued = 0

    for work in works:
        work_id = _canonical_work_id(person_id, work["normalized_title"])
        c.table("canonical_works").upsert({
            "canonical_work_id": work_id,
            "person_id": person_id,
            "canonical_title": work["canonical_title"],
            "canonical_title_reading": work["canonical_title_reading"],
            "title_variants": work["title_variants"],
            "first_publication": _blank_to_none(work["editions"][0].get("初出")),
            "ndc": _blank_to_none(work["editions"][0].get("分類番号")),
        }).execute()

        primary = pick_primary_edition(work["editions"])
        primary_id = primary["作品ID"] if primary else None
        for row in work["editions"]:
            record = build_edition_record(row, work_id)
            record["is_primary_retrieval_edition"] = record["edition_id"] == primary_id
            c.table("work_editions").upsert(record).execute()

        if work["needs_review"]:
            # 同じ作品を何度もキューに積まないよう、未解決の同一作品があればスキップ
            existing = (
                c.table("canonical_work_review_queue")
                .select("queue_id")
                .eq("resolved_canonical_work_id", work_id)
                .eq("status", "open")
                .execute()
                .data
            )
            if not existing:
                c.table("canonical_work_review_queue").insert({
                    "person_id": person_id,
                    "aozora_work_ids": sorted(e["作品ID"] for e in work["editions"]),
                    "reason": (
                        "作品名は一致するが読みが割れているため、同一作品かを確認すること: "
                        + " / ".join(
                            f"{e['作品ID']}={e.get('作品名')}({e.get('作品名読み')})"
                            for e in work["editions"]
                        )
                    ),
                    "resolved_canonical_work_id": work_id,
                }).execute()
                review_queued += 1

    return {
        "works": len(works),
        "editions": sum(len(w["editions"]) for w in works),
        "review_queued": review_queued,
    }


def import_in_progress_entries(
    entries: list[dict], *, person_id: str, source_page_url: str, client=None
) -> int:
    """作業中作品を記録する。本文取得・Index登録は行わない(指示書§2.1)。"""
    c = client or db.client()
    for entry in entries:
        c.table("aozora_manifest_entries").upsert({
            "entry_id": f"{person_id}:{entry['aozora_work_id']}",
            "person_id": person_id,
            "aozora_work_id": entry["aozora_work_id"],
            "title": entry["title"],
            "orthography": entry.get("orthography"),
            "work_status": "in_progress",
            "source_page_url": source_page_url,
        }).execute()
    return len(entries)
