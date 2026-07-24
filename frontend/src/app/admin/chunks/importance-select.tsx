"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { updateImportance } from "./actions";

export function ImportanceSelect({
  distillationId,
  value,
}: {
  distillationId: string;
  value: string;
}) {
  const [isPending, startTransition] = useTransition();
  const router = useRouter();
  return (
    <select
      defaultValue={value}
      disabled={isPending}
      onChange={(e) =>
        startTransition(async () => {
          await updateImportance(
            distillationId,
            e.target.value as "high" | "normal" | "low"
          );
          router.refresh();
        })
      }
      className="rounded border border-stone-300 bg-white px-2 py-1 text-xs"
    >
      <option value="high">high</option>
      <option value="normal">normal</option>
      <option value="low">low</option>
    </select>
  );
}
