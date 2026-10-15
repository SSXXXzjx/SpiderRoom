"""Discovery and download orchestration, counting only effective images."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from pathlib import Path

from .browser import GoogleBrowser, GoogleVerificationError, StopRequested
from .downloader import ImageDownloader, keyword_folder


class GoogleCrawler:
    def __init__(self, config, image_output, emit, stopped=lambda: False):
        self.config, self.output, self.emit, self.stopped = config, image_output, emit, stopped

    async def run(self):
        root = Path(self.config["output_dir"]).expanduser().resolve()
        errors = []
        async with GoogleBrowser(self.config, self.emit, self.stopped) as browser:
            self.config["user_agent"] = await browser.page.evaluate("navigator.userAgent")
            downloader = ImageDownloader(self.config, self.output, self.emit, self.stopped)
            pool = ThreadPoolExecutor(max_workers=self.config["download_workers"])
            try:
                for keyword in self.config["keywords"]:
                    browser.check_stop()
                    self.emit("target", target=keyword)
                    folder = root / keyword_folder(keyword)
                    folder.mkdir(parents=True, exist_ok=True)
                    used, filtered, failed = 0, 0, 0
                    limit = self.config["max_images"]
                    try:
                        async with aclosing(browser.batches(keyword)) as batches:
                            async for batch in batches:
                                offset = 0
                                while offset < len(batch) and (not limit or used < limit):
                                    browser.check_stop()
                                    count = min(self.config["download_workers"], limit - used if limit else len(batch))
                                    group = batch[offset:offset + count]
                                    offset += len(group)
                                    loop = asyncio.get_running_loop()
                                    results = await asyncio.gather(*(loop.run_in_executor(pool, downloader.download, item, folder) for item in group))
                                    used += sum(result in ("downloaded", "existing") for result in results)
                                    filtered += sum(result in ("filtered", "duplicate") for result in results)
                                    failed += results.count("failed")
                                if limit and used >= limit:
                                    break
                        if not used:
                            raise RuntimeError(f"关键词「{keyword}」未获得有效图片（过滤 / 重复 {filtered}，下载失败 {failed}），请调整条件或检查网络")
                        self.emit("log", message=f"关键词「{keyword}」完成：有效 {used}，过滤 / 重复 {filtered}，失败 {failed}", level="info")
                        if limit and used < limit:
                            self.emit("log", message=f"当前结果已结束，未达到 {limit} 张；已保留取得的图片", level="warning")
                        self.emit("target_done")
                    except (StopRequested, GoogleVerificationError):
                        raise
                    except Exception as error:
                        errors.append(str(error))
                        self.emit("log", message=str(error), level="error")
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
                downloader.close()
        if errors:
            raise RuntimeError(f"{len(errors)} 个关键词采集失败：{errors[-1]}")
