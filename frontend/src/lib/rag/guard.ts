import "server-only";
import { callJson, MODEL_LIGHT } from "./llm";
import type { GuardResult, Persona } from "./types";

// 第一段: 文字列完全一致(仕様13.1)。共通の内部語はシステム定数。
// 「資料」「参考」等の日常語は誤検出防止のため第二段のjudge判定に回す(v1.1)
const SYSTEM_BANNED_EXACT = [
  "RAG",
  "thought_id",
  "thought_questions",
  "thought_cards",
  "target_card_id",
  "思想カード",
  "スコアカード",
  "原典チャンク",
  "検索結果",
];

// ASCII語は単語境界で判定(例: "RAG" が英単語の一部に誤ヒットしないように)
function containsTerm(text: string, term: string): boolean {
  if (/^[\x21-\x7e]+$/.test(term)) {
    return new RegExp(`(?<![A-Za-z0-9_])${escapeRegExp(term)}(?![A-Za-z0-9_])`).test(
      text
    );
  }
  return text.includes(term);
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 第一段: 完全一致検査(仕様13.1)+ 文字化け検出。検出=即NG(再生成)。 */
export function runOutputGuardExact(answer: string, persona: Persona): string[] {
  const banned = [...SYSTEM_BANNED_EXACT, ...(persona.banned_terms_exact ?? [])];
  const hits = banned.filter((term) => containsTerm(answer, term));
  // 文字化け: Unicode置換文字(U+FFFD)を含む回答は生成時に壊れている → 再生成させる
  if (answer.includes("�")) hits.push("文字化け(U+FFFD)");
  return hits;
}

/** 第二段: 軽量LLM judge(Haiku、仕様13.2)。文脈依存語と一人称を判定。 */
export async function runOutputGuardJudge(
  answer: string,
  persona: Persona
): Promise<{ pass: boolean; issues: string[] }> {
  const contextualTerms = persona.banned_terms_contextual ?? [];
  try {
    const result = await callJson<{ pass: boolean; issues: string[] }>({
      model: MODEL_LIGHT,
      system: "あなたはAIアバター回答の品質検査官である。明確な違反があるときだけ不合格にする。",
      prompt: `以下は一人称「${persona.first_person}」で話す人物アバターの回答である。
明確な違反が実際にあるときだけ pass=false にせよ。迷ったら pass=true。

## 回答
${answer}

## 不合格(pass=false)にすべき「明確な違反」は次の2つだけ
1. 内部メタ発言: この回答が検索・参照資料・データベース・AIの仕組みなど、回答生成の
   内部処理に言及している。目安の語: ${contextualTerms.join("、")}
2. 自分の一人称の乱れ: アバター自身が自分を指すのに「${persona.first_person}」以外
   (私・僕など)を使っている。

## 違反ではない(pass=trueにすること)
- 他人の発言・セリフの引用の中で私・僕・俺などが使われている(例: 引用した相手が
  「僕は…」と言っている)。話者はその他人であり、アバター自身ではない。
- 引用符や鉤括弧の中の一人称。
- 「資料」「参考」等が内部の仕組みではなく日常的な意味で自然に使われている。
- 文体の好み・一貫性の細かい懸念(明確な違反ではない)。

上記「違反ではない」に該当する場合や、明確な違反が無い場合は必ず pass=true。

出力形式(JSONのみ): {"pass": true/false, "issues": ["明確な違反の具体内容(無ければ空)"]}`,
      maxTokens: 400,
    });
    return { pass: Boolean(result.pass), issues: result.issues ?? [] };
  } catch {
    // judge自体の失敗は回答を止めない(完全一致は通過済み)
    return { pass: true, issues: ["judge実行失敗(スキップ)"] };
  }
}

/** 再生成用の修正指示(仕様13.3)。 */
export function buildRegenerateInstruction(
  exactHits: string[],
  judgeIssues: string[]
): string {
  return `前回回答には使用禁止語または不適切な一人称が含まれていた。
内部参照の仕組みを説明せず、自然な一人称の回答に書き直すこと。
検出語・問題点: ${[...exactHits, ...judgeIssues].join(" / ")}`;
}

/** 再生成も失敗した場合の安全側回答(仕様13.3)。決定的テキストでGuardリスクなし。 */
export function buildSafeAnswer(persona: Persona): string {
  return `すまん、今回はうまく言葉にならなかった。${persona.first_person}なりに真剣に考えたいから、もう一度、別の言い方で聞いてくれないか。`;
}

export function emptyGuardResult(): GuardResult {
  return {
    passed: true,
    exact_match_hits: [],
    judge_result: "skipped",
    regenerated: false,
    safe_answer_used: false,
  };
}
