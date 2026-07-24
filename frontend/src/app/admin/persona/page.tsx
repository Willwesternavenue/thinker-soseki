import { createClient } from "@/lib/supabase/server";
import { PersonaForm } from "./persona-form";

export const dynamic = "force-dynamic";

export default async function PersonaPage() {
  const supabase = await createClient();

  const { data: persona } = await supabase
    .from("personas")
    .select("*")
    .eq("person_id", "merleau_ponty")
    .single();

  const { data: cards } = await supabase
    .from("thought_cards")
    .select("card_id, title")
    .eq("status", "approved")
    .order("card_id");

  if (!persona) {
    return <p className="text-stone-500">ペルソナが未設定です(seedを確認してください)</p>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">ペルソナ設定(プロンプト)</h1>
        <p className="mt-1 text-sm text-stone-500">
          Xメルロ=ポンティの人格・語り口・禁止語・安全方針をここで管理します。
          人物固有の情報はコードではなくこの設定に集約されています。
        </p>
      </div>
      <PersonaForm persona={persona} approvedCards={cards ?? []} />
    </div>
  );
}
