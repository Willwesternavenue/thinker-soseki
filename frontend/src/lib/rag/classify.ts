import "server-only";
import { callJson, MODEL_LIGHT } from "./llm";
import type { Classification, QueryKind } from "./types";

const VALID_KINDS: QueryKind[] = [
  "thought",
  "life_advice",
  "fact",
  "person_or_work",
  "health_or_medical",
  "politics_or_current_affairs",
  "creative",
  "mixed",
];

const SYSTEM = `あなたは質問分類器である。思想家AIアバターへのユーザー質問を分類する。`;

const PROMPT = (query: string) => `質問: ${query}

以下のいずれかに分類せよ:
- thought: 思想・概念そのものについての質問(定義、意味、解釈)
- life_advice: 人生相談・悩み相談・生き方の相談
- fact: 事実確認(経歴、年代、書誌情報など)
- person_or_work: 人物や作品についての質問
- health_or_medical: 健康・医学に関する質問
- politics_or_current_affairs: 政治・時事に関する質問
- creative: 創作依頼(詩、文章を書いて等)
- mixed: 複数が混ざる質問

あわせて、質問が何について尋ねているかを表す語を1つ取り出せ(subject)。
- **質問文に現れる文字列をそのまま抜き出すこと。質問文に無い語を作らない**
- 人物・作品・固有名詞があればそれを優先する
- 姓名は原典に出やすい表記へ短くしてよい(ただし質問文の一部であること)
- 主題語を1語に絞れないときは空文字にする

出力形式(JSONのみ): {"queryKind": "...", "subject": "..."}`;

/**
 * Query Classifier(仕様7.3)。
 * thought / life_advice では必ず needsThoughtCards=true(2.3の不変条件)。
 */
export async function classifyQuery(query: string): Promise<Classification> {
  let kind: QueryKind = "mixed";
  // 主題語。留保の判定に使う(原典に一度も出てこない語なら断定させない)。
  // 取れなかったときは null のままにして、従来の関連度だけの判定に倒す
  let subject: string | null = null;
  try {
    const result = await callJson<{ queryKind: string; subject?: string }>({
      model: MODEL_LIGHT,
      system: SYSTEM,
      prompt: PROMPT(query),
      maxTokens: 100,
    });
    if (VALID_KINDS.includes(result.queryKind as QueryKind)) {
      kind = result.queryKind as QueryKind;
    }
    subject = (result.subject ?? "").trim() || null;
  } catch {
    // 分類失敗時は安全側(mixed → ルーティングを試み、カード無しでも進める)
  }
  return {
    queryKind: kind,
    needsThoughtCards: kind === "thought" || kind === "life_advice",
    subject,
  };
}
