import { describe, expect, it } from "vitest";
import { L3_EXCLUDED_RULE_SCOPES, loadL3RulesForTest, resetL3RulesCache } from "./l3shadow";

/**
 * judgment_rules の読み込みが Bridge Rule を除外することの回帰テスト。
 *
 * ⚠️ 実測(2026-08-02): rule_scope で絞っていなかったため、L3 は judgment 23件では
 * なく bridge_rule 6件を含む29件を評価していた。ブリッジ版の content は
 * trigger_conditions を持たない(source_thought_id / target_creative_card_id /
 * forbidden_inferences のみ)ので、発火条件が空のまま判定にかけられる。
 *
 * さらに L3_MODE の既定は "assist" のため、発火すると創作専用の制約
 * (「思想の文言を登場人物の台詞としてそのまま言わせない」等)が**通常回答**へ
 * 注入されうる。pipeline は routeKind==="creative" のときだけ fetchBridges を
 * 呼んで仕様§6「創作依頼における思想の唯一の経路」を守っているが、L3 経路が
 * その囲いを迂回していた。
 */

type QueryLog = {
  table: string;
  eq: Array<[string, unknown]>;
  neq: Array<[string, unknown]>;
};

/** select→eq/neq/in/order をチェーンできる最小のSupabaseスタブ。 */
function stubDb(rows: Record<string, unknown[]>, log: QueryLog[]) {
  return {
    from(table: string) {
      const entry: QueryLog = { table, eq: [], neq: [] };
      log.push(entry);
      const builder: Record<string, unknown> = {
        select: () => builder,
        eq: (col: string, val: unknown) => {
          entry.eq.push([col, val]);
          return builder;
        },
        neq: (col: string, val: unknown) => {
          entry.neq.push([col, val]);
          return builder;
        },
        in: () => builder,
        order: () => Promise.resolve({ data: rows[table] ?? [] }),
        then: (resolve: (v: unknown) => unknown) =>
          resolve({ data: rows[table] ?? [] }),
      };
      return builder;
    },
  } as never;
}

const identity = (over: Record<string, unknown> = {}) => ({
  rule_id: "jr_1",
  title: "文芸は道徳を超絶しない",
  rule_type: "boundary",
  ...over,
});

const version = (over: Record<string, unknown> = {}) => ({
  rule_id: "jr_1",
  rule_version_id: "v_1",
  version: 1,
  status: "approved",
  content: {
    trigger_conditions: ["文芸と道徳の関係が問われたとき"],
    action: { between: ["A", "B"], criterion: "倫理的分子の有無" },
  },
  ...over,
});

describe("loadL3Rules(Bridge Rule を通常回答の判断規則に混ぜない)", () => {
  it("bridge_rule を rule_scope で除外して問い合わせる", async () => {
    const log: QueryLog[] = [];
    const db = stubDb(
      { judgment_rules: [identity()], judgment_rule_versions: [version()] },
      log
    );
    resetL3RulesCache();

    await loadL3RulesForTest(db);

    const rulesQuery = log.find((q) => q.table === "judgment_rules");
    expect(rulesQuery).toBeDefined();
    // lifecycle=active だけで絞ると bridge_rule が混ざる(実測で29件評価していた)
    expect(rulesQuery!.neq).toContainEqual(["rule_scope", "bridge_rule"]);
  });

  it("除外する scope は bridge_rule だけにする", () => {
    // dialogue / response_policy を将来使い始めたときに黙って落とさない。
    // rule_scope の許容値は judgment / dialogue / response_policy / bridge_rule
    expect(L3_EXCLUDED_RULE_SCOPES).toEqual(["bridge_rule"]);
  });

  it("lifecycle=active と person_id の条件は従来どおり残す", async () => {
    const log: QueryLog[] = [];
    const db = stubDb(
      { judgment_rules: [identity()], judgment_rule_versions: [version()] },
      log
    );
    resetL3RulesCache();

    await loadL3RulesForTest(db);

    const rulesQuery = log.find((q) => q.table === "judgment_rules");
    expect(rulesQuery!.eq).toContainEqual(["lifecycle", "active"]);
    expect(rulesQuery!.eq.some(([col]) => col === "person_id")).toBe(true);
  });
});
