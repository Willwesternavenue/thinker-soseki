"""作品ごとの登場人物辞書(character_id の語彙の単一の出所)。

- 語彙は辞書が定める。Pass2(LLM)は一覧から選ぶだけで、一覧の外のIDは書けない
- ルーティングの人物検出(routing.detect_character)と同じ出所を使う。
  別々に持つと、質問から検出したIDとチャンクに付いたIDが一致しなくなる
"""

from src.aozora import characters, routing


def test_roster_is_scoped_to_the_work():
    """作品の一覧にはその作品の人物だけが入る。

    全作品の人物を一括で渡すと、別作品の人物を誤って付ける混線が起きる。
    """
    roster = characters.roster_for_work("それから")

    assert [c["character_id"] for c in roster] == ["daisuke"]


def test_multi_character_work_lists_everyone():
    ids = {c["character_id"] for c in characters.roster_for_work("吾輩は猫である")}

    assert ids == {"kushami", "meitei", "kangetsu"}


def test_unknown_or_unnamed_work_has_empty_roster():
    """夢十夜の登場人物は無名。辞書に載らない作品は空の一覧を返す。"""
    assert characters.roster_for_work("夢十夜") == []
    assert characters.roster_for_work("存在しない作品") == []


def test_detect_finds_known_names():
    assert characters.detect("代助はなぜ働かないのか") == "daisuke"
    assert characters.detect("津田とお延の関係") == "tsuda"


def test_detect_returns_none_for_plain_questions():
    """名前が挙がらない質問を人物質問にしない(誤判定すると思想が主根拠から外れる)。"""
    assert characters.detect("近代化についてどう考えたか") is None


def test_ids_are_unique():
    ids = characters.all_ids()

    assert len(ids) == len(set(ids))


def test_routing_uses_the_same_dictionary():
    """routing 側の検出は辞書に委譲する(二重定義を残さない)。"""
    assert routing.detect_character("三四郎について") == "sanshiro"
    assert set(routing.KNOWN_CHARACTERS.values()) == set(characters.all_ids())
