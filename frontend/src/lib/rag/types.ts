// 回答時RAGフローの型定義(仕様7章)

export type QueryKind =
  | "thought"
  | "life_advice"
  | "fact"
  | "person_or_work"
  | "health_or_medical"
  | "politics_or_current_affairs"
  | "creative"
  | "mixed";

export type RoutingMethod = "vector" | "llm_classifier" | "fallback" | "none";

export type Persona = {
  person_id: string;
  display_name: string;
  system_prompt: string;
  first_person: string;
  banned_terms_exact: string[];
  banned_terms_contextual: string[];
  style_rules: Record<string, unknown>;
  quote_policy: { max_quote_length?: number; require_verbatim?: boolean };
  safety_policy: Record<string, unknown>;
  fallback_card_id: string | null;
};

export type ThoughtCard = {
  card_id: string;
  thought_id: string;
  title: string;
  importance: string;
  core_claim: string | null;
  distinctions: Array<{ not: string; but: string }>;
  answer_policy: string[];
  prohibitions: string[];
  related_thought_ids: string[];
  representative_chunk_ids: string[];
};

export type Classification = {
  queryKind: QueryKind;
  needsThoughtCards: boolean;
};

export type RouteResult = {
  primaryThoughtId: string | null;
  secondaryThoughtIds: string[];
  confidence: number;
  routingMethod: RoutingMethod;
  fallbackCardUsed: boolean;
  misunderstandingSignal: boolean; // negative_aliasesヒット(仕様7.4 Stage1)
};

export type EvidenceChunk = {
  chunk_id: string;
  source_id: string;
  source_title?: string;
  chapter_title: string | null;
  section_title: string | null;
  source_page: number | null;
  printed_page: number | null;
  text: string;
  summary: string | null;
  verbatim: boolean;
  evidence_role: string | null; // links由来の場合のみ
  quote_allowed: boolean; // links由来の場合のみ
  score: number;
  origin: "linked" | "vector" | "keyword";
};

export type GuardResult = {
  passed: boolean;
  exact_match_hits: string[];
  judge_result: "pass" | "fail" | "skipped";
  judge_issues?: string[];
  regenerated: boolean;
  safe_answer_used: boolean;
};

export type TopHit = {
  source_id: string;
  source_title: string | null;
  chunk_id: string;
  score: number;
  evidence_role: string | null;
  verbatim: boolean;
  quote_allowed: boolean;
  source_page: number | null;
  printed_page: number | null;
  text_excerpt: string;
};

export type AnswerTrace = {
  query_kind: QueryKind;
  routing_method: RoutingMethod;
  fallback_card_used: boolean;
  selected_thought_ids: string[];
  retrieved_card_ids: string[];
  retrieved_chunk_ids: string[];
  top_hits: TopHit[];
  guard_result: GuardResult;
};
