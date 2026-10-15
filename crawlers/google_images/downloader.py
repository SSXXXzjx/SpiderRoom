"""Bounded streamed downloads, real image validation, and resumable output."""

import hashlib
import json
import re
import tempfile
import threading
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from .browser import StopRequested


def keyword_folder(keyword):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", keyword).strip(" .")[:64] or "关键词"
    return f"{name}_{hashlib.sha256(keyword.encode()).hexdigest()[:8]}"


class ImageDownloader:
    def __init__(self, config, image_output, emit, stopped=lambda: False):
        self.config, self.output, self.emit, self.stopped = config, image_output, emit, stopped
        self.local = threading.local()
        self.sessions = []
        self.session_lock = threading.Lock()
        self.save_lock = threading.Lock()
        # Filter changes must not reuse an old, undersized accepted result.
        self.filter_key = f"min={config['min_width']}x{config['min_height']}:"

    def close(self):
        for session in self.sessions:
            session.close()

    def check_stop(self):
        if self.stopped():
            raise StopRequested()

    def pause(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def session(self):
        if not hasattr(self.local, "session"):
            session = requests.Session()
            session.headers.update({"User-Agent": self.config.get("user_agent", "Mozilla/5.0"), "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
            if self.config["proxy"]:
                session.trust_env = False
                session.proxies.update(http=self.config["proxy"], https=self.config["proxy"])
            self.local.session = session
            with self.session_lock:
                self.sessions.append(session)
        return self.local.session

    def download(self, item, folder):
        self.check_stop()
        url_identity = self.filter_key + item.url
        if self.output.existing(folder, url_identity):
            self.emit("image", result="existing", bytes=0)
            return "existing"
        # Known dimensions can save requests; the actual image is always verified below.
        if (item.width and item.width < self.config["min_width"]) or (item.height and item.height < self.config["min_height"]):
            return "filtered"
        try:
            with tempfile.TemporaryDirectory(prefix="spiderroom-google-") as temporary:
                source = Path(temporary) / "image.part"
                self.fetch(item, source)
                self.check_stop()
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(source) as opened:
                        opened.verify()
                    with Image.open(source) as opened:
                        oriented = ImageOps.exif_transpose(opened)
                        width, height = oriented.size
                        oriented.load()
                if width < self.config["min_width"] or height < self.config["min_height"]:
                    return "filtered"
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                content_identity = self.filter_key + "sha256:" + digest
                with self.save_lock:
                    self.check_stop()
                    if self.output.existing(folder, url_identity):
                        self.emit("image", result="existing", bytes=0)
                        return "existing"
                    if self.config["deduplicate"] and self.output.existing(folder, content_identity):
                        self.emit("log", message="跳过同一关键词下内容重复的图片", level="info")
                        return "duplicate"
                    target = self.output.save(source, folder, url_identity)
                    if self.config["deduplicate"]:
                        self.alias(folder, content_identity, url_identity)
                    metadata = {**item.to_dict(), "width": width, "height": height, "sha256": digest, "file": target.name}
                    with (folder / ".sources.jsonl").open("a", encoding="utf-8") as report:
                        report.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                    self.emit("image", result="downloaded", bytes=target.stat().st_size)
                    self.emit("log", message=f"已保存 {target.name} · {width} × {height}", level="success")
                    return "downloaded"
        except StopRequested:
            raise
        except (OSError, ValueError, SyntaxError, requests.RequestException, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            # Do not print signed image URLs or proxy credentials into task logs.
            host = urlsplit(item.url).hostname or "来源站"
            reason = str(error) if isinstance(error, ValueError) and not isinstance(error, requests.RequestException) else type(error).__name__
            self.emit("image", result="failed", bytes=0)
            self.emit("log", message=f"图片下载失败，已跳过：{host}（{reason[:160]}）", level="warning")
            return "failed"

    def alias(self, folder, alias, original):
        with self.output.lock:
            record = self.output.record(folder)
            record[self.output.key(alias)] = record[self.output.key(original)]
            index = folder / ".image_index.json"
            temporary = index.with_suffix(".json.part")
            try:
                temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(index)
            finally:
                temporary.unlink(missing_ok=True)

    def fetch(self, item, source):
        maximum = self.config["max_file_mb"] * 1024 * 1024
        referer = item.source_url if item.source_url.startswith(("https://", "http://")) else "https://www.google.com/"
        for attempt in range(self.config["retries"] + 1):
            self.check_stop()
            try:
                started = time.monotonic()
                with self.session().get(item.url, stream=True, timeout=self.config["timeout"], headers={"Referer": referer}) as response:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < self.config["retries"]:
                        try:
                            delay = min(15, max(1, float(response.headers.get("Retry-After", "0"))))
                        except ValueError:
                            delay = min(2 ** attempt, 8)
                        self.pause(delay)
                        continue
                    response.raise_for_status()
                    if int(response.headers.get("Content-Length") or 0) > maximum:
                        raise ValueError("文件超过大小限制")
                    size = 0
                    with source.open("wb") as target:
                        for chunk in response.iter_content(65536):
                            self.check_stop()
                            if time.monotonic() - started > self.config["timeout"]:
                                raise requests.Timeout("Image total transfer timeout")
                            size += len(chunk)
                            if size > maximum:
                                raise ValueError("文件超过大小限制")
                            target.write(chunk)
                    if not size:
                        raise ValueError("来源返回空文件")
                return
            except (requests.ConnectionError, requests.Timeout):
                if attempt == self.config["retries"]:
                    raise
                self.pause(min(2 ** attempt, 8))
