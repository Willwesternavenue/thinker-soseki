# スクリプト整形(YouTube生書き起こし → 取り込み用TXT)設計

日付: 2026-07-07 / 対象: thinkerllm(X執行) admin UI + frontend

## 目的

整形済みdocxが存在しない新しいYouTube動画の生書き起こし
(タイムスタンプ癒着・話者ラベル無し・ASR誤変換あり)を、
貼り付け→自動整形→人間レビュー→ワンボタン取り込み、で原典化できるようにする。

現状の問題(実測: 絶対負深掘りVer.後編 130k字):
- `0:3131 秒お0:4949 秒社長。` のようにタイムスタンプ+読み上げ重複が本文に癒着
- 話者ラベルが無く、既存チャンカーでは全文が本人発言モノローグになる
  → 聞き手の発言が verbatim(引用可能)チャンクに混入し引用整合性が崩壊
- ASR誤変換(絶対負→絶対府/絶対法、菌→金、腸内細菌→町内細金)で
  本人が言っていない表記が原典化される

## 全体フロー

```
[貼り付け] 生スクリプト + 動画タイトル + URL + 補足ヒント(任意) + 重要度
    ↓ 整形開始
[自動処理]
  ① タイムスタンプ除去(正規表現・決定的)
     行頭の `M:SS` + 読み上げ重複(`N 分 M 秒`)を除去して本文を連結
  ② LLM(Sonnet)で発話単位に分割・話者判定・誤変換修正
     - セグメント分割(文境界、~5k字)して順次処理
     - 直前セグメントの末尾2ターンを文脈として渡す(重複なし)
     - 進捗をストリーミングで画面表示
    ↓
[レビュー画面] 発話ターンのリスト
  - 話者バッジ クリックで 本人発言 ⇄ 質問者 切替
  - 判定に迷ったターン(speaker="?")は赤枠で強調(人間が必ず確定)
  - テキストはその場で編集可能
  - LLM修正箇所は黄色ハイライト(hoverで元表記、クリックで差し戻し)
  - ターンの結合 / 分割
  - 編集は下書き(transcript_drafts)に保存され、リロードしても消えない
    ↓ 確定
[取り込み] 整形TXTを生成して既存パイプラインへ
  - 形式: `動画名：…` + URL + 空行 + `質問者:`/`本人発言:` ラベル行
    (1102/1103 docxで実績のある形。worker clean.py の正規化がそのまま効く)
  - originals/{source_id}/original.txt へStorage保存
  - sources 採番(VIDEO_xxx)+ ingestion_jobs(pending) 作成
    → 以降は常駐Workerが処理、/admin/jobs で進捗確認(既存と同じ)
```

## 技術構成

### ページ: `/admin/transcripts`
- 一覧: 下書き(status: processing / review / ingested)+「新規整形」
- 新規: フォーム(タイトル・URL・補足ヒント・重要度・生テキスト貼り付け)
- レビュー: `/admin/transcripts/[draftId]`
- 既存adminのライトテーマ(stone-50/white/stone-900/blue-700)踏襲

### LLM処理: Next.js route handler(SSEストリーミング)
- Workerジョブ化はしない。ユーザーがその場で待ってレビューする対話フローのため
  画面完結が合う。route handlerからセグメント毎にSonnetを呼び進捗を流す
- モデル: `claude-sonnet-5`(話者判定は原典忠実性に直結するためHaikuにしない)
- 出力: ターン配列 `{speaker: "本人発言"|"質問者"|"?", text, fixes:[{from,to}]}`
  をJSONで返させる

### 誤変換修正の制約(原典性の防衛)
- プロンプトで「意味・言い回しを変えない表記修正のみ」に制約。
  言い換え・要約・文の削除は禁止
- 用語集はコード内定数(絶対負・菌・菌食・腸内細菌・葉隠・超葉隠論・執行草舟・
  戸嶋靖昌・生くる・憧れの思想・絶対否定 等)+画面の補足ヒント欄を注入
- 全修正は fixes として明示させ、UIでハイライト・差し戻し可能にする

### 下書き永続化: `transcript_drafts` テーブル(migration追加)
```
draft_id uuid PK / person_id / title / video_url / hint / priority
raw_text text          -- 貼り付け原文(タイムスタンプ除去前)
turns jsonb            -- [{speaker, text, fixes, flagged}]
status text            -- processing | review | ingested
source_id text null    -- 取り込み後に設定
created_at / updated_at
```
- RLS: adminのみ(既存テーブルと同じパターン)
- 数分かかるLLM処理の結果+手修正をリロードで失わないための最小限の永続化

### 取り込み(確定ボタン)
- server action。既存 `uploadSource`(sources/actions.ts)と同じ手順:
  Storageアップロード → sources採番 insert → ingestion_jobs insert
- 採番プレフィックスは VIDEO 固定(source_type=video_transcript)
- draft.status='ingested'、source_id を記録

## エラー処理
- LLM応答のJSONパース失敗: そのセグメントをリトライ(1回)、
  再失敗時はセグメント本文を speaker="?" の1ターンとして返しレビューで人間が処理
- route handler中断(タブ閉じ等): 処理済みセグメントまでを下書き保存、
  レビュー画面から「続きから整形」で再開
- 確定時のStorage/DBエラー: 既存uploadSourceと同じくエラーメッセージ表示、
  下書きは残る(再試行可能)

## テスト
- タイムスタンプ除去・ターン結合/分割・TXT出力生成は純関数として切り出し
  ユニットテスト(既存 rag.test.ts と同じ流儀)
- 型チェックは `npx tsc --noEmit`(devサーバー稼働中は `npm run build` 禁止)
- E2E: 実物(絶対負深掘りVer.後編 130k字)で貼り付け→整形→レビュー→取り込み
  →チャンク結果の引用整合性(質問者/本人発言の分離)を確認

## スコープ外(YAGNI)
- YouTube URLからの字幕自動取得(貼り付けで足りる)
- 用語集のDB管理UI(コード定数+ヒント欄で足りる)
- docx/RTF等の非テキスト貼り付け対応(既存アップロード経路がある)
- Workerジョブ化・並列処理
