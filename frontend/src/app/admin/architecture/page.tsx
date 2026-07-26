// 親のadmin layoutがcookie認証を行うため、このページも動的レンダリングにする
export const dynamic = "force-dynamic";

// 人物ごとにリポジトリを分けているため、フォーク時に必ず自リポジトリへ差し替える
// (docs/NEW_PERSON_CHECKLIST.md「外部リンク」の項)
const GITHUB = "https://github.com/Willwesternavenue/thinker-soseki/blob/main";

/** 回答パイプラインのステップ定義(表示用) */
const ANSWER_STEPS = [
  ["1. 分類", "classify.ts", "質問を8種(thought / life_advice / fact / person_or_work / health / politics / creative / mixed)に分類。thought・life_adviceは「カード必読」の不変条件が立つ"],
  ["2. 検索クエリ生成", "session.ts", "セッション要約+直近発言から検索用クエリを構築。人物の表記ゆれ(漱石 / 夏目金之助 / Natsume Soseki 等)を正規名「夏目漱石」に統一して検索を安定させる"],
  ["3. ルーティング", "router.ts", "多段でthought_id(思想)を特定: ①概念エイリアス展開(誤解語の検出含む) → ②想定質問(thought_questions)との類似照合 → ③カードembedding類似+LLM。どの段で決まったかはrouting_methodとしてtraceに残る"],
  ["4. カード取得", "cards.ts", "approvedの思想カードのみ取得しマージ。ルーティング全滅時はフォールバックカード(人生相談の基本姿勢)。thought/life_adviceでカード0枚なら例外を投げて止まる(不変条件)"],
  ["5. 原典検索", "evidence.ts", "カードに紐づく承認済み原典リンク(linked)+全原典の関連検索(unscoped)をマージ。source/role偏りの多様性制御、引用可(quote_allowed)フィルタはコードで強制"],
  ["6. コンテキスト構築", "context.ts", "ペルソナ・カード(回答方針)・原典(裏づけ)を三区分でプロンプト化。固有名詞の説明義務などの出力ルールもここ"],
  ["7. 回答生成", "llm.ts", "Sonnetで生成(max 2500 tokens)"],
  ["8. Output Guard", "guard.ts", "二段検査: 完全一致(禁止語・「社長」自称・RAG等のメタ漏れ)→LLM judge。違反時は再生成最大1回、それでも完全一致違反が残る場合のみ安全側回答に差し替え"],
  ["9. L3 shadow(並走)", "l3shadow.ts", "判断規則(L3)の発火判定をHaiku1回で一括実行し痕跡のみ記録。回答には未使用。7と並行するためレイテンシ影響なし"],
  ["10. trace保存", "pipeline.ts", "分類・ルーティング方法・使用カード・原典ヒット・Guard結果・L3発火をanswer_tracesへ。回答の「参照情報を見る」の中身"],
] as const;

const INGEST_STEPS = [
  ["extract", "PDF/docx/txtからテキスト抽出"],
  ["clean", "話者ラベル正規化(「社長:」→本人)・ノイズ除去。聞き手発言が本人発言に混入しないようにする要"],
  ["chunk", "数百〜千数百字に分割。chunk_hashで差分検出(再実行時は変更分のみ)"],
  ["embed", "OpenAI text-embedding-3-small(1536次元)"],
  ["distill_light", "Haikuで各チャンクの要約・属性抽出(軽蒸留)"],
] as const;

const LAYERS = [
  ["L1 原典", "sources / source_chunks", "本人が実際に言ったこと。証拠。回答の裏づけと引用の唯一の源"],
  ["L2 概念", "thought_cards / thought_questions / concept_aliases", "何を考えているか。思想カード=人間が承認した回答方針。想定質問とエイリアスはルーティングの入口"],
  ["L3 判断文法", "judgment_rules ほか5テーブル", "どう考えるか。「いつ・何を・どう捉え直すか」の判断規則(発火条件・例外・禁止推論・優先順位)。現在shadowで実績収集中"],
  ["L4 実行状態", "answer_traces(+l3_shadow)", "今回どう考えたか。1回答ごとの分類・ルーティング・使用カード・発火規則の監査記録"],
] as const;

const KEY_DOCS = [
  ["MVP全体仕様 v1.1(回答フロー・テーブル定義の正本)", "xshigyo_mvp_spec_v1_1.md"],
  ["L3 Judgment Rule データ仕様 v0.2(判断文法・9テーブル設計・実行メタルール)", "docs/judgment_rules_spec_v0_2.md"],
  ["Regression Suite 仕様 v0.2(理由一致型評価・ホールドアウト規律)", "docs/regression_suite_spec_v0_2.md"],
  ["初期判断規則15件ドラフト(規則の実例)", "docs/judgment_rules_initial_draft.md"],
  ["HANDOFF(現状・運用の落とし穴・未了)", "HANDOFF.md"],
] as const;

export default function ArchitecturePage() {
  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <h1 className="text-xl font-bold">設計(エンジニア向けオンボード)</h1>
        <p className="mt-2 text-sm leading-relaxed text-stone-600">
          このシステムは作家・夏目漱石のAIアバターです。一般的なRAG(質問→ベクトル検索→生成)と
          違い、<b>原典を直接検索して答えるのは事実系の質問だけ</b>です。思想・人生相談は、原典から
          蒸留し人間が承認した<b>思想カード(回答方針)</b>を必ず経由する固定ワークフローで答えます。
          設計の中心思想は「LLMの即興に思想を任せず、承認可能な人工物(カード・判断規則)に判断を
          固定し、その適用だけをLLMにやらせる」ことです。
        </p>
      </div>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-3 font-semibold">全体構成(3コンポーネント)</h2>
        <div className="space-y-2 text-sm text-stone-600">
          <div className="rounded bg-stone-50 p-3">
            <b>frontend/</b>(Next.js 16 App Router)— Chat UI・管理画面・
            <b>回答時RAG本体(src/lib/rag/)</b>。回答はサーバーアクション内で完結
          </div>
          <div className="rounded bg-stone-50 p-3">
            <b>worker/</b>(Python, uv)— 取り込みエンジン。ingestion_jobsをポーリングする常駐プロセスで、
            抽出→整形→チャンク化→embedding→蒸留を実行。カード生成(重蒸留)もここ
          </div>
          <div className="rounded bg-stone-50 p-3">
            <b>Supabase(クラウド、共同作業の正本)</b>— Postgres + pgvector + 認証。
            回答時RAGはStorage参照ゼロでDBのみで完結。RLSでtesterは管理データに触れない
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-1 font-semibold">回答パイプライン(1リクエストの流れ)</h2>
        <p className="mb-3 text-xs text-stone-500">
          実装はすべて frontend/src/lib/rag/。入口は pipeline.ts の answerQuestion()。
          自由行動型エージェントは使わず、毎回同じ順序で実行される。
        </p>
        <ol className="space-y-2 text-sm">
          {ANSWER_STEPS.map(([step, file, desc]) => (
            <li key={step} className="rounded bg-stone-50 p-2.5">
              <span className="font-medium">{step}</span>
              <span className="ml-2 rounded bg-stone-200 px-1.5 py-0.5 font-mono text-xs">{file}</span>
              <p className="mt-1 text-xs leading-relaxed text-stone-600">{desc}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-1 font-semibold">取り込みパイプライン(worker)</h2>
        <p className="mb-3 text-xs text-stone-500">
          worker/src/steps/。原典アップロード→ingestion_jobs→常駐Workerが5段階を順次実行。
          失敗はジョブ画面から再実行できる。
        </p>
        <div className="flex flex-wrap items-center gap-1 text-xs">
          {INGEST_STEPS.map(([name, desc], i) => (
            <span key={name} className="flex items-center gap-1">
              <span className="rounded bg-stone-100 px-2 py-1" title={desc}>
                <b>{name}</b>
              </span>
              {i < INGEST_STEPS.length - 1 && <span className="text-stone-400">→</span>}
            </span>
          ))}
        </div>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-stone-600">
          {INGEST_STEPS.map(([name, desc]) => (
            <li key={name}>
              <b>{name}</b>: {desc}
            </li>
          ))}
          <li>
            <b>重蒸留(カード生成)</b>: 別経路(distillation_jobs)。重要チャンクをSonnetで深く分析し
            思想カード候補と想定質問を生成 → 人間レビュー → approved で本番反映
          </li>
        </ul>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-1 font-semibold">データモデル: 思想の四層</h2>
        <p className="mb-3 text-xs text-stone-500">
          「思想の再現」を、層の異なる4種類のデータとして分離している。上の層ほど生データに近く、
          下の層ほど「判断の構造」に近い。
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-stone-200 text-left text-stone-500">
                <th className="py-1.5 pr-3">層</th>
                <th className="py-1.5 pr-3">主なテーブル</th>
                <th className="py-1.5">役割</th>
              </tr>
            </thead>
            <tbody className="text-stone-600">
              {LAYERS.map(([layer, tables, role]) => (
                <tr key={layer} className="border-b border-stone-100 align-top">
                  <td className="py-2 pr-3 font-medium whitespace-nowrap">{layer}</td>
                  <td className="py-2 pr-3 font-mono">{tables}</td>
                  <td className="py-2 leading-relaxed">{role}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs leading-relaxed text-stone-600">
          <b>共通原則</b>: どの層も「LLMが生成 → draft → 人間レビュー → approved で初めて本番使用」。
          本人の回答であっても自動承認しない。テストチャットや評価のやり取りが自動的に
          参照情報へ流れ込むことはない(昇格は常に人間の明示操作)。
        </p>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-1 font-semibold">思想再現の閉ループ(現在進行中の拡張)</h2>
        <p className="mb-2 text-sm leading-relaxed text-stone-600">
          ベクトル検索は「過去の発言の補間」しかできず、本人が語っていない問いへの外挿=思想の
          再現はできない。そこで判断の中間論理を明示的な<b>判断規則(L3)</b>として蓄積し、
          次のループで成長させる:
        </p>
        <pre className="overflow-x-auto rounded bg-stone-50 p-3 text-xs leading-relaxed text-stone-700">
{`回答の失敗を発見(評価のA/B/C/D。特にB=結論は近いが理由が違う)
→ 不足している判断規則を推定
→ 本人・監修者に確認(未解決衝突は本人への質問候補になる)
→ 規則として承認(判断規則タブ)
→ 回帰テストで「理由まで本人か」を測定`}
        </pre>
        <p className="mt-2 text-xs leading-relaxed text-stone-600">
          <b>現在地</b>: 規則15件がdraft、発火判定は<b>shadowモード</b>(回答パイプラインに並走して
          記録のみ、回答には未使用)。規則レビューが進んだら assistモード(approved規則を回答生成へ
          注入)に進む計画。発火単体テストは worker の test_rule_activation(60/60)。
        </p>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-2 font-semibold">モデル割当</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-stone-600">
          <li><b>回答生成</b>: claude-sonnet-5</li>
          <li><b>分類・Guard judge・L3 shadow判定</b>: claude-haiku-4-5</li>
          <li><b>軽蒸留</b>: Haiku / <b>重蒸留(カード生成)</b>: Sonnet</li>
          <li><b>Embedding</b>: OpenAI text-embedding-3-small(1536次元、pgvector)</li>
          <li>workerのLLM呼び出しはコストを agent_runs テーブルに記録</li>
        </ul>
      </section>

      <section className="rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="mb-2 font-semibold">詳細仕様(正本はリポジトリのMarkdown)</h2>
        <ul className="space-y-2 text-sm">
          {KEY_DOCS.map(([label, path]) => (
            <li key={path}>
              <a
                href={`${GITHUB}/${path}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-700 underline hover:text-blue-900"
              >
                {label}
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-stone-500">
          コードを読む順番のおすすめ: pipeline.ts(全体の流れ)→ router.ts(ルーティング)→
          guard.ts(検査)→ worker/src/main.py(取り込み)→ L3仕様書(拡張の設計思想)。
        </p>
      </section>
    </div>
  );
}
