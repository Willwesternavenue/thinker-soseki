import { describe, expect, it } from "vitest";
import {
  attributionFor,
  buildRetrievalRoute,
  corpusRouteFor,
  corpusRouteKind,
  decideAbstention,
  detectCharacter,
  directSourceIds,
  hasDirectSource,
  rankByRoute,
  retrievalFiltersFor,
} from "./corpus-routing";

type TestChunk = {
  chunk_id: string;
  corpus_role: string | null;
  speaker_role: string | null;
  score: number;
};

const chunk = (over: Partial<TestChunk> = {}): TestChunk => ({
  chunk_id: "c1",
  corpus_role: "core_thought",
  speaker_role: "author_direct",
  score: 0.7,
  ...over,
});

describe("detectCharacter(作中人物の検出)", () => {
  it("既知の登場人物名を拾う", () => {
    expect(detectCharacter("代助はなぜ働かないのか")).toBe("daisuke");
    expect(detectCharacter("三四郎について")).toBe("sanshiro");
  });

  it("名前が挙がらない質問は人物質問にしない", () => {
    // 誤判定すると作者の思想が主根拠から外れる
    expect(detectCharacter("近代化についてどう考えたか")).toBeNull();
  });
});

describe("corpusRouteKind(質問種別 → コーパスの検索ルート)", () => {
  it("思想・人生相談は思想ルート", () => {
    expect(corpusRouteKind("thought", "開化とは何か")).toBe("thought");
    expect(corpusRouteKind("life_advice", "仕事が辛い")).toBe("thought");
  });

  it("人物名が入っていれば人物ルートを優先する", () => {
    expect(corpusRouteKind("thought", "代助の生き方をどう見るか")).toBe("character");
    expect(corpusRouteKind("person_or_work", "三四郎とは")).toBe("character");
  });

  it("創作依頼は創作ルート", () => {
    expect(corpusRouteKind("creative", "短編を書いて")).toBe("creative");
  });

  it("人物名があっても創作依頼なら創作ルート(書かせる方が主目的)", () => {
    expect(corpusRouteKind("creative", "代助が出てくる話を書いて")).toBe("creative");
  });

  it("未知・その他は思想ルートに倒す", () => {
    expect(corpusRouteKind("fact", "生年は")).toBe("thought");
    expect(corpusRouteKind("mixed", "なんとなく")).toBe("thought");
  });
});

describe("corpusRouteFor(検索順)", () => {
  it("思想は本人の直接発言から始め、小説は最後に明示付きで置く", () => {
    const route = corpusRouteFor("thought");
    expect(route.map((s) => s.index)).toEqual([
      "author_thought_core",
      "author_thought_support",
      "creative_grammar",
      "narrative_reference",
    ]);
    expect(route[0].requires_attribution_notice).toBe(false);
    expect(route[3].requires_attribution_notice).toBe(true);
  });

  it("創作は作風の論から始め、思想は Bridge Rule 経由に限る", () => {
    const route = corpusRouteFor("creative");
    expect(route[0].index).toBe("creative_grammar");
    expect(route.find((s) => s.index === "author_thought_core")?.requires_bridge_rule).toBe(
      true
    );
  });

  it("人物は作中人物の判断から始め、作者思想は比較対象に限る", () => {
    const route = corpusRouteFor("character");
    expect(route[0].index).toBe("character_judgment");
    expect(route[0].requires_attribution_notice).toBe(true);
    expect(route.find((s) => s.index === "author_thought_core")?.comparison_only).toBe(true);
  });
});

describe("retrievalFiltersFor(実際に引く範囲)", () => {
  it("思想ルートは小説も含めて引く(比較・補助として使うため)", () => {
    expect(retrievalFiltersFor("thought").corpusRoles).toEqual([
      "core_thought",
      "supporting_thought",
      "creative_grammar",
      "narrative_reference",
    ]);
  });

  it("創作ルートは思想を引かない(Bridge Rule が未実装のため)", () => {
    // 思想チャンクを登場人物の台詞へそのまま注入させない
    expect(retrievalFiltersFor("creative").corpusRoles).not.toContain("core_thought");
  });

  it("人物ルートは人物判断・小説・比較用の思想を引く", () => {
    // 段どうしで重なる役割は1回だけ(character_judgment 段と narrative_reference 段)
    expect(retrievalFiltersFor("character").corpusRoles).toEqual([
      "character_judgment",
      "narrative_reference",
      "core_thought",
    ]);
  });

  it("人物判断は小説の中から引く", () => {
    // ⚠️ corpus_role='character_judgment' だけを条件にすると永久に空になる。
    // 取り込みは小説を narrative_reference に割り当てるため。
    const step = corpusRouteFor("character")[0];
    expect(step.index).toBe("character_judgment");
    expect(step.corpusRoles).toContain("narrative_reference");
    expect(step.speakerRoles).toEqual(["character"]);
  });
});

describe("attributionFor(作者の発言かどうかの明示)", () => {
  it("作者の直接発言には注記を付けない", () => {
    expect(attributionFor(chunk())).toBeNull();
  });

  it("登場人物の発言には作者本人でないと明示する", () => {
    const notice = attributionFor(chunk({ speaker_role: "character" }));
    expect(notice).toContain("登場人物");
    expect(notice).toContain("本人");
  });

  it("語り手の文にも明示する", () => {
    expect(attributionFor(chunk({ speaker_role: "narrator" }))).toContain("語り手");
  });

  it("引用された第三者の発言にも明示する", () => {
    expect(attributionFor(chunk({ speaker_role: "quoted_person" }))).toContain("引用");
  });

  it("小説由来なら speaker_role が未分類でも明示する", () => {
    const notice = attributionFor(
      chunk({ corpus_role: "narrative_reference", speaker_role: null })
    );
    expect(notice).not.toBeNull();
  });

  it("コーパス層より前に入れた原典(role未設定)は従来どおり注記なし", () => {
    // 既存の思想モードで投入した資料を壊さない
    expect(attributionFor(chunk({ corpus_role: null, speaker_role: null }))).toBeNull();
  });
});

describe("rankByRoute(思想質問で小説を主根拠にしない)", () => {
  const author = { chunk_id: "a", corpus_role: "core_thought", speaker_role: "author_direct", score: 0.5 };
  const fiction = { chunk_id: "f", corpus_role: "narrative_reference", speaker_role: "character", score: 0.9 };

  it("思想質問では、スコアが高くても小説を作者発言より後ろへ下げる", () => {
    expect(rankByRoute([fiction, author], "thought").map((c) => c.chunk_id)).toEqual([
      "a",
      "f",
    ]);
  });

  it("人物質問では下げない(人物の発言こそが根拠のため)", () => {
    expect(rankByRoute([fiction, author], "character").map((c) => c.chunk_id)).toEqual([
      "f",
      "a",
    ]);
  });

  it("同じ区分の中ではスコア順を保つ", () => {
    const low = { ...author, chunk_id: "a2", score: 0.2 };
    expect(rankByRoute([low, author], "thought").map((c) => c.chunk_id)).toEqual(["a", "a2"]);
  });
});

describe("hasDirectSource(直接の原典があるか)", () => {
  it("作者の直接発言があれば直接の原典あり", () => {
    expect(hasDirectSource([chunk()], "thought")).toBe(true);
  });

  it("関連度が低すぎるヒットは直接の原典として数えない", () => {
    // ベクトル検索は常に上位N件を返すため、関連が無くても何かが返る。
    // 実測: 原典にある話題 0.68 / 現代語(生成AI・暗号資産) 0.19〜0.35
    expect(hasDirectSource([chunk({ score: 0.3 })], "thought")).toBe(false);
    expect(hasDirectSource([chunk({ score: 0.68 })], "thought")).toBe(true);
  });

  it("小説だけなら思想質問の直接の原典にはならない", () => {
    expect(
      hasDirectSource([chunk({ corpus_role: "narrative_reference", speaker_role: "character" })], "thought")
    ).toBe(false);
  });

  it("関連度が低ければ人物質問でも直接の原典にしない", () => {
    expect(
      hasDirectSource(
        [chunk({ corpus_role: "character_judgment", speaker_role: "character", score: 0.2 })],
        "character"
      )
    ).toBe(false);
  });

  it("人物質問では登場人物の発言が直接の原典になる", () => {
    expect(
      hasDirectSource([chunk({ corpus_role: "character_judgment", speaker_role: "character" })], "character")
    ).toBe(true);
  });

  it("根拠が無ければ false", () => {
    expect(hasDirectSource([], "thought")).toBe(false);
  });
});

describe("decideAbstention(留保)", () => {
  it("直接の原典が無ければ留保理由を残す", () => {
    const reason = decideAbstention({ kind: "thought", evidence: [] });
    expect(reason).toContain("直接");
  });

  it("関連の薄いヒットしか無ければ留保する(現代の質問で断定させない)", () => {
    expect(decideAbstention({ kind: "thought", evidence: [chunk({ score: 0.3 })] })).not.toBeNull();
  });

  it("小説しか無い思想質問も留保する(文体の一致を思想の一致にしない)", () => {
    const reason = decideAbstention({
      kind: "thought",
      evidence: [chunk({ corpus_role: "narrative_reference", speaker_role: "character" })],
    });
    expect(reason).not.toBeNull();
  });

  it("直接の原典があれば留保しない", () => {
    expect(decideAbstention({ kind: "thought", evidence: [chunk()] })).toBeNull();
  });
});

describe("directSourceIds(直接の原典として使えたもの)", () => {
  const src = (over: Partial<TestChunk> & { source_id: string }) => ({
    ...chunk(),
    ...over,
  });

  it("作者の直接発言で関連度が足りるものだけを数える", () => {
    expect(
      directSourceIds(
        [
          src({ source_id: "S1" }),
          src({ source_id: "S2", score: 0.2 }),
          src({ source_id: "S3", corpus_role: "narrative_reference", speaker_role: "narrator" }),
        ],
        "thought"
      )
    ).toEqual(["S1"]);
  });

  it("留保したときは空になる(traceの中で矛盾させない)", () => {
    const evidence = [src({ source_id: "S1", score: 0.3 })];
    expect(decideAbstention({ kind: "thought", evidence })).not.toBeNull();
    expect(directSourceIds(evidence, "thought")).toEqual([]);
  });

  it("同じ原典は1回だけ数える", () => {
    expect(
      directSourceIds([src({ source_id: "S1" }), src({ source_id: "S1" })], "thought")
    ).toEqual(["S1"]);
  });
});

describe("buildRetrievalRoute(traceに残す検索経路)", () => {
  it("どのルートで何件引いたかを残す", () => {
    const route = buildRetrievalRoute({
      kind: "thought",
      characterId: null,
      evidence: [
        chunk(),
        chunk({ chunk_id: "c2", corpus_role: "narrative_reference", speaker_role: "narrator" }),
      ],
    });

    expect(route.kind).toBe("thought");
    expect(route.indexes).toEqual([
      "author_thought_core",
      "author_thought_support",
      "creative_grammar",
      "narrative_reference",
    ]);
    expect(route.counts_by_corpus_role).toEqual({
      core_thought: 1,
      narrative_reference: 1,
    });
    expect(route.attributed_chunk_ids).toEqual(["c2"]);
  });

  it("人物質問では対象人物も残す", () => {
    expect(
      buildRetrievalRoute({ kind: "character", characterId: "daisuke", evidence: [] })
        .character_id
    ).toBe("daisuke");
  });

  it("role未設定の原典も件数に数える(取りこぼしを見つけられるように)", () => {
    const route = buildRetrievalRoute({
      kind: "thought",
      characterId: null,
      evidence: [chunk({ corpus_role: null })],
    });
    expect(route.counts_by_corpus_role).toEqual({ unclassified: 1 });
  });
});
