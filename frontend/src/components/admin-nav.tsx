"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { LogoutButton } from "./logout-button";

type NavItem = { href: string; label: string };
type NavEntry =
  | { label: string; href: string }
  | { label: string; items: NavItem[] };

// 関連画面をグループ化(単独タブが13個で見づらくなったため)
const NAV: NavEntry[] = [
  {
    label: "取り込み",
    items: [
      { href: "/admin/sources", label: "原典" },
      { href: "/admin/jobs", label: "ジョブ" },
      { href: "/admin/chunks", label: "チャンク" },
      { href: "/admin/transcripts", label: "スクリプト整形" },
      { href: "/admin/glossary", label: "用語集" },
    ],
  },
  {
    label: "思想モデル",
    items: [
      { href: "/admin/cards", label: "思想カード" },
      { href: "/admin/rules", label: "判断規則" },
      { href: "/admin/questions", label: "質問対応" },
    ],
  },
  {
    // 創作モードは思想モードと別データ。誤認しないようメニューを分ける(仕様§9.1)
    label: "創作",
    items: [
      { href: "/admin/creative-profiles", label: "創作プロファイル" },
      { href: "/admin/creative-cards", label: "創作カード" },
      { href: "/admin/creative-generations", label: "生成ジョブ" },
      // ナビ付きの管理者向け。/creative（会員向け）はナビが無く戻れなくなる
      { href: "/admin/creative", label: "創作画面" },
    ],
  },
  { label: "評価", href: "/admin/evaluations" },
  { label: "設定", href: "/admin/persona" },
  {
    label: "ガイド",
    items: [
      { href: "/admin/help", label: "使い方" },
      { href: "/admin/architecture", label: "設計" },
    ],
  },
  { label: "チャット画面", href: "/admin/chat" },
];

export function AdminNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState<string | null>(null);
  const navRef = useRef<HTMLElement>(null);

  // ページ遷移でメニューを閉じる
  useEffect(() => setOpen(null), [pathname]);

  // メニュー外クリックで閉じる
  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (!navRef.current?.contains(e.target as Node)) setOpen(null);
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  return (
    <nav
      ref={navRef}
      className="mx-auto flex max-w-5xl items-center gap-1 px-6 py-2 text-sm"
    >
      <span className="mr-3 shrink-0 whitespace-nowrap font-bold">X漱石 管理</span>
      {NAV.map((entry) =>
        "href" in entry ? (
          <Link
            key={entry.label}
            href={entry.href}
            className={`shrink-0 whitespace-nowrap rounded px-2.5 py-1.5 ${
              pathname.startsWith(entry.href)
                ? "bg-stone-200 font-medium text-stone-900"
                : "text-stone-600 hover:bg-stone-100 hover:text-stone-900"
            }`}
          >
            {entry.label}
          </Link>
        ) : (
          <div key={entry.label} className="relative shrink-0">
            <button
              type="button"
              onClick={() => setOpen(open === entry.label ? null : entry.label)}
              className={`flex items-center gap-1 whitespace-nowrap rounded px-2.5 py-1.5 ${
                entry.items.some((i) => pathname.startsWith(i.href))
                  ? "bg-stone-200 font-medium text-stone-900"
                  : "text-stone-600 hover:bg-stone-100 hover:text-stone-900"
              }`}
            >
              {entry.label}
              <span className="text-[10px] text-stone-400">▼</span>
            </button>
            {open === entry.label && (
              <div className="absolute left-0 top-full z-20 mt-1 min-w-40 rounded-lg border border-stone-200 bg-white py-1 shadow-lg">
                {entry.items.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`block whitespace-nowrap px-4 py-2 ${
                      pathname.startsWith(item.href)
                        ? "bg-stone-100 font-medium text-stone-900"
                        : "text-stone-600 hover:bg-stone-50 hover:text-stone-900"
                    }`}
                  >
                    {item.label}
                  </Link>
                ))}
              </div>
            )}
          </div>
        )
      )}
      <LogoutButton className="ml-auto shrink-0 whitespace-nowrap text-stone-600 hover:text-red-700" />
    </nav>
  );
}
