/** スクリプト整形の純関数群(仕様: docs/superpowers/specs/2026-07-07-transcript-prep-design.md)。 */

export type TurnFix = { from: string; to: string };

export type Turn = {
  speaker: "本人発言" | "質問者" | "?";
  text: string;
  fixes: TurnFix[];
  /** trueなら取り込みTXTから除外(相槌など)。UIで復帰可能。 */
  excluded?: boolean;
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
    if (DUP_ONLY_RE.test(s) && /\d/.test(s)) continue;

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
    if (turn.excluded) continue;
    const text = turn.text.trim();
    if (!text) continue;
    lines.push(`${turn.speaker}: ${text}`);
  }
  return lines.join("\n") + "\n";
}

// ── 相槌(filler)の自動除外 ──────────────────────────────
// 保守的な語彙のみ: 「そうだ。」「はい、分かりました」等の実質発言を巻き込まない
const FILLER_WORDS = [
  "なるほど",
  "そうそう",
  "はい",
  "うん",
  "ええ",
  "おお",
  "おう",
  "ああ",
  "あー",
  "ほう",
  "へえ",
  "そう",
  "お",
  "ん",
];
const FILLER_MAX_CHARS = 15;

/** 句読点・空白を除いた本文が相槌語彙の組合せだけなら true。 */
export function isFillerText(text: string): boolean {
  const normalized = text.replace(/[。、.,!?！？\s]/g, "");
  if (!normalized || normalized.length > FILLER_MAX_CHARS) return false;
  // 相槌語彙の貪欲な最長一致でセグメントし尽くせるか
  let rest = normalized;
  outer: while (rest.length > 0) {
    for (const word of FILLER_WORDS) {
      if (rest.startsWith(word)) {
        rest = rest.slice(word.length);
        continue outer;
      }
    }
    return false;
  }
  return true;
}

/** 相槌ターンに excluded=true を付ける。既にexcludedが明示されたターンは触らない。 */
export function flagFillers(turns: Turn[]): Turn[] {
  return turns.map((turn) =>
    turn.excluded === undefined && isFillerText(turn.text)
      ? { ...turn, excluded: true }
      : turn
  );
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
