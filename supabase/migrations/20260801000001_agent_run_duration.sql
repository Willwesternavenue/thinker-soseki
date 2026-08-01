-- agent_runs に所要時間を足す(引き継ぎ A-2 残タスク2)。
--
-- 2026-08-01 に LLM 呼び出しへタイムアウトを入れたが(config.LLM_TIMEOUT_SECONDS)、
-- それまで agent_runs には所要時間の記録が無く、レイテンシの議論が「体感」でしか
-- できなかった(検出層の較正走行で1本のハングが46分パイプラインを止めた件も、
-- 発生後にログから遅さの傾向を追う手段が無かった)。
--
-- 既存行への影響なし(追加列は null 許容)。rollback は列を削除するだけ。
alter table public.agent_runs
  add column duration_ms integer;

comment on column public.agent_runs.duration_ms is
  'LLM呼び出しの所要時間(ミリ秒)。call_json 内で計測。タイムアウト・リトライを含む実測値';
