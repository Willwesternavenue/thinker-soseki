import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { L3_SHADOW_DISABLED } from "@/lib/const";
import { callJson, MODEL_LIGHT } from "./llm";

/**
 * L3 Judgment Rule の発火判定(仕様: docs/judgment_rules_spec_v0_2.md 4.3)。
 * 全active規則(draft含む)のtrigger/exceptionsを1回のHaiku呼び出しでまとめて判定する。
 *
 * モードは呼び出し側(pipeline.ts)が制御する:
 * - shadow: 判定結果をL4痕跡(answer_traces.l3_shadow)に記録するのみ。回答には未使用
 * - assist: 発火した**approved規則のみ**を回答生成コンテキストへ注入(L3_MODE=assist +
 *   queryKindゲート)。draft規則は引き続きshadow記録される(一括判定1回で両方まかなう)
 *
 * - 失敗時はerror痕跡を返すのみ(フェイルオープン)
 * - 発火ユニットテスト(worker/src/test_rule_activation.py)は規則ごとのブラインド判定、
 *   こちらは本番形状(全規則一括)。判定方式の差自体も比較データになる
 */

const PERSON_ID = "natsume_soseki";
const CACHE_TTL_MS = 5 * 60 * 1000;
const SHADOW_TIMEOUT_MS = 8000;

/** 判定・注入に必要な規則情報(最新バージョンのcontentから展開) */
export type L3Rule = {
  rule_id: string;
  rule_version_id: string;
  title: string;
  status: string; // versionのstatus。注入はapprovedのみ
  rule_type: string;
  trigger_conditions: string[];
  exceptions: string[];
  action: unknown;
  derived_claims: string[];
  required_distinctions: string[];
  forbidden_inferences: string[];
};

/** answer_traces.l3_shadow に保存する形 */
export type L3Trace = {
  execution_status: "ok" | "error" | "timeout" | "no_rules" | "disabled";
  mode: "shadow" | "assist";
  model?: string;
  rules_evaluated?: number;
  fired?: Array<{
    rule_id: string;
    rule_version_id: string;
    status: string;
    matched_trigger_conditions: string[];
    exception_applied: boolean;
    reason: string;
  }>;
  rejected?: string[];
  injected_rule_ids?: string[]; // assist時に実際に注入した規則(pipeline側で設定)
  duration_ms?: number;
  error?: string;
};

export type L3Outcome = {
  trace: L3Trace;
  /** 発火した規則のフルコンテンツ(assist注入用)。エラー時は空 */
  firedRules: L3Rule[];
};

let rulesCache: { rules: L3Rule[]; at: number } | null = null;

const SYSTEM = `あなたは思想家AIの判断規則の発火判定器である。
与えられた各規則の発火条件と例外条件だけに基づき、入力に対して各規則が発火すべきかを判定する。
相談への回答はしない。`;

function buildPrompt(rules: L3Rule[], userMessage: string): string {
  const ruleBlocks = rules
    .map((r) => {
      const triggers = r.trigger_conditions.map((t) => `  - ${t}`).join("\n");
      const exceptions = r.exceptions.length
        ? r.exceptions.map((e) => `  - ${e}`).join("\n")
        : "  - (なし)";
      return `## ${r.rule_id}: ${r.title}\n発火条件:\n${triggers}\n例外条件(該当時は発火させない):\n${exceptions}`;
    })
    .join("\n\n");
  return `# 判断規則一覧
${ruleBlocks}

# 入力(ユーザー発話)
${userMessage}

# 判定
全規則について発火条件への該当と例外条件を検討し、JSONのみで返答せよ。
firesがtrueの規則のみmatched/reasonを詳述し、falseの規則は省略してよい:
{"activations": [{"rule_id": "...", "fires": true, "matched_trigger_conditions": ["該当した発火条件の文言"], "exception_applied": false, "reason": "一文の判定理由"}]}`;
}

/**
 * L3発火判定を実行する。決して例外を投げない。
 * 呼び出し側は Promise を早期に開始し、shadowならafter()内で、assistならcontext構築前にawaitする。
 */
export async function runL3Shadow(
  db: SupabaseClient,
  userMessage: string
): Promise<L3Outcome> {
  if (L3_SHADOW_DISABLED) {
    return { trace: { execution_status: "disabled", mode: "shadow" }, firedRules: [] };
  }
  const started = Date.now();
  try {
    const rules = await loadL3Rules(db);
    if (!rules.length) {
      return { trace: { execution_status: "no_rules", mode: "shadow" }, firedRules: [] };
    }
    const judged = await withTimeout(
      callJson<{
        activations?: Array<{
          rule_id?: string;
          fires?: boolean;
          matched_trigger_conditions?: string[];
          exception_applied?: boolean;
          reason?: string;
        }>;
      }>({
        model: MODEL_LIGHT,
        system: SYSTEM,
        prompt: buildPrompt(rules, userMessage),
        maxTokens: 2000,
        disableThinking: true,
      }),
      SHADOW_TIMEOUT_MS
    );
    if (judged === null) {
      return {
        trace: { execution_status: "timeout", mode: "shadow", duration_ms: Date.now() - started },
        firedRules: [],
      };
    }

    const byId = new Map(rules.map((r) => [r.rule_id, r]));
    const firedRaw = (judged.activations ?? []).filter(
      (a) => a.fires && a.rule_id && byId.has(a.rule_id)
    );
    const firedIds = new Set(firedRaw.map((a) => a.rule_id as string));
    const firedRules = [...firedIds].map((id) => byId.get(id)!);
    return {
      trace: {
        execution_status: "ok",
        mode: "shadow",
        model: MODEL_LIGHT,
        rules_evaluated: rules.length,
        fired: firedRaw.map((a) => {
          const rule = byId.get(a.rule_id as string)!;
          return {
            rule_id: rule.rule_id,
            rule_version_id: rule.rule_version_id,
            status: rule.status,
            matched_trigger_conditions: a.matched_trigger_conditions ?? [],
            exception_applied: a.exception_applied ?? false,
            reason: a.reason ?? "",
          };
        }),
        rejected: rules.map((r) => r.rule_id).filter((id) => !firedIds.has(id)),
        duration_ms: Date.now() - started,
      },
      firedRules,
    };
  } catch (err) {
    return {
      trace: {
        execution_status: "error",
        mode: "shadow",
        duration_ms: Date.now() - started,
        error: err instanceof Error ? err.message.slice(0, 200) : String(err).slice(0, 200),
      },
      firedRules: [],
    };
  }
}

/** 全active規則の最新バージョン(draft含む)を取得。5分キャッシュ。 */
async function loadL3Rules(db: SupabaseClient): Promise<L3Rule[]> {
  if (rulesCache && Date.now() - rulesCache.at < CACHE_TTL_MS) {
    return rulesCache.rules;
  }
  const { data: identities } = await db
    .from("judgment_rules")
    .select("rule_id, title, rule_type")
    .eq("person_id", PERSON_ID)
    .eq("lifecycle", "active");
  if (!identities?.length) {
    rulesCache = { rules: [], at: Date.now() };
    return [];
  }
  const { data: versions } = await db
    .from("judgment_rule_versions")
    .select("rule_id, rule_version_id, version, status, content")
    .in(
      "rule_id",
      identities.map((r) => r.rule_id)
    )
    .order("version", { ascending: false });

  const latest = new Map<
    string,
    { rule_version_id: string; status: string; content: Record<string, unknown> }
  >();
  for (const v of versions ?? []) {
    if (!latest.has(v.rule_id)) {
      latest.set(v.rule_id, {
        rule_version_id: v.rule_version_id,
        status: v.status,
        content: v.content as Record<string, unknown>,
      });
    }
  }

  const asList = (v: unknown): string[] => (Array.isArray(v) ? (v as string[]) : []);
  const rules: L3Rule[] = [];
  for (const identity of identities) {
    const v = latest.get(identity.rule_id);
    if (!v) continue;
    rules.push({
      rule_id: identity.rule_id,
      rule_version_id: v.rule_version_id,
      title: identity.title,
      status: v.status,
      rule_type: identity.rule_type,
      trigger_conditions: asList(v.content.trigger_conditions),
      exceptions: asList(v.content.exceptions),
      action: v.content.action,
      derived_claims: asList(v.content.derived_claims),
      required_distinctions: asList(v.content.required_distinctions),
      forbidden_inferences: asList(v.content.forbidden_inferences),
    });
  }
  rulesCache = { rules, at: Date.now() };
  return rules;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  return Promise.race([
    promise,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), ms)),
  ]);
}
