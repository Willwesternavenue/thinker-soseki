/**
 * Bridge Rule の読み出しと注入（仕様 docs/CORPUS_T1_SPEC.md §6 / 指示書§12.2）。
 *
 * 思想チャンクを創作依頼へそのまま渡すことは禁止されている（文言が登場人物の
 * 台詞になる）。**承認済みの Bridge Rule を介した場合だけ**、思想は
 * 「書き方の対応」として創作の文脈に入る。橋が無ければ何も入らない —
 * これが現状の挙動で、このモジュールはその唯一の例外経路。
 *
 * 承認の鎖は読み出し時にも検証する: 規則の版が approved でも、元の思想カード・
 * 先の創作カードのどちらかが承認済みでなくなっていれば橋は架からない
 * （worker 側 gen_rules.approve_rule と同じ規律。承認後にカード側だけ
 * 取り消された場合に効く）。
 */

import type { SupabaseClient } from "@supabase/supabase-js";

/** 仕様§6 の禁止。LLM の出力・規則の内容に関わらず必ず添える。 */
export const DEFAULT_BRIDGE_PROHIBITION =
  "思想の文言を登場人物の台詞としてそのまま言わせない";

export type BridgeForPrompt = {
  rule_id: string;
  title: string;
  thought_id: string;
  thought_title: string;
  thought_claim: string | null;
  technique_card_id: string;
  technique_title: string;
  technique_summary: string | null;
  rationale: string | null;
  forbidden: string[];
};

type RuleRow = { rule_id: string; title: string; lifecycle: string };
type VersionRow = {
  rule_id: string;
  version: number;
  status: string;
  content: Record<string, unknown>;
};
type ThoughtRow = {
  thought_id: string;
  title: string;
  core_claim: string | null;
  status: string;
};
type CreativeRow = {
  card_id: string;
  card_type: string;
  title: string;
  summary: string | null;
  status: string;
};

/**
 * 橋を組み立てる（純粋関数。DB取得は `fetchBridges`）。
 *
 * 揃わない橋は黙って落とす — 片側の承認が取り消された規則が残っていても、
 * 創作の文脈に思想が漏れないことを最優先にする。
 */
export function composeBridges(input: {
  rules: RuleRow[];
  versions: VersionRow[];
  thoughtCards: ThoughtRow[];
  creativeCards: CreativeRow[];
}): BridgeForPrompt[] {
  const thoughtById = new Map(
    input.thoughtCards
      .filter((t) => t.status === "approved")
      .map((t) => [t.thought_id, t])
  );
  const creativeById = new Map(
    input.creativeCards
      .filter((c) => c.status === "approved")
      .map((c) => [c.card_id, c])
  );

  // 規則ごとに最新の承認版を採る
  const latestApproved = new Map<string, VersionRow>();
  for (const v of input.versions) {
    if (v.status !== "approved") continue;
    const held = latestApproved.get(v.rule_id);
    if (!held || v.version > held.version) latestApproved.set(v.rule_id, v);
  }

  const bridges: BridgeForPrompt[] = [];
  for (const rule of input.rules) {
    if (rule.lifecycle !== "active") continue;
    const version = latestApproved.get(rule.rule_id);
    if (!version) continue;

    const thoughtId = version.content.source_thought_id as string | undefined;
    const creativeId = version.content.target_creative_card_id as string | undefined;
    const thought = thoughtId ? thoughtById.get(thoughtId) : undefined;
    const creative = creativeId ? creativeById.get(creativeId) : undefined;
    if (!thought || !creative) continue;

    const forbidden = [
      ...((version.content.forbidden_inferences as string[] | undefined) ?? []),
    ];
    if (!forbidden.some((f) => f.includes("台詞"))) {
      forbidden.push(DEFAULT_BRIDGE_PROHIBITION);
    }

    bridges.push({
      rule_id: rule.rule_id,
      title: rule.title,
      thought_id: thought.thought_id,
      thought_title: thought.title,
      thought_claim: thought.core_claim,
      technique_card_id: creative.card_id,
      technique_title: creative.title,
      technique_summary: creative.summary,
      rationale: (version.content.rationale as string | null) ?? null,
      forbidden,
    });
  }
  return bridges;
}

/** 創作依頼のコンテキストに入れる節。橋が無ければ空（節ごと出さない）。 */
export function renderBridgeSection(bridges: BridgeForPrompt[]): string {
  if (bridges.length === 0) return "";
  const parts = bridges.map((b) => {
    const lines = [
      `### ${b.title}`,
      `思想: ${b.thought_title}${b.thought_claim ? ` — ${b.thought_claim}` : ""}`,
      `書き方: ${b.technique_title}${b.technique_summary ? ` — ${b.technique_summary}` : ""}`,
      b.rationale ? `対応の理由: ${b.rationale}` : "",
      b.forbidden.length
        ? `してはいけないこと:\n${b.forbidden.map((f) => `  - ${f}`).join("\n")}`
        : "",
    ];
    return lines.filter(Boolean).join("\n");
  });
  return (
    `## 【思想と書き方の対応（承認済みの橋のみ）】\n` +
    `思想はそのまま書かず、以下の対応が示す「書き方」としてだけ作品へ現す。\n\n` +
    parts.join("\n\n")
  );
}

/** 承認済みの Bridge Rule を読み出して組み立てる。創作依頼のときだけ呼ぶ。 */
export async function fetchBridges(
  db: SupabaseClient,
  personId: string
): Promise<BridgeForPrompt[]> {
  const { data: rules } = await db
    .from("judgment_rules")
    .select("rule_id, title, lifecycle")
    .eq("person_id", personId)
    .eq("rule_scope", "bridge_rule")
    .eq("lifecycle", "active");
  if (!rules?.length) return [];

  const { data: versions } = await db
    .from("judgment_rule_versions")
    .select("rule_id, version, status, content")
    .in("rule_id", rules.map((r) => r.rule_id))
    .eq("status", "approved");
  if (!versions?.length) return [];

  const thoughtIds = [
    ...new Set(
      versions
        .map((v) => (v.content as Record<string, unknown>).source_thought_id)
        .filter((x): x is string => typeof x === "string")
    ),
  ];
  const creativeIds = [
    ...new Set(
      versions
        .map((v) => (v.content as Record<string, unknown>).target_creative_card_id)
        .filter((x): x is string => typeof x === "string")
    ),
  ];

  const [{ data: thoughtCards }, { data: creativeCards }] = await Promise.all([
    thoughtIds.length
      ? db
          .from("thought_cards")
          .select("thought_id, title, core_claim, status")
          .eq("person_id", personId)
          .in("thought_id", thoughtIds)
      : Promise.resolve({ data: [] as ThoughtRow[] }),
    creativeIds.length
      ? db
          .from("creative_cards")
          .select("card_id, card_type, title, summary, status")
          .in("card_id", creativeIds)
      : Promise.resolve({ data: [] as CreativeRow[] }),
  ]);

  return composeBridges({
    rules: rules as RuleRow[],
    versions: versions as VersionRow[],
    thoughtCards: (thoughtCards ?? []) as ThoughtRow[],
    creativeCards: (creativeCards ?? []) as CreativeRow[],
  });
}
