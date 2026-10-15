import argparse
import ast
import copy
import io
import json
import mimetypes
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from crawlers.google_images.config import OPTIONS as GOOGLE_OPTIONS, validate_google
from crawlers.pinterest.config import OPTIONS as PINTEREST_OPTIONS, validate_pinterest


ROOT = Path(__file__).resolve().parent
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
DEFAULTS = {
    "user_ids": ["3576729842"], "album_urls": [], "album_names": [],
    "output_dir": str(ROOT / "downloads"), "max_images": 1000,
    "include_avatar": False, "original_size": True, "max_pages": 500,
    "request_interval": 1, "download_workers": 6, "filename_prefix": "weibo",
    "image_long_edge": 1024, "resize_enabled": False, "crop_bottom": 0, "timeout": 30, "retries": 3,
}
LIMITS = {
    "max_images": (0, 10000000), "max_pages": (1, 1000000),
    "download_workers": (1, 32), "image_long_edge": (1, 20000),
    "retries": (0, 20), "request_interval": (0, 3600), "timeout": (0.1, 3600),
    "crop_bottom": (0, 19999),
}


XHS_DEFAULTS = {
    **DEFAULTS, "user_ids": [], "note_urls": [], "profile_urls": [], "keywords": [],
    "target_mode": "note", "search_limit": 100, "sort_type": 0,
    "filename_prefix": "xhs", "output_dir": str(ROOT / "downloads" / "xhs"),
    "download_workers": 3, "request_interval": 1.5,
}


GOOGLE_DEFAULTS = {
    **DEFAULTS, **GOOGLE_OPTIONS, "user_ids": [], "filename_prefix": "google",
    "output_dir": str(ROOT / "downloads" / "google"), "max_images": 100,
    "max_pages": 50, "download_workers": 6, "request_interval": 1,
}


PINTEREST_DEFAULTS = {
    **DEFAULTS, **PINTEREST_OPTIONS, "user_ids": [], "filename_prefix": "pinterest",
    "output_dir": str(ROOT / "downloads" / "pinterest"), "max_images": 100,
    "max_pages": 100, "download_workers": 4,
}


def defaults_for(tool_id):
    return {"weibo": DEFAULTS, "xhs": XHS_DEFAULTS, "google": GOOGLE_DEFAULTS, "pinterest": PINTEREST_DEFAULTS}[tool_id]


def target_count(config, tool_id):
    if tool_id == "pinterest":
        return len(config["pinterest_urls"])
    if tool_id == "google":
        return len(config["keywords"])
    if tool_id == "xhs":
        return len(config[{"note": "note_urls", "profile": "profile_urls", "search": "keywords"}[config["target_mode"]]])
    return len(config["album_urls"] or config["user_ids"])


def tool_available(tool):
    entry = {"xhs": "apis/xhs_pc_apis.py", "weibo": "weibo_spider/album_spider.py", "google": "crawler.py", "pinterest": "crawler.py"}[tool["id"]]
    return (Path(tool["path"]) / entry).is_file()


def validate_config(values, tool_id="weibo", require_target=True):
    if not isinstance(values, dict):
        raise ValueError("配置格式错误")
    defaults = defaults_for(tool_id)
    config = {key: copy.deepcopy(values.get(key, value)) for key, value in defaults.items()}
    list_keys = ("pinterest_urls",) if tool_id == "pinterest" else (("keywords",) if tool_id == "google" else (("note_urls", "profile_urls", "keywords") if tool_id == "xhs" else ("user_ids", "album_urls", "album_names")))
    for key in list_keys:
        if not isinstance(config[key], list) or any(not isinstance(item, str) for item in config[key]):
            raise ValueError(f"{key} 必须是文本列表")
        config[key] = list(dict.fromkeys(item.strip() for item in config[key] if item.strip()))
    if tool_id == "pinterest":
        validate_pinterest(config, require_target)
    elif tool_id == "google":
        validate_google(config, require_target)
    elif tool_id == "xhs":
        if config["target_mode"] not in ("note", "profile", "search"):
            raise ValueError("小红书采集类型无效")
        if require_target and not target_count(config, tool_id):
            raise ValueError("请填写笔记链接、用户主页或关键词")
        for key, prefixes in (("note_urls", ("/explore/", "/discovery/item/")), ("profile_urls", ("/user/profile/",))):
            for url in config[key]:
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.hostname not in ("www.xiaohongshu.com", "xiaohongshu.com") or parts.port not in (None,443) or parts.username or not any(parts.path.startswith(prefix) and parts.path[len(prefix):].strip("/") for prefix in prefixes):
                    raise ValueError("请粘贴小红书网页版完整笔记 / 主页链接（保留 xsec_token 参数）")
        for key, maximum in (("search_limit", 100000), ("sort_type", 4)):
            if type(config[key]) is not int or not (1 if key == "search_limit" else 0) <= config[key] <= maximum:
                raise ValueError(f"{key} 数值无效")
    else:
        if require_target and not config["album_urls"] and not config["user_ids"]:
            raise ValueError("请填写用户 UID 或相册链接")
        if any(not item.isascii() or not item.isdigit() for item in config["user_ids"]):
            raise ValueError("用户 UID 必须是数字")
        for url in config["album_urls"]:
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.netloc != "weibo.cn" or not parts.path.startswith("/album/"):
                raise ValueError("相册链接必须是 https://weibo.cn/album/…")
    for key, (minimum, maximum) in LIMITS.items():
        value = config[key]
        if type(value) not in (int, float) or not minimum <= value <= maximum:
            raise ValueError(f"{key} 范围：{minimum}–{maximum}")
        if key not in ("request_interval", "timeout") and type(value) is not int:
            raise ValueError(f"{key} 必须是整数")
    for key in ("include_avatar", "original_size", "resize_enabled"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
    if config["resize_enabled"] and config["crop_bottom"] >= config["image_long_edge"]:
        raise ValueError("底部裁剪像素必须小于图片长边")
    for key in ("output_dir", "filename_prefix"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{key} 不能为空")
        config[key] = config[key].strip()
    prefix = config["filename_prefix"]
    if any(character in prefix for character in '<>:"/\\|?*') or any(ord(character) < 32 for character in prefix) or prefix.endswith((" ", ".")) or len(prefix) > 80:
        raise ValueError("文件名前缀包含无效字符")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{name}{number}" for name in ("COM", "LPT") for number in range(1, 10)}
    if prefix.upper() in reserved:
        raise ValueError("文件名前缀是系统保留名称")
    return config


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def choose_output_directory(initial):
    result = subprocess.run(
        [sys.executable, str(ROOT / "directory_picker.py"), str(initial)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        selected = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise ValueError("无法打开目录选择窗口，请手动填写保存目录") from error
    if result.returncode or selected.get("error"):
        raise ValueError(selected.get("error") or "目录选择失败")
    return selected.get("path")


class Room:
    def __init__(self, root=ROOT, tool_id="weibo"):
        self.root = root
        self.lock = threading.RLock()
        self.logs = deque(maxlen=800)
        self.sequence = 0
        self.process = None
        self.stop_file = None
        self.reader = None
        self.cookie = ""
        self.started = None
        self.finished = None
        self.samples = deque(maxlen=60)
        self.sample_time = 0
        self.sample_count = 0
        self.gallery_roots = {}
        self.gallery_lists = {}
        self.directory_picker_lock = threading.Lock()
        manifest = json.loads((root / "tools.json").read_text(encoding="utf-8"))
        self.tools = manifest["tools"]
        for tool in self.tools:
            path = Path(tool["path"]).expanduser()
            tool["path"] = str((path if path.is_absolute() else root / path).resolve())
        self.tool = next(tool for tool in self.tools if tool["id"] == tool_id)
        self.tool_id = tool_id
        self.spider_path = Path(self.tool["path"])
        self.config_path = root / "data" / f"{tool_id}-config.json"
        defaults = copy.deepcopy(defaults_for(tool_id))
        entry = self.spider_path / "main.py"
        if tool_id == "weibo" and entry.is_file():
            for node in ast.parse(entry.read_text(encoding="utf-8-sig")).body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "CONFIG" for target in node.targets):
                    defaults.update({key: value for key, value in ast.literal_eval(node.value).items() if key in DEFAULTS})
                    break
        output = Path(defaults["output_dir"]).expanduser()
        if not output.is_absolute():
            defaults["output_dir"] = str((self.spider_path / output).resolve())
        stored = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else defaults
        self.saved_cookie = stored.get("cookie", "") if tool_id != "google" else ""
        if not isinstance(self.saved_cookie, str):
            self.saved_cookie = ""
        self.config = validate_config(stored, tool_id, require_target=False)
        self.history_path = root / "data" / ("history.json" if tool_id == "weibo" else f"{tool_id}-history.json")
        self.history = json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else []
        self.run_config = copy.deepcopy(self.config)
        self.state = self.empty_state()

    def empty_state(self):
        return {"status": "idle", "downloaded": 0, "existing": 0, "failed": 0,
                "pages": 0, "bytes": 0, "target": "—", "album": "—", "targets_done": 0,
                "targets_total": target_count(self.config, self.tool_id), "error": ""}

    def log(self, message, level="info"):
        with self.lock:
            self.sequence += 1
            message = str(message).replace(self.cookie, "[Cookie]") if self.cookie else str(message)
            self.logs.append({"id": self.sequence, "time": datetime.now().strftime("%H:%M:%S"), "level": level, "message": message[:3000]})

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            active = self.state["status"] in ("running", "stopping")
            elapsed = ((now if active else self.finished or now) - self.started) if self.started else 0
            if active and now - self.sample_time >= 1:
                span = now - self.sample_time
                self.samples.append(round((self.state["downloaded"] - self.sample_count) / span, 2))
                self.sample_count = self.state["downloaded"]
                self.sample_time = now
            total = self.run_config["max_images"] * self.state["targets_total"]
            completed = self.state["downloaded"] + self.state["existing"]
            percent = min(100, round(completed / total * 100, 1)) if total else None
            return {**copy.deepcopy(self.state), "elapsed": max(0, int(elapsed)), "limit": total,
                    "progress": percent, "rate": self.samples[-1] if active and self.samples else 0,
                    "samples": list(self.samples), "logs": list(self.logs), "history": self.history,
                    "tool_id": self.tool_id,
                    "tools": [{**tool, "available": tool_available(tool), "status": getattr(self, "rooms", {}).get(tool["id"], self).state["status"]} for tool in self.tools],
                    "output_dir": self.run_config["output_dir"], "connected": True}

    def settings(self):
        with self.lock:
            return {**copy.deepcopy(self.config), "cookie": self.saved_cookie}

    def save(self, values, cookie=None):
        config = validate_config(values, self.tool_id)
        if cookie is not None and not isinstance(cookie, str):
            raise ValueError("Cookie 必须是文本")
        with self.lock:
            if self.state["status"] in ("running", "stopping"):
                raise ValueError("请先停止任务再修改设置")
            saved_cookie = self.saved_cookie if cookie is None else cookie.strip()
            if self.tool_id == "google":
                saved_cookie = ""
            write_json(self.config_path, {**config, "cookie": saved_cookie})
            self.saved_cookie = saved_cookie
            self.config = config
            if self.state["status"] == "idle":
                self.run_config = copy.deepcopy(config)
                self.state["targets_total"] = target_count(config, self.tool_id)
        return self.settings()

    def start(self, values, cookie):
        with self.lock:
            if self.state["status"] in ("running", "stopping"):
                raise ValueError("任务正在运行")
            if not isinstance(cookie, str):
                raise ValueError("Cookie 必须是文本")
            if self.tool_id in ("weibo", "xhs") and not cookie.strip():
                raise ValueError(f"请填写 {'xiaohongshu.com' if self.tool_id == 'xhs' else 'weibo.cn'} 的 Cookie")
            if not tool_available(self.tool):
                raise ValueError(f"未找到{self.tool['name']}爬虫，请检查 tools.json 中的路径")
            self.save(values)
            self.run_config = copy.deepcopy(self.config)
            self.cookie = cookie.strip() if self.tool_id != "google" else ""
            self.state = self.empty_state()
            self.logs.clear()
            self.samples.clear()
            self.started = self.sample_time = time.monotonic()
            self.finished = None
            self.sample_count = 0
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            worker = {"weibo": "worker.py", "xhs": "xhs_worker.py", "google": "google_worker.py", "pinterest": "pinterest_worker.py"}[self.tool_id]
            if self.tool_id in ("google", "pinterest"):
                self.stop_file = self.root / "data" / "runtime" / f"{self.tool_id}-{secrets.token_hex(12)}.stop"
            try:
                self.process = subprocess.Popen(
                    [self.tool.get("python") or sys.executable, "-u", str(self.root / worker)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", cwd=self.spider_path,
                    env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.process.stdin.write(json.dumps({"path": str(self.spider_path), "config": {**self.run_config, "cookie": self.cookie}, "stop_file": str(self.stop_file or "")}, ensure_ascii=False) + "\n")
                self.process.stdin.close()
            except (OSError, BrokenPipeError) as error:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                self.finished = time.monotonic()
                self.state.update(status="error", error=f"启动失败：{error}")
                self.cookie = ""
                raise ValueError(self.state["error"]) from None
            self.state["status"] = "running"
            self.log("任务已启动")
            self.reader = threading.Thread(target=self.read_worker, args=(self.process,), daemon=True)
            self.reader.start()

    def consume(self, event):
        with self.lock:
            kind = event.get("type")
            if kind == "log":
                self.log(event.get("message", ""), event.get("level", "info"))
            elif kind == "image":
                result = event.get("result")
                counter = {"downloaded": "downloaded", "existing": "existing", "failed": "failed"}.get(result)
                if counter:
                    self.state[counter] += 1
                self.state["bytes"] += max(0, event.get("bytes", 0))
            elif kind == "page":
                self.state["pages"] += 1
            elif kind == "target":
                self.state.update(target=event["target"], album="—")
            elif kind == "target_done":
                self.state["targets_done"] += 1
            elif kind == "album":
                self.state["album"] = event["title"]
            elif kind == "error":
                self.state["error"] = event.get("message", "运行失败")
                self.log(self.state["error"], "error")

    def read_worker(self, process):
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self.log(line.strip(), "warning")
                    continue
                if isinstance(event, dict):
                    self.consume(event)
            code = process.wait()
        finally:
            process.stdout.close()
        with self.lock:
            self.finished = time.monotonic()
            if self.stop_file:
                self.stop_file.unlink(missing_ok=True)
                self.stop_file = None
            if self.state["status"] == "stopping":
                self.state["status"] = "stopped"
                self.log("任务已停止，已下载图片保留", "warning")
            elif code != 0 or self.state["error"]:
                self.state["status"] = "error"
                if not self.state["error"]:
                    self.state["error"] = f"爬虫退出，代码 {code}"
                    self.log(self.state["error"], "error")
            else:
                self.state["status"] = "completed"
                self.log("任务已完成" if self.state["downloaded"] + self.state["existing"] else "任务结束，未获取到图片", "success" if self.state["downloaded"] + self.state["existing"] else "warning")
            self.cookie = ""
            self.history.insert(0, {"time": datetime.now().strftime("%m-%d %H:%M"), "status": self.state["status"], "downloaded": self.state["downloaded"], "failed": self.state["failed"], "elapsed": int(self.finished - self.started)})
            self.history = self.history[:20]
            try:
                write_json(self.history_path, self.history)
            except OSError as error:
                self.log(f"运行记录保存失败：{error}", "warning")

    def stop(self):
        with self.lock:
            if self.state["status"] == "stopping":
                return
            if self.state["status"] != "running":
                raise ValueError("当前没有运行中的任务")
            self.state["status"] = "stopping"
            if self.process and self.process.poll() is None:
                if self.tool_id in ("google", "pinterest"):
                    self.stop_file.parent.mkdir(parents=True, exist_ok=True)
                    self.stop_file.touch()
                    threading.Thread(target=self.finish_graceful_stop, args=(self.process,), daemon=True).start()
                else:
                    self.process.terminate()

    def finish_graceful_stop(self, process):
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                process.kill()

    def output_directory(self, value=None):
        with self.lock:
            value = self.run_config["output_dir"] if value is None else value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请填写保存目录")
        directory = Path(value.strip()).expanduser()
        return (directory if directory.is_absolute() else self.spider_path / directory).resolve()

    def images(self, values):
        directory = self.output_directory(values.get("output_dir"))
        offset, limit = values.get("offset", 0), values.get("limit", 60)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 120:
            raise ValueError("分页参数无效")
        if not directory.exists():
            return {"directory": str(directory), "images": [], "total": 0, "offset": offset}
        if not directory.is_dir():
            raise ValueError("保存路径不是文件夹")
        catalog = values.get("catalog")
        if catalog is not None:
            if not isinstance(catalog, str):
                raise ValueError("图片列表标识无效")
            with self.lock:
                if self.gallery_roots.get(catalog) != directory:
                    raise ValueError("图片列表已失效，请刷新")
                files = self.gallery_lists[catalog]
            return self.image_page(directory, files, catalog, offset, limit)
        files = []

        def walk_error(error):
            raise error

        for folder, folders, names in os.walk(directory, onerror=walk_error, followlinks=False):
            safe_folders = []
            for name in folders:
                child = Path(folder) / name
                try:
                    attributes = getattr(child.lstat(), "st_file_attributes", 0)
                    if not child.is_symlink() and not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and child.resolve().is_relative_to(directory):
                        safe_folders.append(name)
                except (OSError, ValueError):
                    continue
            folders[:] = safe_folders
            for name in names:
                path = Path(folder) / name
                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                try:
                    if not path.resolve().is_relative_to(directory):
                        continue
                    info = path.stat()
                    if info.st_size:
                        files.append((info.st_mtime_ns, path.relative_to(directory).as_posix(), info.st_size))
                except (OSError, ValueError):
                    continue
        files.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        with self.lock:
            token = secrets.token_urlsafe(18)
            self.gallery_roots[token] = directory
            self.gallery_lists[token] = files
            if len(self.gallery_roots) > 16:
                expired = next(iter(self.gallery_roots))
                del self.gallery_roots[expired]
                del self.gallery_lists[expired]
        return self.image_page(directory, files, token, offset, limit)

    def image_page(self, directory, files, token, offset, limit):
        images = []
        for modified, relative, size in files[offset:offset + limit]:
            query = urlencode({"tool": self.tool_id, "root": token, "path": relative, "v": modified})
            url = f"/api/image?{query}"
            images.append({"name": Path(relative).name, "path": relative, "size": size,
                           "url": url, "thumbnail": url + "&thumbnail=1"})
        return {"directory": str(directory), "images": images, "total": len(files), "offset": offset, "catalog": token}

    def image_file(self, query):
        with self.lock:
            directory = self.gallery_roots.get(query.get("root", [""])[0])
        if directory is None:
            raise ValueError("预览已失效，请刷新图片列表")
        relative = query.get("path", [""])[0]
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("图片路径无效")
        if not path.is_file():
            raise FileNotFoundError("图片不存在或已移动")
        if query.get("thumbnail") == ["1"]:
            with Image.open(path) as source:
                source.thumbnail((480, 480))
                thumbnail = ImageOps.exif_transpose(source).convert("RGB")
                body = io.BytesIO()
                thumbnail.save(body, format="JPEG", quality=80)
                return body.getvalue(), "image/jpeg"
        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def shutdown(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                if self.state["status"] == "running":
                    self.stop()
        if self.reader:
            self.reader.join(timeout=12 if self.tool_id in ("google", "pinterest") else 5)


def make_handler(default_room):
    rooms = {default_room.tool_id: default_room}
    for tool in default_room.tools:
        if tool["id"] not in rooms:
            rooms[tool["id"]] = Room(default_room.root, tool["id"])
    for item in rooms.values():
        item.rooms = rooms

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *arguments):
            pass

        def send(self, status, payload, content_type="application/json; charset=utf-8"):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if isinstance(payload, (dict, list)) else payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.allowed_host():
                self.send(403, {"error": "仅允许本机访问"})
                return
            query = parse_qs(urlsplit(self.path).query)
            room = rooms.get(query.get("tool", [default_room.tool_id])[0])
            if room is None:
                self.send(400, {"error": "未知爬虫工具"})
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                self.send(200, room.snapshot())
            elif path == "/api/config":
                with room.lock:
                    self.send(200, room.settings())
            elif path == "/api/image":
                try:
                    body, content_type = room.image_file(parse_qs(urlsplit(self.path).query))
                    self.send(200, body, content_type)
                except FileNotFoundError as error:
                    self.send(404, {"error": str(error)})
                except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
                    self.send(400, {"error": str(error)})
            else:
                icons = {"微博.png", "小红书.png", "谷歌.png", "Pinterest.png", "裁剪,剪开.png",
                         "微博-display.png", "小红书-display.png", "Pinterest-display.png"}
                icon_path = unquote(path)
                if icon_path.startswith("/resources/icons/") and icon_path.removeprefix("/resources/icons/") in icons:
                    try:
                        self.send(200, (room.root / icon_path.lstrip("/")).read_bytes(), "image/png")
                    except OSError:
                        self.send(404, {"error": "图标不存在"})
                    return
                assets = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js"}
                filename = assets.get(path)
                if not filename:
                    self.send(404, {"error": "不存在"})
                    return
                file_path = room.root / filename
                content_type = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(file_path.suffix, mimetypes.guess_type(filename)[0])
                self.send(200, file_path.read_bytes(), content_type + "; charset=utf-8")

        def do_POST(self):
            origin = self.headers.get("Origin")
            origins = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
            if not self.allowed_host() or (origin and origin not in origins) or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send(403, {"error": "请求来源无效"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("请求大小无效")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求格式错误")
                tool_id = parse_qs(urlsplit(self.path).query).get("tool", [default_room.tool_id])[0]
                room = rooms.get(tool_id)
                if room is None:
                    raise ValueError("未知爬虫工具")
                path = urlsplit(self.path).path
                if path == "/api/config":
                    result = room.save(payload.get("config"), payload.get("cookie"))
                    self.send(200, {"config": result})
                elif path == "/api/start":
                    room.start(payload.get("config"), payload.get("cookie", ""))
                    self.send(200, {"ok": True})
                elif path == "/api/stop":
                    room.stop()
                    self.send(200, {"ok": True})
                elif path == "/api/select-output":
                    initial = room.output_directory(payload.get("initial"))
                    if not room.directory_picker_lock.acquire(blocking=False):
                        raise ValueError("目录选择窗口已打开")
                    try:
                        self.send(200, {"path": choose_output_directory(initial)})
                    finally:
                        room.directory_picker_lock.release()
                elif path == "/api/images":
                    self.send(200, room.images(payload))
                elif path == "/api/open-output":
                    with room.lock:
                        target = Path(room.run_config["output_dir"]).expanduser()
                        if not target.is_absolute():
                            target = room.spider_path / target
                        if not target.is_dir():
                            raise ValueError("输出目录尚未创建")
                        if os.name != "nt":
                            raise ValueError("打开目录功能需要 Windows")
                        os.startfile(str(target.resolve()))
                    self.send(200, {"ok": True})
                else:
                    self.send(404, {"error": "不存在"})
            except (ValueError, TypeError, OSError) as error:
                self.send(400, {"error": str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()
    room = Room()
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), make_handler(room))
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"SpiderRoom  {url}\nCtrl+C 停止服务", flush=True)
    if not arguments.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for item in room.rooms.values():
            item.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
