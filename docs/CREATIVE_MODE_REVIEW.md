# 指示書 v0.1 へのレビューと合意済み改訂方針（2026-07-26）

[CREATIVE_MODE_SPEC_v0.1.md](CREATIVE_MODE_SPEC_v0.1.md) に対するレビュー。
前セッションで thinker-maurice のコードベースを実地監査した知見（[T0_AUDIT_NOTES.md](T0_AUDIT_NOTES.md)）に基づく。
**ユーザー（発注者側）は「推奨アクションで進める」と承認済み** — 以下は v0.2 改訂・T0/T1 に反映すべき正本方針。

## 総評

指示書の品質は高い。Style / Narrative Grammar / Intent の三分離、additive migration 原則、
「監査してから実装」「発火だけでなく棄却も記録」は既存 Thinker の設計思想と整合。
弱点は (A) スコープ肥大、(B) 実装済みコードベースへの未接地、の2点。

## 論点1: maurice との関係 → 完全分離で確定

- 現行コードは person_id が定数ハードコードの「1デプロイ=1人物」設計（約15箇所。CONTENT_INGESTION.md 自身が「person置換フェーズ」を明記）
- 切替式にするには multi-tenant 化が先に必要 → 指示書 §1.1「大規模な抽象化を先に行わない」と矛盾
- thinkerllm → thinker-maurice の「人物ごとにリポジトリ+スタック分離」パターンを踏襲
- **決定**: `thinker-soseki` リポジトリ + 新規 GCP/Firebase/Supabase。thinker-maurice を upstream として修正を還流
- 認識済みコスト: コードベース3分岐。将来創作モードが実証されたら統合を検討

## 論点2: v0.1 スコープ削減（§16 受入条件の改訂）

§2.3 は「Style と Narrative Grammar 中心」と言いつつ §16 は L3規則・5モード・評価系まで要求しており自己矛盾。
既存思想モードでも L3 は後から shadow で追加された経緯があり、同じ順序で育てる。

| v0.1 に残す | 後回し(v0.2以降) |
|---|---|
| creative_profiles + creative_cards（承認フロー） | **creative_rules（L3）全体** — cards_only の assist で価値実証可能 |
| brief → outline → draft の段階生成 | revision 独立ステージ（draft プロンプトに統合） |
| 原文類似 Guard + 誤認防止表示 | baseline / rag_only 比較モード、ブラインド評価 |
| creative_traces（使用カード・chunk・生成設定） | 評価UI（§10）、比較実験基盤（§11） |

→ パイプラインは 10段 → 6段（Step 5 の L3発火が消える）。

## 論点3: 実コードとのギャップ（監査で確定した事実）

1. **§14「provider abstraction を維持」→ 存在しない**。モデル名は `frontend/src/lib/rag/llm.ts` / `worker/src/config.py` の定数。改訂: 「既存の定数パターンを踏襲」に修正。
2. **App Hosting のリクエスト上限5分が致命的制約**。現行チャット実測27〜82秒。outline→draft→guard→再生成を直列すると超過し得る。
   **改訂: 生成はフロントのリクエスト内ではなく worker のジョブ型に**。`ingestion_jobs` / `distillation_jobs` と同型の `creative_generations` テーブルを worker がポーリング。UIは「生成中→ポーリング」前提。
3. **既存の蒸留機構を再利用**。§7 は人手起草前提だが、sources→chunk→蒸留→カードdraft生成（`worker/src/steps/gen_cards.py`）の機構が既にある。創作用蒸留プロンプトに差し替えれば「夢十夜からカード候補を自動draft→人間承認」が既存フローで成立。
4. **profile の親キーは `person_id`**（`personas` テーブル）。指示書の `author_id` は存在しない概念。
5. **新テーブル方式で確定**（§12 の別テーブル案）。既存テーブルへの domain カラム追加より安全で、既存テストが構造的に無傷。

## 論点4: 仕様自体の改善（v0.2 に反映）

1. **§5.3 の5モード enum は次元が混線**。「根拠ソース(baseline/rag/cards)」と「規則適用(off/shadow/assist)」は直交。
   改訂: `{use_rag, use_cards, rules: off|shadow|assist}` の設定オブジェクト + B0/B1/B2/Proposed は**プリセット名**として trace に保存。
2. **仮名遣い・字体ポリシーを v0.1 必須に昇格**。夢十夜は文語混じり口語、青空文庫は新字新仮名版が普及。生成文の正書法（新字新仮名 vs 歴史的仮名遣い）は全文に効く決定。`creative_profiles.orthography_policy` を必須フィールドに。
3. **小コーパスの現実を設計に反映**。夢十夜は10篇・数万字。semantic search の価値は薄く、**関連する一夜の全文をコンテキスト投入する方が単純で強い**（パブリックドメインなので可能）。RAG検索は全作品拡張時の将来投資とし、v0.1 の Step 4 は簡略化。
4. **「第十一夜」の題名自体が誤認リスク**。表示題名を「第十一夜（AI創作）」等に固定する規定を §5.1 に追加。
5. **漱石はパブリックドメイン**（没1916年）で著作権面は安全。ただし evidence_links の正本として**青空文庫の底本（版・正字法）まで特定して記録**する規定を §3 L1 metadata に明記。

## 次アクション（合意済みの順序）

1. T0 正式監査レポート（指示書 §18 の 1〜12 項目形式）— T0_AUDIT_NOTES.md を土台に、フォーク後の実コードと照合して正本化
2. 指示書 v0.2 改訂案を発注者に提示（本ドキュメントの内容を反映）
3. T1 正本仕様 → T2 以降は指示書 §17 のフェーズどおり（ただしスコープは論点2の削減後）
