import { loadGlossary } from "@/lib/transcripts/glossary";
import { GlossaryClient } from "./glossary-client";

export default async function GlossaryPage() {
  const { rows } = await loadGlossary();
  const terms = rows.filter((r) => r.kind === "term");
  const rules = rows.filter((r) => r.kind === "rule");

  return (
    <div>
      <h1 className="mb-1 text-xl font-bold">用語集</h1>
      <p className="mb-6 text-sm text-stone-500">
        スクリプト整形のASR誤変換修正・話者判定の基準。読みと備考は将来の音声書き起こし
        (Whisper)のヒントにも使う。ここへの追加・修正は次回以降の整形に反映される。
      </p>
      <GlossaryClient terms={terms} rules={rules} />
    </div>
  );
}
