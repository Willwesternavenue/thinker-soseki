"""PostgREST の行上限(1リクエスト最大1000行)を越えて全行を読むためのヘルパー。

⚠️ 1回の execute で全件が来る前提のコードは、データが1000件を超えた時点で
**黙って切り詰められる**。実測で2回踏んだ:
- retag が 9,669件中1000件で「完了」した
- snapshot / 品質レポートが先頭1000件しか検査していなかった

新しく全件取得を書くときは必ずこれを使うこと。
"""

PAGE_SIZE = 1000


def fetch_all(build_query, *, page_size: int = PAGE_SIZE) -> list[dict]:
    """`build_query()` が返すクエリをページングしながら最後まで読む。

    build_query は**呼ばれるたびに新しいクエリ**を返すこと(order まで付けて)。
    順序が安定しないと、ページ境界で行が重複・欠落する。
    """
    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            build_query().range(offset, offset + page_size - 1).execute().data or []
        )
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size
