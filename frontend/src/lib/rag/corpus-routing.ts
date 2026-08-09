/**
 * コーパス層のルーティングと帰属表示（受入#14・#15 / 仕様 docs/CORPUS_T1_SPEC.md §6・§9）。
 *
 * 目的は一つに尽きる: **小説中の登場人物の発言を、作者本人の思想として提示しない**。
 * そのために(1)質問種別で引く範囲を変え、(2)引いたものが誰の発言かを常に持ち回り、
 * (3)直接の原典が無いときは留保する。
 *
 * ⚠️ INDEXES / ROUTES は worker 側 `worker/src/aozora/routing.py` と対になる。
 * 片方だけ変えると、生成と回答で別の規則が働く。`corpus-routing.test.ts` が
 * 並びとフラグを固定しているので、変える場合は両方を同時に変えること。
 */

import charactersJson from "./characters.json";
import type { QueryKind } from "./types";

/**
 * コーパス層のタグ。コーパス層より前に投入した原典では未設定(null / undefined)。
 * 省略可能にしてあるのは、既存の `EvidenceChunk` をそのまま渡せるようにするため。
 */
export type CorpusTagged = {
  corpus_role?: string | null;
  speaker_role?: string | null;
};

export type CorpusRouteKind = "thought" | "creative" | "character";

export type RouteStep = {
  index: string;
  corpusRoles: string[];
  speakerRoles: string[] | null;
  /** 小説由来を出すときは「作者本人の発言ではない」と明示する(指示書§10.1) */
  requires_attribution_notice: boolean;
  /** 思想を創作へ持ち込むのは Bridge Rule を介する場合のみ(指示書§12.2) */
  requires_bridge_rule: boolean;
  /** 比較対象としてのみ使う(主根拠にしない) */
  comparison_only: boolean;
};

const INDEXES: Record<string, { corpusRoles: string[]; speakerRoles: string[] | null }> = {
  author_thought_core: { corpusRoles: ["core_thought"], speakerRoles: ["author_direct"] },
  author_thought_support: {
    corpusRoles: ["supporting_thought"],
    speakerRoles: ["author_direct"],
  },
  creative_grammar: { corpusRoles: ["creative_grammar"], speakerRoles: null },
  // ⚠️ corpus_role='character_judgment' だけを条件にすると**永久に空になる**。
  // corpus_role は文書単位の単一値で、取り込みは小説を narrative_reference に
  // 割り当てるため、その役割が付く文書が存在しない（Phase C で長編を入れても
  // 解消しない）。作中人物の判断は小説の中にあり、誰の発言かはチャンクの
  // speaker_role が持っている。仕様§5 の定義を実データに合わせて改めた。
  character_judgment: {
    corpusRoles: ["character_judgment", "narrative_reference"],
    speakerRoles: ["character"],
  },
  narrative_reference: { corpusRoles: ["narrative_reference"], speakerRoles: null },
  style_reference: { corpusRoles: ["style_reference"], speakerRoles: null },
};

function step(
  index: string,
  flags: { attribution?: boolean; bridge?: boolean; comparison?: boolean } = {}
): RouteStep {
  return {
    index,
    ...INDEXES[index],
    requires_attribution_notice: flags.attribution ?? false,
    requires_bridge_rule: flags.bridge ?? false,
    comparison_only: flags.comparison ?? false,
  };
}

const ROUTES: Record<CorpusRouteKind, RouteStep[]> = {
  // 思想: 本人の直接発言から。小説は比較・補助として最後に、明示付きで
  thought: [
    step("author_thought_core"),
    step("author_thought_support"),
    step("creative_grammar"),
    step("narrative_reference", { attribution: true }),
  ],
  // 創作: 作風の論から。思想は Bridge Rule を介する場合のみ
  creative: [
    step("creative_grammar"),
    step("narrative_reference"),
    step("style_reference"),
    step("author_thought_core", { bridge: true }),
  ],
  // 人物: 作中人物の判断から。作者思想は比較対象としてのみ
  character: [
    step("character_judgment", { attribution: true }),
    step("narrative_reference", { attribution: true }),
    step("author_thought_core", { comparison: true }),
  ],
};

/**
 * 既知の登場人物。人物質問の判定に使う。
 *
 * 語彙の出所は characters.json — worker 側 `worker/src/aozora/characters.json` の
 * 複製で、テストが同期を検証する。チャンク側の `character_id`(Pass2 が付ける)と
 * ここで検出するIDが同じ辞書から出ることで、人物での絞り込みが結合できる。
 *
 * ⚠️ 名前が挙がらない質問を人物質問にしない。誤判定すると作者の思想が主根拠から外れる。
 */
type CharacterEntry = { names: string[]; detect_names?: string[]; work: string };
const CHARACTERS = charactersJson as Record<string, CharacterEntry>;

// 検出に使う表記。「先生」のような一般名詞は detect_names に複合語だけを載せて
// 質問検出から外す(タグ付けの語彙 names とは別の関心事。worker 側と同じ規則)
const detectNames = (entry: CharacterEntry): string[] =>
  entry.detect_names ?? entry.names;

// 1文字のラテン文字(K)の境界。前後が英数字なら英単語の一部(OK / KPI / 4K)
const BOUNDARY = "[A-Za-z0-9Ａ-Ｚａ-ｚ０-９]";

function matchesName(name: string, query: string): boolean {
  if (name.length === 1 && /[A-Za-zＡ-Ｚａ-ｚ]/.test(name)) {
    const code = name.charCodeAt(0);
    const half = code >= 0xff21 ? String.fromCharCode(code - 0xfee0) : name;
    const full = code < 0xff00 ? String.fromCharCode(code + 0xfee0) : name;
    return new RegExp(`(?<!${BOUNDARY})[${half}${full}](?!${BOUNDARY})`).test(query);
  }
  return query.includes(name);
}

export const KNOWN_CHARACTERS: Record<string, string> = Object.fromEntries(
  Object.entries(CHARACTERS).flatMap(([id, entry]) =>
    detectNames(entry).map((name) => [name, id])
  )
);

export function detectCharacter(query: string): string | null {
  for (const [id, entry] of Object.entries(CHARACTERS)) {
    if (detectNames(entry).some((name) => matchesName(name, query))) return id;
  }
  return null;
}

/**
 * 既存の `QueryKind`(8値)を、コーパスの検索ルート(3値)へ写す。
 *
 * ⚠️ 既存 `QueryKind` の `"creative"` は「創作依頼」の意味で、創作モード
 * (`/creative` の作品生成)とは**別概念**。ここでは前者を扱う。
 */
export function corpusRouteKind(queryKind: QueryKind, query: string): CorpusRouteKind {
  // 創作依頼が先。人物名が入っていても、書かせることが主目的なので創作ルートで引く
  if (queryKind === "creative") return "creative";
  if (detectCharacter(query)) return "character";
  return "thought";
}

export function corpusRouteFor(kind: CorpusRouteKind): RouteStep[] {
  return ROUTES[kind] ?? ROUTES.thought;
}

/**
 * 実際に検索で使う corpus_role の集合。
 *
 * **Bridge Rule を要する段は外す**。創作規則(L3)は未実装なので、思想チャンクを
 * 素通しで創作依頼へ渡さないためにここで落とす(仕様§6の禁止事項)。
 */
export function retrievalFiltersFor(kind: CorpusRouteKind): {
  corpusRoles: string[];
  speakerRoles: string[] | null;
} {
  const usable = corpusRouteFor(kind).filter((s) => !s.requires_bridge_rule);
  return {
    // 段どうしで役割が重なることがある(人物ルートの character_judgment と
    // narrative_reference)。検索条件としては1回でよい
    corpusRoles: [...new Set(usable.flatMap((s) => s.corpusRoles))],
    // speaker_role は段ごとに違うため、検索では絞らずチャンク側の値で判断する
    // (絞ると narrative_reference の語り手文が丸ごと落ちる)
    speakerRoles: null,
  };
}

/** 小説・評伝など、作者本人の直接発言ではない corpus_role。 */
const INDIRECT_CORPUS_ROLES = new Set([
  "narrative_reference",
  "character_judgment",
  "style_reference",
]);

const SPEAKER_NOTICES: Record<string, string> = {
  character: "作品内の登場人物の発言であり、本人の直接の主張ではない",
  narrator: "作品の語り手の文であり、本人の直接の主張ではない",
  quoted_person: "本人が引用した第三者の発言であり、本人の主張ではない",
};

/**
 * このチャンクを回答に使うとき、帰属の明示が要るか(指示書§10.1)。
 *
 * `corpus_role` も `speaker_role` も無いチャンクは、コーパス層より前に投入された
 * 既存の原典。従来どおり注記を付けない(既存の思想モードの挙動を変えない)。
 */
export function attributionFor(chunk: CorpusTagged): string | null {
  if (chunk.speaker_role && SPEAKER_NOTICES[chunk.speaker_role]) {
    return SPEAKER_NOTICES[chunk.speaker_role];
  }
  if (chunk.corpus_role && INDIRECT_CORPUS_ROLES.has(chunk.corpus_role)) {
    return "作品由来の記述であり、本人の直接の主張ではない";
  }
  return null;
}

/** 作者本人の直接発言として扱えるチャンクか。 */
function isAuthorDirect(chunk: CorpusTagged) {
  return attributionFor(chunk) === null;
}

/**
 * 思想質問では、関連度が高くても小説を作者の発言より後ろへ下げる。
 *
 * ベクトル検索は文体の似ている小説をよく引く。順序をそのまま使うと、
 * **文体の一致が思想の一致として提示される**(指示書§18)。
 * 人物質問では下げない — その場合は人物の発言こそが根拠だから。
 */
export function rankByRoute<T extends CorpusTagged & { score: number }>(
  chunks: T[],
  kind: CorpusRouteKind
): T[] {
  if (kind === "character") return chunks;
  // 呼び出し側がスコア順に並べてあるかに依存しない(並べ替えの前提を外へ出さない)
  const byScore = (a: T, b: T) => b.score - a.score;
  return [
    ...chunks.filter(isAuthorDirect).sort(byScore),
    ...chunks.filter((c) => !isAuthorDirect(c)).sort(byScore),
  ];
}

/**
 * 「直接の原典」と数えるための関連度の下限。
 *
 * ベクトル検索は関連が無くても常に上位N件を返すため、ヒットの有無だけでは
 * 「原典がある」ことにならない。閾値が無いと**現代の質問にも必ず原典があること
 * になり、留保が一度も発火しない**。
 *
 * 実測(Phase A 483チャンク・text-embedding-3-small):
 *   「現代日本の開化について」 0.68 / 「生成AIをどう思うか」 0.35 /
 *   「ブロックチェーンの規制について」 0.21 / 「暗号資産の確定申告」 0.24
 * 原典にある話題と現代語の間がはっきり空くので、その間に置く。
 */
export const MIN_DIRECT_SOURCE_SCORE = 0.45;

/**
 * 関連度として意味のある `score` を持つ根拠だけを残す。
 *
 * ⚠️ **`score` は経路ごとに意味が違う。** 閾値と比べてよいのはベクトル検索
 * (コサイン類似度)だけである。
 *
 * | 経路 | score の中身 |
 * |---|---|
 * | vector  | コサイン類似度(0〜1)。`MIN_DIRECT_SOURCE_SCORE` はこれ用に較正した |
 * | linked  | `strength` の変換値 0.6/0.8/1.0。**質問との関連度ではない** |
 * | keyword | 固定 0.5。PGroonga のスコアは正規化されていないため捨てている |
 *
 * linked と keyword は必ず閾値を超えるので、混ぜると「原典あり」が常に真になる。
 * 2026-08-09 実測: 「森鴎外の作品について」で、鴎外への言及がコーパスに1件も
 * 無いのに、無関係な『創作家の態度』が score=0.8 で入り留保が発火せず、
 * 鴎外の作風について根拠の無い断定を本人の声で述べた。
 */
function relevanceScored<T extends { score?: number; origin?: string }>(
  evidence: T[]
): T[] {
  return evidence.filter(
    (c) =>
      (c.origin ?? "vector") === "vector" &&
      (c.score ?? 0) >= MIN_DIRECT_SOURCE_SCORE
  );
}

/** そのルートにとって「直接の原典」と言える根拠があるか。 */
export function hasDirectSource(
  evidence: Array<CorpusTagged & { score?: number; origin?: string }>,
  kind: CorpusRouteKind
): boolean {
  const relevant = relevanceScored(evidence);
  if (kind === "character") {
    // 人物質問は、その人物の発言そのものが直接の原典
    return relevant.some(
      (c) => c.speaker_role === "character" || c.corpus_role === "character_judgment"
    );
  }
  return relevant.some(isAuthorDirect);
}

/**
 * 留保の理由(指示書§13)。直接の原典が無いまま断定させないための記録。
 *
 * 「小説しか無い思想質問」も留保する。作中人物の言葉から作者の思想を
 * 名乗らせないための、最後の歯止め。
 */
export function decideAbstention(options: {
  kind: CorpusRouteKind;
  evidence: Array<CorpusTagged & { score?: number; origin?: string }>;
  /**
   * 質問の主題語と、それがコーパスに実在するか。取れなかったときは null。
   *
   * ⚠️ **ベクトルの関連度だけでは足りない。** 2026-08-09 実測:
   *   「森鴎外の作品について」 最高 0.443 / 鴎外への言及 0件   → 留保すべき
   *   「明治維新は…」         最高 0.445 / 維新17・開化32件   → 留保すべきでない
   * 分けたい2つが 0.002 差で、閾値をどこに置いても分離できない。
   * 「その語が原典に出てくるか」は、スコアが持っていない情報を持っている。
   */
  subject?: { term: string; foundInCorpus: boolean } | null;
}): string | null {
  const { kind, evidence, subject } = options;

  // 主題語が原典に一度も出てこないなら、関連度が足りていても断定させない。
  // ここを抜けると、実在の人物について根拠の無い記述を本人の声で述べることになる
  if (subject && !subject.foundInCorpus) {
    return (
      `「${subject.term}」を扱った原典が見つからなかった。` +
      "原典に基づく主張として述べていない。"
    );
  }

  if (hasDirectSource(evidence, kind)) return null;

  const relevant = relevanceScored(evidence);
  if (relevant.length === 0) {
    return "この主題を直接扱った原典が見つからなかった。原典に基づく主張として述べていない。";
  }
  return (
    "直接の原典が無く、作品由来の記述しか見つからなかった。" +
    "作品内の表現を本人の主張として扱っていない。"
  );
}

/**
 * 直接の原典として実際に使えた source_id(受入#14)。
 *
 * `hasDirectSource` と同じ条件で数える。片方だけ緩いと、留保を出しているのに
 * trace には原典が並ぶという矛盾した記録になり、監査の役に立たなくなる。
 */
export function directSourceIds(
  evidence: Array<CorpusTagged & { source_id: string; score?: number; origin?: string }>,
  kind: CorpusRouteKind
): string[] {
  const relevant = new Set(relevanceScored(evidence));
  const qualifies = (c: CorpusTagged & { score?: number }) =>
    relevant.has(c as never) &&
    (kind === "character"
      ? c.speaker_role === "character" || c.corpus_role === "character_judgment"
      : isAuthorDirect(c));
  return [...new Set(evidence.filter(qualifies).map((c) => c.source_id))];
}

export type RetrievalRouteTrace = {
  kind: CorpusRouteKind;
  indexes: string[];
  character_id: string | null;
  counts_by_corpus_role: Record<string, number>;
  attributed_chunk_ids: string[];
};

/** `answer_traces.retrieval_route` に残す内容(どのルートで何を引いたか)。 */
export function buildRetrievalRoute(options: {
  kind: CorpusRouteKind;
  characterId: string | null;
  evidence: Array<CorpusTagged & { chunk_id: string }>;
}): RetrievalRouteTrace {
  const counts: Record<string, number> = {};
  for (const c of options.evidence) {
    // role未設定も数える。コーパス層へ移行しきれていない原典を見つけられるように
    const key = c.corpus_role ?? "unclassified";
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return {
    kind: options.kind,
    indexes: corpusRouteFor(options.kind).map((s) => s.index),
    character_id: options.characterId,
    counts_by_corpus_role: counts,
    attributed_chunk_ids: options.evidence
      .filter((c) => attributionFor(c) !== null)
      .map((c) => c.chunk_id),
  };
}
