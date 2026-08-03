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
import { fetchBridges } from "./bridges";
import { buildAnswerContext } from "./context";
import {
  buildRetrievalRoute,
  corpusRouteKind,
  decideAbstention,
  detectCharacter,
  directSourceIds,
  rankByRoute,
  retrievalFiltersFor,
} from "./corpus-routing";
import {
  diversifyEvidence,
  fetchLinkedEvidence,
  filterQuotableChunks,
  mergeEvidence,
  retrieveRoutedEvidence,
  retrieveUnscopedEvidence,
} from "./evidence";
import {
  buildRegenerateInstruction,
  buildSafeAnswer,
  judgeResultFor,
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
  // コーパス層のルーティング(受入#15)。質問種別に応じて引く範囲を変える。
  // ⚠️ 既存の無絞り込み検索は残す。コーパス層より前に投入した原典は corpus_role が
  // null で、絞り込みだけにすると丸ごと落ちるため。
  const routeKind = corpusRouteKind(classification.queryKind, retrievalQuery);
  const characterId = detectCharacter(retrievalQuery);
  const { corpusRoles } = retrievalFiltersFor(routeKind);

  const [unscoped, routed, linked, bridges] = await Promise.all([
    retrieveUnscopedEvidence(db, PERSON_ID, retrievalQuery),
    retrieveRoutedEvidence(db, PERSON_ID, retrievalQuery, corpusRoles),
    thoughtIds.length > 0
      ? fetchLinkedEvidence(db, PERSON_ID, thoughtIds)
      : Promise.resolve([] as EvidenceChunk[]),
    // 創作依頼における思想の唯一の経路(仕様§6)。承認済みの橋だけが
    // 「書き方の対応」として入る。橋が無ければ従来どおり何も入らない
    routeKind === "creative"
      ? fetchBridges(db, PERSON_ID)
      : Promise.resolve([]),
  ]);
  // linked(承認リンクの代表原典)を最優先し、関連度検索を足す。
  // 重複はchunk_idで排除、スコア順に多様性制御(source/role偏り防止、3〜8件)。
  // そのうえで、思想質問では小説由来を作者の直接発言より後ろへ下げる
  // (ベクトル検索は文体の似た小説をよく引く。順序をそのまま使うと、
  //  文体の一致が思想の一致として提示されてしまう)。
  const evidence = rankByRoute(
    diversifyEvidence(mergeEvidence(linked, routed, unscoped)),
    routeKind
  );

  // 直接の原典が無いまま断定させない(受入#14 / 指示書§13)
  const abstentionReason = decideAbstention({ kind: routeKind, evidence });

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
    abstentionReason,
    bridges,
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

  const retrievalRoute = {
    ...buildRetrievalRoute({
      kind: routeKind,
      characterId,
      evidence: evidence.map((c) => ({
        chunk_id: c.chunk_id,
        corpus_role: c.corpus_role ?? null,
        speaker_role: c.speaker_role ?? null,
      })),
    }),
    // 発火した橋を trace に残す(思想が創作へ入った経路の監査。受入#14)
    bridge_rules: bridges.map((b) => ({ rule_id: b.rule_id, title: b.title })),
  };
  const trace: AnswerTrace = {
    query_kind: classification.queryKind,
    routing_method: route.routingMethod,
    fallback_card_used: fallbackCardUsed,
    selected_thought_ids: thoughtIds,
    retrieved_card_ids: merged ? merged.all.map((c) => c.card_id) : [],
    retrieved_chunk_ids: evidence.map((c) => c.chunk_id),
    top_hits: evidence.map(toTopHit),
    guard_result: guardResult.guard,
    retrieval_route: retrievalRoute,
    direct_source_ids: directSourceIds(evidence, routeKind),
    abstention_reason: abstentionReason,
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
        // コーパス層(C-T7)の痕跡。原典とAI外挿を分けて残す(受入#14・#15)
        retrieval_route: trace.retrieval_route,
        direct_source_ids: trace.direct_source_ids,
        abstention_reason: trace.abstention_reason,
        activated_rules: l3InjectedRules.map((r) => ({
          rule_id: r.rule_id,
          title: r.title,
        })),
        // 棄却した規則も残す(発火だけでなく棄却理由も記録する。指示書§18)
        rejected_rules: l3Outcome.trace.rejected ?? [],
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
  // 完全一致でヒットした回は judge を**実行しない**(再生成が確定しているため)。
  // その場合は null にする — 走っていない判定を "fail" として記録しないため
  const firstJudge =
    firstExact.length === 0 ? await runOutputGuardJudge(answer, persona) : null;

  if (firstExact.length === 0 && firstJudge?.pass) {
    return {
      finalAnswer: answer,
      guard: {
        passed: true,
        exact_match_hits: [],
        // judge が例外で落ちた回は "pass" ではなく "skipped"(検査を静かに死なせない)
        judge_result: judgeResultFor(firstJudge),
        judge_issues: firstJudge.issues,
        regenerated: false,
        safe_answer_used: false,
      },
    };
  }

  // 再生成(最大1回、仕様13.3)
  const instruction = buildRegenerateInstruction(firstExact, firstJudge?.issues ?? []);
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
        // 完全一致で落としたので judge は実行していない。走っていない判定を
        // "fail" と書かない(不合格の根拠は exact_match_hits 側にある)
        judge_result: judgeResultFor(firstJudge),
        judge_issues: firstJudge?.issues ?? [],
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
      judge_result: judgeResultFor(secondJudge),
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
