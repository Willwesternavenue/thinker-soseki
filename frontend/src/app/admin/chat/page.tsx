import { getUserWithProfile } from "@/lib/supabase/server";
import { ChatClient } from "@/app/chat/chat-client";

export const dynamic = "force-dynamic";

// 管理画面レイアウト(上部ナビ)の中にチャットを表示する。
// 会員向けのフルスクリーン版は /chat(仕様は今後変更予定)。
export default async function AdminChatPage() {
  const auth = await getUserWithProfile();
  // 管理レイアウトでadmin判定済みだが、displayName取得のため参照
  return (
    <ChatClient
      isAdmin
      embedded
      displayName={auth?.profile.display_name ?? auth?.user.email ?? ""}
    />
  );
}
