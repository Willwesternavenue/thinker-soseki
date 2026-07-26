"""Phase 2 蒸留パイプラインCLI(仕様6.6〜6.9)。

使い方:
  uv run python -m src.distill heavy               # 重蒸留(importance=highの未処理チャンク)
  uv run python -m src.distill source BOOK_001      # 原典単位蒸留
  uv run python -m src.distill cards                # 思想カード候補生成(横断)
  uv run python -m src.distill questions            # 質問対応情報生成(全カード)
  uv run python -m src.distill all                  # heavy → source(全原典) → cards → questions
"""

import sys

from . import db
from .steps import distill_heavy, distill_source, gen_cards, gen_questions


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    command = args[0]

    if command == "heavy":
        n = distill_heavy.run()
        print(f"重蒸留: {n}件処理")
    elif command == "source":
        if len(args) < 2:
            sys.exit("source_id を指定してください")
        distilled_id = distill_source.run(args[1])
        print(f"原典蒸留: {distilled_id}")
    elif command == "cards":
        created = gen_cards.run()
        print(f"カード候補生成: {len(created)}件 {created}")
    elif command == "questions":
        n = gen_questions.run_for_all_cards()
        print(f"質問生成: {n}件")
    elif command == "all":
        n = distill_heavy.run()
        print(f"重蒸留: {n}件処理")
        sources = (
            db.client().table("sources").select("source_id")
            .eq("status", "distilled").execute()
        ).data
        for s in sources:
            print(f"原典蒸留: {distill_source.run(s['source_id'])}")
        created = gen_cards.run()
        print(f"カード候補生成: {len(created)}件")
        n = gen_questions.run_for_all_cards()
        print(f"質問生成: {n}件")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
