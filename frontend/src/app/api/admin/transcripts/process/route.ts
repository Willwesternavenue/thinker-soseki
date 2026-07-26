import { NextRequest } from "next/server";
import { createClient, getUserWithProfile } from "@/lib/supabase/server";
import { callJson, MODEL_ANSWER } from "@/lib/rag/llm";
import {
  flagFillers,
  segmentText,
  stripTimestamps,
  type Turn,
} from "@/lib/transcripts/prep";
import {
  buildSegmentPrompt,
  buildSegmentSystem,
  normalizeTurns,
} from "@/lib/transcripts/prompts";
import { loadGlossary } from "@/lib/transcripts/glossary";

// 長尺書き起こし(数十セグメント×Sonnet)を順次処理するため長めに確保。
// Vercelの上限(Proは最大1800s)内に収める。1リクエストで終わらなくても
// processed_segments を都度保存しており「続きから整形」で再開できるため、
// 800sで打ち切られても処理は失われない。
export const maxDuration = 800;

export async function POST(req: NextRequest) {
  const auth = await getUserWithProfile();
  if (!auth || auth.profile.role !== "admin") {
    return new Response("forbidden", { status: 403 });
  }

  const { draftId } = (await req.json()) as { draftId?: string };
  if (!draftId) return new Response("draftId required", { status: 400 });

  const supabase = await createClient();
  const { data: draft } = await supabase
    .from("transcript_drafts")
    .select("*")
    .eq("draft_id", draftId)
    .single();
  if (!draft) return new Response("not found", { status: 404 });
  if (draft.status === "ingested") {
    return new Response("already ingested", { status: 409 });
  }

  const cleaned = stripTimestamps(draft.raw_text as string);
  // 3000字/セグメント: 出力JSON(本文複写+fixes)がmaxTokensに収まる安全圏
  const segments = segmentText(cleaned, 3000);
  // 用語集をDBから読み、systemプロンプトを一度だけ組み立てる
  const { terms, rules } = await loadGlossary();
  const system = buildSegmentSystem(terms, rules);
  let turns = (draft.turns ?? []) as Turn[];
  let done = (draft.processed_segments as number) ?? 0;

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (obj: unknown) =>
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`));
      send({ done, total: segments.length });
      try {
        for (; done < segments.length; done++) {
          // 直前の処理済み末尾2ターンを文脈として渡す(話者の流れを保つ)
          const prevTail = turns
            .slice(-2)
            .map((t) => `${t.speaker}: ${t.text.slice(-150)}`)
            .join("\n");
          const segmentTurns = await processSegment(
            system,
            prevTail,
            segments[done],
            draft.hint as string | null
          );
          // 相槌は自動で除外候補フラグ(レビューでワンクリック復帰可能)
          turns = [...turns, ...flagFillers(segmentTurns)];
          await supabase
            .from("transcript_drafts")
            .update({ turns, processed_segments: done + 1 })
            .eq("draft_id", draftId);
          send({ done: done + 1, total: segments.length });
        }
        await supabase
          .from("transcript_drafts")
          .update({ status: "review" })
          .eq("draft_id", draftId);
        send({ finished: true });
      } catch (e) {
        send({ error: (e as Error).message });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
    },
  });
}

/** 1セグメントをLLMで整形する。パース失敗は1回リトライ、再失敗は"?"ターンで返す。 */
async function processSegment(
  system: string,
  prevTail: string,
  segment: string,
  hint: string | null
): Promise<Turn[]> {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const raw = await callJson<unknown>({
        model: MODEL_ANSWER,
        system,
        prompt: buildSegmentPrompt(prevTail, segment, hint),
        maxTokens: 20000,
        // 機械的な整形タスク: 思考トークンが出力枠を食い潰すのを防ぐ
        disableThinking: true,
      });
      return normalizeTurns(raw);
    } catch (e) {
      // リトライ(1回)。再失敗は下のフォールバックへ
      console.error(
        `transcripts/process: セグメント整形失敗(attempt ${attempt + 1}):`,
        (e as Error).message
      );
    }
  }
  // 再失敗: セグメント全体を話者未確定ターンとしてレビューに回す(データを失わない)
  return [{ speaker: "?", text: segment.trim(), fixes: [] }];
}
