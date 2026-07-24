import { NextResponse } from "next/server";
import { getUserWithProfile } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { answerQuestion } from "@/lib/rag/pipeline";

export const maxDuration = 120;

/**
 * 評価テスト実行API(仕様14章)。adminのみ。
 * 1リクエスト=1質問。独立した評価用セッション(status='eval')で実行し、
 * evaluation_logs に記録する。
 */
export async function POST(request: Request) {
  const auth = await getUserWithProfile();
  if (!auth || auth.profile.role !== "admin") {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  const { question, questionId } = (await request.json()) as {
    question?: string;
    questionId?: string;
  };
  if (!question?.trim()) {
    return NextResponse.json({ error: "question は必須です" }, { status: 400 });
  }

  const admin = createAdminClient();

  // 文脈の混入を避けるため質問ごとに独立セッション(チャット一覧には出さない)
  const { data: session, error: sessionError } = await admin
    .from("chat_sessions")
    .insert({
      user_id: auth.user.id,
      person_id: "x_shigyo",
      title: `eval:${questionId ?? "manual"}`,
      status: "eval",
    })
    .select("session_id")
    .single();
  if (sessionError) {
    return NextResponse.json({ error: sessionError.message }, { status: 500 });
  }

  try {
    const result = await answerQuestion(admin, session.session_id, question.trim());

    const { data: log } = await admin
      .from("evaluation_logs")
      .insert({
        user_query: question.trim(),
        selected_thought_ids: result.trace.selected_thought_ids,
        answer: result.answer,
        scores: {},
        issues: [],
      })
      .select("evaluation_id")
      .single();

    return NextResponse.json({
      answer: result.answer,
      trace: result.trace,
      evaluationId: log?.evaluation_id ?? null,
    });
  } catch (error) {
    console.error("eval error:", error);
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}
