import { createClient } from "@/lib/supabase/server";

export type GlossaryRow = {
  id: string;
  kind: "term" | "rule";
  content: string;
  reading: string | null;
  note: string | null;
};

/** glossary_terms を読み込み、term(正しい表記)と rule(使い分け)に分けて返す。 */
export async function loadGlossary(): Promise<{
  terms: string[];
  rules: string[];
  rows: GlossaryRow[];
}> {
  const supabase = await createClient();
  const { data } = await supabase
    .from("glossary_terms")
    .select("id, kind, content, reading, note")
    .order("kind", { ascending: true })
    .order("content", { ascending: true });
  const rows = (data ?? []) as GlossaryRow[];
  return {
    terms: rows.filter((r) => r.kind === "term").map((r) => r.content),
    rules: rows.filter((r) => r.kind === "rule").map((r) => r.content),
    rows,
  };
}
