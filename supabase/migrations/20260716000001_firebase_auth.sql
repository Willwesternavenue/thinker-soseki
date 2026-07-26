-- Firebase Auth移行(2026-07-16)
-- 認証をSupabase Auth → Firebase Authへ置き換えたことに伴うDB側の変更。
-- - ユーザーIDは auth.users(uuid) ではなく Firebase UID(text)になる
-- - アプリからのアクセスは全てサーバー側の service_role 経由に一本化(anonキー廃止)
-- - 権限チェックはアプリ層(requireAdmin / requireUser + user_id絞り込み)で行う
-- - RLSは全テーブルで有効のまま「ポリシー無し」にする
--   = anon / authenticated キーではどのテーブルにも一切アクセスできない(deny-all)。
--     service_role はRLSをバイパスするため影響なし。キー漏洩時の防御層として残す。

-- ── 1. 旧RLSポリシーを削除(auth.uid()/is_admin()はFirebase移行後は機能しない) ──

-- 20260704000004_rls.sql
drop policy if exists "admin all" on public.personas;
drop policy if exists "admin all" on public.sources;
drop policy if exists "admin all" on public.source_chunks;
drop policy if exists "admin all" on public.chunk_distillations;
drop policy if exists "admin all" on public.source_distillations;
drop policy if exists "admin all" on public.thought_cards;
drop policy if exists "admin all" on public.thought_card_revisions;
drop policy if exists "admin all" on public.thought_questions;
drop policy if exists "admin all" on public.thought_evidence_links;
drop policy if exists "admin all" on public.concept_aliases;
drop policy if exists "admin all" on public.ingestion_jobs;
drop policy if exists "admin all" on public.agent_runs;
drop policy if exists "admin read" on public.answer_traces;
drop policy if exists "admin all" on public.evaluation_logs;
drop policy if exists "own profile read" on public.user_profiles;
drop policy if exists "admin manage profiles" on public.user_profiles;
drop policy if exists "own sessions" on public.chat_sessions;
drop policy if exists "admin read sessions" on public.chat_sessions;
drop policy if exists "own messages" on public.chat_messages;
drop policy if exists "admin read messages" on public.chat_messages;

-- 20260704000005_storage.sql
drop policy if exists "admin read originals" on storage.objects;
drop policy if exists "admin insert originals" on storage.objects;
drop policy if exists "admin update originals" on storage.objects;
drop policy if exists "admin delete originals" on storage.objects;

-- 20260704000008_worker_heartbeat.sql
drop policy if exists "admin read heartbeat" on public.worker_heartbeats;

-- 20260704000010_distillation_jobs.sql
drop policy if exists "admin all" on public.distillation_jobs;

-- 20260707000001_transcript_drafts.sql
drop policy if exists "admin all" on public.transcript_drafts;

-- 20260707000002_glossary_terms.sql
drop policy if exists "admin all" on public.glossary_terms;

-- 20260713000002_judgment_rules.sql
drop policy if exists "admin all" on public.judgment_rules;
drop policy if exists "admin all" on public.judgment_rule_versions;
drop policy if exists "admin all" on public.judgment_rule_evidence;
drop policy if exists "admin all" on public.judgment_rule_examples;
drop policy if exists "admin all" on public.judgment_rule_reviews;

-- ロール判定関数(ポリシー削除後は未参照)
drop function if exists public.is_admin();

-- ── 2. ユーザーID列を Firebase UID(text)へ ──
-- 既存データのuuid値はtext表現のまま残る。旧Supabaseユーザーのセッションを
-- 新Firebaseアカウントへ引き継ぐ場合は docs/FIREBASE_MIGRATION.md のSQLで
-- user_id を旧uuid → 新UID に置換する。

alter table public.user_profiles
  drop constraint if exists user_profiles_user_id_fkey;
alter table public.user_profiles
  alter column user_id type text using user_id::text;

alter table public.chat_sessions
  drop constraint if exists chat_sessions_user_id_fkey;
alter table public.chat_sessions
  alter column user_id type text using user_id::text;

alter table public.thought_card_revisions
  drop constraint if exists thought_card_revisions_edited_by_fkey;
alter table public.thought_card_revisions
  alter column edited_by type text using edited_by::text;
