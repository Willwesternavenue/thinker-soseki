import { describe, expect, it } from "vitest";
import { mergeThoughtCards } from "./cards";
import {
  diversifyEvidence,
  filterQuotableChunks,
  mergeEvidence,
  selectEvidence,
} from "./evidence";
import { runOutputGuardExact, buildSafeAnswer } from "./guard";
import { aggregateThoughtHits } from "./router";
import { buildRetrievalQuery, normalizeSubjectReferences } from "./session";
import type { EvidenceChunk, Persona, ThoughtCard } from "./types";

const persona: Persona = {
  person_id: "natsume_soseki",
  display_name: "X漱石",
  system_prompt: "",
  first_person: "俺",
  banned_terms_exact: ["社長が", "社長は", "社長として"],
  banned_terms_contextual: ["資料", "参考", "私は", "私が", "本人"],
  style_rules: {},
  quote_policy: { max_quote_length: 100 },
  safety_policy: {},
  fallback_card_id: "card_fallback_001",
};

function card(overrides: Partial<ThoughtCard>): ThoughtCard {
  return {
    card_id: "card_x",
    thought_id: "X",
    title: "テスト",
    importance: "core",
    core_claim: "",
    distinctions: [],
    answer_policy: [],
    prohibitions: [],
    related_thought_ids: [],
    representative_chunk_ids: [],
    ...overrides,
  };
}

function chunk(overrides: Partial<EvidenceChunk>): EvidenceChunk {
  return {
    chunk_id: "c1",
    source_id: "BOOK_001",
    chapter_title: null,
    section_title: null,
    source_page: null,
    printed_page: null,
    text: "テキスト",
    summary: null,
    verbatim: false,
    evidence_role: null,
    quote_allowed: false,
    score: 0.8,
    origin: "vector",
    ...overrides,
  };
}

describe("filterQuotableChunks(仕様7.8: 引用可能条件のコード強制)", () => {
  it("role=quote かつ verbatim かつ quote_allowed のみ通す", () => {
    const chunks = [
      chunk({ chunk_id: "ok", evidence_role: "quote", verbatim: true, quote_allowed: true }),
      chunk({ chunk_id: "not_quote_role", evidence_role: "example", verbatim: true, quote_allowed: true }),
      chunk({ chunk_id: "not_verbatim", evidence_role: "quote", verbatim: false, quote_allowed: true }),
      chunk({ chunk_id: "not_allowed", evidence_role: "quote", verbatim: true, quote_allowed: false }),
      chunk({ chunk_id: "vector_hit", evidence_role: null, verbatim: true, quote_allowed: false }),
    ];
    const quotable = filterQuotableChunks(chunks);
    expect(quotable.map((c) => c.chunk_id)).toEqual(["ok"]);
  });
});

describe("mergeThoughtCards(仕様7.5: primary1+secondary最大2、prohibitions和集合)", () => {
  it("secondaryは2件までに切り詰める", () => {
    const merged = mergeThoughtCards(card({ thought_id: "A" }), [
      card({ thought_id: "B" }),
      card({ thought_id: "C" }),
      card({ thought_id: "D" }),
    ]);
    expect(merged.all).toHaveLength(3);
    expect(merged.all[0].thought_id).toBe("A");
  });

  it("prohibitionsは和集合(重複排除)", () => {
    const merged = mergeThoughtCards(
      card({ prohibitions: ["禁止1", "禁止2"] }),
      [card({ prohibitions: ["禁止2", "禁止3"] })]
    );
    expect(merged.prohibitions.sort()).toEqual(["禁止1", "禁止2", "禁止3"]);
  });
});

describe("runOutputGuardExact(仕様13.1: 完全一致)", () => {
  it("内部語を検出する", () => {
    expect(runOutputGuardExact("これは思想カードの内容だ", persona)).toContain("思想カード");
    expect(runOutputGuardExact("RAG の仕組みでは", persona)).toContain("RAG");
    expect(runOutputGuardExact("社長として言うが", persona)).toContain("社長として");
  });

  it("ASCII語は単語境界で判定(誤検出防止)", () => {
    expect(runOutputGuardExact("fragmentという英語がある", persona)).toEqual([]);
    expect(runOutputGuardExact("STORAGEの話", persona)).toEqual([]);
  });

  it("日常語(資料・参考)は完全一致では検出しない(v1.1: judgeに回す)", () => {
    expect(runOutputGuardExact("この資料を参考にした", persona)).toEqual([]);
  });

  it("問題ない回答は通す", () => {
    expect(
      runOutputGuardExact("俺はそう思う。憧れに向かって生きろ。", persona)
    ).toEqual([]);
  });

  it("文字化け(置換文字U+FFFD)を検出して再生成対象にする", () => {
    const hits = runOutputGuardExact("死の�coverage、死の覚悟が生を支える", persona);
    expect(hits.some((h) => h.includes("文字化け"))).toBe(true);
  });

  it("安全側回答自体はGuardを通る", () => {
    expect(runOutputGuardExact(buildSafeAnswer(persona), persona)).toEqual([]);
  });
});

describe("aggregateThoughtHits(仕様7.4 Stage2: thought_id集計)", () => {
  it("ヒット数・最大類似度・平均類似度を集計し最大類似度順に返す", () => {
    const result = aggregateThoughtHits([
      { question_id: "q1", target_thought_id: "A", question: "", intent: "definition", similarity: 0.7 },
      { question_id: "q2", target_thought_id: "A", question: "", intent: "definition", similarity: 0.6 },
      { question_id: "q3", target_thought_id: "B", question: "", intent: "definition", similarity: 0.8 },
    ]);
    expect(result[0].thoughtId).toBe("B");
    expect(result[0].votes).toBe(1);
    const a = result.find((r) => r.thoughtId === "A")!;
    expect(a.votes).toBe(2);
    expect(a.maxSim).toBeCloseTo(0.7);
    expect(a.avgSim).toBeCloseTo(0.65);
  });
});

describe("normalizeSubjectReferences(人物の表記ゆれ正規化)", () => {
  it("号のみ・姓+号・敬称付きを「夏目漱石」に統一する", () => {
    expect(normalizeSubjectReferences("漱石の則天去私とは"))
      .toBe("夏目漱石の則天去私とは");
    expect(normalizeSubjectReferences("夏目漱石はどう考える"))
      .toBe("夏目漱石はどう考える");
    expect(normalizeSubjectReferences("漱石先生の講義"))
      .toBe("夏目漱石の講義");
    expect(normalizeSubjectReferences("夏目先生の晩年"))
      .toBe("夏目漱石の晩年");
    expect(normalizeSubjectReferences("夏目金之助の生涯"))
      .toBe("夏目漱石の生涯");
  });

  it("ラテン表記 Natsume Soseki / Sōseki も正規化する", () => {
    expect(normalizeSubjectReferences("Natsume Sosekiの文明論"))
      .toBe("夏目漱石の文明論");
    expect(normalizeSubjectReferences("sōseki の個人主義"))
      .toBe("夏目漱石 の個人主義");
  });

  it("既に正規名なら二重化しない", () => {
    expect(normalizeSubjectReferences("夏目漱石の文学"))
      .toBe("夏目漱石の文学");
  });

  it("アバターへの二人称「あなた」「ご自身」を本人に正規化する(検索改善)", () => {
    expect(normalizeSubjectReferences("あなたの代表作を教えてください"))
      .toBe("夏目漱石の代表作を教えてください");
    expect(normalizeSubjectReferences("あなた様のお考えは"))
      .toBe("夏目漱石のお考えは");
    expect(normalizeSubjectReferences("ご自身の体験は"))
      .toBe("夏目漱石の体験は");
  });
});

describe("buildRetrievalQuery(直近文脈の連結は指示語・省略形のみ)", () => {
  const recent = [
    { role: "user", content: "AIは絶対負を持ちうるのか" },
    { role: "assistant", content: "当然そうです" },
  ];

  it("「それとも」は指示語ではない(選択肢を並べる接続詞)", () => {
    // 2026-08-09 実測: 「明治維新は…それとも精神を後退させたのでしょうか」が
    // 指示語ありと誤判定され、直前の質問が連結されて埋め込まれていた。
    // 連結される中身は毎回違うので、同じ質問なのに検索スコアが動く
    // (0.43 / 0.445 / 0.47)。留保の閾値 0.45 をまたぐ位置なので、
    // 同じ質問が日によって留保したりしなかったりしうる状態だった
    const q = "明治維新は日本を前に進めましたか？それとも精神を後退させたのでしょうか";
    expect(buildRetrievalQuery(q, null, recent)).toBe(q);
  });

  it("「それとは別に」のような本来の指示語は従来どおり連結する", () => {
    expect(buildRetrievalQuery("それはどう違うのか", null, recent)).toBe(
      "AIは絶対負を持ちうるのか それはどう違うのか"
    );
  });

  it("主語が明示された質問には直前の話題を連結しない(短くても)", () => {
    // 19文字。旧実装(20文字未満で連結)ではAI質問が前置され、分類がmixedに崩れて
    // 検索がAI一色になり、ゲーテの原典が1件も引けなかった実測がある
    expect(
      buildRetrievalQuery("ゲーテの素晴らしいところはどこですか？", null, recent)
    ).toBe("ゲーテの素晴らしいところはどこですか？");
    expect(buildRetrievalQuery("絶対負とは何ですか？", null, recent)).toBe(
      "絶対負とは何ですか？"
    );
  });

  it("指示語を含む質問には直前のユーザー発言を補う", () => {
    expect(buildRetrievalQuery("それはどういう意味?", null, recent)).toBe(
      "AIは絶対負を持ちうるのか それはどういう意味?"
    );
    // 20文字超でも指示語があれば補う(旧実装では取りこぼしていた)
    expect(
      buildRetrievalQuery("それについてもう少し詳しく教えてください", null, recent)
    ).toBe("AIは絶対負を持ちうるのか それについてもう少し詳しく教えてください");
  });

  it("省略形の追い質問にも直前のユーザー発言を補う", () => {
    expect(buildRetrievalQuery("もっと詳しく", null, recent)).toBe(
      "AIは絶対負を持ちうるのか もっと詳しく"
    );
    expect(buildRetrievalQuery("なぜ?", null, recent)).toBe(
      "AIは絶対負を持ちうるのか なぜ?"
    );
  });

  it("指示語に見える慣用句・自立した疑問詞では連結しない", () => {
    expect(buildRetrievalQuery("この世に絶対負はあるのか", null, recent)).toBe(
      "この世に絶対負はあるのか"
    );
    expect(buildRetrievalQuery("これからの日本はどうなりますか", null, recent)).toBe(
      "これからの日本はどうなりますか"
    );
    expect(buildRetrievalQuery("なぜ人は生きるのですか", null, recent)).toBe(
      "なぜ人は生きるのですか"
    );
  });

  it("直近メッセージが無い(新規セッション)なら連結しない", () => {
    expect(buildRetrievalQuery("それはどういう意味?", null, [])).toBe(
      "それはどういう意味?"
    );
  });

  it("連結した文脈側の人物表記も正規化される", () => {
    expect(
      buildRetrievalQuery("それはどこで論じられていますか", null, [
        { role: "user", content: "漱石先生の主著は?" },
      ])
    ).toBe("夏目漱石の主著は? それはどこで論じられていますか");
  });
});

describe("mergeEvidence / diversifyEvidence(仕様7.7)", () => {
  it("同一chunk_idは重複排除し、linked情報を優先する", () => {
    const merged = mergeEvidence(
      [chunk({ chunk_id: "c1", origin: "linked", evidence_role: "quote", quote_allowed: true, score: 0.9 })],
      [chunk({ chunk_id: "c1", origin: "vector", score: 0.95 })],
      [chunk({ chunk_id: "c2", origin: "keyword", score: 0.5 })]
    );
    expect(merged).toHaveLength(2);
    const c1 = merged.find((c) => c.chunk_id === "c1")!;
    expect(c1.origin).toBe("linked");
    expect(c1.evidence_role).toBe("quote");
  });

  it("最大10件に絞り、同一sourceは5件まで", () => {
    const many = Array.from({ length: 30 }, (_, i) =>
      chunk({
        chunk_id: `c${i}`,
        source_id: i < 15 ? "BOOK_001" : `SRC_${i}`,
        score: 1 - i * 0.01,
      })
    );
    const result = diversifyEvidence(many);
    expect(result.length).toBeLessThanOrEqual(10);
    const fromBook1 = result.filter((c) => c.source_id === "BOOK_001");
    expect(fromBook1.length).toBeLessThanOrEqual(5);
  });
});

describe("selectEvidence(選抜と並べ替えの順序)", () => {
  // 小説はベクトル検索でよく上位に来る(文体が似ているため)。作者の直接発言は
  // 全10,152チャンク中408件(4%)しかない
  const fiction = Array.from({ length: 10 }, (_, i) =>
    chunk({
      chunk_id: `f${i}`,
      source_id: `NOVEL_${i}`,
      corpus_role: "narrative_reference",
      speaker_role: "character",
      score: 0.9 - i * 0.01,
      origin: "vector",
    })
  );
  const author = chunk({
    chunk_id: "a",
    source_id: "ESSAY_1",
    corpus_role: "core_thought",
    speaker_role: "author_direct",
    score: 0.5,
    origin: "vector",
  });

  it("思想質問では、小説がスコア上位を占めても作者の直接発言を落とさない", () => {
    // 2026-08-02 実測: 小説寄りの検索語で上位10件が全て小説になった。
    // 切り詰めてから並べ替えると、作者発言は選抜の段階で消えている
    const got = selectEvidence([...fiction, author], "thought");
    expect(got.map((c) => c.chunk_id)).toContain("a");
    expect(got[0].chunk_id).toBe("a");
  });

  it("人物質問では下げない(人物の発言こそが根拠のため)", () => {
    const got = selectEvidence([...fiction, author], "character");
    expect(got[0].chunk_id).toBe("f0");
  });

  it("多様性の制御(同一source は MAX_PER_SOURCE=5 まで)は保つ", () => {
    const sameSource = Array.from({ length: 8 }, (_, i) =>
      chunk({ chunk_id: `s${i}`, source_id: "ESSAY_1", score: 0.9 - i * 0.01, origin: "vector" })
    );
    expect(selectEvidence(sameSource, "thought")).toHaveLength(5);
  });
});
