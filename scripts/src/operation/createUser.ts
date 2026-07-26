/**
 * ユーザー作成スクリプト(Firebase Authにユーザーを発行し、user_profilesにロールを登録する)。
 *
 * 使い方(scripts/ ディレクトリで):
 *   npm run create-user -- --email tester1@example.com                # tester、パスワード自動生成
 *   npm run create-user -- --email admin@example.com --role admin --name 管理者
 *   npm run create-user -- --email x@example.com --password <8文字以上>  # パスワード指定
 *
 * - 既に同じメールのユーザーがいる場合は作り直さず、ロール/表示名/パスワード(指定時)を更新する。
 * - 認証はADC(gcloud auth application-default login)。thinker-soseki-TBDの権限が必要:
 *   Firebase Authユーザー作成 = roles/firebaseauth.admin相当、Secret Manager読み取り。
 * - Firebaseコンソール(Authentication → Users)での手動発行と同じ結果になる。
 *   どちらを使ってもよい(docs/FIREBASE_MIGRATION.md 1章)。
 */

import { randomBytes } from "node:crypto";
import { parseArgs } from "node:util";
import { applicationDefault, initializeApp } from "firebase-admin/app";
import { getAuth, type UserRecord } from "firebase-admin/auth";
import { createClient } from "@supabase/supabase-js";
import { SecretManagerServiceClient } from "@google-cloud/secret-manager";

// 正本は frontend/src/lib/const.ts / worker/src/config.py(変えるときは全て揃える)
const GCP_PROJECT_ID = "thinker-soseki-TBD";
// ⚠️ TBD: 漱石用Supabaseプロジェクト作成後に実値へ。ローカル開発時は
// SUPABASE_URL=http://127.0.0.1:55421 を環境変数で渡す。
const SUPABASE_URL =
  process.env.SUPABASE_URL ?? "https://thinker-soseki-TBD.supabase.co";

const USAGE = `使い方: npm run create-user -- --email <メール> [オプション]
  --email     (必須)ログイン用メールアドレス
  --password  パスワード(8文字以上。省略時は安全なものを自動生成して表示)
  --role      admin | tester(既定: tester)
  --name      表示名(既定: メールアドレス)
  --help      このヘルプ`;

async function main() {
  const { values } = parseArgs({
    options: {
      email: { type: "string" },
      password: { type: "string" },
      role: { type: "string", default: "tester" },
      name: { type: "string" },
      help: { type: "boolean", default: false },
    },
  });

  if (values.help || !values.email) {
    console.log(USAGE);
    process.exit(values.help ? 0 : 1);
  }
  const email = values.email;
  const role = values.role;
  if (role !== "admin" && role !== "tester") {
    console.error(`--role は admin か tester を指定してください(指定値: ${role})`);
    process.exit(1);
  }
  if (values.password && values.password.length < 8) {
    console.error("--password は8文字以上にしてください");
    process.exit(1);
  }
  const generated = !values.password;
  const password = values.password ?? randomBytes(9).toString("base64url");
  const displayName = values.name ?? email;

  // 1. Firebase Auth にユーザーを作成(既存なら更新)
  initializeApp({ credential: applicationDefault(), projectId: GCP_PROJECT_ID });
  const auth = getAuth();
  let user: UserRecord;
  let created = false;
  try {
    user = await auth.createUser({ email, password });
    created = true;
  } catch (e) {
    if ((e as { code?: string }).code !== "auth/email-already-exists") throw e;
    user = await auth.getUserByEmail(email);
    if (values.password) {
      await auth.updateUser(user.uid, { password });
    }
  }

  // 2. user_profiles にロールを登録(既存なら上書き)
  const serviceRoleKey = await loadServiceRoleKey();
  const db = createClient(SUPABASE_URL, serviceRoleKey, {
    auth: { persistSession: false },
  });
  const { error } = await db
    .from("user_profiles")
    .upsert(
      { user_id: user.uid, role, display_name: displayName },
      { onConflict: "user_id" }
    );
  if (error) {
    console.error(`user_profiles の登録に失敗: ${error.message}`);
    console.error(`Firebase側のユーザーは存在します(UID: ${user.uid})。再実行してください。`);
    process.exit(1);
  }

  console.log(created ? "✅ ユーザーを作成しました" : "✅ 既存ユーザーを更新しました");
  console.log(`  email : ${email}`);
  console.log(`  UID   : ${user.uid}`);
  console.log(`  role  : ${role}`);
  console.log(`  表示名: ${displayName}`);
  if (created || values.password) {
    console.log(`  パスワード: ${password}${generated ? "(自動生成。本人に安全な方法で共有)" : ""}`);
  }
}

/** SUPABASE_SERVICE_ROLE_KEY を環境変数 → Secret Manager の順で取得する。 */
async function loadServiceRoleKey(): Promise<string> {
  if (process.env.SUPABASE_SERVICE_ROLE_KEY) {
    return process.env.SUPABASE_SERVICE_ROLE_KEY;
  }
  const client = new SecretManagerServiceClient({
    projectId: GCP_PROJECT_ID,
    clientOptions: { quotaProjectId: GCP_PROJECT_ID },
  });
  try {
    const [version] = await client.accessSecretVersion({
      name: `projects/${GCP_PROJECT_ID}/secrets/SUPABASE_SERVICE_ROLE_KEY/versions/latest`,
    });
    const value = version.payload?.data?.toString();
    if (!value) throw new Error("シークレットが空です");
    return value;
  } finally {
    await client.close().catch(() => {});
  }
}

main().catch((e) => {
  console.error("失敗:", (e as Error).message);
  process.exit(1);
});
