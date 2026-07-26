import { redirect } from "next/navigation";
import { getUserWithProfile } from "@/lib/supabase/server";
import { ChatClient } from "./chat-client";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const auth = await getUserWithProfile();
  if (!auth) redirect("/login");

  return (
    <ChatClient
      isAdmin={auth.profile.role === "admin"}
      displayName={auth.profile.display_name ?? auth.user.email ?? ""}
    />
  );
}
