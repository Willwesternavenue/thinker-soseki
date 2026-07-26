/**
 * 非秘匿の設定値(.env廃止に伴いコードに定数として持つ)。
 * 秘匿すべきキー(service_role / ANTHROPIC / OPENAI)はここに書かず、
 * Google Cloud Secret Manager から取得する(src/lib/secrets.ts)。
 *
 * 一部は環境変数で上書き可能にしてある(ローカルSupabaseに切り替える等、
 * シェルの環境変数での一時上書きを想定。ファイルとしての .env は使わない)。
 */

/**
 * GCP / Firebase プロジェクトID(Secret Manager・firebase-admin が使う)。
 * ⚠️ TBD: 漱石用 Firebase プロジェクト作成後に実値へ差し替える(HANDOFF.md チェックリスト)。
 * maurice の値を残すと誤って maurice 本番へ接続するため、確定までプレースホルダにしてある。
 */
export const GCP_PROJECT_ID = "thinker-soseki-TBD";

/** Firebase クライアント設定(公開情報。APIキーは秘密ではない)。⚠️ TBD: Webアプリ登録後に6項目を転記 */
export const FIREBASE_CONFIG = {
  apiKey: "TBD",
  authDomain: "thinker-soseki-TBD.firebaseapp.com",
  projectId: GCP_PROJECT_ID,
  storageBucket: "thinker-soseki-TBD.firebasestorage.app",
  messagingSenderId: "TBD",
  appId: "TBD",
} as const;

/**
 * セッションCookie名。Firebase Hosting/App HostingのCDNは `__session` 以外の
 * Cookieをバックエンドへ通さないため、この名前で固定する。
 */
export const SESSION_COOKIE_NAME = "__session";

/** セッションCookieの有効期間(ミリ秒)。firebase-adminの上限は14日 */
export const SESSION_COOKIE_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;

/** SupabaseプロジェクトURL(公開情報)。ローカルDBで動かす時だけ環境変数で上書き。⚠️ TBD: 漱石用プロジェクト作成後に実値へ */
export const SUPABASE_URL =
  process.env.SUPABASE_URL ?? "https://thinker-soseki-TBD.supabase.co";

/** L3判断文法の動作モード: "assist"=approved規則を回答に注入 / それ以外=shadow */
export const L3_MODE = process.env.L3_MODE ?? "assist";

/** L3のshadow判定を止める運用キルスイッチ("1"で停止) */
export const L3_SHADOW_DISABLED = process.env.L3_SHADOW_DISABLED === "1";

/** Secret Manager から取得する秘匿キーの一覧(シークレット名=環境変数名) */
export const SECRET_ENV_KEYS = [
  "SUPABASE_SERVICE_ROLE_KEY",
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
] as const;
