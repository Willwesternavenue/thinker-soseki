-- L3 shadowモード(仕様: docs/judgment_rules_spec_v0_2.md 4.3)。
-- 回答パイプラインに並走したJudgment Rule発火判定の痕跡(L4)を保存する。
-- shadowは回答に一切影響しない。draft規則も判定対象(レビュー用の発火実績を集めるため)。
alter table public.answer_traces
  add column l3_shadow jsonb;

comment on column public.answer_traces.l3_shadow is
  'L3発火判定のshadow実行痕跡: {execution_status, mode, model, rules_evaluated, '
  'fired:[{rule_id, rule_version_id, matched_trigger_conditions, exception_applied, reason}], '
  'rejected:[rule_id], duration_ms}。回答には未使用(shadow)';
