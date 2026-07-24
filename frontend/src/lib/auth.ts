import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { adminAuth } from "@/lib/firebase/admin";
import { createAdminClient } from "@/lib/supabase/admin";
import { SESSION_COOKIE_NAME } from "@/lib/const";

export type UserProfile = {
  user_id: string;
  role: "admin" | "tester";
  display_name: string | null;
};

export type AuthUser = {
  /** Firebase UID(旧Supabase時代の user.id に相当) */
  id: string;
  email: string | null;
};

export type AuthContext = { user: AuthUser; profile: UserProfile };

/**
 * ログイン中ユーザーとロールを取得する。未ログイン(またはCookie無効)なら null。
 * __session Cookie(Firebaseセッション)を検証し、user_profiles からロールを引く。
 * アカウント自体はFirebaseコンソールで管理者が発行する前提のため、
 * プロファイル未作成の初回ログイン時は tester として自動作成する
 * (admin昇格は user_profiles.role をSQLで更新)。
 * React cache() で同一リクエスト内の重複呼び出しを1回にまとめる。
 */
export const getUserWithProfile = cache(
  async (): Promise<AuthContext | null> => {
    const cookieStore = await cookies();
    const sessionCookie = cookieStore.get(SESSION_COOKIE_NAME)?.value;
    if (!sessionCookie) return null;

    let uid: string;
    let email: string | null;
    try {
      const decoded = await adminAuth().verifySessionCookie(sessionCookie);
      uid = decoded.uid;
      email = decoded.email ?? null;
    } catch {
      return null; // 失効・改ざんCookieは未ログイン扱い(proxyが/loginへ誘導)
    }

    const db = createAdminClient();
    const { data: existing } = await db
      .from("user_profiles")
      .select("user_id, role, display_name")
      .eq("user_id", uid)
      .maybeSingle();

    let profile = existing as UserProfile | null;
    if (!profile) {
      const { data: created } = await db
        .from("user_profiles")
        .insert({ user_id: uid, role: "tester", display_name: email })
        .select("user_id, role, display_name")
        .single();
      profile = created as UserProfile | null;
    }
    if (!profile) return null;

    return { user: { id: uid, email }, profile };
  }
);

/** admin必須のサーバーアクション/route用ガード。RLS廃止後の権限チェックはここで行う。 */
export async function requireAdmin(): Promise<AuthContext> {
  const auth = await getUserWithProfile();
  if (!auth || auth.profile.role !== "admin") {
    throw new Error("forbidden: admin権限が必要です");
  }
  return auth;
}

/** ログイン必須のサーバーアクション用ガード。 */
export async function requireUser(): Promise<AuthContext> {
  const auth = await getUserWithProfile();
  if (!auth) throw new Error("unauthorized: ログインが必要です");
  return auth;
}
