import "server-only";
import { createClient as createSupabaseClient } from "@supabase/supabase-js";
import { SUPABASE_URL } from "@/lib/const";

/**
 * service_role クライアント(RLSバイパス)。
 * Firebase Auth移行後、SupabaseはDBとしてのみ使い、アクセスは全てサーバー側の
 * このクライアント経由になる(anonキーは廃止)。
 * 権限チェックはDB(RLS)ではなくアプリ層で行うこと:
 * 管理系のサーバーアクション/route は必ず requireAdmin()(@/lib/auth)を通す。
 * クライアントコードから import してはならない("server-only" で強制)。
 */
export function createAdminClient() {
  return createSupabaseClient(
    SUPABASE_URL,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { persistSession: false } }
  );
}
