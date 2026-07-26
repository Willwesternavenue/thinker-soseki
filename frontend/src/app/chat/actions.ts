"use server";

import { createHash } from "crypto";
import { createAdminClient } from "@/lib/supabase/admin";
import { requireUser } from "@/lib/auth";

export type SessionRow = {
  session_id: string;
  title: string | null;
  updated_at: string;
};

export type MessageRow = {
  message_id: string;
  role: "user" | "assistant";
  content: string;
  // 参照情報。answer_traces から復元して付与する。
  trace?: unknown;
};

/** 自分のセッション一覧(RLS廃止に伴いuser_idで明示的に絞る)。 */
export async function listSessions(): Promise<{
  sessions?: SessionRow[];
  error?: string;
}> {
  const auth = await requireUser();
  const supabase = createAdminClient();
  const { data, error } = await supabase
    .from("chat_sessions")
    .select("session_id, title, updated_at")
    .eq("user_id", auth.user.id)
    .is("deleted_at", null)
    .neq("status", "eval")
    .order("updated_at", { ascending: false });
  if (error) return { error: error.message };
  return { sessions: (data as SessionRow[]) ?? [] };
}

/** 自分のセッションのメッセージ一覧。 */
export async function listMessages(sessionId: string): Promise<{
  messages?: MessageRow[];
  error?: string;
}> {
  const auth = await requireUser();
  const supabase = createAdminClient();
  const owned = await ownSession(auth.user.id, sessionId);
  if (!owned) return { error: "セッションが見つかりません" };
  const { data, error } = await supabase
    .from("chat_messages")
    .select("message_id, role, content")
    .eq("session_id", sessionId)
    .is("deleted_at", null)
    .order("created_at");
  if (error) return { error: error.message };
  const messages = (data as MessageRow[]) ?? [];

  // 参照情報(trace)を復元して付与する。trace は answer_traces に保存済みだが
  // 従来ここで取得しておらず、送信直後(APIレスポンス由来)しか表示されず
  // 再訪・再読込で消えていた(TracePanelが読む形に合わせて返す)。
  // セッション所有権は上の ownSession で確認済みのため、自分の会話に限られる。
  if (messages.length) {
    const assistantIds = messages
      .filter((m) => m.role === "assistant")
      .map((m) => m.message_id);
    if (assistantIds.length) {
      const { data: traces } = await supabase
        .from("answer_traces")
        .select(
          "message_id, query_kind, routing_method, fallback_card_used, selected_thought_ids, retrieved_card_ids, top_hits, guard_result"
        )
        .in("message_id", assistantIds);
      const byMessage = new Map((traces ?? []).map((t) => [t.message_id, t]));
      for (const m of messages) {
        const t = byMessage.get(m.message_id);
        if (t) m.trace = t;
      }
    }
  }
  return { messages };
}

export async function createSession(): Promise<{
  sessionId?: string;
  error?: string;
}> {
  const auth = await requireUser();
  const supabase = createAdminClient();
  const { data, error } = await supabase
    .from("chat_sessions")
    .insert({ user_id: auth.user.id, person_id: "natsume_soseki", title: "新しい相談" })
    .select("session_id")
    .single();
  if (error) return { error: error.message };
  return { sessionId: data.session_id };
}

export async function renameSession(
  sessionId: string,
  title: string
): Promise<{ error?: string }> {
  const auth = await requireUser();
  const supabase = createAdminClient();
  const { error } = await supabase
    .from("chat_sessions")
    .update({ title: title.slice(0, 60) })
    .eq("session_id", sessionId)
    .eq("user_id", auth.user.id); // 所有権(旧RLS相当)
  return error ? { error: error.message } : {};
}

/**
 * 会話削除(仕様8.4の匿名化ポリシー):
 * - chat_messages: content null化 + deleted_at
 * - chat_sessions: deleted_at(summaryも削除)
 * - answer_traces: user_query をハッシュ化。構造データ(thought_id等)は匿名保持
 */
export async function deleteSession(sessionId: string): Promise<{ error?: string }> {
  const auth = await requireUser();

  // 所有権チェック(旧RLS相当: 自分のセッションのみ削除できる)
  const owned = await ownSession(auth.user.id, sessionId);
  if (!owned) return { error: "セッションが見つかりません" };

  const admin = createAdminClient();
  const now = new Date().toISOString();

  // trace の user_query をハッシュ化(改善用の構造データは保持)
  const { data: messages } = await admin
    .from("chat_messages")
    .select("message_id")
    .eq("session_id", sessionId);
  const messageIds = (messages ?? []).map((m) => m.message_id);
  if (messageIds.length) {
    const { data: traces } = await admin
      .from("answer_traces")
      .select("trace_id, user_query")
      .in("message_id", messageIds);
    for (const trace of traces ?? []) {
      if (trace.user_query) {
        const hashed = createHash("sha256").update(trace.user_query).digest("hex");
        await admin
          .from("answer_traces")
          .update({ user_query: `sha256:${hashed}` })
          .eq("trace_id", trace.trace_id);
      }
    }
  }

  const { error: messagesError } = await admin
    .from("chat_messages")
    .update({ content: null, deleted_at: now })
    .eq("session_id", sessionId);
  if (messagesError) return { error: messagesError.message };

  const { error: sessionError } = await admin
    .from("chat_sessions")
    .update({ deleted_at: now, summary: null, status: "deleted" })
    .eq("session_id", sessionId);
  if (sessionError) return { error: sessionError.message };

  return {};
}

/** sessionIdが自分の(未削除)セッションかを確認する。 */
async function ownSession(userId: string, sessionId: string): Promise<boolean> {
  const admin = createAdminClient();
  const { data } = await admin
    .from("chat_sessions")
    .select("session_id")
    .eq("session_id", sessionId)
    .eq("user_id", userId)
    .is("deleted_at", null)
    .maybeSingle();
  return !!data;
}
