import { createClient } from "@/lib/supabase/server";
import { QuestionManager } from "./question-manager";

export const dynamic = "force-dynamic";

export default async function QuestionsPage() {
  const supabase = await createClient();
  const { data: questions } = await supabase
    .from("thought_questions")
    .select("question_id, question, target_thought_id, intent, answer_direction, status")
    .order("target_thought_id")
    .order("question_id");

  const { data: cards } = await supabase
    .from("thought_cards")
    .select("thought_id")
    .neq("status", "rejected");
  const thoughtIds = [...new Set((cards ?? []).map((c) => c.thought_id))].sort();

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">質問対応情報</h1>
      <QuestionManager questions={questions ?? []} thoughtIds={thoughtIds} />
    </div>
  );
}
