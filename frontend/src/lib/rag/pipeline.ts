import "server-only";
import { after } from "next/server";
import type { SupabaseClient } from "@supabase/supabase-js";
import { L3_MODE } from "@/lib/const";
import { classifyQuery } from "./classify";
import {
  fetchAllApprovedCards,
  fetchApprovedThoughtCards,
  mergeThoughtCards,
  resolveFallbackCard,
  type MergedCards,
} from "./cards";
import { buildAnswerContext } from "./context";
import {
  diversifyEvidence,
  fetchLinkedEvidence,
  filterQuotableChunks,
  mergeEvidence,
  retrieveUnscopedEvidence,
} from "./evidence";
import {
  buildRegenerateInstruction,
  buildSafeAnswer,
  runOutputGuardExact,
  runOutputGuardJudge,
} from "./guard";
import { runL3Shadow, type L3Rule } from "./l3shadow";
import { callText, MODEL_ANSWER } from "./llm";
import { routeThoughts } from "./router";
import {
  buildRetrievalQuery,
  getSessionContext,
  maybeUpdateSessionSummary,
  saveChatMessages,
} from "./session";
import type {
  AnswerTrace,
  EvidenceChunk,
  GuardResult,
  Persona,
  TopHit,
} from "./types";

const PERSON_ID = "natsume_soseki";

export type PipelineResult = {
  answer: string;
  trace: AnswerTrace;
};

/**
 * 回答時固定ワークフロー(仕様4.2 / 7.1)。自由行動型エージェントは使わない。
 * db は service_role クライアント(内部検索・trace保存用)。
 */
export async function answerQuestion(
  db: SupabaseClient,
  sessionId: string,
  userMessage: string
): Promise<PipelineResult> {
  // personas(仕様5.1)
  const { data: personaRow } = await db
    .from("personas")
    .select("*")
    .eq("person_id", PERSON_ID)
    .single();
  if (!personaRow) throw new Error("persona未設定");
  const persona = personaRow as Persona;

  // L3発火判定を並走開始(仕様: judgment_rules_spec_v0_2 4.3)。
  // shadow(既定): 判定結果は痕跡記録のみ。回答には使わない。
  // assist(L3_MODE=assist): 発火したapproved規則をコンテキストへ注入する(後段でawait)。
  // いずれも失敗時はerror痕跡を返すのみ(フェイルオープン)
  const l3Promise = runL3Shadow(db, userMessage);

  // 1. セッション文脈取得
  const sessionContext = await getSessionContext(db, sessionId);

  // 2. 検索用クエリ生成
  const retrievalQuery = buildRetrievalQuery(
    userMessage,
    sessionContext.summary,
    sessionContext.recentMessages
  );

  // 3. 質問分類
  const classification = await classifyQuery(retrievalQuery);

  // 4. Thought Router(多段)
  // フォールバックカードはルーティング候補から除外する(全滅時の安全網であり、
  // ルーターが積極的に選ぶとfact質問等が誤ってフォールバックに流れ、原典検索を潰す)
  const approvedCards = (await fetchAllApprovedCards(db, PERSON_ID)).filter(
    (c) => c.card_id !== persona.fallback_card_id
  );
  const route = await routeThoughts({
    db,
    personId: PERSON_ID,
    retrievalQuery,
    classification,
    approvedCards,
  });

  // 5-6. approvedカード取得(ID直接)+ 統合
  let merged: MergedCards | null = null;
  let fallbackCardUsed = route.fallbackCardUsed;
  if (route.primaryThoughtId) {
    const cards = await fetchApprovedThoughtCards(db, PERSON_ID, [
      route.primaryThoughtId,
      ...route.secondaryThoughtIds,
    ]);
    if (cards.length) {
      merged = mergeThoughtCards(cards[0], cards.slice(1));
    }
  }
  // ルーティング全滅 or カード取得失敗 → フォールバックカード(仕様7.5)
  if (!merged && classification.needsThoughtCards) {
    const fallback = await resolveFallbackCard(db, persona);
    if (fallback) {
      merged = mergeThoughtCards(fallback, []);
      fallbackCardUsed = true;
    }
  }

  // 不変条件(仕様2.3): thought / life_advice でカード0枚のまま回答生成に進めない
  if (classification.needsThoughtCards && (!merged || merged.all.length === 0)) {
    throw new Error(
      `不変条件違反: ${classification.queryKind} 質問でapproved思想カードが0枚。` +
        `フォールバックカード(personas.fallback_card_id)がapprovedで存在するか確認してください。`
    );
  }

  // 7-9. Evidence取得・統合・多様性制御
  const thoughtIds = merged ? merged.all.map((c) => c.thought_id) : [];
  // 原典全体の関連検索は常に行う。カードに紐づかない原典(未リンクの著作・経歴・
  // エピソード等)を取りこぼさないため。thought_idで絞る検索だけだと、リンク未整備の
  // 原典は related_thought_ids が空で永久にヒットしない。
  const [unscoped, linked] = await Promise.all([
    retrieveUnscopedEvidence(db, PERSON_ID, retrievalQuery),
    thoughtIds.length > 0
      ? fetchLinkedEvidence(db, PERSON_ID, thoughtIds)
      : Promise.resolve([] as EvidenceChunk[]),
  ]);
  // linked(承認リンクの代表原典)を最優先し、関連度検索(unscoped)を足す。
  // 重複はchunk_idで排除、スコア順に多様性制御(source/role偏り防止、3〜8件)。
  const evidence = diversifyEvidence(mergeEvidence(linked, unscoped, []));

  // 引用可能フィルタ(仕様7.8、コードで強制)
  const quotable = filterQuotableChunks(evidence);

  // 9.5. L3 assistモード(仕様4.3): 発火したapproved規則を回答生成へ注入する。
  // queryKindゲート: 思想・人生相談系のみ(一括判定はfact等で発火が緩い実測があるため)。
  // shadowモードではここで待たない(after()内で回収)。
  const l3Mode: "shadow" | "assist" =
    L3_MODE === "assist" &&
    ["thought", "life_advice", "mixed"].includes(classification.queryKind)
      ? "assist"
      : "shadow";
  let l3InjectedRules: L3Rule[] = [];
  if (l3Mode === "assist") {
    const outcome = await l3Promise; // 判定は冒頭から並走済み(追加待ちは通常1秒前後)
    l3InjectedRules = outcome.firedRules.filter((r) => r.status === "approved");
  }

  // 10. Context Builder(三区分+判断規則)
  const context = buildAnswerContext({
    persona,
    question: userMessage,
    sessionSummary: sessionContext.summary,
    recentMessages: sessionContext.recentMessages,
    cards: merged,
    evidence,
    quotable,
    misunderstandingSignal: route.misunderstandingSignal,
    judgmentRules: l3InjectedRules,
  });

  // 11. 回答生成(Sonnet)
  let answer = await callText({
    model: MODEL_ANSWER,
    system: context.system,
    messages: [{ role: "user", content: context.userContent }],
    maxTokens: 2500,
  });

  // 12. Output Guard(二段階、再生成は最大1回。仕様13章)
  const guardResult = await runGuardWithRegenerate(answer, persona, context);
  answer = guardResult.finalAnswer;

  // 13. メッセージ + trace保存
  const { assistantMessageId } = await saveChatMessages(
    db,
    sessionId,
    userMessage,
    answer
  );

  const trace: AnswerTrace = {
    query_kind: classification.queryKind,
    routing_method: route.routingMethod,
    fallback_card_used: fallbackCardUsed,
    selected_thought_ids: thoughtIds,
    retrieved_card_ids: merged ? merged.all.map((c) => c.card_id) : [],
    retrieved_chunk_ids: evidence.map((c) => c.chunk_id),
    top_hits: evidence.map(toTopHit),
    guard_result: guardResult.guard,
  };

  // 14. trace保存・L3痕跡回収・セッション要約はレスポンス送信後に実行(after)。
  // shadow判定(Haiku 3〜7秒)が回答生成より遅く終わる回で返却をブロックしていたため、
  // 分析用の書き込みは全てユーザー応答後に回す(失敗しても回答には影響しない)
  after(async () => {
    try {
      const l3Outcome = await l3Promise;
      const l3Shadow = {
        ...l3Outcome.trace,
        mode: l3Mode,
        injected_rule_ids: l3InjectedRules.map((r) => r.rule_id),
      };
      await db.from("answer_traces").insert({
        message_id: assistantMessageId,
        person_id: PERSON_ID,
        user_query: userMessage,
        query_kind: trace.query_kind,
        routing_method: trace.routing_method,
        fallback_card_used: trace.fallback_card_used,
        selected_thought_ids: trace.selected_thought_ids,
        retrieved_card_ids: trace.retrieved_card_ids,
        retrieved_chunk_ids: trace.retrieved_chunk_ids,
        top_hits: trace.top_hits,
        guard_result: trace.guard_result,
        l3_shadow: l3Shadow,
      });
      // セッション要約の更新(非致命)
      await maybeUpdateSessionSummary(db, sessionId, persona, sessionContext);
    } catch (err) {
      console.error("after(trace/l3/summary) error:", err);
    }
  });

  // 非ストリーミングで返却
  return { answer, trace };
}

async function runGuardWithRegenerate(
  answer: string,
  persona: Persona,
  context: { system: string; userContent: string }
): Promise<{ finalAnswer: string; guard: GuardResult }> {
  const firstExact = runOutputGuardExact(answer, persona);
  const firstJudge =
    firstExact.length === 0
      ? await runOutputGuardJudge(answer, persona)
      : { pass: false, issues: [] as string[] };

  if (firstExact.length === 0 && firstJudge.pass) {
    return {
      finalAnswer: answer,
      guard: {
        passed: true,
        exact_match_hits: [],
        judge_result: "pass",
        regenerated: false,
        safe_answer_used: false,
      },
    };
  }

  // 再生成(最大1回、仕様13.3)
  const instruction = buildRegenerateInstruction(firstExact, firstJudge.issues);
  const regenerated = await callText({
    model: MODEL_ANSWER,
    system: context.system,
    messages: [
      { role: "user", content: context.userContent },
      { role: "assistant", content: answer },
      { role: "user", content: instruction },
    ],
    maxTokens: 2500,
  });

  const secondExact = runOutputGuardExact(regenerated, persona);

  // 再生成後も確実な違反(社長/RAG等の完全一致)が残る場合のみ安全側回答。
  // 完全一致がクリアなら、judgeのソフトな懸念が残っても再生成文を返す
  // (judgeは誤検出があるため、良い回答を無内容の謝罪に潰さない)。
  if (secondExact.length > 0) {
    return {
      finalAnswer: buildSafeAnswer(persona),
      guard: {
        passed: false,
        exact_match_hits: [...new Set([...firstExact, ...secondExact])],
        judge_result: "fail",
        judge_issues: firstJudge.issues,
        regenerated: true,
        safe_answer_used: true,
      },
    };
  }

  const secondJudge = await runOutputGuardJudge(regenerated, persona);
  return {
    finalAnswer: regenerated,
    guard: {
      passed: secondJudge.pass,
      exact_match_hits: firstExact,
      judge_result: secondJudge.pass ? "pass" : "fail",
      judge_issues: secondJudge.issues,
      regenerated: true,
      safe_answer_used: false,
    },
  };
}

function toTopHit(chunk: {
  source_id: string;
  source_title?: string;
  chunk_id: string;
  score: number;
  evidence_role: string | null;
  verbatim: boolean;
  quote_allowed: boolean;
  source_page: number | null;
  printed_page: number | null;
  text: string;
}): TopHit {
  return {
    source_id: chunk.source_id,
    source_title: chunk.source_title ?? null,
    chunk_id: chunk.chunk_id,
    score: Math.round(chunk.score * 1000) / 1000,
    evidence_role: chunk.evidence_role,
    verbatim: chunk.verbatim,
    quote_allowed: chunk.quote_allowed,
    source_page: chunk.source_page,
    printed_page: chunk.printed_page,
    text_excerpt: chunk.text.slice(0, 200),
  };
}
