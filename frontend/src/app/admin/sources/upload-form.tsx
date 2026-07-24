"use client";

import { useRef, useState, useTransition } from "react";
import { uploadSource } from "./actions";

const SOURCE_TYPES = [
  ["book", "書籍"],
  ["video_transcript", "動画書き起こし"],
  ["interview", "インタビュー"],
  ["dialogue", "対談"],
  ["lecture", "講義"],
  ["article", "記事"],
  ["essay", "随筆"],
  ["profile", "プロフィール"],
  ["document", "資料"],
  ["other", "その他"],
] as const;

const PRIORITIES = [
  ["core", "core(中核)"],
  ["important", "important(重要)"],
  ["support", "support(補助)"],
  ["style", "style(語り口)"],
  ["archive", "archive(保管)"],
] as const;

/** ファイル名から拡張子を除いてタイトル候補にする。 */
function titleFromFileName(fileName: string): string {
  return fileName.replace(/\.[^.]+$/, "").trim();
}

export function UploadForm() {
  const formRef = useRef<HTMLFormElement>(null);
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  // ユーザーが手でタイトルを編集したら、以後ファイル選択で上書きしない
  const [titleEdited, setTitleEdited] = useState(false);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) {
      setFileName(null);
      return;
    }
    setFileName(file.name);
    if (!titleEdited || title.trim() === "") {
      setTitle(titleFromFileName(file.name));
    }
  }

  function handleSubmit(formData: FormData) {
    startTransition(async () => {
      setMessage(null);
      const result = await uploadSource(formData);
      if (result.error) {
        setMessage(result.error);
      } else {
        setMessage("アップロードしました。ジョブが作成されました。");
        formRef.current?.reset();
        setTitle("");
        setFileName(null);
        setTitleEdited(false);
      }
    });
  }

  return (
    <form
      ref={formRef}
      action={handleSubmit}
      className="space-y-4 rounded-lg border border-stone-200 bg-white p-5"
    >
      <h2 className="font-semibold">原典アップロード</h2>
      <p className="text-xs text-stone-500">
        PDF / Word / TXT。スキャンPDF(OCRが必要なもの)は対象外。
      </p>

      {/* 1. ファイル選択が最初のステップ */}
      <div>
        <label className="mb-1 block text-xs text-stone-600">ファイル *</label>
        <label className="inline-flex cursor-pointer items-center gap-3 rounded border border-stone-300 bg-stone-50 px-3 py-2 text-sm hover:bg-stone-100">
          <span className="rounded bg-blue-700 px-3 py-1 text-xs font-medium text-white">
            ファイルを選択
          </span>
          <span className="text-stone-600">
            {fileName ?? "選択されていません"}
          </span>
          <input
            type="file"
            name="file"
            required
            accept=".pdf,.docx,.txt"
            onChange={handleFileChange}
            className="hidden"
          />
        </label>
      </div>

      {/* 2. タイトルはファイル名から自動入力され、編集できる */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs text-stone-600">
            タイトル *
            <span className="ml-1 font-normal text-stone-400">
              (ファイル名から自動入力・編集可)
            </span>
          </label>
          <input
            name="title"
            required
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setTitleEdited(true);
            }}
            placeholder="ファイルを選択すると自動で入ります"
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-stone-600">著者</label>
          <input
            name="author"
            defaultValue="メルロ=ポンティ"
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-stone-600">種別 *</label>
          <select
            name="source_type"
            required
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          >
            {SOURCE_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-stone-600">優先度</label>
          <select
            name="priority"
            defaultValue="core"
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          >
            {PRIORITIES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {message && <p className="text-sm text-amber-700">{message}</p>}
      <button
        type="submit"
        disabled={isPending}
        className="rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {isPending ? "アップロード中..." : "アップロード"}
      </button>
    </form>
  );
}
