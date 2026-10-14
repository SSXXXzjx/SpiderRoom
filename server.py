import argparse
import ast
import copy
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
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


def validate_config(values):
    if not isinstance(values, dict):
        raise ValueError("配置格式错误")
    config = {key: values.get(key, value) for key, value in DEFAULTS.items()}
    for key in ("user_ids", "album_urls", "album_names"):
        if not isinstance(config[key], list) or any(not isinstance(item, str) for item in config[key]):
            raise ValueError(f"{key} 必须是文本列表")
        config[key] = list(dict.fromkeys(item.strip() for item in config[key] if item.strip()))
    if not config["album_urls"] and not config["user_ids"]:
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


class Room:
    def __init__(self, root=ROOT):
        self.root = root
        self.lock = threading.RLock()
        self.logs = deque(maxlen=800)
        self.sequence = 0
        self.process = None
        self.reader = None
        self.cookie = ""
        self.started = None
        self.finished = None
        self.samples = deque(maxlen=60)
        self.sample_time = 0
        self.sample_count = 0
        manifest = json.loads((root / "tools.json").read_text(encoding="utf-8"))
        self.tools = manifest["tools"]
        for tool in self.tools:
            path = Path(tool["path"]).expanduser()
            tool["path"] = str((path if path.is_absolute() else root / path).resolve())
        self.tool = self.tools[0]
        self.spider_path = Path(self.tool["path"])
        self.config_path = root / "data" / "weibo-config.json"
        defaults = copy.deepcopy(DEFAULTS)
        entry = self.spider_path / "main.py"
        if entry.is_file():
            for node in ast.parse(entry.read_text(encoding="utf-8-sig")).body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "CONFIG" for target in node.targets):
                    defaults.update({key: value for key, value in ast.literal_eval(node.value).items() if key in DEFAULTS})
                    break
        output = Path(defaults["output_dir"]).expanduser()
        if not output.is_absolute():
            defaults["output_dir"] = str((self.spider_path / output).resolve())
        self.config = validate_config(json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else defaults)
        self.history_path = root / "data" / "history.json"
        self.history = json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else []
        self.run_config = copy.deepcopy(self.config)
        self.state = self.empty_state()

    def empty_state(self):
        return {"status": "idle", "downloaded": 0, "existing": 0, "failed": 0,
                "pages": 0, "bytes": 0, "target": "—", "album": "—", "targets_done": 0,
                "targets_total": len(self.config["album_urls"] or self.config["user_ids"]), "error": ""}

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
                    "tools": [{**tool, "available": (Path(tool["path"]) / "weibo_spider" / "album_spider.py").is_file()} for tool in self.tools],
                    "output_dir": self.run_config["output_dir"], "connected": True}

    def save(self, values):
        config = validate_config(values)
        with self.lock:
            if self.state["status"] in ("running", "stopping"):
                raise ValueError("请先停止任务再修改设置")
            write_json(self.config_path, config)
            self.config = config
            if self.state["status"] == "idle":
                self.run_config = copy.deepcopy(config)
                self.state["targets_total"] = len(config["album_urls"] or config["user_ids"])
        return config

    def start(self, values, cookie):
        with self.lock:
            if self.state["status"] in ("running", "stopping"):
                raise ValueError("任务正在运行")
            if not isinstance(cookie, str) or not cookie.strip():
                raise ValueError("请填写 weibo.cn 的 Cookie")
            if not (self.spider_path / "weibo_spider" / "album_spider.py").is_file():
                raise ValueError("未找到微博相册爬虫，请检查 tools.json 中的路径")
            self.save(values)
            self.run_config = copy.deepcopy(self.config)
            self.cookie = cookie.strip()
            self.state = self.empty_state()
            self.logs.clear()
            self.samples.clear()
            self.started = self.sample_time = time.monotonic()
            self.finished = None
            self.sample_count = 0
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            try:
                self.process = subprocess.Popen(
                    [self.tool.get("python") or sys.executable, "-u", str(self.root / "worker.py")],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", cwd=self.spider_path,
                    env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.process.stdin.write(json.dumps({"path": str(self.spider_path), "config": {**self.run_config, "cookie": self.cookie}}, ensure_ascii=False) + "\n")
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
                self.process.terminate()

    def shutdown(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.state["status"] = "stopping"
                self.process.terminate()
        if self.reader:
            self.reader.join(timeout=5)


def make_handler(room):
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
            path = urlsplit(self.path).path
            if path == "/api/state":
                self.send(200, room.snapshot())
            elif path == "/api/config":
                with room.lock:
                    self.send(200, copy.deepcopy(room.config))
            else:
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
                path = urlsplit(self.path).path
                if path == "/api/config":
                    result = room.save(payload.get("config"))
                    self.send(200, {"config": result})
                elif path == "/api/start":
                    room.start(payload.get("config"), payload.get("cookie", ""))
                    self.send(200, {"ok": True})
                elif path == "/api/stop":
                    room.stop()
                    self.send(200, {"ok": True})
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
        room.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
