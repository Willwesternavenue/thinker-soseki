import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { adminAuth } from "@/lib/firebase/admin";
import { SESSION_COOKIE_NAME, SESSION_COOKIE_MAX_AGE_MS } from "@/lib/const";

/**
 * セッションCookieの発行/破棄。
 * POST: クライアントでFirebaseログインして得たidTokenを検証し、
 *       httpOnlyの__session Cookie(14日)へ交換する。
 * DELETE: Cookieを破棄する(ログアウト)。
 */
export async function POST(request: Request) {
  let idToken: string | undefined;
  try {
    ({ idToken } = (await request.json()) as { idToken?: string });
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  if (!idToken) {
    return NextResponse.json({ error: "idToken は必須です" }, { status: 400 });
  }

  try {
    const auth = adminAuth();
    // idTokenの正当性を確認してからセッションCookieへ交換する
    await auth.verifyIdToken(idToken);
    const sessionCookie = await auth.createSessionCookie(idToken, {
      expiresIn: SESSION_COOKIE_MAX_AGE_MS,
    });
    const cookieStore = await cookies();
    cookieStore.set(SESSION_COOKIE_NAME, sessionCookie, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: SESSION_COOKIE_MAX_AGE_MS / 1000,
    });
    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error("session cookie発行失敗:", (e as Error).message);
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
}

export async function DELETE() {
  const cookieStore = await cookies();
  cookieStore.delete(SESSION_COOKIE_NAME);
  return NextResponse.json({ ok: true });
}
