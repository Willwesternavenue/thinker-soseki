import { redirect } from "next/navigation";
import { getUserWithProfile } from "@/lib/supabase/server";
import { MemberHeader } from "@/components/member-header";

/**
 * 会員向け思想対話画面のレイアウト。/creative と同じヘッダーを出す。
 *
 * ⚠️ `/admin/chat` はこのレイアウトを通らない(管理レイアウトの下にある)。
 * ChatClient の `embedded` はそちら用で、ヘッダーの二重表示を避けている。
 */
export default async function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = await getUserWithProfile();
  if (!auth) redirect("/login");

  return (
    <div className="flex h-screen flex-col">
      <MemberHeader isAdmin={auth.profile.role === "admin"} />
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
