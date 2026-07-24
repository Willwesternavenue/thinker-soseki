import "server-only";
import { getApps, initializeApp, applicationDefault } from "firebase-admin/app";
import { getAuth, type Auth } from "firebase-admin/auth";
import { GCP_PROJECT_ID } from "@/lib/const";

/**
 * サーバー用 firebase-admin。
 * - セッションCookieの発行(createSessionCookie)と検証(verifySessionCookie)に使う。
 * - 認証情報はADC: 本番はApp Hosting/Cloud Runのサービスアカウント、
 *   ローカルは gcloud auth application-default login。
 */
export function adminAuth(): Auth {
  const app =
    getApps()[0] ??
    initializeApp({
      credential: applicationDefault(),
      projectId: GCP_PROJECT_ID,
    });
  return getAuth(app);
}
