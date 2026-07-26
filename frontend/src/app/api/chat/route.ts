import { NextResponse } from "next/server";
import { getUserWithProfile } from "@/lib/auth";
import { createAdminClient } from "@/lib/supabase/admin";
import { answerQuestion } from "@/lib/rag/pipeline";

export const maxDuration = 120; // Guard含む非ストリーミング処理のため長め

/**
 * Chat API(仕様11.1)。非ストリーミング。
 * 入力: { sessionId, message } / 出力: { answer, trace }
 */
export async function POST(request: Request) {
  const auth = await getUserWithProfile();
  if (!auth) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: { sessionId?: string; message?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const { sessionId, message } = body;
  if (!sessionId || !message?.trim()) {
    return NextResponse.json(
      { error: "sessionId と message は必須です" },
      { status: 400 }
    );
  }
  if (message.length > 4000) {
    return NextResponse.json({ error: "メッセージが長すぎます" }, { status: 400 });
  }

  // セッション所有権チェック(RLS廃止に伴いuser_idで明示確認)
  const adminClient = createAdminClient();
  const { data: session } = await adminClient
    .from("chat_sessions")
    .select("session_id, deleted_at")
    .eq("session_id", sessionId)
    .eq("user_id", auth.user.id)
    .maybeSingle();
  if (!session || session.deleted_at) {
    return NextResponse.json({ error: "セッションが見つかりません" }, { status: 404 });
  }

  try {
    const result = await answerQuestion(adminClient, sessionId, message.trim());

    // 参照情報はログインユーザー全員に返却。testerにも回答根拠を確認して
    // もらうため、admin限定(旧仕様3.9 / 11.1)を解除した。
    return NextResponse.json({
      answer: result.answer,
      trace: result.trace,
    });
  } catch (error) {
    console.error("chat pipeline error:", error);
    return NextResponse.json(
      { error: "回答の生成に失敗しました。時間をおいて再度お試しください。" },
      { status: 500 }
    );
  }
}
