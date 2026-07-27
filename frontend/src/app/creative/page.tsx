import { redirect } from "next/navigation";
import { getUserWithProfile } from "@/lib/supabase/server";
import { createClient } from "@/lib/supabase/server";
import { CreativeClient } from "./creative-client";
import type { ProfileOption } from "./actions";

export const dynamic = "force-dynamic";

/**
 * 創作モードのユーザー画面(T5)。
 *
 * 思想対話(`/chat`)とは**別の画面**にする。同じ画面に混ぜると、生成された小説を
 * 本人の思想として受け取られる恐れがあるため(仕様§5.1・§9.1)。
 */
export default async function CreativePage() {
  const auth = await getUserWithProfile();
  if (!auth) redirect("/login");

  const supabase = createClient();
  const { data } = await supabase
    .from("creative_profiles")
    .select("profile_id, name, description, disclosure_text")
    .eq("status", "active")
    .order("name");

  return (
    <CreativeClient
      profiles={(data ?? []) as ProfileOption[]}
      isAdmin={auth.profile.role === "admin"}
    />
  );
}
