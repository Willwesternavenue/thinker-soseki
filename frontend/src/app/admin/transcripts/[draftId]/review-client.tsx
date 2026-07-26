"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import {
  mergeTurns,
  revertFix,
  splitTurnAtNewline,
  type Turn,
} from "@/lib/transcripts/prep";
import { ingestDraft, saveTurns } from "../actions";

const SPEAKER_STYLE: Record<Turn["speaker"], string> = {
  本人発言: "bg-blue-700 text-white",
  質問者: "bg-stone-200 text-stone-700",
  "?": "bg-white text-red-600 border border-red-400",
};

function nextSpeaker(s: Turn["speaker"]): Turn["speaker"] {
  if (s === "本人発言") return "質問者";
  return "本人発言"; // 質問者・? はクリックで本人発言へ
}

export function ReviewClient({
  draftId,
  title,
  status,
  sourceId,
  initialTurns,
}: {
  draftId: string;
  title: string;
  status: string;
  sourceId: string | null;
  initialTurns: Turn[];
}) {
  const [turns, setTurns] = useState<Turn[]>(initialTurns);
  const [dirty, setDirty] = useState(false);
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [processing, setProcessing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const unresolved = useMemo(
    () => turns.filter((t) => t.speaker === "?" && !t.excluded).length,
    [turns]
  );
  const excludedCount = useMemo(
    () => turns.filter((t) => t.excluded).length,
    [turns]
  );

  function update(next: Turn[]) {
    setTurns(next);
    setDirty(true);
  }

  async function runProcess() {
    setProcessing(true);
    setMessage(null);
    try {
      const res = await fetch("/api/admin/transcripts/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draftId }),
      });
      if (!res.ok || !res.body) throw new Error(`整形APIエラー: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const evt = JSON.parse(line.slice(6));
          if (evt.error) throw new Error(evt.error);
          if (evt.total !== undefined)
            setProgress({ done: evt.done, total: evt.total });
          if (evt.finished) window.location.reload();
        }
      }
    } catch (e) {
      setMessage((e as Error).message);
      setProcessing(false);
    }
  }

  function handleSave() {
    startTransition(async () => {
      const result = await saveTurns(draftId, turns);
      setMessage(result.error ?? "保存しました");
      if (!result.error) setDirty(false);
    });
  }

  function handleIngest() {
    if (!window.confirm("この内容で原典として取り込みますか?")) return;
    startTransition(async () => {
      if (dirty) {
        const saved = await saveTurns(draftId, turns);
        if (saved.error) {
          setMessage(saved.error);
          return;
        }
        setDirty(false);
      }
      const result = await ingestDraft(draftId);
      if (result.error) setMessage(result.error);
      else setMessage(`取り込み開始: ${result.sourceId}(ジョブ画面で進捗確認)`);
    });
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">{title}</h1>
          <p className="text-sm text-stone-500">
            ターン {turns.length} 件
            {excludedCount > 0 && (
              <span className="ml-2 text-stone-400">
                (相槌など除外 {excludedCount} 件)
              </span>
            )}
            {unresolved > 0 && (
              <span className="ml-2 text-red-600">
                話者未確定 {unresolved} 件
              </span>
            )}
            {sourceId && <span className="ml-2">→ {sourceId}</span>}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleSave}
            disabled={isPending || !dirty}
            className="rounded border border-stone-300 bg-white px-4 py-2 text-sm hover:bg-stone-50 disabled:opacity-50"
          >
            保存
          </button>
          <button
            onClick={handleIngest}
            disabled={
              isPending ||
              status === "ingested" ||
              turns.length === 0 ||
              unresolved > 0
            }
            className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800 disabled:opacity-50"
          >
            確定して取り込み
          </button>
        </div>
      </div>

      {message && (
        <p className="mb-4 rounded border border-stone-200 bg-stone-50 px-3 py-2 text-sm">
          {message}{" "}
          {message.startsWith("取り込み開始") && (
            <Link href="/admin/jobs" className="text-blue-700 underline">
              ジョブ画面へ
            </Link>
          )}
        </p>
      )}

      {(status === "processing" || turns.length === 0) &&
        status !== "ingested" && (
          <div className="mb-6 rounded border border-amber-200 bg-amber-50 p-4">
            {processing ? (
              <div>
                <p className="mb-2 text-sm text-amber-800">
                  LLM整形中…{" "}
                  {progress
                    ? `${progress.done} / ${progress.total} セグメント`
                    : "準備中"}
                </p>
                <div className="h-2 overflow-hidden rounded bg-amber-100">
                  <div
                    className="h-full bg-amber-500 transition-all"
                    style={{
                      width:
                        progress && progress.total > 0
                          ? `${(progress.done / progress.total) * 100}%`
                          : "0%",
                    }}
                  />
                </div>
              </div>
            ) : (
              <button
                onClick={runProcess}
                className="rounded bg-amber-600 px-4 py-2 text-sm text-white hover:bg-amber-700"
              >
                {turns.length > 0 ? "続きから整形" : "整形を開始"}
              </button>
            )}
          </div>
        )}

      <ol className="space-y-2">
        {turns.map((turn, i) => (
          <li
            key={i}
            className={`rounded border p-3 ${
              turn.excluded
                ? "border-stone-200 bg-stone-100 opacity-60"
                : turn.speaker === "?"
                  ? "border-red-400 bg-white"
                  : "border-stone-200 bg-white"
            }`}
          >
            <div className="mb-2 flex items-center gap-2">
              <button
                onClick={() =>
                  update(
                    turns.map((t, j) =>
                      j === i ? { ...t, speaker: nextSpeaker(t.speaker) } : t
                    )
                  )
                }
                className={`rounded px-2 py-0.5 text-xs ${SPEAKER_STYLE[turn.speaker]}`}
                title="クリックで話者を切替"
              >
                {turn.speaker}
              </button>
              {turn.excluded && (
                <span className="text-xs text-stone-400">除外中(取り込まれません)</span>
              )}
              <span className="flex-1" />
              <button
                onClick={() =>
                  update(
                    turns.map((t, j) =>
                      j === i ? { ...t, excluded: !t.excluded } : t
                    )
                  )
                }
                className={`text-xs ${
                  turn.excluded
                    ? "font-medium text-blue-700 hover:text-blue-900"
                    : "text-stone-400 hover:text-stone-700"
                }`}
                title={turn.excluded ? "取り込み対象に戻す" : "取り込みから除外する"}
              >
                {turn.excluded ? "戻す" : "除外"}
              </button>
              <button
                onClick={() => update(splitTurnAtNewline(turns, i))}
                disabled={!turn.text.includes("\n")}
                className="text-xs text-stone-400 hover:text-stone-700 disabled:opacity-30"
                title="本文に改行を入れてから押すと、その位置で2ターンに分割"
              >
                改行で分割
              </button>
              <button
                onClick={() => update(mergeTurns(turns, i))}
                disabled={i >= turns.length - 1}
                className="text-xs text-stone-400 hover:text-stone-700 disabled:opacity-30"
              >
                下と結合
              </button>
            </div>
            <textarea
              value={turn.text}
              onChange={(e) =>
                update(
                  turns.map((t, j) =>
                    j === i ? { ...t, text: e.target.value } : t
                  )
                )
              }
              rows={Math.max(2, Math.ceil(turn.text.length / 60))}
              className={`w-full rounded border border-stone-200 bg-stone-50 px-2 py-1 text-sm ${
                turn.excluded ? "line-through" : ""
              }`}
            />
            {turn.fixes.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {turn.fixes.map((fix, fi) => (
                  <button
                    key={fi}
                    onClick={() =>
                      update(turns.map((t, j) => (j === i ? revertFix(t, fi) : t)))
                    }
                    className="rounded bg-yellow-100 px-2 py-0.5 text-xs text-yellow-900 hover:bg-yellow-200"
                    title="クリックで元の表記に戻す"
                  >
                    {fix.from} → {fix.to} ✕
                  </button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
