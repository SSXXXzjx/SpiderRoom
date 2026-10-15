"""Google worker: platform JSON events, shared processing, and stop-file cancellation."""

import asyncio
import json
import sys
from pathlib import Path

from worker import LogStream, emit as platform_emit


def run(config, stop_file=""):
    from crawlers.google_images.browser import StopRequested

    secrets = [config.get("proxy", "")]

    def emit(kind, **values):
        if isinstance(values.get("message"), str):
            for secret in secrets:
                if secret:
                    values["message"] = values["message"].replace(secret, "[代理]")
        platform_emit(kind, **values)

    stopped = lambda: bool(stop_file and Path(stop_file).exists())
    try:
        from crawlers.google_images.crawler import GoogleCrawler
        from crop_output import make_image_output
        from crawlers.weibo.weibo_spider.image_output import ImageOutput
        image_output = make_image_output(ImageOutput, config["filename_prefix"], config["image_long_edge"], config["crop_bottom"], config["resize_enabled"])
        asyncio.run(GoogleCrawler(config, image_output, emit, stopped).run())
        return 0
    except (StopRequested, KeyboardInterrupt):
        emit("log", message="采集已停止，已下载文件保留", level="warning")
        return 0
    except ModuleNotFoundError as error:
        emit("error", message=f"缺少依赖 {error.name}，请在平台目录运行 python -m pip install -r requirements.txt")
    except Exception as error:
        emit("error", message=str(error))
    return 1


def main():
    payload = json.loads(sys.stdin.readline())
    sys.stdout = sys.stderr = LogStream(payload["config"].get("proxy", ""))
    return run(payload["config"], payload.get("stop_file", ""))


if __name__ == "__main__":
    raise SystemExit(main())
