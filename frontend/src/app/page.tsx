import { redirect } from "next/navigation";
import { getUserWithProfile } from "@/lib/supabase/server";

export default async function Home() {
  const auth = await getUserWithProfile();
  if (!auth) redirect("/login");
  // adminは上部ナビ付きの埋め込みチャット、testerはフルスクリーンチャット
  redirect(auth.profile.role === "admin" ? "/admin/chat" : "/chat");
}
