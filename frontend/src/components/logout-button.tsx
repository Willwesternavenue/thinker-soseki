"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

/** ログアウトボタン。呼び出し側で className を渡して見た目を合わせる。 */
export function LogoutButton({ className }: { className?: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function handleLogout() {
    setLoading(true);
    await fetch("/api/auth/session", { method: "DELETE" }).catch(() => {});
    router.push("/login");
    router.refresh();
  }

  return (
    <button
      type="button"
      onClick={handleLogout}
      disabled={loading}
      className={className}
    >
      {loading ? "ログアウト中..." : "ログアウト"}
    </button>
  );
}
