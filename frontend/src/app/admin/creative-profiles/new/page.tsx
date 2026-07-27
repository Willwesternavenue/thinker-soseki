import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { ProfileForm } from "../profile-form";

export const dynamic = "force-dynamic";

export default async function NewCreativeProfilePage() {
  const supabase = createClient();
  const { data: personas } = await supabase.from("personas").select("person_id");

  return (
    <div className="space-y-4">
      <Link href="/admin/creative-profiles" className="text-sm text-blue-700 underline">
        ← 創作プロファイル一覧
      </Link>
      <h1 className="text-xl font-bold">創作プロファイルの新規作成</h1>
      <p className="text-sm text-stone-600">
        作成直後は下書きです。承認済みカードを用意してから運用中にしてください。
      </p>
      <ProfileForm
        mode="create"
        personIds={(personas ?? []).map((p) => p.person_id as string)}
      />
    </div>
  );
}
