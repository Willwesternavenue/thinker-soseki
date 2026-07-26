"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession,
  deleteSession,
  listMessages,
  listSessions,
  renameSession,
  type SessionRow as Session,
} from "./actions";
import { LogoutButton } from "@/components/logout-button";

type Message = {
  message_id: string;
  role: "user" | "assistant";
  content: string;
  trace?: unknown;
};

export function ChatClient({
  isAdmin,
  displayName,
  embedded = false,
}: {
  isAdmin: boolean;
  displayName: string;
  // embedded: 管理画面レイアウト内に埋め込む(上部ナビの下にパネル表示)
  embedded?: boolean;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 送信フローが「作成直後のセッション」を自前で楽観描画する間、activeId変更で
  // 走る自動読込(まだDB未保存で空を返し、表示した質問文を消す)を1回だけ抑止する。
  const skipLoadRef = useRef<string | null>(null);

  const loadSessions = useCallback(async () => {
    const { sessions } = await listSessions();
    setSessions(sessions ?? []);
  }, []);

  const loadMessages = useCallback(async (sessionId: string) => {
    const { messages } = await listMessages(sessionId);
    setMessages(messages ?? []);
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    if (skipLoadRef.current === activeId) {
      skipLoadRef.current = null;
      return;
    }
    loadMessages(activeId);
  }, [activeId, loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function handleNewSession() {
    const result = await createSession();
    if (result.sessionId) {
      await loadSessions();
      setActiveId(result.sessionId);
    }
  }

  async function handleDelete(sessionId: string) {
    if (!confirm("この会話を削除しますか?(本文は復元できません)")) return;
    await deleteSession(sessionId);
    if (activeId === sessionId) setActiveId(null);
    await loadSessions();
  }

  async function handleSend() {
    if (!input.trim() || sending) return;
    let sessionId = activeId;
    if (!sessionId) {
      const created = await createSession();
      if (!created.sessionId) {
        setError(created.error ?? "セッション作成に失敗しました");
        return;
      }
      sessionId = created.sessionId;
      // 直後の setActiveId で走る自動読込を1回抑止(空読込で質問文が消えるのを防ぐ)
      skipLoadRef.current = sessionId;
      setActiveId(sessionId);
      await loadSessions();
    }

    const userText = input.trim();
    setInput("");
    setError(null);
    setSending(true);
    setMessages((prev) => [
      ...prev,
      { message_id: `tmp-user-${prev.length}`, role: "user", content: userText },
    ]);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, message: userText }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "エラーが発生しました");
      setMessages((prev) => [
        ...prev,
        {
          message_id: `tmp-assistant-${prev.length}`,
          role: "assistant",
          content: data.answer,
          trace: data.trace,
        },
      ]);
      // 最初の質問をセッションタイトルに
      if (messages.length === 0) {
        await renameSession(sessionId, userText.slice(0, 30));
        await loadSessions();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className={
        embedded
          ? "flex h-[calc(100vh-8rem)] overflow-hidden rounded-lg border border-stone-200"
          : "flex h-screen"
      }
    >
      {/* セッション一覧 */}
      <aside className="flex w-64 flex-col border-r border-stone-200 bg-white">
        <div className="flex items-center justify-between border-b border-stone-200 p-3">
          <span className="text-sm font-bold">Xメルロ=ポンティ</span>
          <button
            onClick={handleNewSession}
            className="rounded bg-blue-700 px-2 py-1 text-xs font-medium text-white"
          >
            + 新規
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {sessions.map((session) => (
            <div
              key={session.session_id}
              className={`group flex items-center gap-1 border-b border-stone-200 px-3 py-2 text-sm ${
                activeId === session.session_id
                  ? "bg-stone-200"
                  : "hover:bg-stone-100"
              }`}
            >
              <button
                onClick={() => setActiveId(session.session_id)}
                className="flex-1 truncate text-left"
              >
                {session.title ?? "無題"}
              </button>
              <button
                onClick={() => handleDelete(session.session_id)}
                className="hidden text-xs text-stone-500 hover:text-red-700 group-hover:block"
                title="削除"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <div className="border-t border-stone-200 p-3 text-xs text-stone-500">
          <p>{displayName}</p>
          {isAdmin && !embedded && (
            <a href="/admin/sources" className="underline hover:text-stone-700">
              管理画面へ
            </a>
          )}
          {!embedded && (
            <LogoutButton className="mt-1 block underline hover:text-red-700" />
          )}
          <p className="mt-2 text-[10px] leading-relaxed">
            これはAI対話体験であり、本人の実際の判断を保証するものではありません。
          </p>
        </div>
      </aside>

      {/* メッセージ */}
      <main className="flex flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {messages.length === 0 && !sending && (
            <div className="flex h-full items-center justify-center text-stone-500">
              なんでも聞いてくれ。
            </div>
          )}
          {messages.map((message) => (
            <div key={message.message_id}>
              <div
                className={`max-w-2xl whitespace-pre-wrap rounded-lg px-4 py-3 text-sm leading-relaxed ${
                  message.role === "user"
                    ? "ml-auto bg-stone-200"
                    : "bg-white border border-stone-200"
                }`}
              >
                {message.content}
              </div>
              {message.role === "assistant" && (
                <div className="mt-1">
                  <CopyButton text={message.content} />
                  {!!message.trace && (
                    <TracePanel trace={message.trace} isAdmin={isAdmin} />
                  )}
                </div>
              )}
            </div>
          ))}
          {sending && (
            <div className="max-w-2xl rounded-lg border border-stone-200 bg-white px-4 py-3 text-sm text-stone-500">
              考え中…
            </div>
          )}
          {error && <p className="text-sm text-red-700">{error}</p>}
          <div ref={bottomRef} />
        </div>
        <div className="border-t border-stone-200 p-4">
          <div className="mx-auto flex max-w-2xl gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              rows={2}
              placeholder="質問や相談を入力(Enterで送信)"
              className="flex-1 resize-none rounded border border-stone-300 bg-white px-3 py-2 text-sm"
            />
            <button
              onClick={handleSend}
              disabled={sending || !input.trim()}
              className="rounded bg-blue-700 px-4 text-sm font-medium text-white disabled:opacity-50"
            >
              送信
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

/** 回答本文をクリップボードにコピー(評価でスプレッドシートに貼るため)。 */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // clipboard APIが使えない場合のフォールバック
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <button
      onClick={copy}
      className="rounded border border-stone-300 px-2 py-0.5 text-xs text-stone-600 hover:bg-stone-100"
    >
      {copied ? "コピーしました" : "回答をコピー"}
    </button>
  );
}

/**
 * 参照情報パネル(仕様3.9 / 10.4)。デフォルト非表示。
 * testerにも回答根拠を確認してもらうため admin限定を解除した。ただしカードIDの
 * リンク先 /admin はtesterが開けないため、リンク化はadminのみとする。
 */
function TracePanel({ trace, isAdmin }: { trace: unknown; isAdmin: boolean }) {
  const [open, setOpen] = useState(false);
  const t = trace as {
    query_kind: string;
    routing_method: string;
    fallback_card_used: boolean;
    selected_thought_ids: string[];
    retrieved_card_ids: string[];
    top_hits: Array<{
      chunk_id: string;
      source_title: string | null;
      score: number;
      evidence_role: string | null;
      verbatim: boolean;
      quote_allowed: boolean;
      source_page: number | null;
      printed_page: number | null;
      text_excerpt: string;
    }>;
    guard_result: {
      passed: boolean;
      exact_match_hits: string[];
      judge_result: string;
      regenerated: boolean;
      safe_answer_used: boolean;
    };
  };
  return (
    <div className="mt-1 max-w-2xl">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-stone-500 underline hover:text-stone-700"
      >
        {open ? "参照情報を閉じる" : "参照情報を見る"}
      </button>
      {open && (
        <div className="mt-2 space-y-2 rounded border border-stone-200 bg-stone-100 p-3 text-xs text-stone-600">
          <p>
            分類: <b>{t.query_kind}</b> / ルーティング: <b>{t.routing_method}</b>
            {t.fallback_card_used && (
              <span className="ml-2 rounded bg-amber-100 px-1.5 text-amber-800">
                fallbackカード使用
              </span>
            )}
          </p>
          <div>
            <span>参照カード: </span>
            {t.retrieved_card_ids.length === 0 ? (
              <span className="text-stone-500">なし</span>
            ) : (
              <span className="inline-flex flex-wrap gap-1">
                {t.retrieved_card_ids.map((cid, i) =>
                  isAdmin ? (
                    <a
                      key={cid}
                      href={`/admin/cards/${cid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="クリックでカードの中核命題・区別・禁止・原典リンクを表示"
                      className="rounded bg-white px-1.5 py-0.5 font-mono text-blue-700 underline hover:bg-blue-50"
                    >
                      {cid}
                      {t.selected_thought_ids[i] ? ` · ${t.selected_thought_ids[i]}` : ""}
                    </a>
                  ) : (
                    <span
                      key={cid}
                      className="rounded bg-white px-1.5 py-0.5 font-mono text-stone-600"
                    >
                      {cid}
                      {t.selected_thought_ids[i] ? ` · ${t.selected_thought_ids[i]}` : ""}
                    </span>
                  )
                )}
              </span>
            )}
          </div>
          {t.selected_thought_ids.length > t.retrieved_card_ids.length && (
            <p>
              thought_ids:{" "}
              <span className="font-mono">
                {t.selected_thought_ids.join(", ")}
              </span>
            </p>
          )}
          <p>
            Guard: {t.guard_result.passed ? "通過" : "失敗"} / judge:{" "}
            {t.guard_result.judge_result}
            {t.guard_result.regenerated && " / 再生成あり"}
            {t.guard_result.safe_answer_used && " / 安全側回答"}
            {t.guard_result.exact_match_hits.length > 0 &&
              ` / 検出語: ${t.guard_result.exact_match_hits.join(", ")}`}
          </p>
          <div className="space-y-1">
            {t.top_hits.map((hit) => (
              <details key={hit.chunk_id} className="rounded bg-white px-2 py-1">
                <summary className="cursor-pointer">
                  <span className="font-mono">{hit.chunk_id}</span>{" "}
                  {hit.source_title ?? ""} score={hit.score}{" "}
                  {hit.evidence_role && `role=${hit.evidence_role}`}{" "}
                  {hit.verbatim && "verbatim"} {hit.quote_allowed && "quote_ok"}{" "}
                  {hit.source_page != null && `p.${hit.source_page}`}
                </summary>
                <p className="mt-1 whitespace-pre-wrap text-stone-500">
                  {hit.text_excerpt}
                </p>
              </details>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
