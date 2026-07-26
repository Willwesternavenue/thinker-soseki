import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import type { Turn } from "@/lib/transcripts/prep";
import { ReviewClient } from "./review-client";

export default async function TranscriptDraftPage({
  params,
}: {
  params: Promise<{ draftId: string }>;
}) {
  const { draftId } = await params;
  const supabase = await createClient();
  const { data: draft } = await supabase
    .from("transcript_drafts")
    .select(
      "draft_id, title, video_url, hint, priority, status, source_id, turns, processed_segments"
    )
    .eq("draft_id", draftId)
    .single();
  if (!draft) notFound();

  return (
    <ReviewClient
      draftId={draft.draft_id}
      title={draft.title}
      status={draft.status}
      sourceId={draft.source_id}
      initialTurns={(draft.turns ?? []) as Turn[]}
    />
  );
}
