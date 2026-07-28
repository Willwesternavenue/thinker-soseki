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


# ── 1文字の名前(K)と一般名詞と衝突する呼称(先生) ──


def test_detects_k_with_boundaries():
    """K は前後が英数字でないときだけ一致する。"""
    assert characters.detect("Kとは誰ですか") == "k"
    assert characters.detect("Kの自殺をどう思うか") == "k"
    # 青空文庫の本文・日本語入力は全角のことがある
    assert characters.detect("Ｋについて") == "k"


def test_k_does_not_match_inside_words():
    """OK・KPI などの英単語の中の K に反応しない。

    1文字の名前を部分一致に載せると、K を含むあらゆる質問が人物ルートへ
    入り、作者の思想が主根拠から外れる。
    """
    assert characters.detect("OKですか") is None
    assert characters.detect("KPIとは何ですか") is None
    assert characters.detect("4Kテレビについて") is None


def test_sensei_is_not_detected_bare():
    """「先生」単独では検出しない。

    「漱石先生はどう考える？」のような言い回しは頻出で、部分一致に載せると
    大量の思想質問が人物ルートへ誤って入る。
    """
    assert characters.detect("漱石先生はどう考えますか") is None
    assert characters.detect("先生についてどう思いますか") is None


def test_sensei_is_detected_with_work_context():
    """作品名を伴う複合語なら衝突しない。"""
    assert characters.detect("こころの先生はなぜ死んだのか") == "sensei_kokoro"


def test_sensei_is_still_in_the_tagging_roster():
    """タグ付け側(作品スコープ)では「先生」をそのまま使える。

    Pass2 は部分一致ではなく一覧から選ぶ方式なので、『こころ』のチャンクに
    「先生」を候補として渡しても衝突は起きない。検出と語彙は別の関心事。
    """
    roster = {c["character_id"]: c for c in characters.roster_for_work("こころ")}
    assert set(roster) == {"sensei_kokoro", "k"}
    assert "先生" in roster["sensei_kokoro"]["names"]


def test_new_works_have_rosters():
    assert {c["character_id"] for c in characters.roster_for_work("行人")} == {
        "ichiro", "jiro", "onao",
    }
    assert {c["character_id"] for c in characters.roster_for_work("草枕")} == {"nami"}
