import { describe, expect, it } from "vitest";
import { checkApprovable, evidenceTypeLabel } from "./approval";

describe("checkApprovable(承認前の根拠検証)", () => {
  it("根拠がすべて実在すれば承認できる", () => {
    expect(checkApprovable(["c1", "c2"], ["c1", "c2", "c3"])).toEqual({ ok: true });
  });

  it("根拠が無いカードは承認できない", () => {
    const result = checkApprovable([], ["c1"]);
    expect(result.ok).toBe(false);
    expect(result).toMatchObject({ reason: expect.stringContaining("根拠チャンクが無い") });
  });

  it("null の根拠も承認できない", () => {
    expect(checkApprovable(null, ["c1"]).ok).toBe(false);
  });

  it("根拠が実在しなければ承認できず、欠けているIDを示す", () => {
    const result = checkApprovable(["c1", "c_missing"], ["c1"]);
    expect(result.ok).toBe(false);
    expect(result).toMatchObject({ reason: expect.stringContaining("c_missing") });
  });

  it("一部でも欠けていれば承認しない(部分的な承認をしない)", () => {
    expect(checkApprovable(["c1", "c2", "c3"], ["c1", "c2"]).ok).toBe(false);
  });
});

describe("evidenceTypeLabel(根拠種別の表示)", () => {
  it("創作論と小説本文での実演を区別して表示する", () => {
    expect(evidenceTypeLabel("author_creative_theory")).toContain("創作論");
    expect(evidenceTypeLabel("demonstrated_in_fiction")).toContain("小説本文");
  });

  it("未分類でも落ちない", () => {
    expect(evidenceTypeLabel(null)).toBe("（未分類）");
    expect(evidenceTypeLabel("unknown_type")).toBe("unknown_type");
  });
});
