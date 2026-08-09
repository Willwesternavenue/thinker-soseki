import { redirect } from "next/navigation";
import { getUserWithProfile } from "@/lib/supabase/server";
import { MemberHeader } from "@/components/member-header";

/**
 * 会員向け創作画面のレイアウト。
 *
 * ⚠️ 管理レイアウト(`admin/layout.tsx`)では包めない。あちらは非管理者を
 * `/chat` へリダイレクトするため、tester がこの画面を開けなくなる。
 * そのため会員向けの共通ヘッダーを別に持つ。
 */
export default async function CreativeLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = await getUserWithProfile();
  if (!auth) redirect("/login");

  return (
    <div className="flex min-h-screen flex-col">
      <MemberHeader isAdmin={auth.profile.role === "admin"} />
      <main className="flex-1">{children}</main>
    </div>
  );
}
