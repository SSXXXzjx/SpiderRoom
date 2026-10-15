"""Run from the platform directory: python -m crawlers.google_images --keyword ..."""

import argparse
from pathlib import Path

from server import GOOGLE_DEFAULTS, validate_config
from google_worker import run


def main():
    parser = argparse.ArgumentParser(description="Google 原图采集：搜索、尺寸校验、去重与批量下载")
    parser.add_argument("--keyword", action="append", required=True, help="可重复填写多个关键词")
    parser.add_argument("--count", type=int, help="每个关键词有效图片上限，默认 100，0 为不限")
    parser.add_argument("--output")
    parser.add_argument("--proxy")
    parser.add_argument("--min-width", type=int)
    parser.add_argument("--min-height", type=int)
    parser.add_argument("--show-browser", action="store_true", default=None)
    parser.add_argument("--config", type=Path, help="附加 JSON 配置文件，CLI 参数覆盖对应项")
    args = parser.parse_args()
    config = dict(GOOGLE_DEFAULTS)
    if args.config:
        import json
        config.update(json.loads(args.config.read_text(encoding="utf-8")))
    config["keywords"] = args.keyword
    for flag, key in (("count", "max_images"), ("output", "output_dir"), ("proxy", "proxy"), ("min_width", "min_width"), ("min_height", "min_height")):
        if (value := getattr(args, flag)) is not None:
            config[key] = value
    if args.show_browser:
        config["headless"] = False
    try:
        return run(validate_config(config, "google"))
    except ValueError as error:
        parser.error(str(error))


raise SystemExit(main())
