import "server-only";

/**
 * 互換レイヤー: Supabase Auth時代は「ユーザーセッション付き(RLSが効く)」クライアント
 * だったが、Firebase Auth移行でRLSベースの権限制御は廃止した。
 * createClient は service_role クライアント(= createAdminClient)を返す。
 * 従って呼び出し側(サーバーアクション/route)は必ず自前で権限チェックすること:
 * - 管理系 → requireAdmin()(@/lib/auth)
 * - ユーザー系 → requireUser() + user_id での絞り込み
 */
export { createAdminClient, createAdminClient as createClient } from "./admin";
export { getUserWithProfile } from "@/lib/auth";
export type { UserProfile } from "@/lib/auth";
