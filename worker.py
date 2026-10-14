import json
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit


OUTPUT = sys.stdout
OUTPUT_LOCK = threading.Lock()


def emit(kind, **values):
    with OUTPUT_LOCK:
        OUTPUT.write(json.dumps({"type": kind, **values}, ensure_ascii=False) + "\n")
        OUTPUT.flush()


class LogStream:
    def __init__(self, cookie):
        self.local = threading.local()
        self.cookie = cookie

    def write(self, text):
        buffer = getattr(self.local, "buffer", "") + text
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            if line.strip():
                line = line.replace(self.cookie, "[Cookie]") if self.cookie else line
                level = "warning" if "失败" in line or "没有获取" in line else "success" if line.startswith("已保存") else "info"
                emit("log", message=line, level=level)
        self.local.buffer = buffer
        return len(text)

    def flush(self):
        pass


def main():
    payload = json.loads(sys.stdin.readline())
    path = Path(payload["path"])
    config = payload["config"]
    sys.path.insert(0, str(path))
    sys.stdout = sys.stderr = LogStream(config.get("cookie", ""))
    try:
        from weibo_spider.album_spider import AlbumSpider, image_url

        class ManagedSpider(AlbumSpider):
            def get_page(self, url):
                before = self.page_count
                try:
                    return super().get_page(url)
                finally:
                    if self.page_count > before:
                        emit("page")

            def crawl(self, albums, folder):
                target = folder.name
                if config["album_urls"]:
                    target = config["album_urls"][self.target_index]
                self.target_index += 1
                emit("target", target=target)
                super().crawl(self.report_albums(albums), folder)
                emit("target_done")

            def report_albums(self, albums):
                for url, title in albums:
                    emit("album", title=title)
                    yield url, title

            def download(self, source, folder, referer, session=None):
                result = super().download(source, folder, referer, session=session)
                size = 0
                if result == "downloaded":
                    identity = Path(urlsplit(image_url(source, True)).path).name
                    with self.image_output.lock:
                        filename = self.image_output.record(folder).get(self.image_output.key(identity))
                    if filename:
                        try:
                            size = (folder / filename).stat().st_size
                        except OSError:
                            pass
                emit("image", result=result, bytes=size)
                return result

        spider = ManagedSpider(config, base_dir=str(path))
        if config.get("crop_bottom", 0) or not config.get("resize_enabled", False):
            from crop_output import make_image_output
            from weibo_spider.image_output import ImageOutput

            spider.image_output = make_image_output(
                ImageOutput, config["filename_prefix"], config["image_long_edge"], config.get("crop_bottom", 0), config.get("resize_enabled", False)
            )
        emit("log", message=f"长边缩放：{str(config['image_long_edge']) + 'px' if config.get('resize_enabled', False) else '关闭 · 保留原图尺寸'}", level="info")
        if config.get("crop_bottom", 0):
            emit("log", message=f"底部裁剪：{config['crop_bottom']}px（输出尺寸）", level="info")
        spider.target_index = 0
        spider.run()
    except Exception as error:
        message = str(error)
        cookie = config.get("cookie", "")
        if cookie:
            message = message.replace(cookie, "[Cookie]")
        emit("error", message=f"{type(error).__name__}: {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
