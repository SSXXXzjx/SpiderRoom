"""Run from SpiderRoom: python -m crawlers.pinterest TARGET --count 2."""
import argparse
import json
from pathlib import Path

from server import PINTEREST_DEFAULTS, validate_config
from pinterest_worker import run


def main():
    parser = argparse.ArgumentParser(description='Pinterest 图片采集：Pin / 画板 / 分区 / 用户')
    parser.add_argument('targets',nargs='+')
    parser.add_argument('--count',type=int,help='每条输入链接有效图片上限；0 为不限')
    parser.add_argument('--output')
    parser.add_argument('--proxy')
    parser.add_argument('--cookie-file',type=Path,help='读取 Cookie 请求头文本，仅本次使用')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--show-browser',action='store_true')
    args=parser.parse_args()
    config=dict(PINTEREST_DEFAULTS)
    if args.config:
        config.update(json.loads(args.config.read_text(encoding='utf-8')))
    config['pinterest_urls']=args.targets
    for flag,key in (('count','max_images'),('output','output_dir'),('proxy','proxy')):
        if getattr(args,flag) is not None:
            config[key]=getattr(args,flag)
    if args.show_browser:
        config['headless']=False
    try:
        config=validate_config(config,'pinterest')
        config['cookie']=args.cookie_file.read_text(encoding='utf-8').strip() if args.cookie_file else ''
        return run(config)
    except (ValueError,OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
