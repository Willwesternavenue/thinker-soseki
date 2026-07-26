import { describe, expect, it } from "vitest";
import {
  stripTimestamps,
  segmentText,
  buildCleanTxt,
  mergeTurns,
  splitTurnAtNewline,
  revertFix,
  isFillerText,
  flagFillers,
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
    // 各文4字。8字までは同一セグメントに収まり、超える分は次へ
    expect(segmentText(text, 8)).toEqual(["一文目。二文目。", "三文目。"]);
    expect(segmentText(text, 7)).toEqual(["一文目。", "二文目。", "三文目。"]);
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
    expect(buildCleanTxt("題", null, turns)).toBe(
      "動画名：【題】\n\n本人発言: 本文\n"
    );
  });

  it("excludedのターンはスキップ", () => {
    const turns: Turn[] = [
      { speaker: "質問者", text: "はい。", fixes: [], excluded: true },
      { speaker: "本人発言", text: "本文だ。", fixes: [] },
    ];
    expect(buildCleanTxt("題", null, turns)).toBe(
      "動画名：【題】\n\n本人発言: 本文だ。\n"
    );
  });
});

describe("相槌(filler)判定", () => {
  it("相槌のみのテキストを判定する", () => {
    for (const t of [
      "はい。",
      "うん。うん。",
      "はい。はい。",
      "おお。",
      "お",
      "ええ",
      "そうそう",
      "なるほど",
      "うん、はい",
      "ああ、そう。",
      "おう",
    ]) {
      expect(isFillerText(t), t).toBe(true);
    }
  });

  it("実質的な発言は相槌と判定しない", () => {
    for (const t of [
      "そうだ。",
      "はい、分かりました",
      "うん、それでね",
      "やってたよ。",
      "本文だ。",
      "それが宇宙の意思なんだ。",
      "",
      "  ",
    ]) {
      expect(isFillerText(t), t).toBe(false);
    }
  });

  it("flagFillers: 相槌ターンにexcluded=trueを付け、他は触らない", () => {
    const turns: Turn[] = [
      { speaker: "質問者", text: "はい。", fixes: [] },
      { speaker: "本人発言", text: "本文だ。", fixes: [] },
      { speaker: "質問者", text: "うん。うん。", fixes: [] },
    ];
    const flagged = flagFillers(turns);
    expect(flagged.map((t) => t.excluded ?? false)).toEqual([
      true,
      false,
      true,
    ]);
    // 手動で戻した(excluded=false)ものを再フラグしない
    const manual: Turn[] = [
      { speaker: "質問者", text: "はい。", fixes: [], excluded: false },
    ];
    expect(flagFillers(manual)[0].excluded).toBe(false);
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
      {
        speaker: "本人発言",
        text: "前半だ\n後半だ",
        fixes: [{ from: "a", to: "b" }],
      },
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
