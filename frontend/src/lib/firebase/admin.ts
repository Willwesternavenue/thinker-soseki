import "server-only";
import { readFileSync } from "node:fs";
import { getApps, initializeApp, applicationDefault, cert } from "firebase-admin/app";
import { getAuth, type Auth } from "firebase-admin/auth";
import { GCP_PROJECT_ID } from "@/lib/const";
import { adminCredentialSource } from "./credentials";

/**
 * サーバー用 firebase-admin。
 * - セッションCookieの発行(createSessionCookie)と検証(verifySessionCookie)に使う。
 * - 本番(App Hosting / Cloud Run)は付与されたサービスアカウント = ADC。
 * - ローカルは `SOSEKI_ADMIN_CREDENTIALS` に鍵のパスを置く。
 *   ⚠️ `GOOGLE_APPLICATION_CREDENTIALS` は使わない。理由は `credentials.ts`。
 */
export function adminAuth(): Auth {
  const app = getApps()[0] ?? initializeApp({
    credential: buildCredential(),
    projectId: GCP_PROJECT_ID,
  });
  return getAuth(app);
}

function buildCredential() {
  const source = adminCredentialSource(process.env);
  if (source.kind === "adc") return applicationDefault();
  try {
    return cert(JSON.parse(readFileSync(source.path, "utf8")));
  } catch (err) {
    // 鍵を指定したのに読めないなら、黙って ADC へ落とさない。
    // 落とすと「別プロジェクトの鍵で権限不足」という分かりにくい失敗になる
    throw new Error(
      `SOSEKI_ADMIN_CREDENTIALS の鍵を読めません(${source.path}): ` +
        (err instanceof Error ? err.message : String(err))
    );
  }
}
