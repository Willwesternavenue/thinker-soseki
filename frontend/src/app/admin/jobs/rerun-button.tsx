"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { rerunJob } from "./actions";

export function RerunButton({ jobId }: { jobId: string }) {
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  return (
    <button
      disabled={isPending}
      onClick={() =>
        startTransition(async () => {
          await rerunJob(jobId);
          router.refresh();
        })
      }
      className="rounded border border-stone-300 px-2 py-1 text-xs hover:bg-stone-100 disabled:opacity-50"
    >
      {isPending ? "..." : "再実行"}
    </button>
  );
}
