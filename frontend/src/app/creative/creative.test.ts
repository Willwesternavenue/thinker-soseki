import { describe, expect, it } from "vitest";
import {
  STEPS,
  buildBriefRaw,
  canRenderWork,
  failureMessage,
  isDuplicateKeyError,
  shouldKeepPolling,
  stepProgress,
  validateBrief,
} from "./creative";

const validInput = {
  profileId: "cp_yume_juya",
  motif: "鏡",
  situation: "鏡の中の自分だけが年を取る",
  emotionalTarget: "静かな恐ろしさ",
  period: "",
  length: "1500",
  constraints: "",
};

describe("validateBrief(入力検証)", () => {
  it("プロファイル未選択は生成させない", () => {
    const r = validateBrief({ ...validInput, profileId: "" });
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.errors.join()).toContain("プロファイル");
  });

  it("モチーフが空なら生成させない", () => {
    const r = validateBrief({ ...validInput, motif: "  " });
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.errors.join()).toContain("モチーフ");
  });

  it("文字数が数値でなければ生成させない", () => {
    const r = validateBrief({ ...validInput, length: "たくさん" });
    expect(r.ok).toBe(false);
    expect(r.ok === false && r.errors.join()).toContain("文字数");
  });

  it("文字数が範囲外なら生成させない", () => {
    expect(validateBrief({ ...validInput, length: "50" }).ok).toBe(false);
    expect(validateBrief({ ...validInput, length: "100000" }).ok).toBe(false);
  });

  it("文字数は未入力でもよい(workerが既定値を決める)", () => {
    expect(validateBrief({ ...validInput, length: "" }).ok).toBe(true);
  });

  it("エラーは一度にまとめて返す", () => {
    const r = validateBrief({ ...validInput, profileId: "", motif: "" });
    expect(r.ok === false && r.errors.length).toBe(2);
  });
});

describe("buildBriefRaw(worker へ渡す brief)", () => {
  it("worker のキー名で組み立てる", () => {
    expect(buildBriefRaw(validInput)).toEqual({
      motif: "鏡",
      situation: "鏡の中の自分だけが年を取る",
      emotional_target: "静かな恐ろしさ",
      length: 1500,
    });
  });

  it("空欄のキーは入れない(workerの『指定なし』扱いに任せる)", () => {
    const brief = buildBriefRaw({ ...validInput, situation: "", length: "" });
    expect(brief).not.toHaveProperty("situation");
    expect(brief).not.toHaveProperty("length");
    expect(brief).not.toHaveProperty("period");
  });

  it("追加制約は改行区切りで配列にし、空行は捨てる", () => {
    const brief = buildBriefRaw({
      ...validInput,
      constraints: "夢の記述から始める\n\n  \n登場人物は二人まで",
    });
    expect(brief.constraints).toEqual(["夢の記述から始める", "登場人物は二人まで"]);
  });
});

describe("stepProgress(進捗表示)", () => {
  it("worker の実行順どおりに並ぶ", () => {
    expect(STEPS.map((s) => s.key)).toEqual([
      "profile",
      "cards",
      "brief",
      "sources",
      "outline",
      "draft",
      "guard",
      "save",
    ]);
  });

  it("現在のステップの位置と日本語ラベルを返す", () => {
    const p = stepProgress("draft");
    expect(p.index).toBe(6);
    expect(p.total).toBe(8);
    expect(p.label).toContain("本文");
  });

  it("再生成で draft へ戻っても位置が飛ばない", () => {
    expect(stepProgress("guard").index).toBe(7);
  });

  it("未開始・未知のステップでも落ちない", () => {
    expect(stepProgress(null).index).toBe(0);
    expect(stepProgress("なにか").index).toBe(0);
  });
});

describe("shouldKeepPolling(ポーリング停止条件)", () => {
  it("pending / running は続ける", () => {
    expect(shouldKeepPolling("pending")).toBe(true);
    expect(shouldKeepPolling("running")).toBe(true);
  });

  it("succeeded / failed で止める", () => {
    expect(shouldKeepPolling("succeeded")).toBe(false);
    expect(shouldKeepPolling("failed")).toBe(false);
  });
});

describe("failureMessage(失敗分類)", () => {
  it("カード未承認は何をすればよいかを示す", () => {
    const m = failureMessage("invariant_violation: 承認済みカードが0枚です");
    expect(m.title).toContain("承認");
    expect(m.hint).toContain("創作カード");
  });

  it("Guard超過は原典に似すぎたことを示す", () => {
    const m = failureMessage("guard_exhausted: 再生成2回でも通らなかった");
    expect(m.title).toContain("原典");
  });

  it("LLMエラーは再試行を促す", () => {
    const m = failureMessage("llm_error: timeout");
    expect(m.hint).toContain("もう一度");
  });

  it("未知の失敗でも原文を落とさない", () => {
    const m = failureMessage("なにか想定外");
    expect(m.detail).toBe("なにか想定外");
  });

  it("error_message が無くても落ちない", () => {
    expect(failureMessage(null).title).toBeTruthy();
  });
});

describe("isDuplicateKeyError(二重送信の判定)", () => {
  it("idempotency_key の一意制約違反を見分ける", () => {
    expect(isDuplicateKeyError({ code: "23505" })).toBe(true);
  });

  it("他のDBエラーは二重送信として扱わない", () => {
    expect(isDuplicateKeyError({ code: "23503" })).toBe(false);
    expect(isDuplicateKeyError(null)).toBe(false);
  });
});

describe("canRenderWork(本文表示の不変条件)", () => {
  const ok = {
    status: "succeeded",
    final_text: "こんな夢を見た。",
    display_title: "鏡（AI創作）",
  };

  it("本文・表示題名・免責が揃って初めて表示できる", () => {
    expect(canRenderWork(ok, "これはAIによる創作です")).toBe(true);
  });

  it("表示題名が無ければ表示しない(素の題名を出さないため)", () => {
    expect(canRenderWork({ ...ok, display_title: null }, "免責")).toBe(false);
  });

  it("免責文が無ければ表示しない(仕様§5.1)", () => {
    expect(canRenderWork(ok, "")).toBe(false);
    expect(canRenderWork(ok, null)).toBe(false);
  });

  it("未完了のジョブは表示しない", () => {
    expect(canRenderWork({ ...ok, status: "running" }, "免責")).toBe(false);
  });
});
