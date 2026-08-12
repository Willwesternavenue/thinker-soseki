/**
 * firebase-admin にどの資格情報を使わせるかの判断（純関数）。
 *
 * ⚠️ **`GOOGLE_APPLICATION_CREDENTIALS` を当てにしない。**
 * あれはマシン全体で共有される名前で、他のプロジェクトが先に使っていることがある。
 * 2026-08-12 の実測では `~/.zshrc` が別プロジェクト（Cloud SQL 用・
 * `nxtsquare-444701`）の鍵を export しており、dev server がそれを掴んでいた。
 * **シェルの環境変数は `.env.local` より優先される**ので、こちらのファイルで
 * 正しい値を書いても上書きできない。エラーは
 * 「Credential ... has insufficient permission」で、別プロジェクトの鍵を
 * 使っているとは一言も言わない。
 *
 * そこで**専用の名前**（`SOSEKI_ADMIN_CREDENTIALS`）を用意して衝突自体を避ける。
 * 未指定なら従来どおり ADC に任せる（本番の App Hosting / Cloud Run では
 * 付与されたサービスアカウントが使われるので、そちらでは何も設定しない）。
 *
 * この事象が Supabase Auth から Firebase Auth への移行後に初めて出たのは、
 * 移行前は ADC を参照する仕組みが無く、`.zshrc` の設定が無害だったため。
 */
export type AdminCredentialSource =
  | { kind: "cert"; path: string }
  | { kind: "adc" };

export function adminCredentialSource(
  env: Record<string, string | undefined>
): AdminCredentialSource {
  const path = (env.SOSEKI_ADMIN_CREDENTIALS ?? "").trim();
  return path ? { kind: "cert", path } : { kind: "adc" };
}
