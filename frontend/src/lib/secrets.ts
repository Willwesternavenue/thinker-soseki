import "server-only";
import { GCP_PROJECT_ID, SECRET_ENV_KEYS } from "@/lib/const";

/**
 * 秘匿キーを Google Cloud Secret Manager から process.env へ注入する(.env廃止)。
 * - 本番(App Hosting / Cloud Run): apphosting.yaml / --set-secrets で環境変数として
 *   注入済みなら何もしない(このコードはフォールバック)。
 * - ローカル開発: ADC(gcloud auth application-default login)で取得する。
 * サーバー起動時に instrumentation.ts から一度だけ呼ばれる。
 */
export async function loadSecrets(): Promise<void> {
  const missing = SECRET_ENV_KEYS.filter((key) => !process.env[key]);
  if (missing.length === 0) return;

  // 遅延import: 環境変数が揃っている環境ではSDKを読み込まない
  const { SecretManagerServiceClient } = await import(
    "@google-cloud/secret-manager"
  );
  // projectId/quotaProjectIdを明示: 複数プロジェクトを扱う開発機のADCデフォルトに依存しない
  const client = new SecretManagerServiceClient({
    projectId: GCP_PROJECT_ID,
    clientOptions: { quotaProjectId: GCP_PROJECT_ID },
  });

  try {
    await Promise.all(
      missing.map(async (key) => {
        const [version] = await client.accessSecretVersion({
          name: `projects/${GCP_PROJECT_ID}/secrets/${key}/versions/latest`,
        });
        const value = version.payload?.data?.toString();
        if (!value) throw new Error(`Secret ${key} が空です`);
        process.env[key] = value;
      })
    );
  } catch (e) {
    throw new Error(
      `Secret Manager からの秘匿キー取得に失敗しました(${missing.join(", ")})。` +
        `ローカル開発では「gcloud auth application-default login」を実行し、` +
        `${GCP_PROJECT_ID} の roles/secretmanager.secretAccessor 権限があることを確認してください。` +
        `本番では実行サービスアカウントに同権限が必要です。原因: ${
          (e as Error).message
        }`
    );
  } finally {
    await client.close().catch(() => {});
  }
}
