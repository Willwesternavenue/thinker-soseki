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

/**
 * judge の実行結果。
 *
 * ⚠️ `pass` と `executed` を分ける。judge 自体が落ちた場合も回答は止めない
 * (フェイルオープン。完全一致は通過済み)が、**それを「合格」として記録しない**。
 * 分けないと、APIキー失効やモデルID変更で judge が全件失敗しても trace 上は
 * 「全件 Guard 通過」に見え、検査が静かに死んでいることに気づけない。
 */
export type JudgeOutcome = {
  pass: boolean;
  issues: string[];
  /** judge を実際に実行して判定を得られたか(false は例外で判定不能) */
  executed: boolean;
};

/** 第二段: 軽量LLM judge(Haiku、仕様13.2)。文脈依存語と一人称を判定。 */
export async function runOutputGuardJudge(
  answer: string,
  persona: Persona
): Promise<JudgeOutcome> {
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
    return {
      pass: Boolean(result.pass),
      issues: result.issues ?? [],
      executed: true,
    };
  } catch {
    // judge自体の失敗は回答を止めない(完全一致は通過済み)。
    // ただし executed=false で「判定できなかった」ことを残す — 合格と区別する
    return { pass: true, issues: ["judge実行失敗(スキップ)"], executed: false };
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

/**
 * Guard の結果を GuardResult(trace記録用)へ変換する。
 *
 * judge を「実行して合格/不合格だった」のと「そもそも実行していない/できなかった」
 * のを混ぜない。混ぜると監査記録が実態と食い違う:
 *
 * - judge が例外で落ちた回を "pass" と書くと、APIキー失効やモデルID変更で judge が
 *   全件失敗しても trace 上は「全件 Guard 通過」に見える(静かに死ぬ)
 * - 完全一致でヒットして judge を**実行していない**回を "fail" と書くと、走って
 *   いない判定を失敗として記録することになる
 *
 * どちらも "skipped" で表す(型は types.ts の GuardResult)。
 */
export function judgeResultFor(judge: JudgeOutcome | null): GuardResult["judge_result"] {
  if (!judge || !judge.executed) return "skipped";
  return judge.pass ? "pass" : "fail";
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
