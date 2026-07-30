"""装置カタログの生成と判定ルール(続編生成の防具)。

最重要は2つ。
1. 根拠のない装置をカタログへ入れないこと — judge が fail を出したとき
   「どの夜のどこと衝突したか」を出せなくなる
2. 判定はフラグ単独で書かないこと — 中心=即fail、付随=同一章で共起2つ以上。
   `verdict_for_matches` に集約し、(2)カード除外 と (3)judge で共有する

LLM呼び出しは注入して差し替える(実APIを叩かない)。
"""

from src.creative import device_catalog as dc


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {"devices": []}


def _seed_work(client, *, source_id="SRC_DREAM", chapters=("第一夜", "第二夜")):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": f"cw_{source_id}", "person_id": "natsume_soseki",
        "canonical_title": source_id}).execute()
    client.table("work_editions").upsert({
        "edition_id": f"ed_{source_id}", "canonical_work_id": f"cw_{source_id}",
        "aozora_work_id": "000799", "orthography": "新字新仮名"}).execute()
    client.table("sources").upsert({
        "source_id": source_id, "person_id": "natsume_soseki", "title": "夢十夜",
        "source_type": "book", "edition_id": f"ed_{source_id}",
        "corpus_role": "narrative_reference", "document_genre": "short_story",
        "source_provider": "aozora"}).execute()
    ids = []
    for ci, chapter in enumerate(chapters):
        for i in range(2):
            cid = f"{source_id}_C{ci:02d}_{i:03d}"
            client.table("source_chunks").upsert({
                "chunk_id": cid, "source_id": source_id,
                "person_id": "natsume_soseki", "chapter_title": chapter,
                "text": f"{chapter}の本文({i})。", "chunker_version": "aozora_v1",
                "chunk_hash": f"h{cid}", "speaker_role": "narrator",
                "thought_eligibility": "excluded",
            }).execute()
            ids.append(cid)
    return ids


# ── 章ごとに分けて渡す ──


def test_calls_the_model_once_per_chapter(clean_corpus, client):
    """全章を1プロンプトに詰めない（思想カードで先頭しか出なかった失敗の再発防止）。"""
    _seed_work(client)
    llm = FakeLLM(
        {"devices": [{"device_id": "kazoeru", "name": "赤い日を数える",
                      "role": "central", "description": "…",
                      "evidence_chunk_ids": ["SRC_DREAM_C00_000"]}]},
        {"devices": [{"device_id": "buta", "name": "豚に舐められる",
                      "role": "central", "description": "…",
                      "evidence_chunk_ids": ["SRC_DREAM_C01_000"]}]},
    )

    catalog = dc.generate_catalog("SRC_DREAM", work_title="夢十夜",
                                  client=client, call_json=llm)

    assert len(llm.calls) == 2, "章ごとに1回ずつ"
    assert [c["chapter_title"] for c in catalog["chapters"]] == ["第一夜", "第二夜"]
    assert catalog["meta"]["devices"] == 2


def test_records_prompt_version_and_model_in_meta(clean_corpus, client):
    """どのプロンプト・どのモデルで作った派生物かを残す。"""
    _seed_work(client, chapters=("第一夜",))
    llm = FakeLLM({"devices": [{"device_id": "d", "name": "装置", "role": "central",
                                "description": "…",
                                "evidence_chunk_ids": ["SRC_DREAM_C00_000"]}]})

    meta = dc.generate_catalog("SRC_DREAM", client=client, call_json=llm)["meta"]

    assert meta["prompt_version"] == dc.PROMPT_VERSION
    assert meta["model_id"] == dc.config.MODEL_CREATIVE_MAIN
    assert meta["source_id"] == "SRC_DREAM"


# ── 根拠の検証 ──


def test_drops_device_whose_evidence_is_outside_the_chapter():
    """他章・実在しないチャンクを根拠に挙げた装置は捨てる。"""
    response = {"devices": [
        {"device_id": "ok", "name": "章内の装置", "role": "central",
         "description": "…", "evidence_chunk_ids": ["C00_000", "よそのID"]},
        {"device_id": "ng", "name": "根拠が他章だけ", "role": "central",
         "description": "…", "evidence_chunk_ids": ["C99_000"]},
    ]}

    devices = dc.absorb_devices(
        response, valid_chunk_ids={"C00_000"}, chapter_title="第一夜"
    )

    assert [d["name"] for d in devices] == ["章内の装置"]
    assert devices[0]["evidence_chunk_ids"] == ["C00_000"], "章外のIDは落とす"


def test_drops_device_without_any_evidence():
    response = {"devices": [{"device_id": "x", "name": "根拠なし", "role": "central",
                             "description": "…", "evidence_chunk_ids": []}]}

    assert dc.absorb_devices(response, valid_chunk_ids={"C00_000"},
                             chapter_title="第一夜") == []


def test_second_central_without_justification_is_demoted():
    """上限方式にするとモデルは上限まで埋める。理由の無い2件目は付随へ降ろす。"""
    response = {"devices": [
        {"device_id": "a", "name": "頂点", "role": "central", "description": "…",
         "evidence_chunk_ids": ["C00_000"]},
        {"device_id": "b", "name": "理由なし2件目", "role": "central",
         "description": "…", "evidence_chunk_ids": ["C00_000"]},
    ]}

    devices = dc.absorb_devices(response, valid_chunk_ids={"C00_000"},
                               chapter_title="第一夜")

    assert [d["role"] for d in devices] == [dc.ROLE_CENTRAL, dc.ROLE_INCIDENTAL]
    assert devices[1]["demoted_from_central"] is True


def test_second_central_with_justification_is_kept():
    response = {"devices": [
        {"device_id": "a", "name": "頂点", "role": "central", "description": "…",
         "evidence_chunk_ids": ["C00_000"]},
        {"device_id": "b", "name": "理由あり2件目", "role": "central",
         "description": "…", "justification": "どちらを欠いても章が別物になる",
         "evidence_chunk_ids": ["C00_000"]},
    ]}

    devices = dc.absorb_devices(response, valid_chunk_ids={"C00_000"},
                               chapter_title="第一夜")

    assert [d["role"] for d in devices] == [dc.ROLE_CENTRAL, dc.ROLE_CENTRAL]


def test_never_demotes_the_only_central():
    """全部落として中心0件にしない（先頭はモデルが立てた頂点として残す）。"""
    response = {"devices": [
        {"device_id": f"d{i}", "name": f"装置{i}", "role": "central",
         "description": "…", "evidence_chunk_ids": ["C00_000"]}
        for i in range(3)
    ]}

    devices = dc.absorb_devices(response, valid_chunk_ids={"C00_000"},
                               chapter_title="第一夜")

    assert sum(1 for d in devices if d["role"] == dc.ROLE_CENTRAL) == 1


def test_unknown_role_falls_back_to_incidental():
    """役割が不明なら弱い側へ倒す（除外を効かせすぎない）。"""
    response = {"devices": [{"device_id": "x", "name": "装置", "role": "たぶん中心",
                             "description": "…", "evidence_chunk_ids": ["C00_000"]}]}

    devices = dc.absorb_devices(response, valid_chunk_ids={"C00_000"},
                               chapter_title="第一夜")

    assert devices[0]["role"] == dc.ROLE_INCIDENTAL


# ── 判定ルール（(2)と(3)で共有） ──


def _device(name, role, chapter="第一夜"):
    return {"device_id": name, "name": name, "role": role,
            "chapter_title": chapter, "evidence_chunk_ids": ["C00_000"]}


def test_central_device_fails_immediately():
    verdict = dc.verdict_for_matches([_device("赤い日を数える", dc.ROLE_CENTRAL)])

    assert verdict["passed"] is False
    assert "第一夜" in verdict["reasons"][0]
    assert "C00_000" in verdict["reasons"][0], "根拠チャンクを判定理由に含める"


def test_single_incidental_device_passes():
    """付随装置の単体は偶然ありうる。ここで落とすと効きすぎる。"""
    verdict = dc.verdict_for_matches([_device("数える", dc.ROLE_INCIDENTAL)])

    assert verdict["passed"] is True


def test_two_incidental_devices_from_the_same_chapter_fail():
    """装置は単体でなく共起で章を再現する。"""
    verdict = dc.verdict_for_matches([
        _device("赤い日", dc.ROLE_INCIDENTAL, "第一夜"),
        _device("数える", dc.ROLE_INCIDENTAL, "第一夜"),
    ])

    assert verdict["passed"] is False
    assert "第一夜" in verdict["co_occurring_chapters"]


def test_incidental_devices_from_different_chapters_pass():
    """別々の章から1つずつなら、その章のなぞりではない。"""
    verdict = dc.verdict_for_matches([
        _device("数える", dc.ROLE_INCIDENTAL, "第一夜"),
        _device("待つ", dc.ROLE_INCIDENTAL, "第二夜"),
    ])

    assert verdict["passed"] is True


def test_no_matches_passes():
    assert dc.verdict_for_matches([])["passed"] is True


# ── 保存 ──


def test_catalog_path_is_per_source():
    """他作品・他作家へそのまま増やせる形にする。"""
    path = dc.catalog_path("AOZORA_000799")

    assert path.name == "AOZORA_000799.json"
    assert path.parent.name == "device_catalogs"


# ── 検出層（同時比較 + 根拠引用の機械検証） ──


class _FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {}


_DETECT_CATALOG = {
    "meta": {"work_title": "夢十夜"},
    "chapters": [
        {"chapter_title": "第一夜", "devices": [
            {"device_id": "d_kei", "role": "central", "chapter_title": "第一夜",
             "name": "偽の計数", "description": "…", "evidence_chunk_ids": ["C01_006"]}]},
        {"chapter_title": "第三夜", "devices": [
            {"device_id": "d_ko", "role": "central", "chapter_title": "第三夜",
             "name": "全知の子供", "description": "…", "evidence_chunk_ids": ["C03_001"]}]},
    ],
}


def test_detect_compares_all_devices_in_one_call():
    """装置ごとの独立二値判定にしない（試行回数で偽陽性が膨らむ）。"""
    llm = _FakeLLM({"reproduced": []})

    dc.detect_devices("こんな夢を見た。", _DETECT_CATALOG, call_json=llm)

    assert len(llm.calls) == 1, "全装置を一度に見せる"
    assert "d_kei" in llm.calls[0]["prompt"] and "d_ko" in llm.calls[0]["prompt"]


def test_detect_allows_multiple_hits():
    """テキストは複数装置を同時に含みうる（単一選択にすると偽陰性へ壊れる）。"""
    draft = "赤い日を数えた。子供が来歴を言い当てた。"
    llm = _FakeLLM({"reproduced": [
        {"device_id": "d_kei", "quote": "赤い日を数えた", "reason": "…"},
        {"device_id": "d_ko", "quote": "来歴を言い当てた", "reason": "…"},
    ]})

    got = dc.detect_devices(draft, _DETECT_CATALOG, call_json=llm)

    assert {d["device_id"] for d in got} == {"d_kei", "d_ko"}


def test_detect_allows_empty():
    llm = _FakeLLM({"reproduced": []})

    assert dc.detect_devices("こんな夢を見た。", _DETECT_CATALOG, call_json=llm) == []


def test_detect_drops_hallucinated_quotes():
    """引用が検査対象に実在しなければ破棄する（機械で落とせる偽陽性）。"""
    llm = _FakeLLM({"reproduced": [
        {"device_id": "d_kei", "quote": "存在しない一文である", "reason": "…"},
    ]})

    assert dc.detect_devices("こんな夢を見た。", _DETECT_CATALOG, call_json=llm) == []


def test_detect_ignores_whitespace_when_verifying_quotes():
    llm = _FakeLLM({"reproduced": [
        {"device_id": "d_kei", "quote": "赤い日を 数えた", "reason": "…"},
    ]})

    got = dc.detect_devices("赤い日を数えた。", _DETECT_CATALOG, call_json=llm)

    assert [d["device_id"] for d in got] == ["d_kei"]


def test_detect_drops_unknown_device_id():
    llm = _FakeLLM({"reproduced": [
        {"device_id": "存在しない装置", "quote": "こんな夢を見た", "reason": "…"},
    ]})

    assert dc.detect_devices("こんな夢を見た。", _DETECT_CATALOG, call_json=llm) == []
