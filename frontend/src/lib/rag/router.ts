import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { embedText } from "@/lib/embedding";
import { callJson, MODEL_LIGHT } from "./llm";
import type { Classification, RouteResult, ThoughtCard } from "./types";

// Stage 3 の採用しきい値(仕様7.4)。MVP初期値、Phase 4で調整する
const CONFIDENT_MAX_SIM = 0.6;
const CONFIDENT_WITH_VOTES_SIM = 0.5;
const MIN_VOTES = 2;

type QuestionHit = {
  question_id: string;
  target_thought_id: string;
  question: string;
  intent: string;
  similarity: number;
};

export type AliasExpansion = {
  expandedQuery: string;
  misunderstandingSignal: boolean;
  matchedConceptIds: string[];
};

/** Stage 1: concept_aliases前処理(仕様7.4)。エイリアス→正規ラベル展開。 */
export async function expandQueryWithAliases(
  db: SupabaseClient,
  personId: string,
  query: string
): Promise<AliasExpansion> {
  const { data: aliases } = await db
    .from("concept_aliases")
    .select("concept_id, canonical_label, aliases, negative_aliases")
    .eq("person_id", personId);

  let expanded = query;
  let misunderstanding = false;
  const matched: string[] = [];
  for (const row of aliases ?? []) {
    const hitAlias = (row.aliases as string[]).find((a) => query.includes(a));
    if (hitAlias && !query.includes(row.canonical_label)) {
      expanded += ` ${row.canonical_label}`;
      matched.push(row.concept_id);
    }
    // 誤解語ヒット → 誤解訂正シグナル(該当概念も検索対象に加える)
    if ((row.negative_aliases as string[]).some((a) => query.includes(a))) {
      misunderstanding = true;
      if (!matched.includes(row.concept_id)) {
        expanded += ` ${row.canonical_label}`;
        matched.push(row.concept_id);
      }
    }
  }
  return {
    expandedQuery: expanded,
    misunderstandingSignal: misunderstanding,
    matchedConceptIds: matched,
  };
}

/** Stage 2: thought_questionsベクトル検索 + thought_id集計(仕様7.4)。 */
export function aggregateThoughtHits(hits: QuestionHit[]): Array<{
  thoughtId: string;
  votes: number;
  maxSim: number;
  avgSim: number;
}> {
  const groups = new Map<string, number[]>();
  for (const hit of hits) {
    const sims = groups.get(hit.target_thought_id) ?? [];
    sims.push(hit.similarity);
    groups.set(hit.target_thought_id, sims);
  }
  return [...groups.entries()]
    .map(([thoughtId, sims]) => ({
      thoughtId,
      votes: sims.length,
      maxSim: Math.max(...sims),
      avgSim: sims.reduce((a, b) => a + b, 0) / sims.length,
    }))
    .sort((a, b) => b.maxSim - a.maxSim);
}

/** Stage 4: LLMカード分類器(仕様7.4)。approvedカード一覧から選定。 */
export async function classifyThoughtByLLM(
  query: string,
  approvedCards: ThoughtCard[]
): Promise<string | null> {
  if (!approvedCards.length) return null;
  const cardList = approvedCards
    .map((c) => `- ${c.thought_id}: ${c.title} — ${c.core_claim ?? ""}`)
    .join("\n");
  try {
    const result = await callJson<{ thought_id: string | null }>({
      model: MODEL_LIGHT,
      system:
        "あなたは質問と思想の対応を判定する分類器である。該当がなければnullを返す。",
      prompt: `質問: ${query}

以下の思想一覧から、この質問に最も関係する思想IDを1つ選べ。
どれにも該当しない場合は null。

${cardList}

出力形式(JSONのみ): {"thought_id": "..." または null}`,
      maxTokens: 200,
    });
    const tid = result.thought_id;
    if (tid && approvedCards.some((c) => c.thought_id === tid)) return tid;
  } catch {
    // 分類器失敗 → Stage 5へ
  }
  return null;
}

/**
 * Thought Router(多段、仕様7.4)。
 * vector → llm_classifier → fallback(thought/life_advice)/ none(fact系)
 */
export async function routeThoughts(options: {
  db: SupabaseClient;
  personId: string;
  retrievalQuery: string;
  classification: Classification;
  approvedCards: ThoughtCard[];
}): Promise<RouteResult> {
  const { db, personId, retrievalQuery, classification, approvedCards } = options;

  // Stage 1: エイリアス前処理
  const expansion = await expandQueryWithAliases(db, personId, retrievalQuery);

  // Stage 2: ベクトル検索 + 集計
  const embedding = await embedText(expansion.expandedQuery);
  const { data: hits } = await db.rpc("match_thought_questions", {
    query_embedding: JSON.stringify(embedding),
    target_person_id: personId,
    match_count: 20,
  });
  const approvedThoughtIds = new Set(approvedCards.map((c) => c.thought_id));
  const aggregated = aggregateThoughtHits(
    ((hits ?? []) as QuestionHit[]).filter((h) =>
      approvedThoughtIds.has(h.target_thought_id)
    )
  );

  // Stage 3: 十分な信頼度なら採用
  const top = aggregated[0];
  if (
    top &&
    (top.maxSim >= CONFIDENT_MAX_SIM ||
      (top.votes >= MIN_VOTES && top.maxSim >= CONFIDENT_WITH_VOTES_SIM))
  ) {
    return {
      primaryThoughtId: top.thoughtId,
      secondaryThoughtIds: aggregated
        .slice(1, 3)
        .filter((a) => a.maxSim >= CONFIDENT_WITH_VOTES_SIM)
        .map((a) => a.thoughtId),
      confidence: top.maxSim,
      routingMethod: "vector",
      fallbackCardUsed: false,
      misunderstandingSignal: expansion.misunderstandingSignal,
    };
  }

  // Stage 4: LLMカード分類器
  const llmThoughtId = await classifyThoughtByLLM(
    expansion.expandedQuery,
    approvedCards
  );
  if (llmThoughtId) {
    return {
      primaryThoughtId: llmThoughtId,
      secondaryThoughtIds: [],
      confidence: top?.maxSim ?? 0,
      routingMethod: "llm_classifier",
      fallbackCardUsed: false,
      misunderstandingSignal: expansion.misunderstandingSignal,
    };
  }

  // Stage 5: フォールバック
  if (classification.needsThoughtCards) {
    return {
      primaryThoughtId: null, // フォールバックカードはpipeline側で解決
      secondaryThoughtIds: [],
      confidence: top?.maxSim ?? 0,
      routingMethod: "fallback",
      fallbackCardUsed: true,
      misunderstandingSignal: expansion.misunderstandingSignal,
    };
  }
  return {
    primaryThoughtId: null,
    secondaryThoughtIds: [],
    confidence: top?.maxSim ?? 0,
    routingMethod: "none",
    fallbackCardUsed: false,
    misunderstandingSignal: expansion.misunderstandingSignal,
  };
}
