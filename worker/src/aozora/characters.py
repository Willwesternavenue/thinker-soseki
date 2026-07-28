"""作品ごとの登場人物辞書（`character_id` の語彙の単一の出所）。

正本仕様: docs/CORPUS_T1_SPEC.md §5.2。

役割分担:
- **辞書（このファイル + characters.json）**: 使ってよい `character_id` と表記を定める
- **Pass2（LLM）**: チャンクの発言者を一覧から選んで割り当てる（`tag.merge_pass2` が
  一覧の外のIDを捨てる）

こうする理由: LLM の自由生成に任せるとIDの語彙が揺れ（daisuke / 長井代助 / 代助）、
質問側の検出（`detect`）とチャンク側の `character_id` が結合できなくなる。
逆に辞書だけでは「誰がいるか」しか分からず「誰が言ったか」を判定できない。

⚠️ frontend の `src/lib/rag/characters.json` は本ファイルの複製。frontend 側の
テストが同期を検証する（片方だけ変えるとテストが落ちる）。

⚠️ 「先生」のような一般名詞と衝突する呼称は入れない。`detect` は部分一致なので、
無関係な質問（「先生についてどう思うか」）を人物質問に誤判定してしまう。
"""

import json
from pathlib import Path

_PATH = Path(__file__).with_name("characters.json")
_DATA: dict[str, dict] = json.loads(_PATH.read_text(encoding="utf-8"))


def all_ids() -> list[str]:
    """全 character_id（辞書の記載順）。"""
    return list(_DATA)


def name_map() -> dict[str, str]:
    """表記 → character_id。ルーティングの検出辞書として使う。"""
    return {
        name: character_id
        for character_id, entry in _DATA.items()
        for name in entry["names"]
    }


def roster_for_work(canonical_title: str) -> list[dict]:
    """その作品の登場人物一覧。Pass2 にはこれだけを渡す。

    全作品ぶんを一括で渡すと、別作品の人物を誤って付ける混線が起きる
    （『三四郎』のチャンクに『それから』の代助が付く類）。
    辞書に載らない作品（夢十夜など登場人物が無名のもの）は空を返す。
    """
    return [
        {"character_id": character_id, **entry}
        for character_id, entry in _DATA.items()
        if entry["work"] == canonical_title
    ]


def detect(query: str) -> str | None:
    """質問に含まれる既知の登場人物を返す。

    ⚠️ 名前が挙がらない質問を人物質問にしない。誤判定すると
    作者の思想が主根拠から外れる。
    """
    for character_id, entry in _DATA.items():
        if any(name in query for name in entry["names"]):
            return character_id
    return None
