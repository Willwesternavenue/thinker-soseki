-- Bridge Rule(C-T6)。思想と創作を繋ぐ規則を rule_scope に追加する。
-- 正本仕様: docs/CORPUS_T1_SPEC.md §6・§14 / 上位指示 §12.2。
--
-- 思想チャンクを登場人物の台詞へそのまま注入することは禁止されている(仕様§6)。
-- 一方で「作者の思想が作品にどう現れるか」は創作に要る。その橋渡しを
-- **暗黙にやらせず、明示された規則を介してのみ許す**ための値。
--
-- 既存テーブルへの変更だが、check制約への**値の追加のみ**で既存行に影響しない
-- (既存の3値はそのまま有効)。rollback は制約を元の3値へ戻すだけ。

alter table public.judgment_rules
  drop constraint judgment_rules_rule_scope_check;

alter table public.judgment_rules
  add constraint judgment_rules_rule_scope_check check (rule_scope in (
    'judgment',         -- 判断の文法(既存)
    'dialogue',         -- 対話の運び方(既存)
    'response_policy',  -- 回答方針(既存)
    -- 追加: 思想 → 創作 の橋渡し。どの思想カードが、どの創作カードとして
    -- 作品に現れるかを明示する。これが無い限り思想は創作へ渡らない
    'bridge_rule'
  ));

comment on column public.judgment_rules.rule_scope is
  'judgment / dialogue / response_policy に加え bridge_rule(C-T6)。'
  'bridge_rule は思想カードと創作カードを結ぶ規則で、'
  '思想チャンクを作中人物の台詞へ直接注入させないための唯一の経路。';
