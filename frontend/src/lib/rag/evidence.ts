import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { embedText } from "@/lib/embedding";
import type { EvidenceChunk } from "./types";

const MAX_EVIDENCE = 10; // LLMへ渡す原典の上限(伝記質問で複数エピソードを拾えるよう拡大)
const MAX_PER_SOURCE = 5; // 同一原典からの上限(1冊に集中する伝記章で複数箇所を拾えるよう緩和)
const MAX_PER_ROLE = 3;

/** A系統: thought_evidence_links(approved)から代表チャンクを取得(仕様7.6)。 */
export async function fetchLinkedEvidence(
  db: SupabaseClient,
  personId: string,
  thoughtIds: string[]
): Promise<EvidenceChunk[]> {
  if (!thoughtIds.length) return [];
  const { data } = await db
    .from("thought_evidence_links")
    .select(
      // speaker_role / corpus_role も取る。これが無いと承認リンク由来の根拠だけ
      // 帰属が判定できず(誰の発言か不明のまま回答へ入り)、trace でも
      // unclassified として数えられてしまう
      "chunk_id, evidence_role, strength, quote_allowed, source_chunks(chunk_id, source_id, chapter_title, section_title, source_page, printed_page, text, summary, verbatim, status, speaker_role), sources(title, corpus_role)"
    )
    .eq("person_id", personId)
    .eq("status", "approved")
    .in("thought_id", thoughtIds);

  const strengthScore = { high: 1.0, medium: 0.8, low: 0.6 } as Record<string, number>;
  const result: EvidenceChunk[] = [];
  for (const link of data ?? []) {
    const chunk = Array.isArray(link.source_chunks)
      ? link.source_chunks[0]
      : link.source_chunks;
    const source = Array.isArray(link.sources) ? link.sources[0] : link.sources;
    if (!chunk || chunk.status !== "active") continue;
    result.push({
      chunk_id: chunk.chunk_id,
      source_id: chunk.source_id,
      source_title: source?.title,
      chapter_title: chunk.chapter_title,
      section_title: chunk.section_title,
      source_page: chunk.source_page,
      printed_page: chunk.printed_page,
      text: chunk.text,
      summary: chunk.summary,
      verbatim: chunk.verbatim,
      evidence_role: link.evidence_role,
      quote_allowed: link.quote_allowed,
      score: strengthScore[link.strength] ?? 0.7,
      origin: "linked",
      speaker_role: (chunk.speaker_role as string | null) ?? null,
      corpus_role: (source?.corpus_role as string | null) ?? null,
    });
  }
  return result;
}

/** B系統(1): pgvectorでthought_id絞り込み検索(仕様7.6)。 */
export async function searchSourceChunks(
  db: SupabaseClient,
  personId: string,
  query: string,
  thoughtIds: string[]
): Promise<EvidenceChunk[]> {
  if (!thoughtIds.length) return [];
  const embedding = await embedText(query);
  const { data } = await db.rpc("match_source_chunks_by_thoughts", {
    query_embedding: JSON.stringify(embedding),
    thought_ids: thoughtIds,
    target_person_id: personId,
    match_count: 10,
  });
  return ((data ?? []) as Record<string, unknown>[]).map((r) => toChunk(r, "vector"));
}

/** B系統(2): PGroonga全文検索(仕様7.6)。 */
export async function keywordSearchChunks(
  db: SupabaseClient,
  personId: string,
  query: string,
  thoughtIds: string[]
): Promise<EvidenceChunk[]> {
  if (!thoughtIds.length) return [];
  const { data, error } = await db.rpc("search_source_chunks_fulltext", {
    query_text: query,
    thought_ids: thoughtIds,
    target_person_id: personId,
    match_count: 10,
  });
  if (error) return []; // クエリ構文エラー等は補助検索なので握りつぶす
  return ((data ?? []) as Record<string, unknown>[]).map((r) => {
    const chunk = toChunk(r, "keyword");
    // PGroongaスコアは正規化されていないため控えめな固定重みに
    chunk.score = 0.5;
    return chunk;
  });
}

/**
 * 思想IDに紐づかない質問(fact / person_or_work 等)向けの原典直検索。
 * 思想カードが無くても、その人物の全チャンクをベクトル+PGroongaで検索して根拠にする。
 * これがないと経歴・会社情報など、カード化されていない事実が一切取得できない。
 */
export async function retrieveUnscopedEvidence(
  db: SupabaseClient,
  personId: string,
  query: string
): Promise<EvidenceChunk[]> {
  const embedding = await embedText(query);
  const [vectorRes, keywordRes] = await Promise.all([
    db.rpc("match_source_chunks_all", {
      query_embedding: JSON.stringify(embedding),
      target_person_id: personId,
      match_count: 10,
    }),
    db.rpc("search_source_chunks_fulltext", {
      query_text: query,
      thought_ids: null,
      target_person_id: personId,
      match_count: 10,
    }),
  ]);
  const vectorHits = ((vectorRes.data ?? []) as Record<string, unknown>[]).map((r) =>
    toChunk(r, "vector")
  );
  const keywordHits = keywordRes.error
    ? []
    : ((keywordRes.data ?? []) as Record<string, unknown>[]).map((r) => {
        const chunk = toChunk(r, "keyword");
        chunk.score = 0.5;
        return chunk;
      });
  return mergeEvidence([], vectorHits, keywordHits);
}

/**
 * ルートで絞った原典検索(受入#15)。
 *
 * 既存の `retrieveUnscopedEvidence` を**置き換えない**。コーパス層より前に投入した
 * 原典は `corpus_role` が null で、絞り込むと丸ごと落ちるため。両方引いて統合する。
 * ここは「そのルートで引くべきものを確実に取る」ための追加検索。
 */
export async function retrieveRoutedEvidence(
  db: SupabaseClient,
  personId: string,
  query: string,
  corpusRoles: string[]
): Promise<EvidenceChunk[]> {
  if (corpusRoles.length === 0) return [];
  const embedding = await embedText(query);
  const [vectorRes, keywordRes] = await Promise.all([
    db.rpc("match_source_chunks_all", {
      query_embedding: JSON.stringify(embedding),
      target_person_id: personId,
      match_count: 10,
      target_corpus_roles: corpusRoles,
      // 版違いで同じ段落を重複して返さない(仕様§1.3)
      primary_edition_only: true,
    }),
    db.rpc("search_source_chunks_fulltext", {
      query_text: query,
      thought_ids: null,
      target_person_id: personId,
      match_count: 10,
      target_corpus_roles: corpusRoles,
      primary_edition_only: true,
    }),
  ]);
  // 拡張RPCが未適用の環境でも回答は続ける(絞り込み無しの検索結果で答える)
  if (vectorRes.error && keywordRes.error) return [];

  const vectorHits = ((vectorRes.data ?? []) as Record<string, unknown>[]).map((r) =>
    toChunk(r, "vector")
  );
  const keywordHits = keywordRes.error
    ? []
    : ((keywordRes.data ?? []) as Record<string, unknown>[]).map((r) => {
        const chunk = toChunk(r, "keyword");
        chunk.score = 0.5;
        return chunk;
      });
  return mergeEvidence([], vectorHits, keywordHits);
}

function toChunk(r: Record<string, unknown>, origin: "vector" | "keyword"): EvidenceChunk {
  return {
    chunk_id: r.chunk_id as string,
    source_id: r.source_id as string,
    chapter_title: (r.chapter_title as string) ?? null,
    section_title: (r.section_title as string) ?? null,
    source_page: (r.source_page as number) ?? null,
    printed_page: (r.printed_page as number) ?? null,
    text: r.text as string,
    summary: (r.summary as string) ?? null,
    verbatim: Boolean(r.verbatim),
    evidence_role: null,
    quote_allowed: false,
    score: (r.similarity as number) ?? 0.5,
    origin,
    // 拡張RPCが返すコーパス層のタグ。旧シグネチャの呼び出しでは undefined
    speaker_role: (r.speaker_role as string | null) ?? null,
    corpus_role: (r.corpus_role as string | null) ?? null,
  };
}

/** Evidence統合・重複排除(仕様7.7)。linked > vector > keyword の優先で残す。 */
export function mergeEvidence(
  linked: EvidenceChunk[],
  vectorHits: EvidenceChunk[],
  keywordHits: EvidenceChunk[]
): EvidenceChunk[] {
  const seen = new Map<string, EvidenceChunk>();
  for (const chunk of [...linked, ...vectorHits, ...keywordHits]) {
    const existing = seen.get(chunk.chunk_id);
    if (!existing) {
      seen.set(chunk.chunk_id, chunk);
    } else if (existing.origin !== "linked" && chunk.origin === "linked") {
      seen.set(chunk.chunk_id, chunk);
    }
  }
  return [...seen.values()].sort((a, b) => b.score - a.score);
}

/** 多様性制御(仕様7.7): 同一source・同一roleに偏らせず3〜8件に絞る。 */
export function diversifyEvidence(hits: EvidenceChunk[]): EvidenceChunk[] {
  const perSource = new Map<string, number>();
  const perRole = new Map<string, number>();
  const result: EvidenceChunk[] = [];
  for (const chunk of hits) {
    if (result.length >= MAX_EVIDENCE) break;
    const sourceCount = perSource.get(chunk.source_id) ?? 0;
    if (sourceCount >= MAX_PER_SOURCE) continue;
    // roleの偏り制御はrole割当済み(linked由来)のみ対象。未割当のvector/keywordヒットは絞らない
    if (chunk.evidence_role) {
      const roleCount = perRole.get(chunk.evidence_role) ?? 0;
      if (roleCount >= MAX_PER_ROLE) continue;
      perRole.set(chunk.evidence_role, roleCount + 1);
    }
    result.push(chunk);
    perSource.set(chunk.source_id, sourceCount + 1);
  }
  return result;
}

/**
 * 引用可能フィルタ(仕様7.8)。コードで強制:
 * evidence_role = quote かつ verbatim = true かつ quote_allowed = true
 */
export function filterQuotableChunks(hits: EvidenceChunk[]): EvidenceChunk[] {
  return hits.filter(
    (c) => c.evidence_role === "quote" && c.verbatim === true && c.quote_allowed === true
  );
}

/**
 * 主題語が原典に実在するかを確かめる(留保の判定用)。
 *
 * ⚠️ ベクトルの関連度では足りないために足した検査である。2026-08-09 実測:
 *   「森鴎外の作品について」 最高 0.443 / 鴎外への言及 0件
 *   「明治維新は…」         最高 0.445 / 維新17・開化32件
 * 分けたい2つが 0.002 差で、閾値では分離できない。
 * 「その語が原典に出てくるか」だけがこの2つを分ける。
 *
 * 取得に失敗したときは true を返す(フェイルオープン)。DBの一時的な失敗で
 * 「原典が無い」と誤って断定させないため。
 */
export async function subjectFoundInCorpus(
  db: SupabaseClient,
  personId: string,
  subject: string
): Promise<boolean> {
  const term = subject.trim();
  if (!term) return true;
  try {
    const { data, error } = await db.rpc("search_source_chunks_fulltext", {
      query_text: term,
      target_person_id: personId,
      match_count: 1,
    });
    if (error) return true;
    return (data as unknown[] | null)?.length ? true : false;
  } catch {
    return true;
  }
}
