import { NextResponse, type NextRequest } from "next/server";
import { adminAuth } from "@/lib/firebase/admin";
import { SESSION_COOKIE_NAME } from "@/lib/const";

/**
 * ルート保護(Next.js 16 proxy)。
 * __session Cookie(Firebaseセッション)を検証し、
 * - 未ログイン → /login へ
 * - ログイン済みで /login → / へ
 * /admin 配下のロール確認はlayoutで行う(ここではログインのみ確認)。
 */
export default async function proxy(request: NextRequest) {
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME)?.value;

  let authed = false;
  if (sessionCookie) {
    try {
      await adminAuth().verifySessionCookie(sessionCookie);
      authed = true;
    } catch {
      // 失効・改ざんは未ログイン扱い
    }
  }

  const isLoginPage = request.nextUrl.pathname.startsWith("/login");
  if (!authed && !isLoginPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  if (authed && isLoginPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/).*)"],
};
