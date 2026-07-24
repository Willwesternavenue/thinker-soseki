import "server-only";
import Anthropic from "@anthropic-ai/sdk";

// モデル割当(仕様13.5)
export const MODEL_ANSWER = "claude-sonnet-5"; // 回答生成(語り口不足時はOpus検討)
export const MODEL_LIGHT = "claude-haiku-4-5-20251001"; // 分類・Guard judge

let client: Anthropic | null = null;

function getClient() {
  if (!client) client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
  return client;
}

export async function callText(options: {
  model: string;
  system: string;
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  maxTokens?: number;
  /** trueで拡張思考を無効化。機械的な変換タスク(整形等)で
   * 思考トークンがmax_tokensを食い潰して出力が切断されるのを防ぐ。 */
  disableThinking?: boolean;
}): Promise<string> {
  const message = await getClient().messages.create({
    model: options.model,
    max_tokens: options.maxTokens ?? 1500,
    system: options.system,
    messages: options.messages,
    ...(options.disableThinking ? { thinking: { type: "disabled" as const } } : {}),
  });
  return message.content
    .filter((block) => block.type === "text")
    .map((block) => (block as { text: string }).text)
    .join("");
}

export async function callJson<T>(options: {
  model: string;
  system: string;
  prompt: string;
  maxTokens?: number;
  disableThinking?: boolean;
}): Promise<T> {
  const text = await callText({
    model: options.model,
    system: options.system + "\nJSONのみを出力する。",
    messages: [{ role: "user", content: options.prompt }],
    maxTokens: options.maxTokens ?? 1000,
    disableThinking: options.disableThinking,
  });
  return parseJson<T>(text);
}

function parseJson<T>(text: string): T {
  const trimmed = text.trim();
  const fence = trimmed.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
  if (fence) return JSON.parse(fence[1]);
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start >= 0 && end > start) return JSON.parse(trimmed.slice(start, end + 1));
  return JSON.parse(trimmed);
}
