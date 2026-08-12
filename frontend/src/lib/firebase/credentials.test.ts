import { describe, expect, it } from "vitest";
import { adminCredentialSource } from "./credentials";

describe("adminCredentialSource(どの資格情報を使うか)", () => {
  it("専用の変数が指定されていれば、その鍵を明示的に使う", () => {
    expect(
      adminCredentialSource({ SOSEKI_ADMIN_CREDENTIALS: "/keys/soseki.json" })
    ).toEqual({ kind: "cert", path: "/keys/soseki.json" });
  });

  it("指定が無ければ ADC に任せる(本番はApp Hostingのサービスアカウント)", () => {
    expect(adminCredentialSource({})).toEqual({ kind: "adc" });
  });

  it("空文字は未指定として扱う", () => {
    expect(adminCredentialSource({ SOSEKI_ADMIN_CREDENTIALS: "  " })).toEqual({
      kind: "adc",
    });
  });

  it("GOOGLE_APPLICATION_CREDENTIALS は見ない", () => {
    // 2026-08-12 実測: ~/.zshrc が別プロジェクト(Cloud SQL)の鍵を
    // GOOGLE_APPLICATION_CREDENTIALS に export しており、dev server が
    // それを掴んでいた。シェルの環境変数は .env.local より優先されるため
    // 上書きできない。名前を分けて衝突そのものを避ける
    expect(
      adminCredentialSource({ GOOGLE_APPLICATION_CREDENTIALS: "/keys/other-project.json" })
    ).toEqual({ kind: "adc" });
  });

  it("専用の変数は GOOGLE_APPLICATION_CREDENTIALS より優先する", () => {
    expect(
      adminCredentialSource({
        GOOGLE_APPLICATION_CREDENTIALS: "/keys/other-project.json",
        SOSEKI_ADMIN_CREDENTIALS: "/keys/soseki.json",
      })
    ).toEqual({ kind: "cert", path: "/keys/soseki.json" });
  });
});
