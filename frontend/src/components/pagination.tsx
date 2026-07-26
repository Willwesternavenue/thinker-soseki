"use client";

/** ページ送りの共通UI(用語集・原典・ジョブ等で共有)。1ページのみなら非表示。 */
export function Pagination({
  page,
  pageCount,
  onPage,
}: {
  page: number;
  pageCount: number;
  onPage: (p: number) => void;
}) {
  if (pageCount <= 1) return null;
  const btn =
    "rounded border border-stone-300 px-3 py-1 hover:bg-stone-50 disabled:opacity-40";
  return (
    <div className="flex items-center justify-center gap-2 text-sm">
      <button
        onClick={() => onPage(Math.max(0, page - 1))}
        disabled={page === 0}
        className={btn}
      >
        前へ
      </button>
      <span className="text-stone-500">
        {page + 1} / {pageCount}
      </span>
      <button
        onClick={() => onPage(Math.min(pageCount - 1, page + 1))}
        disabled={page >= pageCount - 1}
        className={btn}
      >
        次へ
      </button>
    </div>
  );
}

/** 配列を検索語で絞り込みページングする共通ロジック。 */
export function paginate<T>(
  items: T[],
  page: number,
  perPage: number
): { pageItems: T[]; pageCount: number; clampedPage: number } {
  const pageCount = Math.max(1, Math.ceil(items.length / perPage));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageItems = items.slice(
    clampedPage * perPage,
    clampedPage * perPage + perPage
  );
  return { pageItems, pageCount, clampedPage };
}
