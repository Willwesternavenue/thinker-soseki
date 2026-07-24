import type { Turn } from "./prep";

/** 用語集(kind='term')と使い分けルール(kind='rule')からsystemプロンプトを組み立てる。
 * termsとrulesは glossary_terms テーブル(loadGlossary)から取得する。 */
export function buildSegmentSystem(terms: string[], rules: string[]): string {
  const glossaryLine = terms.length
    ? `\n用語集(正しい表記): ${terms.join("、")}`
    : "";
  const rulesBlock = rules.length
    ? `\n\n表記の使い分け(文脈で判断):\n${rules.map((r) => `- ${r}`).join("\n")}`
    : "";
  return `あなたはYouTube動画の自動文字起こしを整形する編集者である。
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
4. 入力の本文をすべてターンに含める(取りこぼし禁止)${glossaryLine}${rulesBlock}

出力はJSONのみ:
{"turns": [{"speaker": "本人発言" | "質問者" | "?", "text": "...", "fixes": [{"from": "...", "to": "..."}]}]}`;
}

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
            .filter(
              (f) => typeof f?.from === "string" && typeof f?.to === "string"
            )
            .map((f) => ({ from: f.from as string, to: f.to as string }))
        : [],
    }))
    .filter((t) => t.text.trim().length > 0);
}
