# スクリプト整形(YouTube生書き起こし→取り込み用TXT)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube生書き起こし(タイムスタンプ癒着・話者ラベル無し・ASR誤変換)を貼り付け→LLM整形→人間レビュー→ワンボタンで原典取り込みできる admin ページを作る。

**Architecture:** 純関数ライブラリ(タイムスタンプ除去/セグメント分割/TXT生成/ターン操作)+ transcript_drafts テーブル + SSEストリーミングroute handler(Sonnetでセグメント毎に話者判定・誤変換修正)+ レビューUI + 確定取り込みserver action(既存uploadSourceと同じ手順)。

**Tech Stack:** Next.js 16 App Router / TypeScript / Tailwind / vitest / @anthropic-ai/sdk(既存 `src/lib/rag/llm.ts` 経由)/ Supabase(ローカル55321系)

## Global Constraints

- 仕様書: `docs/superpowers/specs/2026-07-07-transcript-prep-design.md`
- 作業ディレクトリ: `/Users/will/thinkerllm`(mainブランチ、ユーザーのdev serverが :3000 で稼働中)
- **`npm run build` 禁止**(dev serverの`.next`を壊す)。型チェックは `cd frontend && npx tsc --noEmit`
- テストは vitest: `cd frontend && npx vitest run src/lib/transcripts/prep.test.ts`
- UIは常時ライトテーマ(stone-50/white/stone-900/blue-700)。globals.cssに手を入れない
- LLMモデル: 整形は `MODEL_ANSWER`(claude-sonnet-5)。`src/lib/rag/llm.ts` の `callJson` を再利用
- migrationの適用: `cd /Users/will/thinkerllm && supabase migration up`(supabase startは稼働済み)
- コミットメッセージは日本語・末尾に `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: 純関数ライブラリ prep.ts(タイムスタンプ除去・分割・TXT生成・ターン操作)

**Files:**
- Create: `frontend/src/lib/transcripts/prep.ts`
- Test: `frontend/src/lib/transcripts/prep.test.ts`

**Interfaces:**
- Produces(後続タスクが依存):
  - `type TurnFix = { from: string; to: string }`
  - `type Turn = { speaker: "本人発言" | "質問者" | "?"; text: string; fixes: TurnFix[] }`
  - `stripTimestamps(raw: string): string`
  - `segmentText(text: string, maxChars?: number): string[]`(既定4000)
  - `buildCleanTxt(title: string, videoUrl: string | null, turns: Turn[]): string`
  - `mergeTurns(turns: Turn[], index: number): Turn[]`(indexとindex+1を結合)
  - `splitTurnAtNewline(turns: Turn[], index: number): Turn[]`(text中の最初の改行で分割)
  - `revertFix(turn: Turn, fixIndex: number): Turn`(修正を1件差し戻す)

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/lib/transcripts/prep.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  stripTimestamps,
  segmentText,
  buildCleanTxt,
  mergeTurns,
  splitTurnAtNewline,
  revertFix,
  type Turn,
} from "./prep";

describe("stripTimestamps", () => {
  it("行頭タイムスタンプ+読み上げ重複(分・秒)を除去する", () => {
    expect(stripTimestamps("1:151 分 15 秒金についてより深く")).toBe(
      "金についてより深く"
    );
  });

  it("秒のみの読み上げ重複を除去する(分が0)", () => {
    expect(stripTimestamps("0:3131 秒お")).toBe("お");
  });

  it("分のみの読み上げ重複を除去する(秒が0)", () => {
    expect(stripTimestamps("2:002 分正直にしかも 受けてる。")).toBe(
      "正直にしかも 受けてる。"
    );
  });

  it("タイムスタンプだけの行・読み上げだけの行は落とす(RTF系レイアウト)", () => {
    expect(stripTimestamps("0:18\n18 秒\n本文です。")).toBe("本文です。");
    expect(stripTimestamps("1:15\n1 分 15 秒\n本文です。")).toBe("本文です。");
  });

  it("数値がタイムスタンプと一致しない読み上げは本文として残す", () => {
    // 「30 秒」は 0:31 と一致しないので本文扱い
    expect(stripTimestamps("0:3130 秒待った")).toBe("30 秒待った");
  });

  it("タイムスタンプの無い行・チャプター行はそのまま通す", () => {
    expect(stripTimestamps("チャプター 1: 第一回 絶対負の定義\n本文。")).toBe(
      "チャプター 1: 第一回 絶対負の定義\n本文。"
    );
  });

  it("空行は詰める", () => {
    expect(stripTimestamps("あ\n\n\nい")).toBe("あ\nい");
  });
});

describe("segmentText", () => {
  it("maxCharsを超えない範囲で文境界で分割する", () => {
    const text = "一文目。二文目。三文目。";
    expect(segmentText(text, 8)).toEqual(["一文目。", "二文目。", "三文目。"]);
  });

  it("maxChars内なら1セグメントにまとめる", () => {
    expect(segmentText("一文目。二文目。", 100)).toEqual(["一文目。二文目。"]);
  });

  it("空文字は空配列", () => {
    expect(segmentText("", 100)).toEqual([]);
  });
});

describe("buildCleanTxt", () => {
  it("動画名ヘッダ+URL+話者ラベル行を生成する(1102 docx形式)", () => {
    const turns: Turn[] = [
      { speaker: "質問者", text: "絶対負とは?", fixes: [] },
      { speaker: "本人発言", text: "俺の中心思想だ。", fixes: [] },
    ];
    expect(buildCleanTxt("絶対負を語る", "https://youtu.be/x", turns)).toBe(
      "動画名：【絶対負を語る】\nhttps://youtu.be/x\n\n質問者: 絶対負とは?\n本人発言: 俺の中心思想だ。\n"
    );
  });

  it("URL無し・空テキストのターンはスキップ", () => {
    const turns: Turn[] = [
      { speaker: "本人発言", text: "  ", fixes: [] },
      { speaker: "本人発言", text: "本文", fixes: [] },
    ];
    expect(buildCleanTxt("題", null, turns)).toBe("動画名：【題】\n\n本人発言: 本文\n");
  });
});

describe("ターン操作", () => {
  const base: Turn[] = [
    { speaker: "質問者", text: "質問です", fixes: [{ from: "a", to: "b" }] },
    { speaker: "本人発言", text: "答えだ", fixes: [{ from: "c", to: "d" }] },
    { speaker: "本人発言", text: "続きだ", fixes: [] },
  ];

  it("mergeTurns: 次のターンを取り込み、話者は前側・fixesは連結", () => {
    const merged = mergeTurns(base, 1);
    expect(merged).toHaveLength(2);
    expect(merged[1]).toEqual({
      speaker: "本人発言",
      text: "答えだ 続きだ",
      fixes: [{ from: "c", to: "d" }],
    });
  });

  it("mergeTurns: 末尾・範囲外は何もしない", () => {
    expect(mergeTurns(base, 2)).toBe(base);
    expect(mergeTurns(base, -1)).toBe(base);
  });

  it("splitTurnAtNewline: 最初の改行で2ターンに分割(fixesは前側に残す)", () => {
    const turns: Turn[] = [
      { speaker: "本人発言", text: "前半だ\n後半だ", fixes: [{ from: "a", to: "b" }] },
    ];
    const split = splitTurnAtNewline(turns, 0);
    expect(split).toEqual([
      { speaker: "本人発言", text: "前半だ", fixes: [{ from: "a", to: "b" }] },
      { speaker: "本人発言", text: "後半だ", fixes: [] },
    ]);
  });

  it("splitTurnAtNewline: 改行が無ければ何もしない", () => {
    expect(splitTurnAtNewline(base, 0)).toBe(base);
  });

  it("revertFix: 修正を差し戻し、fixesから除去する", () => {
    const turn: Turn = {
      speaker: "本人発言",
      text: "絶対負を掴む",
      fixes: [{ from: "絶対府", to: "絶対負" }],
    };
    expect(revertFix(turn, 0)).toEqual({
      speaker: "本人発言",
      text: "絶対府を掴む",
      fixes: [],
    });
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd /Users/will/thinkerllm/frontend && npx vitest run src/lib/transcripts/prep.test.ts`
Expected: FAIL(`Cannot find module './prep'` 等)

- [ ] **Step 3: 実装を書く**

`frontend/src/lib/transcripts/prep.ts`:

```ts
/** スクリプト整形の純関数群(仕様: docs/superpowers/specs/2026-07-07-transcript-prep-design.md)。 */

export type TurnFix = { from: string; to: string };

export type Turn = {
  speaker: "本人発言" | "質問者" | "?";
  text: string;
  fixes: TurnFix[];
};

const TS_RE = /^(\d{1,2}):(\d{2})/;
// 「N 分 M 秒」「M 秒」「N 分」の読み上げ重複
const DUP_RE = /^\s*(?:(\d+)\s*分)?\s*(?:(\d+)\s*秒)?/;
const TS_ONLY_RE = /^\d{1,2}:\d{2}(?::\d{2})?$/;
const DUP_ONLY_RE = /^(?:\d+\s*分)?\s*(?:\d+\s*秒)?$/;

/** 行頭タイムスタンプ+読み上げ重複を除去する。単独行(RTF系レイアウト)も落とす。 */
export function stripTimestamps(raw: string): string {
  const out: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    let s = line.trim();
    if (!s) continue;
    // タイムスタンプ/読み上げだけの行は落とす
    if (TS_ONLY_RE.test(s)) continue;
    if (s !== "" && DUP_ONLY_RE.test(s) && /\d/.test(s)) continue;

    const ts = s.match(TS_RE);
    if (ts) {
      const min = parseInt(ts[1], 10);
      const sec = parseInt(ts[2], 10);
      s = s.slice(ts[0].length);
      const dup = s.match(DUP_RE);
      if (dup && (dup[1] !== undefined || dup[2] !== undefined)) {
        const dupMin = dup[1] !== undefined ? parseInt(dup[1], 10) : null;
        const dupSec = dup[2] !== undefined ? parseInt(dup[2], 10) : null;
        // タイムスタンプの数値と一致する読み上げのみ除去(本文中の数値を守る)
        const matches =
          (dupMin === min && dupSec === sec) ||
          (dupMin === null && dupSec === sec && min === 0) ||
          (dupMin === min && dupSec === null && sec === 0);
        if (matches) s = s.slice(dup[0].length);
      }
      s = s.trim();
    }
    if (s) out.push(s);
  }
  return out.join("\n");
}

/** 文境界(。!?!?と改行)でmaxCharsを超えないようにセグメント分割する。 */
export function segmentText(text: string, maxChars = 4000): string[] {
  if (!text.trim()) return [];
  const sentences = text.split(/(?<=[。!?！？\n])/);
  const segments: string[] = [];
  let buf = "";
  for (const sentence of sentences) {
    if (buf && buf.length + sentence.length > maxChars) {
      segments.push(buf);
      buf = "";
    }
    buf += sentence;
  }
  if (buf.trim()) segments.push(buf);
  return segments;
}

/** 取り込み用TXTを生成する(1102/1103 docxで実績のある形式)。 */
export function buildCleanTxt(
  title: string,
  videoUrl: string | null,
  turns: Turn[]
): string {
  const lines = [`動画名：【${title}】`];
  if (videoUrl) lines.push(videoUrl);
  lines.push("");
  for (const turn of turns) {
    const text = turn.text.trim();
    if (!text) continue;
    lines.push(`${turn.speaker}: ${text}`);
  }
  return lines.join("\n") + "\n";
}

/** index と index+1 のターンを結合する(話者は前側、fixesは連結)。 */
export function mergeTurns(turns: Turn[], index: number): Turn[] {
  if (index < 0 || index >= turns.length - 1) return turns;
  const a = turns[index];
  const b = turns[index + 1];
  const merged: Turn = {
    speaker: a.speaker,
    text: `${a.text.trim()} ${b.text.trim()}`.trim(),
    fixes: [...a.fixes, ...b.fixes],
  };
  return [...turns.slice(0, index), merged, ...turns.slice(index + 2)];
}

/** text中の最初の改行でターンを2つに分割する(fixesは前側に残す)。 */
export function splitTurnAtNewline(turns: Turn[], index: number): Turn[] {
  const turn = turns[index];
  if (!turn) return turns;
  const pos = turn.text.indexOf("\n");
  if (pos < 0) return turns;
  const first: Turn = { ...turn, text: turn.text.slice(0, pos).trim() };
  const second: Turn = {
    speaker: turn.speaker,
    text: turn.text.slice(pos + 1).trim(),
    fixes: [],
  };
  return [...turns.slice(0, index), first, second, ...turns.slice(index + 1)];
}

/** LLM修正を1件差し戻す(textをfrom表記に戻し、fixesから除去)。 */
export function revertFix(turn: Turn, fixIndex: number): Turn {
  const fix = turn.fixes[fixIndex];
  if (!fix) return turn;
  return {
    ...turn,
    text: turn.text.replace(fix.to, fix.from),
    fixes: turn.fixes.filter((_, i) => i !== fixIndex),
  };
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd /Users/will/thinkerllm/frontend && npx vitest run src/lib/transcripts/prep.test.ts`
Expected: PASS(全テスト)

- [ ] **Step 5: 既存テストも含めて全部通ることを確認**

Run: `cd /Users/will/thinkerllm/frontend && npx vitest run`
Expected: PASS(rag.test.ts含む)

- [ ] **Step 6: コミット**

```bash
cd /Users/will/thinkerllm
git add frontend/src/lib/transcripts/
git commit -m "スクリプト整形の純関数群(タイムスタンプ除去・分割・TXT生成・ターン操作)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: transcript_drafts テーブル(migration)

**Files:**
- Create: `supabase/migrations/20260707000001_transcript_drafts.sql`

**Interfaces:**
- Produces: `public.transcript_drafts` テーブル(後続タスクのserver action / route handlerが読み書き)

- [ ] **Step 1: migrationを書く**

`supabase/migrations/20260707000001_transcript_drafts.sql`:

```sql
-- スクリプト整形の下書き(YouTube生書き起こし → LLM整形 → レビュー → 取り込み)
-- 仕様: docs/superpowers/specs/2026-07-07-transcript-prep-design.md
create table public.transcript_drafts (
  draft_id uuid primary key default gen_random_uuid(),
  person_id text not null default 'x_shigyo',
  title text not null,
  video_url text,
  hint text,
  priority text not null default 'core' check (priority in
    ('core','important','support','style','archive')),
  raw_text text not null,
  turns jsonb not null default '[]'::jsonb,
  processed_segments int not null default 0,
  status text not null default 'processing' check (status in
    ('processing','review','ingested')),
  source_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger transcript_drafts_updated_at before update on public.transcript_drafts
  for each row execute function public.set_updated_at();

alter table public.transcript_drafts enable row level security;
create policy "admin all" on public.transcript_drafts
  for all using (public.is_admin()) with check (public.is_admin());
```

- [ ] **Step 2: migrationを適用**

Run: `cd /Users/will/thinkerllm && supabase migration up`
Expected: `Applying migration 20260707000001_transcript_drafts.sql...` 後にエラー無し

- [ ] **Step 3: テーブルができたことを確認**

Run:
```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker exec supabase_db_thinkerllm psql -U postgres -tAc \
  "select count(*) from transcript_drafts; select policyname from pg_policies where tablename='transcript_drafts';"
```
Expected: `0` と `admin all`

- [ ] **Step 4: コミット**

```bash
cd /Users/will/thinkerllm
git add supabase/migrations/20260707000001_transcript_drafts.sql
git commit -m "transcript_draftsテーブル追加(スクリプト整形の下書き)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: LLM整形プロンプト + SSE route handler

**Files:**
- Create: `frontend/src/lib/transcripts/prompts.ts`
- Create: `frontend/src/app/api/admin/transcripts/process/route.ts`

**Interfaces:**
- Consumes: Task 1の `stripTimestamps` / `segmentText` / `Turn`、既存 `callJson` / `MODEL_ANSWER`(`@/lib/rag/llm`)、`getUserWithProfile` / `createClient`(`@/lib/supabase/server`)
- Produces:
  - `POST /api/admin/transcripts/process` body `{ draftId: string }`
  - SSE応答: `data: {"done":n,"total":m}` の進捗行 → `data: {"finished":true}` または `data: {"error":"..."}`
  - 完了時 draft.status='review'、draft.turns に全ターン保存

- [ ] **Step 1: プロンプトを書く**

`frontend/src/lib/transcripts/prompts.ts`:

```ts
import type { Turn } from "./prep";

/** ドメイン用語集(誤変換修正の基準)。 */
export const GLOSSARY = [
  "絶対負",
  "絶対否定",
  "菌",
  "菌食",
  "腸内細菌",
  "葉隠",
  "超葉隠論",
  "執行草舟",
  "戸嶋靖昌",
  "生くる",
  "憧れの思想",
  "おゝポポイ",
  "死に狂い",
  "忍ぶ恋",
  "武士道",
  "暗黒流体",
  "躍動",
  "煩悶",
] as const;

export const SEGMENT_SYSTEM = `あなたはYouTube動画の自動文字起こしを整形する編集者である。
入力は執行草舟(社長)と聞き手のインタビュー書き起こしで、話者ラベルがなく、
音声認識の誤変換が含まれる。

タスク:
1. 本文を発話のまとまり(ターン)に分割し、話者を判定する。
   - "本人発言" = 執行草舟(社長)。思想を一人称「俺」で語る長い発話が中心
   - "質問者" = 聞き手。質問・相槌(はい。うん。おお。等)・敬語が中心
   - 判定に迷う場合は "?" とする(勝手に決めない)
2. 明らかな音声認識の誤変換のみ修正する。
   - 修正は表記の訂正に限る。言い回しの変更・要約・削除・追加は禁止
   - 修正した箇所は必ず fixes に {"from": 元の表記, "to": 修正後} で列挙する
3. 相槌だけの短い発話も独立したターンとして残す(削除しない)
4. 入力の本文をすべてターンに含める(取りこぼし禁止)

用語集(正しい表記): ${GLOSSARY.join("、")}

出力はJSONのみ:
{"turns": [{"speaker": "本人発言" | "質問者" | "?", "text": "...", "fixes": [{"from": "...", "to": "..."}]}]}`;

/** セグメント1つ分のユーザープロンプトを組み立てる。 */
export function buildSegmentPrompt(
  prevTail: string,
  segment: string,
  hint: string | null
): string {
  const parts: string[] = [];
  if (hint) parts.push(`この動画についての補足: ${hint}`);
  if (prevTail) {
    parts.push(`直前の本文(文脈。処理済みなので出力に含めない):\n${prevTail}`);
  }
  parts.push(`本文(この部分だけをターンに分割して出力):\n${segment}`);
  return parts.join("\n\n");
}

/** LLM応答を検証してTurn[]に正規化する。不正なら例外。 */
export function normalizeTurns(raw: unknown): Turn[] {
  const obj = raw as { turns?: unknown };
  if (!obj || !Array.isArray(obj.turns)) throw new Error("turns配列がない");
  return (obj.turns as Array<Record<string, unknown>>)
    .map((t) => ({
      speaker:
        t.speaker === "本人発言" || t.speaker === "質問者"
          ? (t.speaker as Turn["speaker"])
          : ("?" as const),
      text: String(t.text ?? ""),
      fixes: Array.isArray(t.fixes)
        ? (t.fixes as Array<Record<string, unknown>>)
            .filter((f) => typeof f?.from === "string" && typeof f?.to === "string")
            .map((f) => ({ from: f.from as string, to: f.to as string }))
        : [],
    }))
    .filter((t) => t.text.trim().length > 0);
}
```

- [ ] **Step 2: route handlerを書く**

`frontend/src/app/api/admin/transcripts/process/route.ts`:

```ts
import { NextRequest } from "next/server";
import { createClient, getUserWithProfile } from "@/lib/supabase/server";
import { callJson, MODEL_ANSWER } from "@/lib/rag/llm";
import { segmentText, stripTimestamps, type Turn } from "@/lib/transcripts/prep";
import {
  SEGMENT_SYSTEM,
  buildSegmentPrompt,
  normalizeTurns,
} from "@/lib/transcripts/prompts";

// 長尺書き起こし(数十セグメント×Sonnet)を順次処理するため長めに確保
export const maxDuration = 3600;

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
  const segments = segmentText(cleaned);
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
            prevTail,
            segments[done],
            draft.hint as string | null
          );
          turns = [...turns, ...segmentTurns];
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
  prevTail: string,
  segment: string,
  hint: string | null
): Promise<Turn[]> {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const raw = await callJson<unknown>({
        model: MODEL_ANSWER,
        system: SEGMENT_SYSTEM,
        prompt: buildSegmentPrompt(prevTail, segment, hint),
        maxTokens: 10000,
      });
      return normalizeTurns(raw);
    } catch {
      // リトライ(1回)。再失敗は下のフォールバックへ
    }
  }
  // 再失敗: セグメント全体を話者未確定ターンとしてレビューに回す(データを失わない)
  return [{ speaker: "?", text: segment.trim(), fixes: [] }];
}
```

- [ ] **Step 3: 型チェック**

Run: `cd /Users/will/thinkerllm/frontend && npx tsc --noEmit`
Expected: エラー無し

- [ ] **Step 4: コミット**

```bash
cd /Users/will/thinkerllm
git add frontend/src/lib/transcripts/prompts.ts frontend/src/app/api/admin/transcripts/
git commit -m "スクリプト整形のLLMプロンプトとSSE整形API

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: server actions(下書き作成・保存・確定取り込み)

**Files:**
- Create: `frontend/src/app/admin/transcripts/actions.ts`

**Interfaces:**
- Consumes: Task 1の `buildCleanTxt` / `Turn`、`createClient`(`@/lib/supabase/server`)
- Produces(UIタスクが呼ぶ):
  - `createDraft(formData: FormData): Promise<{ error?: string }>` — 成功時 `/admin/transcripts/{draft_id}` へredirect
  - `saveTurns(draftId: string, turns: Turn[]): Promise<{ error?: string }>`
  - `ingestDraft(draftId: string): Promise<{ error?: string; sourceId?: string }>`

- [ ] **Step 1: actionsを書く**

`frontend/src/app/admin/transcripts/actions.ts`:

```ts
"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { buildCleanTxt, type Turn } from "@/lib/transcripts/prep";

/** 下書きを作成して レビューページへ遷移する。 */
export async function createDraft(
  formData: FormData
): Promise<{ error?: string }> {
  const supabase = await createClient();
  const title = ((formData.get("title") as string) || "").trim();
  const rawText = ((formData.get("raw_text") as string) || "").trim();
  const videoUrl = ((formData.get("video_url") as string) || "").trim() || null;
  const hint = ((formData.get("hint") as string) || "").trim() || null;
  const priority = (formData.get("priority") as string) || "core";
  if (!title || !rawText) return { error: "タイトルと本文は必須です" };

  const { data, error } = await supabase
    .from("transcript_drafts")
    .insert({ title, raw_text: rawText, video_url: videoUrl, hint, priority })
    .select("draft_id")
    .single();
  if (error) return { error: error.message };

  revalidatePath("/admin/transcripts");
  redirect(`/admin/transcripts/${data.draft_id}`);
}

/** レビュー中の編集内容を保存する。 */
export async function saveTurns(
  draftId: string,
  turns: Turn[]
): Promise<{ error?: string }> {
  const supabase = await createClient();
  const { error } = await supabase
    .from("transcript_drafts")
    .update({ turns })
    .eq("draft_id", draftId);
  if (error) return { error: error.message };
  return {};
}

/** 確定: 整形TXTをStorageへ保存し sources + ingestion_jobs を作成する。 */
export async function ingestDraft(
  draftId: string
): Promise<{ error?: string; sourceId?: string }> {
  const supabase = await createClient();
  const { data: draft } = await supabase
    .from("transcript_drafts")
    .select("*")
    .eq("draft_id", draftId)
    .single();
  if (!draft) return { error: "下書きが見つかりません" };
  if (draft.status === "ingested") return { error: "取り込み済みです" };

  const turns = (draft.turns ?? []) as Turn[];
  if (turns.length === 0) return { error: "整形結果がありません" };
  const unresolved = turns.filter((t) => t.speaker === "?").length;
  if (unresolved > 0) {
    return { error: `話者未確定(?)のターンが${unresolved}件あります。レビューで確定してください` };
  }

  // sources採番(uploadSourceと同じ流儀。VIDEO固定)
  const { data: existing } = await supabase
    .from("sources")
    .select("source_id")
    .like("source_id", "VIDEO\\_%")
    .order("source_id", { ascending: false })
    .limit(1);
  const lastNum = existing?.[0]
    ? parseInt(existing[0].source_id.split("_").pop() ?? "0", 10)
    : 0;
  const sourceId = `VIDEO_${String(lastNum + 1).padStart(3, "0")}`;

  const txt = buildCleanTxt(
    draft.title as string,
    draft.video_url as string | null,
    turns
  );
  const storagePath = `${sourceId}/original.txt`;
  const { error: uploadError } = await supabase.storage
    .from("originals")
    .upload(storagePath, new Blob([txt], { type: "text/plain" }), {
      upsert: true,
    });
  if (uploadError) return { error: `アップロード失敗: ${uploadError.message}` };

  const { error: sourceError } = await supabase.from("sources").insert({
    source_id: sourceId,
    person_id: "x_shigyo",
    title: draft.title,
    source_type: "video_transcript",
    author: "執行草舟",
    file_type: "txt",
    priority: draft.priority,
    status: "raw",
    original_file_path: storagePath,
  });
  if (sourceError) return { error: `sources作成失敗: ${sourceError.message}` };

  const { error: jobError } = await supabase
    .from("ingestion_jobs")
    .insert({ source_id: sourceId, status: "pending" });
  if (jobError) return { error: `ジョブ作成失敗: ${jobError.message}` };

  await supabase
    .from("transcript_drafts")
    .update({ status: "ingested", source_id: sourceId })
    .eq("draft_id", draftId);

  revalidatePath("/admin/transcripts");
  revalidatePath("/admin/sources");
  revalidatePath("/admin/jobs");
  return { sourceId };
}
```

- [ ] **Step 2: 型チェック**

Run: `cd /Users/will/thinkerllm/frontend && npx tsc --noEmit`
Expected: エラー無し

- [ ] **Step 3: コミット**

```bash
cd /Users/will/thinkerllm
git add frontend/src/app/admin/transcripts/actions.ts
git commit -m "スクリプト整形のserver actions(下書き作成・保存・確定取り込み)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: UIページ(一覧・新規・レビュー)+ ナビ追加

**Files:**
- Create: `frontend/src/app/admin/transcripts/page.tsx`(一覧)
- Create: `frontend/src/app/admin/transcripts/new/page.tsx`(新規)
- Create: `frontend/src/app/admin/transcripts/new/new-form.tsx`(client)
- Create: `frontend/src/app/admin/transcripts/[draftId]/page.tsx`(レビュー、server)
- Create: `frontend/src/app/admin/transcripts/[draftId]/review-client.tsx`(client)
- Modify: `frontend/src/app/admin/layout.tsx`(NAV配列に1行追加)

**Interfaces:**
- Consumes: Task 4の `createDraft` / `saveTurns` / `ingestDraft`、Task 3の `POST /api/admin/transcripts/process`(SSE)、Task 1の `Turn` / `mergeTurns` / `splitTurnAtNewline` / `revertFix`

- [ ] **Step 1: NAVに追加**

`frontend/src/app/admin/layout.tsx` のNAV配列、`{ href: "/admin/sources", label: "原典" },` の直後に:

```ts
  { href: "/admin/transcripts", label: "スクリプト整形" },
```

- [ ] **Step 2: 一覧ページ**

`frontend/src/app/admin/transcripts/page.tsx`:

```tsx
import Link from "next/link";
import { createClient } from "@/lib/supabase/server";

const STATUS_LABEL: Record<string, [string, string]> = {
  processing: ["整形中", "bg-amber-100 text-amber-800"],
  review: ["レビュー待ち", "bg-blue-100 text-blue-800"],
  ingested: ["取り込み済み", "bg-green-100 text-green-800"],
};

export default async function TranscriptsPage() {
  const supabase = await createClient();
  const { data: drafts } = await supabase
    .from("transcript_drafts")
    .select("draft_id, title, status, source_id, turns, created_at")
    .order("created_at", { ascending: false });

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h1 className="text-xl font-bold">スクリプト整形</h1>
        <Link
          href="/admin/transcripts/new"
          className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800"
        >
          新規整形
        </Link>
      </div>
      <p className="mb-6 text-sm text-stone-500">
        YouTube生書き起こしを貼り付け → 話者切り分け・誤変換修正 → 原典として取り込む。
      </p>
      <div className="overflow-hidden rounded border border-stone-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>
              <th className="px-4 py-2">タイトル</th>
              <th className="px-4 py-2">状態</th>
              <th className="px-4 py-2">ターン数</th>
              <th className="px-4 py-2">原典</th>
            </tr>
          </thead>
          <tbody>
            {(drafts ?? []).map((d) => {
              const [label, cls] =
                STATUS_LABEL[d.status] ?? [d.status, "bg-stone-100"];
              return (
                <tr key={d.draft_id} className="border-t border-stone-100">
                  <td className="px-4 py-2">
                    <Link
                      href={`/admin/transcripts/${d.draft_id}`}
                      className="text-blue-700 hover:underline"
                    >
                      {d.title}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>
                      {label}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-stone-500">
                    {Array.isArray(d.turns) ? d.turns.length : 0}
                  </td>
                  <td className="px-4 py-2 text-stone-500">
                    {d.source_id ?? "—"}
                  </td>
                </tr>
              );
            })}
            {(drafts ?? []).length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-stone-400">
                  下書きはまだありません
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 新規ページ+フォーム**

`frontend/src/app/admin/transcripts/new/page.tsx`:

```tsx
import { NewForm } from "./new-form";

export default function NewTranscriptPage() {
  return (
    <div>
      <h1 className="mb-6 text-xl font-bold">新規整形</h1>
      <NewForm />
    </div>
  );
}
```

`frontend/src/app/admin/transcripts/new/new-form.tsx`:

```tsx
"use client";

import { useState, useTransition } from "react";
import { createDraft } from "../actions";

const PRIORITIES = [
  ["core", "core(中核)"],
  ["important", "important(重要)"],
  ["support", "support(補助)"],
  ["style", "style(語り口)"],
  ["archive", "archive(保管)"],
] as const;

export function NewForm() {
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    startTransition(async () => {
      const result = await createDraft(formData);
      if (result?.error) setError(result.error);
    });
  }

  const inputCls =
    "w-full rounded border border-stone-300 bg-white px-3 py-2 text-sm";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="mb-1 block text-sm text-stone-600">
          動画タイトル(必須)
        </label>
        <input name="title" required className={inputCls} />
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">動画URL</label>
        <input name="video_url" placeholder="https://www.youtube.com/watch?v=…" className={inputCls} />
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">
          補足ヒント(任意。例:「今回は菌と腸内細菌がテーマ」)
        </label>
        <input name="hint" className={inputCls} />
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">重要度</label>
        <select name="priority" defaultValue="core" className={inputCls}>
          {PRIORITIES.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">
          生スクリプト貼り付け(必須)
        </label>
        <textarea
          name="raw_text"
          required
          rows={16}
          placeholder="YouTubeの文字起こしを全文コピペ(タイムスタンプ付きのままでOK)"
          className={`${inputCls} font-mono`}
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={isPending}
        className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800 disabled:opacity-50"
      >
        {isPending ? "作成中…" : "下書きを作成して整形へ"}
      </button>
    </form>
  );
}
```

- [ ] **Step 4: レビューページ(server)**

`frontend/src/app/admin/transcripts/[draftId]/page.tsx`:

```tsx
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
    .select("draft_id, title, video_url, hint, priority, status, source_id, turns, processed_segments")
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
```

- [ ] **Step 5: レビューUI(client)**

`frontend/src/app/admin/transcripts/[draftId]/review-client.tsx`:

```tsx
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
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [processing, setProcessing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const unresolved = useMemo(
    () => turns.filter((t) => t.speaker === "?").length,
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
          if (evt.total !== undefined) setProgress({ done: evt.done, total: evt.total });
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
            {unresolved > 0 && (
              <span className="ml-2 text-red-600">話者未確定 {unresolved} 件</span>
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
            disabled={isPending || status === "ingested" || turns.length === 0 || unresolved > 0}
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

      {(status === "processing" || turns.length === 0) && status !== "ingested" && (
        <div className="mb-6 rounded border border-amber-200 bg-amber-50 p-4">
          {processing ? (
            <div>
              <p className="mb-2 text-sm text-amber-800">
                LLM整形中… {progress ? `${progress.done} / ${progress.total} セグメント` : "準備中"}
              </p>
              <div className="h-2 overflow-hidden rounded bg-amber-100">
                <div
                  className="h-full bg-amber-500 transition-all"
                  style={{
                    width: progress && progress.total > 0
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
          <li key={i} className={`rounded border bg-white p-3 ${turn.speaker === "?" ? "border-red-400" : "border-stone-200"}`}>
            <div className="mb-2 flex items-center gap-2">
              <button
                onClick={() => update(turns.map((t, j) => (j === i ? { ...t, speaker: nextSpeaker(t.speaker) } : t)))}
                className={`rounded px-2 py-0.5 text-xs ${SPEAKER_STYLE[turn.speaker]}`}
                title="クリックで話者を切替"
              >
                {turn.speaker}
              </button>
              <span className="flex-1" />
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
                update(turns.map((t, j) => (j === i ? { ...t, text: e.target.value } : t)))
              }
              rows={Math.max(2, Math.ceil(turn.text.length / 60))}
              className="w-full rounded border border-stone-200 bg-stone-50 px-2 py-1 text-sm"
            />
            {turn.fixes.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {turn.fixes.map((fix, fi) => (
                  <button
                    key={fi}
                    onClick={() => update(turns.map((t, j) => (j === i ? revertFix(t, fi) : t)))}
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
```

- [ ] **Step 6: 型チェック**

Run: `cd /Users/will/thinkerllm/frontend && npx tsc --noEmit`
Expected: エラー無し

- [ ] **Step 7: 全テスト**

Run: `cd /Users/will/thinkerllm/frontend && npx vitest run`
Expected: PASS

- [ ] **Step 8: コミット**

```bash
cd /Users/will/thinkerllm
git add frontend/src/app/admin/transcripts/ frontend/src/app/admin/layout.tsx
git commit -m "スクリプト整形UI(一覧・新規・レビュー)とナビ追加

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: E2E検証(実データ)

**Files:** なし(検証のみ)

**Interfaces:**
- Consumes: Task 1〜5の全成果物、実データ `/Users/will/Desktop/ 【衝撃すぎて覚醒する絶対負の話―絶対負深掘りVer.＜後編＞】.txt`

- [ ] **Step 1: dev serverとWorkerの稼働確認**

Run:
```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN | tail -1
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker exec supabase_db_thinkerllm psql -U postgres -tAc \
  "select status from worker_heartbeats order by last_seen_at desc limit 1;"
```
Expected: node LISTEN 行 と `idle`(または`processing`)

- [ ] **Step 2: 抜粋データでUIフローを検証**

実ファイルの先頭6000字を使う(全文だとLLM整形に数十分かかるため):
1. admin(admin@test.local / testpass123)でログインし `/admin/transcripts` を開く
2. 新規整形 → タイトル「E2Eテスト(削除予定)」、本文に抜粋を貼り付け → 下書き作成
3. 「整形を開始」→ 進捗バーが動き、完了後リロードでターン一覧が出る
4. 確認observations:
   - タイムスタンプ(`0:31`等)が本文に残っていない
   - 話者バッジで 本人発言/質問者 が分かれている(冒頭は聞き手の挨拶)
   - 誤変換修正チップ(例: 絶対府→絶対負、金→菌)が黄色で表示される
   - チップのクリックで差し戻せる/話者バッジのクリックで切替わる
   - 保存 → リロードしても編集が残る

- [ ] **Step 3: 確定取り込み→チャンク検証**

1. 「確定して取り込み」→ VIDEO_xxx が採番される
2. `/admin/jobs` でジョブがsucceededになるまで待つ
3. チャンクの引用整合性を確認:
```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker exec supabase_db_thinkerllm psql -U postgres -c \
  "select chunk_type, speaker, count(*) from source_chunks where source_id='VIDEO_XXX' group by 1,2;"
```
Expected: `qa_pair | 本人発言` が主(モノローグ全融合になっていない)

- [ ] **Step 4: テストデータの掃除**

E2Eで作ったVIDEO_xxx(タイトル「E2Eテスト(削除予定)」)と下書きを削除:
```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker exec supabase_db_thinkerllm psql -U postgres -c "
delete from ingestion_jobs where source_id='VIDEO_XXX';
delete from source_chunks where source_id='VIDEO_XXX';
delete from sources where source_id='VIDEO_XXX';
delete from transcript_drafts where title like 'E2Eテスト%';
"
docker exec supabase_db_thinkerllm psql -U postgres -tAc \
  "select count(*) from storage.objects where name like 'VIDEO_XXX/%' or name like 'VIDEO_XXX.%';"
```
Storage残骸(originals/clean_texts)もStudioまたはSQLで削除する。

- [ ] **Step 5: HANDOFF.mdに機能を追記してコミット**

HANDOFF.mdの「構成 / 起動」または直近修正のセクションに1-2行:
「/admin/スクリプト整形: YouTube生書き起こしを貼り付け→LLM整形(話者切り分け・誤変換修正)→レビュー→取り込み。下書きはtranscript_drafts。」

```bash
cd /Users/will/thinkerllm
git add HANDOFF.md
git commit -m "HANDOFF: スクリプト整形ページを追記

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
