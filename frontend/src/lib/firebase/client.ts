"use client";

import { getApps, initializeApp } from "firebase/app";
import { getAuth, type Auth } from "firebase/auth";
import { FIREBASE_CONFIG } from "@/lib/const";

/** ブラウザ用Firebase Auth。ログイン時のidToken取得にのみ使う(セッションの正本は__session Cookie)。 */
export function getFirebaseAuth(): Auth {
  const app = getApps()[0] ?? initializeApp(FIREBASE_CONFIG);
  return getAuth(app);
}
