// 親のadmin layoutがcookie認証を行うため、このページも動的レンダリングにする
export const dynamic = "force-dynamic";

const STEPS = [
  {
    tab: "原典",
    title: "1. 原典を投入する",
    body: "書籍PDF・Word・動画書き起こしをアップロードします。アップロードすると自動で処理ジョブが作られ、テキスト抽出 → 整形(「社長:」の話者正規化)→ チャンク分割 → 検索用インデックス作成 → 要約(軽蒸留)まで進みます。",
    note: "スキャンPDF(文字が画像になっているもの)は対象外です。",
  },
  {
    tab: "ジョブ",
    title: "2. 処理の進み具合を確認する",
    body: "各原典の処理状況が数秒ごとに自動更新されます。上部にWorker(処理エンジン)の稼働状態が出ます。処理中はどのステップまで進んだか(5段階)と経過時間が表示され、失敗時はエラー内容と「再実行」ボタンが出ます。",
    note: "「Workerが停止しています」と出ているときは、表示された起動コマンドをターミナルで実行してください。処理はWorkerが動いている間だけ進みます。",
  },
  {
    tab: "チャンク",
    title: "3. 重要な箇所に印をつける",
    body: "原典は数百字〜千数百字の「チャンク」に分割されます。中核思想に関わる箇所の重要度を high にすると、深い分析(重蒸留)の対象になり、思想カードの材料として使われます。この重要度はカード生成時のみ使われ、チャット回答には影響しません。",
    note: "重要度は自動判定されます。手で調整してからカード生成すると、重蒸留の対象を絞れます。",
  },
  {
    tab: "思想カード",
    title: "4. カードをレビューして承認する",
    body: "思想カード(回答方針)がここに並びます。ページ上部の「カードを生成」ボタンを押すと、投入済み原典を重蒸留してカード候補と質問対応を自動生成します(ターミナル不要、生成物はdraft)。中核命題・区別・禁止事項を確認・編集し、「承認」すると本番の回答で使われます。カード詳細では原典リンクの承認と、引用してよい箇所(quote_allowed)の設定も行います。",
    note: "本番回答に使われるのは approved のカードだけです。カードの内容は回答方針であり、本人の発言そのものではありません。",
  },
  {
    tab: "判断規則",
    title: "4.5 判断文法(L3)を育てる",
    body: "思想カードが「何を考えているか(概念)」なら、判断規則は「どう考えるか(いつ・何を・どう捉え直すか)」です。各規則の発火条件・例外・禁止推論と、実際の質問での発火実績(shadow=記録のみで回答には未使用)を確認し、スコープ別レビュー(意味/理由/適用範囲など)を記録します。回答品質評価でB判定(結論は近いが理由が違う)が出た失敗が、新しい規則の種になります。",
    note: "approvedにしても現在は回答に使われません(shadowで実績を貯める段階)。回答への使用は将来のassistモードから。",
  },
  {
    tab: "質問対応",
    title: "5. 質問の入口を育てる",
    body: "ユーザーの質問はまずここに登録された想定質問と照合され、どの思想で答えるかが決まります。テスト検索で「この質問がどの思想にヒットするか」を確認でき、足りない質問は手で追加できます。",
    note: "回答の的中率を上げたいときは、優先度ではなくここを充実させるのが最も効果的です。",
  },
  {
    tab: "評価",
    title: "6. 品質を測って改善する",
    body: "評価セット(想定質問集)を一括実行し、ルーティングの正誤とGuard(禁止語検査)の結果を確認できます。各回答には5観点(思想一貫性・ペルソナ・根拠適合・メタ漏れなし・安全性)で点数をつけられます。ルーティングに失敗した実質問も集計されるので、それを質問対応に追加する改善ループを回します。",
    note: "会員公開前に、評価テストで重大な禁則違反がないことを確認してください。",
  },
  {
    tab: "設定",
    title: "7. 人格プロンプトを調整する",
    body: "Xメルロ=ポンティの人格(システムプロンプト)・一人称・語り口・禁止語・断定しない話題をここで編集します。保存すると次の回答からすぐ反映されます。",
    note: "",
  },
];

export default function HelpPage() {
  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <h1 className="text-xl font-bold">使い方</h1>
        <p className="mt-2 text-sm leading-relaxed text-stone-600">
          このツールは質問の性質で答え方を2つに分けます。思想・人生相談・価値判断の
          問いは、人間が承認した<b>思想カード(回答方針)</b>に基づいて答えます。
          経歴・会社・作品などの事実の問いは、投入した<b>原典を直接検索</b>して答えます
          (カード不要)。日々の運用は「原典を入れる → カードを承認・整える →
          質問の入口を育てる」の繰り返しです。
        </p>
      </div>

      <div className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-2 font-semibold">回答が作られる流れ</h2>
        <p className="text-sm leading-relaxed text-stone-600">
          まず質問を分類し、経路が分かれます。
        </p>
        <ul className="mt-2 space-y-1 text-sm leading-relaxed text-stone-600">
          <li>
            <b>思想・人生相談系</b>: 質問対応情報で思想を特定 → 承認済みカードを取得
            (見つからない場合も「人生相談の基本姿勢」カードを必ず使用)→ 紐づく原典を補強に添付。
          </li>
          <li>
            <b>事実系(経歴・会社・作品)</b>: カードは使わず、投入した原典を直接検索して該当箇所を取得。
          </li>
        </ul>
        <p className="mt-2 text-sm leading-relaxed text-stone-600">
          その後、回答生成 → 禁止語・一人称の検査(「社長」自称やRAG等の確実な違反は
          通さず、必要なら1回だけ書き直し)→ 回答。管理者は各回答の「参照情報を見る」で
          分類・ルーティング・参照カード(クリックで詳細へ)・原典ヒット・検査結果を確認できます。
        </p>
      </div>

      <div className="space-y-4">
        {STEPS.map((step) => (
          <section
            key={step.tab}
            className="rounded-lg border border-stone-200 bg-white p-5"
          >
            <div className="mb-1 flex items-center gap-2">
              <span className="rounded bg-stone-200 px-2 py-0.5 text-xs font-medium">
                {step.tab}
              </span>
              <h2 className="font-semibold">{step.title}</h2>
            </div>
            <p className="text-sm leading-relaxed text-stone-600">{step.body}</p>
            {step.note && (
              <p className="mt-2 text-xs text-amber-700">※ {step.note}</p>
            )}
          </section>
        ))}
      </div>

      <div className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-2 font-semibold">設計ドキュメント</h2>
        <p className="mb-3 text-sm leading-relaxed text-stone-600">
          重要な仕様設計はリポジトリのMarkdownが正本です。リンクは常にmainブランチの
          最新内容を表示します(仕様が改版されるとファイル名の版番号が変わるため、
          見つからない場合はdocsフォルダ一覧から最新版を開いてください)。
        </p>
        <ul className="space-y-2 text-sm">
          {[
            ["docs/ フォルダ一覧(最新版はここから)", "tree/main/docs"],
            ["L3 Judgment Rule データ仕様書 v0.2(判断文法)", "blob/main/docs/judgment_rules_spec_v0_2.md"],
            ["理由一致型 Regression Suite 仕様書 v0.2(評価)", "blob/main/docs/regression_suite_spec_v0_2.md"],
            ["初期 Judgment Rule ドラフト(レビュー用・15規則)", "blob/main/docs/judgment_rules_initial_draft.md"],
            ["MVP全体仕様 v1.1", "blob/main/xshigyo_mvp_spec_v1_1.md"],
            ["HANDOFF(現状と未了事項)", "blob/main/HANDOFF.md"],
            ["クラウドSupabase セットアップ / オンボード", "blob/main/CLOUD_SETUP.md"],
          ].map(([label, path]) => (
            <li key={path}>
              <a
                href={`https://github.com/Willwesternavenue/thinkerllm/${path}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-700 underline hover:text-blue-900"
              >
                {label}
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-amber-700">
          ※ 閲覧にはGitHubリポジトリへのアクセス権が必要です(プライベートリポジトリ)。
        </p>
      </div>

      <div className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-2 font-semibold">よくある質問</h2>
        <dl className="space-y-3 text-sm">
          <div>
            <dt className="font-medium">Q. 回答が意図した思想にヒットしない</dt>
            <dd className="mt-1 leading-relaxed text-stone-600">
              「質問対応」タブでテスト検索し、ヒットしなければその質問を追加してください。
              原典の優先度を変えても検索順位は変わりません。
            </dd>
          </div>
          <div>
            <dt className="font-medium">Q. 経歴や会社、作品のことは答えられる?</dt>
            <dd className="mt-1 leading-relaxed text-stone-600">
              答えられます。事実系の質問はカードが無くても、投入した原典を直接検索して
              その内容から答えます。うまく答えない場合は、該当する原典が投入・処理
              (蒸留完了)まで進んでいるかを「原典」「ジョブ」タブで確認してください。
            </dd>
          </div>
          <div>
            <dt className="font-medium">Q. 回答に原典の文章を引用させたい</dt>
            <dd className="mt-1 leading-relaxed text-stone-600">
              カード詳細の原典リンクで、本人発言そのもの(verbatim)のチャンクに
              「引用可」を付けてください。3条件(引用ロール・本人発言・引用可)が
              揃った箇所だけが引用されます。
            </dd>
          </div>
          <div>
            <dt className="font-medium">Q. ユーザーを追加したい</dt>
            <dd className="mt-1 leading-relaxed text-stone-600">
              登録フォームは意図的にありません。Supabaseの管理画面からユーザーを作成し、
              user_profilesにロール(admin / tester)を登録します(READMEに手順があります)。
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
