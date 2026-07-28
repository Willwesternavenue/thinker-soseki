import { describe, expect, it } from "vitest";
import {
  DEFAULT_BRIDGE_PROHIBITION,
  composeBridges,
  renderBridgeSection,
} from "./bridges";

const rule = (over: Record<string, unknown> = {}) => ({
  rule_id: "br_1",
  title: "傍観者の視線としての大人と小児の構図",
  lifecycle: "active",
  ...over,
});

const version = (over: Record<string, unknown> = {}) => ({
  rule_id: "br_1",
  version: 1,
  status: "approved",
  content: {
    source_thought_id: "bokan_sha_no_genkai",
    target_creative_card_id: "cc_A",
    rationale: "観察の限界の主張が、距離を保つ視点の型に対応する",
    forbidden_inferences: ["思想の文言を登場人物の台詞としてそのまま言わせない"],
  },
  ...over,
});

const thought = (over: Record<string, unknown> = {}) => ({
  thought_id: "bokan_sha_no_genkai",
  title: "傍観者の観察は対象と同化できない",
  core_claim: "外から眺める理解は形式に流れやすい",
  status: "approved",
  ...over,
});

const creative = (over: Record<string, unknown> = {}) => ({
  card_id: "cc_A",
  card_type: "perspective",
  title: "大人が小児を見る距離を保った視点",
  summary: "一段離れた立場から人事を観察する",
  status: "approved",
  ...over,
});

describe("composeBridges(橋の組み立て)", () => {
  it("承認済みの規則・思想カード・創作カードが揃った橋だけを返す", () => {
    const bridges = composeBridges({
      rules: [rule()],
      versions: [version()],
      thoughtCards: [thought()],
      creativeCards: [creative()],
    });

    expect(bridges).toHaveLength(1);
    expect(bridges[0]).toMatchObject({
      rule_id: "br_1",
      thought_title: "傍観者の観察は対象と同化できない",
      thought_claim: "外から眺める理解は形式に流れやすい",
      technique_title: "大人が小児を見る距離を保った視点",
    });
  });

  it("版が承認済みでない規則は使わない", () => {
    expect(
      composeBridges({
        rules: [rule()],
        versions: [version({ status: "draft" })],
        thoughtCards: [thought()],
        creativeCards: [creative()],
      })
    ).toEqual([]);
  });

  it("元の思想カードが承認済みでなければ橋は架からない", () => {
    // 承認の取り消しが橋に波及すること。gen_rules.approve_rule と同じ規律を
    // 読み出し時にも掛ける(承認後にカード側だけ取り消された場合に効く)
    expect(
      composeBridges({
        rules: [rule()],
        versions: [version()],
        thoughtCards: [thought({ status: "draft" })],
        creativeCards: [creative()],
      })
    ).toEqual([]);
  });

  it("先の創作カードが承認済みでなければ橋は架からない", () => {
    expect(
      composeBridges({
        rules: [rule()],
        versions: [version()],
        thoughtCards: [thought()],
        creativeCards: [creative({ status: "rejected" })],
      })
    ).toEqual([]);
  });

  it("lifecycle が active でない規則は使わない", () => {
    expect(
      composeBridges({
        rules: [rule({ lifecycle: "deprecated" })],
        versions: [version()],
        thoughtCards: [thought()],
        creativeCards: [creative()],
      })
    ).toEqual([]);
  });

  it("同じ規則に複数の承認版があれば最新版を使う", () => {
    const bridges = composeBridges({
      rules: [rule()],
      versions: [
        version({ version: 1, content: { ...version().content, rationale: "旧" } }),
        version({ version: 2, content: { ...version().content, rationale: "新" } }),
      ],
      thoughtCards: [thought()],
      creativeCards: [creative()],
    });

    expect(bridges[0].rationale).toBe("新");
  });

  it("禁止事項が空でも既定の禁止を必ず持つ", () => {
    const bridges = composeBridges({
      rules: [rule()],
      versions: [
        version({ content: { ...version().content, forbidden_inferences: [] } }),
      ],
      thoughtCards: [thought()],
      creativeCards: [creative()],
    });

    expect(bridges[0].forbidden).toContain(DEFAULT_BRIDGE_PROHIBITION);
  });
});

describe("renderBridgeSection(プロンプト片)", () => {
  const bridge = composeBridges({
    rules: [rule()],
    versions: [version()],
    thoughtCards: [thought()],
    creativeCards: [creative()],
  })[0];

  it("思想→書き方の対応と禁止を含む", () => {
    const text = renderBridgeSection([bridge]);

    expect(text).toContain("傍観者の観察は対象と同化できない");
    expect(text).toContain("大人が小児を見る距離を保った視点");
    expect(text).toContain("台詞");
  });

  it("橋が無ければ空文字(節を出さない)", () => {
    expect(renderBridgeSection([])).toBe("");
  });
});
