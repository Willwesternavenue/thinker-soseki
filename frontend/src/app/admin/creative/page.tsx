import { createClient } from "@/lib/supabase/server";
import { getUserWithProfile } from "@/lib/supabase/server";
import { CreativeClient } from "@/app/creative/creative-client";
import type { ProfileOption } from "@/app/creative/actions";

export const dynamic = "force-dynamic";

// 管理画面レイアウト(上部ナビ)の中に創作画面を表示する。/admin/chat と同じ形。
// 会員向けは /creative。あちらを管理レイアウトで包むことはできない
// (レイアウトが非管理者を /chat へリダイレクトするため)。ナビ無しの /creative へ
// 管理者を送ると、そこから他の画面へ戻れなくなる。
export default async function AdminCreativePage() {
  // 管理レイアウトでadmin判定済み。isAdmin を渡すためだけに参照する
  const auth = await getUserWithProfile();

  const supabase = createClient();
  const { data } = await supabase
    .from("creative_profiles")
    .select("profile_id, name, description, disclosure_text")
    .eq("status", "active")
    .order("name");

  return (
    <CreativeClient
      profiles={(data ?? []) as ProfileOption[]}
      isAdmin={auth?.profile.role === "admin"}
    />
  );
}
