-- 理由一致4分類(Regression Suite仕様v0.2 3.2)。
-- A: 結論も理由も近い / B: 結論は近いが理由が違う / C: 理由の方向は近いが結論が違う / D: 結論も理由も違う
-- B判定がL3 Judgment Rule不足を発見する最重要データになるため、jsonbではなく専用列で持つ(B判定の抽出クエリ用)。
alter table public.evaluation_logs
  add column reason_alignment text check (reason_alignment in ('A', 'B', 'C', 'D')),
  add column reason_alignment_note text;

comment on column public.evaluation_logs.reason_alignment is
  '理由一致4分類。B=結論は近いが理由が違う(L3規則候補の主要データ源)';
comment on column public.evaluation_logs.reason_alignment_note is
  '理由がどう違うか。L3 Judgment Rule候補の種として使う';
