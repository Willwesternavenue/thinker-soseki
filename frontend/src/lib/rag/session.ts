import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { callText, MODEL_LIGHT } from "./llm";
import type { Persona } from "./types";

const RECENT_TURNS = 6; // 直近参照するメッセージ数(仕様8.2)
const SUMMARY_THRESHOLD = 10; // これ以上溜まったら要約を更新

export type SessionContext = {
  summary: string | null;
  recentMessages: Array<{ role: string; content: string }>;
  messageCount: number;
};

/** セッション文脈取得(仕様8.2): 直近数ターン + セッション要約。 */
export async function getSessionContext(
  db: SupabaseClient,
  sessionId: string
): Promise<SessionContext> {
  const { data: session } = await db
    .from("chat_sessions")
    .select("summary")
    .eq("session_id", sessionId)
    .single();

  const { data: messages, count } = await db
    .from("chat_messages")
    .select("role, content", { count: "exact" })
    .eq("session_id", sessionId)
    .is("deleted_at", null)
    .order("created_at", { ascending: false })
    .limit(RECENT_TURNS);

  return {
    summary: session?.summary ?? null,
    recentMessages: (messages ?? [])
      .reverse()
      .filter((m) => m.content)
      .map((m) => ({ role: m.role, content: m.content as string })),
    messageCount: count ?? 0,
  };
}

/**
 * この人物(メルロ=ポンティ)を指す正規名。merleau_ponty固有。
 * 将来取り込む原典(訳書)で最も多い表記に合わせると検索が安定する。訳書が
 * 「メルロ＝ポンティ」(全角=)中心ならこの値を差し替える。
 */
const SUBJECT_CANONICAL = "メルロ=ポンティ";

/**
 * 検索クエリ内の、この人物を指す各種表記・呼称を正規名「メルロ=ポンティ」に寄せる
 * (merleau_ponty固有)。
 *
 * フランス人名の複合姓のため、日本語では区切り文字(= ＝ ・ -)や長音の有無で表記が
 * 大きく割れる。表記がばらつくと、質問と原典・カードの間で同一人物の一致が取れず検索が
 * 弱くなる。表記ゆれを1つに統一してから埋め込む。※検索クエリ(分類・ルーティング・原典
 * 検索)にのみ適用。回答生成は原文を使う。
 *
 * 対応: メルロ=ポンティ / メルロ＝ポンティ / メルロ・ポンティ / メルロポンティ /
 *       メルロー(＝)ポンティ(ー) / Merleau-Ponty / Merleau Ponty(先生・氏付きも) /
 *       このアバターへの二人称 ご自身 / あなた(様) → メルロ=ポンティ
 */
export function normalizeSubjectReferences(query: string): string {
  return query
    // ラテン表記: Merleau-Ponty / Merleau Ponty(大小文字・区切り問わず)
    .replace(/Merleau[\s\-‐−―]*Ponty/gi, SUBJECT_CANONICAL)
    // カナ表記の揺れ: メルロ(ー) + 区切り(= ＝ ・ - 無し) + ポンティ(ー)、敬称付きも
    .replace(/メルロ[ー]?[=＝・‐−―\-]?ポンティ[ー]?(さん|先生|氏)?/g, SUBJECT_CANONICAL)
    // このアバターに向けられた「ご自身」= 本人
    .replace(/ご自身/g, SUBJECT_CANONICAL)
    // このアバターに向けられた二人称「あなた(様)」= 本人(人物アンカー無しの
    // 「あなたの思想を教えて」が原典・プロフィールに届かないのを防ぐ)
    .replace(/あなた(様|さま)?/g, SUBJECT_CANONICAL)
    // 置換で連続した正規名を1つに畳む
    .replace(/メルロ=ポンティ(?:\s*メルロ=ポンティ)+/g, SUBJECT_CANONICAL);
}

/**
 * 直前の話題を指す指示語。「これから」「この世」「あの世」など指示的でない慣用句は
 * 負の先読みで除外する(「この世に絶対負はあるか」を文脈依存と誤判定しないため)。
 */
const ANAPHORA_RE =
  /それ|そこ|そちら|そう(いう|いった|した)|その(?!他|もの|ため|後)|これ(?!から)|こちら|この(?!世|ため|後|方|辺)|あれ|あそこ|あちら|あの(?!世|方|頃|時)/;

/**
 * 指示語を含まないが直前の話題に依存する省略形(「もっと詳しく」「なぜ?」等)。
 * 「なぜ人は生きるのか」のような自立した質問を拾わないよう、単独形のみ対象にする。
 */
const ELLIPTICAL_RE =
  /^(もっと|もう少し|詳しく|続きは?|他には?|具体的に|例えば)|^(なぜ|どうして|本当に?)[?？]?$/;

/** 検索用クエリ生成(仕様7.1 step2)。直近文脈を軽く連結し、本人呼称を正規化する(LLMは使わない)。 */
export function buildRetrievalQuery(
  currentMessage: string,
  sessionSummary: string | null,
  recentMessages: Array<{ role: string; content: string }>
): string {
  // 指示語・省略形の質問(「それはどういう意味?」等)だけ直前の話題を補う。
  //
  // 以前は「20文字未満なら連結」という文字数判定だったが、日本語は主語が明示された
  // 完全な質問でも20文字を下回るため誤爆していた。実測では
  // 「ゲーテの素晴らしいところはどこですか？」(19文字)に直前のAI質問が連結され、
  // 分類がmixedに、検索がAI一色になってゲーテの原典が1件も引けなかった
  // (回答はモデルの一般知識で生成され、本人の思想に基づかないものになった)。
  // 逆に「それについて詳しく教えてください」(>20文字)は文脈が補われず取りこぼしていた。
  const needsContext =
    ANAPHORA_RE.test(currentMessage) || ELLIPTICAL_RE.test(currentMessage);

  let base = currentMessage;
  if (needsContext && recentMessages.length) {
    const lastUser = [...recentMessages]
      .reverse()
      .find((m) => m.role === "user");
    base = [lastUser?.content, currentMessage].filter(Boolean).join(" ");
  }
  return normalizeSubjectReferences(base);
}

export async function saveChatMessages(
  db: SupabaseClient,
  sessionId: string,
  userMessage: string,
  assistantMessage: string
): Promise<{ userMessageId: string; assistantMessageId: string }> {
  const { data: userRow, error: userError } = await db
    .from("chat_messages")
    .insert({ session_id: sessionId, role: "user", content: userMessage })
    .select("message_id")
    .single();
  if (userError) throw new Error(`メッセージ保存失敗: ${userError.message}`);

  const { data: assistantRow, error: assistantError } = await db
    .from("chat_messages")
    .insert({ session_id: sessionId, role: "assistant", content: assistantMessage })
    .select("message_id")
    .single();
  if (assistantError) throw new Error(`メッセージ保存失敗: ${assistantError.message}`);

  await db
    .from("chat_sessions")
    .update({ updated_at: new Date().toISOString() })
    .eq("session_id", sessionId);

  return {
    userMessageId: userRow.message_id,
    assistantMessageId: assistantRow.message_id,
  };
}

/** 長くなったらセッション要約を更新する(仕様8.2)。失敗しても回答は返す。 */
export async function maybeUpdateSessionSummary(
  db: SupabaseClient,
  sessionId: string,
  persona: Persona,
  context: SessionContext
): Promise<void> {
  if (context.messageCount < SUMMARY_THRESHOLD) return;
  try {
    const history = context.recentMessages
      .map((m) => `${m.role === "user" ? "あなた" : persona.display_name}: ${m.content}`)
      .join("\n");
    const summary = await callText({
      model: MODEL_LIGHT,
      system: "会話セッションの要約者。相談の主題と経緯を簡潔にまとめる。",
      messages: [
        {
          role: "user",
          content: `以下の会話を150字以内で要約せよ。${context.summary ? `\n既存の要約: ${context.summary}` : ""}\n\n${history}`,
        },
      ],
      maxTokens: 300,
    });
    await db
      .from("chat_sessions")
      .update({ summary: summary.trim() })
      .eq("session_id", sessionId);
  } catch {
    // 要約失敗は無視
  }
}
