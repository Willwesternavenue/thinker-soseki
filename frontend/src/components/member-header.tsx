"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogoutButton } from "./logout-button";

/**
 * 会員向け画面(/chat・/creative)の共通ヘッダー。
 *
 * ⚠️ 管理画面の `AdminNav` とは別部品にする。会員向けは tester も開くので、
 * 管理用の項目を並べられない。逆に会員向けの動線(思想対話⇄創作)は管理者にも
 * 要るため、両方に同じ形のヘッダーがある状態にしている。
 *
 * これを作る前は、移動手段が画面ごとに別の場所へ散っていた
 * (/chat は左サイドバーの最下部、/creative は説明文の中の小さなリンク)。
 * その結果 /creative から戻れないという指摘が繰り返し出ていた。
 */

const LINKS = [
  { href: "/chat", label: "思想対話" },
  // 創作は思想対話と別機能。動線は用意しつつ呼称で区別する(仕様§9.1)
  { href: "/creative", label: "創作" },
] as const;

export function MemberHeader({ isAdmin }: { isAdmin: boolean }) {
  const pathname = usePathname();

  return (
    <header className="border-b border-stone-200 bg-white">
      <nav className="mx-auto flex w-full max-w-5xl items-center gap-1 px-6 py-2 text-sm">
        <span className="mr-3 font-bold">X漱石</span>
        {LINKS.map(({ href, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`rounded px-3 py-1 ${
                active ? "bg-stone-200 font-medium" : "hover:bg-stone-100"
              }`}
            >
              {label}
            </Link>
          );
        })}
        <div className="ml-auto flex items-center gap-3 text-xs text-stone-500">
          {isAdmin && (
            <Link href="/admin/sources" className="underline hover:text-stone-700">
              管理画面へ
            </Link>
          )}
          <LogoutButton className="underline hover:text-red-700" />
        </div>
      </nav>
    </header>
  );
}
