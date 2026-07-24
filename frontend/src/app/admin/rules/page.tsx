import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { ReviewForm } from "./review-form";

export const dynamic = "force-dynamic";

const STATUSES = ["draft", "reviewing", "approved", "rejected", "deprecated"] as const;

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-stone-200 text-stone-700",
  reviewing: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  rejected: "bg-red-100 text-red-800",
  deprecated: "bg-stone-100 text-stone-400",
};

const SCOPE_LABELS: Record<string, string> = {
  judgment: "判断",
  dialogue: "対話",
  response_policy: "回答方針",
};

type RuleContent = Record<string, unknown>;

type LatestVersion = {
  rule_version_id: string;
  version: number;
  status: string;
  content: RuleContent;
};

type Firing = { count: number; examples: Array<{ query: string; reason: string }> };

export default async function RulesPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  const supabase = await createClient();

  const { data: rules } = await supabase
    .from("judgment_rules")
    .select("rule_id, title, rule_scope, rule_type, lifecycle, creation_method, creation_rationale, source_card_id")
    .eq("lifecycle", "active")
    .order("rule_id");

  const ruleIds = (rules ?? []).map((r) => r.rule_id);

  // 最新バージョン(version降順で最初の1件)
  const { data: versionRows } = await supabase
    .from("judgment_rule_versions")
    .select("rule_id, rule_version_id, version, status, content")
    .in("rule_id", ruleIds)
    .order("version", { ascending: false });
  const latest = new Map<string, LatestVersion>();
  for (const v of versionRows ?? []) {
    if (!latest.has(v.rule_id)) latest.set(v.rule_id, v as unknown as LatestVersion);
  }

  // レビュー履歴(最新バージョン分)
  type ReviewRow = {
    rule_version_id: string;
    reviewer_id: string | null;
    reviewer_role: string;
    verdict: string;
    review_scope: string;
    note: string | null;
    created_at: string;
  };
  const versionIds = [...latest.values()].map((v) => v.rule_version_id);
  const { data: reviewRows } = versionIds.length
    ? await supabase
        .from("judgment_rule_reviews")
        .select("rule_version_id, reviewer_id, reviewer_role, verdict, review_scope, note, created_at")
        .in("rule_version_id", versionIds)
        .order("created_at", { ascending: false })
    : { data: [] as ReviewRow[] };
  const reviewsByVersion = new Map<string, ReviewRow[]>();
  for (const r of (reviewRows ?? []) as ReviewRow[]) {
    const list = reviewsByVersion.get(r.rule_version_id) ?? [];
    list.push(r);
    reviewsByVersion.set(r.rule_version_id, list);
  }

  // 証拠・例の件数
  const { data: evidenceRows } = await supabase
    .from("judgment_rule_evidence")
    .select("rule_id")
    .in("rule_id", ruleIds);
  const { data: exampleRows } = await supabase
    .from("judgment_rule_examples")
    .select("rule_id")
    .in("rule_id", ruleIds);
  const countBy = (rows: Array<{ rule_id: string }> | null) => {
    const m = new Map<string, number>();
    for (const r of rows ?? []) m.set(r.rule_id, (m.get(r.rule_id) ?? 0) + 1);
    return m;
  };
  const evidenceCount = countBy(evidenceRows);
  const exampleCount = countBy(exampleRows);

  // shadow発火実績(answer_tracesはtester遮断のためadminクライアント)
  const admin = createAdminClient();
  const { data: traces } = await admin
    .from("answer_traces")
    .select("user_query, l3_shadow, created_at")
    .not("l3_shadow", "is", null)
    .order("created_at", { ascending: false })
    .limit(200);
  const firing = new Map<string, Firing>();
  let shadowTraceCount = 0;
  for (const t of traces ?? []) {
    const shadow = t.l3_shadow as { execution_status?: string; fired?: Array<{ rule_id: string; reason: string }> };
    if (shadow?.execution_status !== "ok") continue;
    shadowTraceCount += 1;
    for (const f of shadow.fired ?? []) {
      const entry = firing.get(f.rule_id) ?? { count: 0, examples: [] };
      entry.count += 1;
      if (entry.examples.length < 3) {
        entry.examples.push({ query: t.user_query ?? "", reason: f.reason ?? "" });
      }
      firing.set(f.rule_id, entry);
    }
  }

  const filtered = (rules ?? []).filter(
    (r) => !status || latest.get(r.rule_id)?.status === status
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">判断規則(L3)</h1>
        <div className="flex gap-2 text-sm">
          <Link
            href="/admin/rules"
            className={`rounded px-2 py-1 ${!status ? "bg-stone-300" : "hover:bg-stone-100"}`}
          >
            全部
          </Link>
          {STATUSES.map((s) => (
            <Link
              key={s}
              href={`/admin/rules?status=${s}`}
              className={`rounded px-2 py-1 ${status === s ? "bg-stone-300" : "hover:bg-stone-100"}`}
            >
              {s}
            </Link>
          ))}
        </div>
      </div>
      <p className="text-xs text-stone-500">
        「いつ・何を・どう捉え直すか」の判断文法。発火実績は直近{shadowTraceCount}件の
        shadow判定(回答には未使用)から集計。レビューはスコープ別に記録され、
        「結論は承認・理由は未承認」のような状態を表現できます。仕様は使い方タブの設計ドキュメント参照。
      </p>

      <div className="space-y-2">
        {filtered.map((rule) => {
          const v = latest.get(rule.rule_id);
          const content = (v?.content ?? {}) as RuleContent;
          const fire = firing.get(rule.rule_id);
          const reviews = v ? (reviewsByVersion.get(v.rule_version_id) ?? []) : [];
          return (
            <details key={rule.rule_id} className="rounded-lg border border-stone-200 bg-white">
              <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-4 py-3 text-sm">
                <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${STATUS_STYLES[v?.status ?? "draft"]}`}>
                  {v?.status ?? "?"}
                </span>
                <span className="rounded bg-stone-100 px-1.5 py-0.5 text-xs">
                  {SCOPE_LABELS[rule.rule_scope] ?? rule.rule_scope} / {rule.rule_type}
                </span>
                <span className="flex-1 font-semibold">{rule.title}</span>
                <span className="text-xs text-stone-500">
                  🔥{fire?.count ?? 0} / 証拠{evidenceCount.get(rule.rule_id) ?? 0} /
                  例{exampleCount.get(rule.rule_id) ?? 0} / レビュー{reviews.length}
                </span>
                <span className="font-mono text-xs text-stone-400">{rule.rule_id} v{v?.version}</span>
              </summary>
              <div className="space-y-4 border-t border-stone-200 px-4 py-4 text-sm">
                <ContentSection label="発火条件" items={content.trigger_conditions as string[]} />
                <ContentSection label="前提" items={content.premises as string[]} />
                {content.action != null && (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold text-stone-500">判断操作</h4>
                    <pre className="overflow-x-auto rounded bg-stone-50 p-2 text-xs">
                      {JSON.stringify(content.action, null, 2)}
                    </pre>
                  </div>
                )}
                <ContentSection label="導かれる主張" items={content.derived_claims as string[]} />
                <ContentSection label="必要な区別" items={content.required_distinctions as string[]} />
                <ContentSection label="例外" items={content.exceptions as string[]} />
                <ContentSection label="禁止推論" items={content.forbidden_inferences as string[]} emphasis />
                <ContentSection label="本人への確認質問" items={content.author_questions as string[]} emphasis />
                {Array.isArray(content.conflicts) && content.conflicts.length > 0 && (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold text-amber-700">未解決の規則間衝突</h4>
                    <ul className="space-y-1 text-xs text-stone-600">
                      {(content.conflicts as Array<{ rule_id: string; resolution: string; note?: string }>).map((c, i) => (
                        <li key={i}>
                          ⚡ vs <span className="font-mono">{c.rule_id}</span>({c.resolution})
                          {c.note ? ` — ${c.note}` : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {fire && fire.examples.length > 0 && (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold text-stone-500">
                      発火実績(shadow、最近{fire.examples.length}件)
                    </h4>
                    <ul className="space-y-1 text-xs text-stone-600">
                      {fire.examples.map((e, i) => (
                        <li key={i} className="rounded bg-stone-50 p-2">
                          <span className="font-medium">{e.query}</span>
                          <br />→ {e.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {rule.creation_rationale && (
                  <p className="text-xs text-stone-400">抽出根拠: {rule.creation_rationale}</p>
                )}
                {reviews.length > 0 && (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold text-stone-500">レビュー履歴</h4>
                    <ul className="space-y-1 text-xs text-stone-600">
                      {reviews.map((r, i) => (
                        <li key={i}>
                          [{r.review_scope}] <b>{r.verdict}</b>({r.reviewer_role}
                          {r.reviewer_id ? `: ${r.reviewer_id}` : ""})
                          {r.note ? ` — ${r.note}` : ""}
                          <span className="text-stone-400"> {new Date(r.created_at).toLocaleDateString("ja-JP")}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {v && <ReviewForm ruleVersionId={v.rule_version_id} currentStatus={v.status} />}
              </div>
            </details>
          );
        })}
        {!filtered.length && <p className="text-sm text-stone-500">該当する規則がありません</p>}
      </div>
    </div>
  );
}

function ContentSection({
  label,
  items,
  emphasis,
}: {
  label: string;
  items?: string[];
  emphasis?: boolean;
}) {
  if (!items?.length) return null;
  return (
    <div>
      <h4 className={`mb-1 text-xs font-semibold ${emphasis ? "text-red-700" : "text-stone-500"}`}>
        {label}
      </h4>
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-stone-600">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
