"""只读能力的本地命令行示例；发布操作请显式调用 Creator API。"""

import argparse
import json
import os

from builder.auth import TouTiaoAuth
from tou_tiao_api import TouTiaoApi


def main() -> None:
    parser = argparse.ArgumentParser(description="今日头条只读 API 示例")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="搜索作品")
    search.add_argument("keyword")
    search.add_argument("--page", type=int, default=0)
    item = commands.add_parser("item", help="读取作品详情")
    item.add_argument("url")
    args = parser.parse_args()

    # 凭据只从环境读取，避免命令行历史记录留存 Cookie。
    with TouTiaoAuth(os.getenv("TOUTIAO_COOKIE", "")) as auth:
        api = TouTiaoApi()
        if args.command == "search":
            result = api.search(args.keyword, args.page, auth)
        else:
            result = api.item(args.url, auth)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
