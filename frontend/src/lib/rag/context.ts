import "server-only";
import type { MergedCards } from "./cards";
import type { EvidenceChunk, Persona } from "./types";

export type AnswerContext = {
  system: string;
  userContent: string;
};

/** assist時に注入する判断規則(L3)。l3shadow.ts の L3Rule と構造互換 */
export type JudgmentRuleForPrompt = {
  title: string;
  rule_type: string;
  action: unknown;
  derived_claims: string[];
  required_distinctions: string[];
  forbidden_inferences: string[];
};

/** actionをrule_typeに応じて1行の日本語に整形する */
function renderAction(action: unknown): string | null {
  if (!action || typeof action !== "object") return null;
  const a = action as Record<string, unknown>;
  if (a.from && a.to) return `「${a.from}」を「${a.to}」へ捉え直す`;
  if (a.between && Array.isArray(a.between))
    return `「${(a.between as string[]).join("」と「")}」を区別する${a.criterion ? `(基準: ${a.criterion})` : ""}`;
  if (a.higher && a.lower)
    return `「${a.higher}」を「${a.lower}」より優先する${a.condition ? `(条件: ${a.condition})` : ""}`;
  if (a.left && a.right && a.stance) return `${a.stance}`;
  if (a.description) return String(a.description);
  if (a.response_style) return String(a.response_style);
  return null;
}

/**
 * Context Builder(仕様7.9)。三区分を構造で分離する:
 * 【回答方針】= 思想カード(本人の発言ではない)
 * 【原典本文(参考・引用不可)】
 * 【引用可能箇所】= 7.8の条件を満たすチャンクのみ(呼び出し側でフィルタ済み)
 */
export function buildAnswerContext(options: {
  persona: Persona;
  question: string;
  sessionSummary: string | null;
  recentMessages: Array<{ role: string; content: string }>;
  cards: MergedCards | null;
  evidence: EvidenceChunk[];
  quotable: EvidenceChunk[];
  misunderstandingSignal: boolean;
  /** assist時のみ。発火したapproved判断規則(L3) */
  judgmentRules?: JudgmentRuleForPrompt[];
}): AnswerContext {
  const {
    persona,
    question,
    sessionSummary,
    recentMessages,
    cards,
    evidence,
    quotable,
    misunderstandingSignal,
    judgmentRules,
  } = options;

  const maxQuoteLength = persona.quote_policy?.max_quote_length ?? 100;

  const parts: string[] = [];

  parts.push(`## 今回の質問\n${question}`);

  if (sessionSummary || recentMessages.length) {
    const history = recentMessages
      .map((m) => `${m.role === "user" ? "あなた" : persona.display_name}: ${m.content}`)
      .join("\n");
    parts.push(
      `## 会話の文脈\n${sessionSummary ? `これまでの要約: ${sessionSummary}\n` : ""}${history}`
    );
  }

  if (cards) {
    const cardTexts = cards.all.map((card, i) => {
      const label = i === 0 ? "主方針" : `補助方針${i}`;
      const distinctions = (card.distinctions ?? [])
        .map((d) => `  - 「${d.not}」ではなく「${d.but}」`)
        .join("\n");
      return [
        `### ${label}: ${card.title}`,
        `中核: ${card.core_claim ?? ""}`,
        distinctions ? `区別:\n${distinctions}` : "",
        card.answer_policy?.length
          ? `方針:\n${card.answer_policy.map((p) => `  - ${p}`).join("\n")}`
          : "",
      ]
        .filter(Boolean)
        .join("\n");
    });
    parts.push(
      `## 【回答方針】(これは回答の方針であり、本人の発言ではない。引用禁止)\n` +
        cardTexts.join("\n\n") +
        (cards.prohibitions.length
          ? `\n\n### 禁止事項(全カードの和集合)\n${cards.prohibitions.map((p) => `- ${p}`).join("\n")}`
          : "")
    );
  }

  if (judgmentRules?.length) {
    const ruleTexts = judgmentRules.map((r) => {
      const action = renderAction(r.action);
      return [
        `### ${r.title}`,
        action ? `判断操作: ${action}` : "",
        r.derived_claims.length
          ? `導く:\n${r.derived_claims.map((d) => `  - ${d}`).join("\n")}`
          : "",
        r.required_distinctions.length
          ? `必ず区別:\n${r.required_distinctions.map((d) => `  - ${d}`).join("\n")}`
          : "",
        r.forbidden_inferences.length
          ? `導いてはいけない:\n${r.forbidden_inferences.map((f) => `  - ${f}`).join("\n")}`
          : "",
      ]
        .filter(Boolean)
        .join("\n");
    });
    parts.push(
      `## 【判断規則】(承認済みの判断文法。この相談で該当。回答の推論の骨格として使う。本人の発言ではない・引用禁止)\n` +
        ruleTexts.join("\n\n")
    );
  }

  if (misunderstandingSignal) {
    parts.push(
      `## 注意\n質問には誤解に基づく表現が含まれている可能性が高い。誤解を訂正する方向で答える。`
    );
  }

  if (evidence.length) {
    const evidenceTexts = evidence.map(
      (c) =>
        `### ${c.source_title ?? c.source_id} ${c.chapter_title ?? ""}\n${c.summary ?? c.text.slice(0, 400)}`
    );
    parts.push(
      `## 【原典本文(参考・引用不可)】\n` + evidenceTexts.join("\n\n")
    );
  }

  if (quotable.length) {
    const quoteTexts = quotable.map(
      (c) => `### 引用元: ${c.source_title ?? c.source_id}\n${c.text}`
    );
    parts.push(`## 【引用可能箇所】\n` + quoteTexts.join("\n\n"));
  }

  parts.push(
    `## 出力ルール
- 一人称は「${persona.first_person}」。自分を「社長」と呼ばない
- 回答本文にRAG・資料・検索結果・カード・チャンク等の内部語を出さない。内部参照の仕組みを説明しない
- 【回答方針】の文章を本人の発言として引用しない
- 引用してよいのは【引用可能箇所】のテキストのみ。${maxQuoteLength}字以内の短い引用に限る${quotable.length === 0 ? "(今回、引用可能箇所はない。引用符を使った引用はしない)" : ""}
- 人物・書名・特定の言葉に触れるときは、それが誰/何で、何が重要か(何と言ったか)を同じ文脈で必ず説明する。背景を欠いた名前や「ある言葉」を仄めかしたまま終えない。裏づけが足りなければ、固有名を出さず一般化して語る
- 確認できない事実、医学的効能、政治的賛否、家族や故人についての未確認情報は断定しない
- 語り口は抑えめ。口癖は少しだけ
- 相手のことは「あなた」と呼ぶ。「相談者」「利用者」などの内部的な呼称は絶対に使わない
- あなたにそのまま話しかけるように答える`
  );

  return {
    system: persona.system_prompt,
    userContent: parts.join("\n\n"),
  };
}
