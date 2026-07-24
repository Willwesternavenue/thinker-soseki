"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { reviewLink } from "../actions";
import { StatusBadge } from "../status-badge";

type LinkRow = {
  link_id: string;
  chunk_id: string;
  evidence_role: string;
  strength: string;
  quote_allowed: boolean;
  status: string;
  note: string | null;
  verbatim: boolean;
  chunk_text: string;
};

export function LinkReview({ links }: { links: LinkRow[] }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  function update(
    linkId: string,
    change: { status?: "approved" | "rejected" | "draft"; quote_allowed?: boolean }
  ) {
    startTransition(async () => {
      setMessage(null);
      const result = await reviewLink(linkId, change);
      if (result.error) setMessage(result.error);
      router.refresh();
    });
  }

  return (
    <div className="space-y-2">
      {message && <p className="text-sm text-red-700">{message}</p>}
      {links.map((link) => (
        <details
          key={link.link_id}
          className="rounded-lg border border-stone-200 bg-white"
        >
          <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
            <StatusBadge status={link.status} />
            <span className="font-mono text-xs text-stone-600">{link.chunk_id}</span>
            <span className="rounded bg-stone-200 px-1.5 py-0.5 text-xs">
              {link.evidence_role} / {link.strength}
            </span>
            {link.verbatim && (
              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">
                verbatim
              </span>
            )}
            <label className="ml-auto flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                checked={link.quote_allowed}
                disabled={isPending || !link.verbatim}
                onChange={(e) =>
                  update(link.link_id, { quote_allowed: e.target.checked })
                }
              />
              引用可(quote_allowed)
            </label>
            {link.status !== "approved" && (
              <button
                disabled={isPending}
                onClick={(e) => {
                  e.preventDefault();
                  update(link.link_id, { status: "approved" });
                }}
                className="rounded border border-green-300 px-2 py-0.5 text-xs text-green-800 hover:bg-stone-100"
              >
                承認
              </button>
            )}
            {link.status !== "rejected" && (
              <button
                disabled={isPending}
                onClick={(e) => {
                  e.preventDefault();
                  update(link.link_id, { status: "rejected" });
                }}
                className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 hover:bg-stone-100"
              >
                却下
              </button>
            )}
          </summary>
          <div className="border-t border-stone-200 px-3 py-2">
            {link.note && (
              <p className="mb-2 text-xs text-amber-800">メモ: {link.note}</p>
            )}
            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded bg-stone-100 p-3 text-xs text-stone-700">
              {link.chunk_text}
            </pre>
          </div>
        </details>
      ))}
      {!links.length && (
        <p className="text-sm text-stone-500">
          リンク候補がありません(worker: uv run python -m src.distill cards で生成)
        </p>
      )}
    </div>
  );
}
