# thinker-soseki 引き継ぎ書（2026-07-26 作成）

夏目漱石版 Thinker + **創作モード（Creative Mode）v0.1** の立ち上げプロジェクト。
前セッション（thinker-maurice のオンボード〜本番デプロイ）からの引き継ぎ。

## このプロジェクトは何か

- 既存の思想蒸留型RAG「Thinker」（thinkerllm → thinker-maurice と人物ごとにフォークされてきた）の**3本目のフォーク**。
- 対象人物: **夏目漱石**（故人・没後100年超 = パブリックドメイン。青空文庫あり。著作権面で最も扱いやすい人物）。
- 新機能: 『夢十夜』を参照した新作「第十一夜」を生成する**創作モード**。仕様は [docs/CREATIVE_MODE_SPEC_v0.1.md](docs/CREATIVE_MODE_SPEC_v0.1.md)（発注者からの指示書・正本）。
- 指示書へのレビュー（合意済みの改訂方針）: [docs/CREATIVE_MODE_REVIEW.md](docs/CREATIVE_MODE_REVIEW.md)
- 前セッションで実施済みのコードベース監査メモ: [docs/T0_AUDIT_NOTES.md](docs/T0_AUDIT_NOTES.md)

## 確定済みの意思決定（ユーザー合意済み・再議論不要）

1. **maurice とは完全分離**。同一スタック切替案は却下。理由: 現行コードは person_id ハードコードの「1デプロイ=1人物」設計で、切替式は指示書 §1.1（大規模抽象化の先行禁止）と矛盾。人物ごとリポジトリ+スタック分離が既存パターン。
2. **リポジトリ**: thinker-maurice をフォークして `thinker-soseki` を作る。
   - `origin` = 新規GitHubリポジトリ `thinker-soseki`（ユーザーが作成）
   - `upstream` = `https://github.com/Willwesternavenue/thinker-maurice.git`（修正の還流用）
3. **クラウドも新規**: GCP/Firebase プロジェクト・Supabase プロジェクトを漱石用に新設（ユーザーがコンソールで作成する分担だった）。
4. **v0.1 スコープ削減**（レビューで提案しユーザーが「推奨アクションで進めて」と承認）:
   - 残す: creative_profiles + creative_cards（承認フロー）/ brief→outline→draft 段階生成 / 原文類似Guard + 誤認防止表示 / creative_traces
   - 後回し: **L3 creative_rules 全体** / revision独立ステージ / baseline・rag_only比較モード / ブラインド評価 / 評価UI（指示書10章）/ 比較実験基盤(11章)
5. **アーキテクチャ上の必須判断**（レビュー §3）:
   - 生成はフロントのリクエスト内でなく **worker のジョブ型**（App Hosting のリクエスト上限5分のため。`ingestion_jobs`/`distillation_jobs` と同型の `creative_generations` を worker がポーリング）
   - カード起草は既存の**蒸留機構（gen_cards.py 系）を創作用プロンプトに差し替えて再利用**（人手ゼロ起草ではなく「自動draft→人間承認」）
   - 生成モードは5値enumでなく**直交フラグ** `{use_rag, use_cards, rules: off|shadow|assist}` + プリセット名をtraceに保存
   - `creative_profiles` の親キーは `person_id`（`author_id` という概念は存在しない）
   - **仮名遣い・字体ポリシー**（例: 新字新仮名）を creative_profiles の必須フィールドに昇格
   - 夢十夜は10篇・小コーパスなので v0.1 の原典取得は「関連する一夜の全文をコンテキスト投入」で簡略化可（semantic searchは全作品拡張時の将来投資）
   - 表示題名は「第十一夜（AI創作）」のように誤認防止を題名レベルで固定

## 次にやること（順番）

> **進捗（2026-07-26 更新）**: 1・2 は完了、T1 も正本化済み。
> T0 正式版 = [docs/T0_AUDIT_REPORT.md](docs/T0_AUDIT_REPORT.md) / 正本仕様（発注者承認済み）= [docs/CREATIVE_MODE_SPEC_v0.2.md](docs/CREATIVE_MODE_SPEC_v0.2.md) /
> T1 実装設計 = [docs/T1_CREATIVE_MODE_DESIGN.md](docs/T1_CREATIVE_MODE_DESIGN.md) / 青空文庫取得手順 = [docs/AOZORA_INGESTION.md](docs/AOZORA_INGESTION.md)。
> 次は T1 設計書 §11 のタスク分割どおり T2（migration）から。並行の環境構築はユーザー作成分担（GitHub/Firebase/Supabase）が起点。

1. ~~T0 正式監査レポート~~ 完了 → docs/T0_AUDIT_REPORT.md
2. ~~指示書 v0.2 改訂案~~ 完了・発注者承認済み → docs/CREATIVE_MODE_SPEC_v0.2.md（v0.1 を置換）
3. T1 以降（正本仕様 → DB → 管理 → パイプライン → UI）は v0.2 §17 のフェーズどおり。実装順の正本は T1 設計書 §11
4. 並行して環境構築: フォーク→person置換（natsume_soseki）→新スタック接続（下記チェックリスト）

## 環境構築チェックリスト（maurice で踏んだ罠を回避）

> 📋 **別人物で新規に立ち上げる場合は [docs/NEW_PERSON_CHECKLIST.md](docs/NEW_PERSON_CHECKLIST.md) を使うこと**。
> 執行草舟→メルロ=ポンティ→漱石の3世代で実際に踏んだ置換漏れ・事故を網羅した汎用チェックリスト。
> 以下は漱石スタック固有の進捗管理。

maurice 立ち上げ時の実績手順。詳細は `/Users/will/.claude/projects/-Users-will-thinker-maurice/memory/` の
`maurice-stack.md` / `maurice-local-dev-adc.md` / `maurice-prod-deploy.md` を参照。

- [x] GitHub: `thinker-soseki` リポジトリ作成 → maurice(b92e60f) を履歴ごと統合、origin=thinker-soseki / upstream=thinker-maurice 設定済み（2026-07-26）
- [x] person置換: `merleau_ponty` → `natsume_soseki` 完了（2026-07-26。コード・SQL・UI呼称・正規化ロジック・テスト。接続先6行はTBDプレースホルダ化して maurice 誤接続を防止。既存テスト green: worker 28 / frontend 43 / tsc）
- [ ] Firebase プロジェクト新規作成（ユーザー作業）→ Webアプリ登録 → firebaseConfig 6項目をコードへ
  → **手順は [docs/FIREBASE_SETUP.md](docs/FIREBASE_SETUP.md)**（漱石の実値に落とした版）
  - ⚠️ Firebase が別サフィックス付きprojectIdを作ることがある（maurice では thinker-maurice と thinker-maurice-9082f の2つが生まれ混乱した）
  - ⚠️ Authentication → メール/パスワード有効化を忘れない
- [x] ローカル Supabase スタックの分離（2026-07-26）: `config.toml` の `project_id` が maurice と同じ
  `thinkerllm`・ポートも同一だったため、`thinker-soseki` / ポート 55421-55429 に変更。
  分離前に `supabase db reset` すると **maurice のローカルDBまで消える**ので注意
- [ ] Supabase プロジェクト新規作成（ユーザー作業）→ URL をコードへ → `supabase link` → `db push`
  - ⚠️ Supabase CLI のログインアカウントとプロジェクト所有アカウントの不一致に注意（maurice で発生）
- [ ] ローカル秘匿キー: `~/.config/gcp-keys/soseki.env` を作成（GOOGLE_APPLICATION_CREDENTIALS=SAキー / SUPABASE_SERVICE_ROLE_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY の4行）
  - ⚠️ firebase-admin は ADC の quota project を無視する → **サービスアカウントキー必須**（maurice-local-dev-adc.md 参照）
- [ ] `personas` に natsume_soseki 行を INSERT + フォールバックカード（maurice と同じ手順）
- [ ] admin ユーザー作成: `scripts && npm run create-user`
- [ ] 本番化時: 請求先リンク（⚠️ アカウント `012B6E-...` はプロジェクト数クォータ超過。`01B6B4-...` を使った）→ Secret Manager 登録 → App Hosting backends:create → grantaccess 3キー → Cloud Run は `--set-secrets` + secretAccessor（maurice-prod-deploy.md 参照）

## 参照

- 上流リポジトリ（動く実装の正本）: `/Users/will/thinker-maurice`（GitHub: Willwesternavenue/thinker-maurice, main = b92e60f 時点で本番稼働）
- maurice 本番: frontend `https://web-frontend--thinker-maurice-9082f.asia-east1.hosted.app` / worker Cloud Run `thinkerllm-worker`（漱石とは無関係。壊さないこと）
- 汎用の人物投入手順: maurice リポジトリの `docs/CONTENT_INGESTION.md`（存命/故人で仕様が変わる点も記載）
