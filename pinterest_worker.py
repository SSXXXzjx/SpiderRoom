"""Pinterest process adapter; credentials are received via stdin and kept in memory."""
import asyncio
import json
import sys
from pathlib import Path

from worker import LogStream, emit as platform_emit


def run(config, stop_file=''):
    from crawlers.google_images.browser import StopRequested
    def emit(kind, **values):
        if isinstance(values.get('message'),str):
            for secret in (config.get('cookie',''),config.get('proxy','')):
                if secret:
                    values['message'] = values['message'].replace(secret,'[凭据]')
            values['message'] = values['message'].replace('同一关键词下','同一采集目录下')
        platform_emit(kind,**values)
    try:
        from crawlers.pinterest.crawler import PinterestCrawler
        from crop_output import make_image_output
        from crawlers.weibo.weibo_spider.image_output import ImageOutput
        output = make_image_output(ImageOutput,config['filename_prefix'],config['image_long_edge'],config['crop_bottom'],config['resize_enabled'])
        asyncio.run(PinterestCrawler(config,output,emit,lambda:bool(stop_file and Path(stop_file).exists())).run())
        return 0
    except (StopRequested,KeyboardInterrupt):
        emit('log',message='采集已停止，已完成图片保留',level='warning')
        return 0
    except ModuleNotFoundError as error:
        emit('error',message=f'缺少依赖 {error.name}，请运行 python -m pip install -r requirements.txt')
    except Exception as error:
        # HTTP exceptions can include signed URLs; emit a stable message instead.
        emit('error',message=str(error))
    return 1


def main():
    payload = json.loads(sys.stdin.readline())
    sys.stdout = sys.stderr = LogStream(payload['config'].get('cookie',''))
    return run(payload['config'],payload.get('stop_file',''))


if __name__ == '__main__':
    raise SystemExit(main())
