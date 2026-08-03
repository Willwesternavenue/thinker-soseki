import { describe, expect, it } from "vitest";
import { judgeResultFor } from "./guard";

/**
 * judge の記録が実態と一致することの回帰テスト。
 *
 * ⚠️ 修正前(2026-08-02)は2つの食い違いがあった:
 * 1. judge が例外で落ちた回を `{pass:true}` として返し trace に "pass" と記録して
 *    いた。APIキー失効やモデルID変更で judge が全件失敗しても「全件 Guard 通過」に
 *    見え、検査が静かに死んでいることに気づけない
 * 2. 完全一致でヒットした回は judge を**実行しない**のに "fail" と記録していた。
 *    走っていない判定を失敗として残していた
 *
 * どちらも "skipped" で表す(GuardResult.judge_result は pass/fail/skipped)。
 */
describe("judgeResultFor(judgeの記録が実態と一致する)", () => {
  it("judgeを実行して合格なら pass", () => {
    expect(judgeResultFor({ pass: true, issues: [], executed: true })).toBe("pass");
  });

  it("judgeを実行して不合格なら fail", () => {
    expect(
      judgeResultFor({ pass: false, issues: ["一人称の乱れ"], executed: true })
    ).toBe("fail");
  });

  it("judgeが例外で落ちた回は pass ではなく skipped", () => {
    // フェイルオープン(回答は止めない)だが、合格として記録はしない
    expect(
      judgeResultFor({ pass: true, issues: ["judge実行失敗(スキップ)"], executed: false })
    ).toBe("skipped");
  });

  it("judgeを実行していない回(null)は fail ではなく skipped", () => {
    // 完全一致でヒットして再生成が確定した場合。不合格の根拠は exact_match_hits 側
    expect(judgeResultFor(null)).toBe("skipped");
  });
});
