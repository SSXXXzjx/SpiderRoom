"""将小红书 PC API 接入平台的任务和图片事件协议。"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from worker import LogStream, emit as emit_event


SECRET_VALUES = []


def emit(kind, **values):
    if isinstance(values.get("message"), str):
        for secret in SECRET_VALUES:
            values["message"] = values["message"].replace(secret, "[Cookie]")
    emit_event(kind, **values)


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:80]
    return value or "未命名"


def image_sources(card, original=True):
    for index, image in enumerate(card.get("image_list") or []):
        variants = [item for item in image.get("info_list", []) if item.get("url")]
        preferred = "WB_DFT" if original else "WB_PRV"
        url = next((item["url"] for item in variants if item.get("image_scene") == preferred), None)
        url = url or image.get("url_default") or (variants[-1]["url"] if variants else None) or image.get("url_pre")
        if url and urlsplit(url).scheme in ("http", "https"):
            yield index, url


class XhsCrawler:
    def __init__(self, api, config, image_output):
        self.api = api
        self.config = config
        self.image_output = image_output
        self.root = Path(config["output_dir"]).expanduser().resolve()
        self.last_request = 0

    def request(self, method, *args, **kwargs):
        message = "请求失败"
        for attempt in range(self.config["retries"] + 1):
            time.sleep(max(0, self.config["request_interval"] - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                success, message, result = method(*args, **kwargs)
                emit("page")
                if success:
                    return (result or {}).get("data") or {}
            except Exception as error:
                message = str(error)
            if attempt < self.config["retries"]:
                emit("log", message=f"请求失败，重试 {attempt + 1}：{message}", level="warning")
        raise RuntimeError(f"小红书接口请求失败：{message}。请检查完整 Cookie、链接参数及账号状态")

    @staticmethod
    def note_link(item):
        note_id = item.get("note_id") or item.get("id")
        if not note_id or item.get("model_type", "note") != "note":
            return None
        return f"https://www.xiaohongshu.com/explore/{note_id}?" + urlencode({
            "xsec_token": item.get("xsec_token", ""), "xsec_source": "pc_search",
        })

    def notes(self, target):
        mode = self.config["target_mode"]
        if mode == "note":
            parts = urlsplit(target)
            yield parts._replace(path=parts.path.rstrip("/")).geturl()
            return
        cursor = ""
        seen = set()
        emitted = 0
        if mode == "profile":
            parts = urlsplit(target)
            user_id = parts.path.rstrip("/").split("/")[-1]
            query = parse_qs(parts.query)
        else:
            from xhs_utils.xhs_pc.params import generate_search_id
            root_search_id = generate_search_id()
        for page in range(1, self.config["max_pages"] + 1):
            if mode == "profile":
                data = self.request(self.api.get_user_note_info, user_id, cursor,
                                    query.get("xsec_token", [""])[0], query.get("xsec_source", ["pc_user"])[0])
                items = data.get("notes") or []
            else:
                data = self.request(self.api.search_note, target, page=page,
                                    sort_type_choice=self.config["sort_type"], note_type=2,
                                    search_id=generate_search_id(root_search_id))
                items = data.get("items") or []
            for item in items:
                link = self.note_link(item)
                note_id = item.get("note_id") or item.get("id")
                if link and note_id not in seen:
                    seen.add(note_id)
                    emitted += 1
                    yield link
                    if mode == "search" and emitted >= self.config["search_limit"]:
                        return
            if not items or not data.get("has_more"):
                return
            if mode == "profile":
                next_cursor = str(data.get("cursor") or "")
                if not next_cursor or next_cursor == cursor:
                    return
                cursor = next_cursor
        emit("log", message="已达到当前目标的页面上限", level="info")

    def download(self, task):
        import requests

        url, folder, identity = task
        if self.image_output.existing(folder, identity):
            emit("image", result="existing", bytes=0)
            return "existing"
        for attempt in range(self.config["retries"] + 1):
            try:
                # 图片 CDN 无需登录 Cookie；每个线程独立请求。
                with requests.get(url, headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
                                  timeout=self.config["timeout"], stream=True) as response:
                    response.raise_for_status()
                    with tempfile.TemporaryDirectory(prefix="spiderroom-xhs-") as temporary:
                        source = Path(temporary) / "image"
                        with source.open("wb") as output:
                            for chunk in response.iter_content(128 * 1024):
                                output.write(chunk)
                        target = self.image_output.save(source, folder, identity)
                emit("image", result="downloaded", bytes=target.stat().st_size)
                emit("log", message=f"已保存 {target.relative_to(self.root)}", level="success")
                return "downloaded"
            except Exception as error:
                if attempt == self.config["retries"]:
                    emit("image", result="failed", bytes=0)
                    emit("log", message=f"图片下载失败 {identity}：{error}", level="warning")
                else:
                    time.sleep(self.config["request_interval"])
        return "failed"

    def crawl_target(self, target, pool):
        emit("target", target=target)
        used = 0
        seen = set()
        note_errors = 0
        fetched = 0
        limit = self.config["max_images"]
        # 主页和搜索共用同一目标限量；不会预先请求整个账号的全部笔记。
        for link in self.notes(target):
            note_id = urlsplit(link).path.rstrip("/").split("/")[-1]
            if note_id in seen:
                continue
            seen.add(note_id)
            try:
                data = self.request(self.api.get_note_info, link)
                items = data.get("items") or []
                if not items:
                    raise RuntimeError(f"笔记 {note_id} 未返回详情，请检查链接中的 xsec_token")
            except Exception as error:
                if self.config["target_mode"] == "note":
                    raise
                note_errors += 1
                emit("log", message=f"跳过无法读取的笔记 {note_id}：{error}", level="warning")
                continue
            fetched += 1
            card = items[0].get("note_card") or {}
            title = card.get("title") or note_id
            emit("album", title=title)
            if card.get("type") != "normal":
                emit("log", message=f"跳过视频笔记：{title}", level="info")
                continue
            user = card.get("user") or {}
            folder = self.root / safe_name(f"{str(user.get('nickname', '用户'))[:32]}_{user.get('user_id', 'unknown')}") / safe_name(f"{title[:40]}_{note_id}")
            sources = list(image_sources(card, self.config["original_size"]))
            if not sources:
                emit("log", message=f"笔记没有可下载图片：{title}", level="warning")
            tasks = [(url, folder, f"{note_id}:{index}") for index, url in sources]
            folder.mkdir(parents=True, exist_ok=True)
            while tasks and (not limit or used < limit):
                batch_size = min(self.config["download_workers"], limit - used if limit else len(tasks))
                batch, tasks = tasks[:batch_size], tasks[batch_size:]
                results = list(pool.map(self.download, batch))
                used += sum(result in ("downloaded", "existing") for result in results)
            if limit and used >= limit:
                emit("log", message=f"已达到当前目标的图片上限 {limit}", level="info")
                break
        if note_errors and not fetched:
            raise RuntimeError(f"当前目标的 {note_errors} 篇笔记均无法读取，请检查链接及登录状态")
        if note_errors:
            emit("log", message=f"当前目标已完成，{note_errors} 篇无法读取的笔记已跳过", level="warning")
        emit("target_done")

    def run(self):
        field = {"note": "note_urls", "profile": "profile_urls", "search": "keywords"}[self.config["target_mode"]]
        errors = []
        with ThreadPoolExecutor(max_workers=self.config["download_workers"]) as pool:
            for target in self.config[field]:
                try:
                    self.crawl_target(target, pool)
                except Exception as error:
                    errors.append(str(error))
                    emit("log", message=str(error), level="error")
        if errors:
            raise RuntimeError(f"{len(errors)} 个目标采集失败：{errors[-1]}")


def main():
    payload = json.loads(sys.stdin.readline())
    path, config = Path(payload["path"]), payload["config"]
    cookie = config.get("cookie", "")
    SECRET_VALUES[:] = sorted({cookie, *(part.split("=", 1)[1].strip() for part in cookie.split(";")
                                        if "=" in part and len(part.split("=", 1)[1].strip()) >= 8)} - {""}, key=len, reverse=True)
    sys.path.insert(0, str(path))
    sys.stdout = sys.stderr = LogStream(config.get("cookie", ""))
    auth = None
    try:
        if not shutil.which("node"):
            raise RuntimeError("小红书签名需要 Node.js 22.15+（推荐 24 LTS），请安装后重启平台")
        runtime = subprocess.run(
            ["node", "-e", "if(typeof require('zlib').crc32!=='function')process.exit(2);require('crypto-js')"],
            cwd=path, capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if runtime.returncode == 2:
            raise RuntimeError("当前 Node.js 不支持签名压缩，请升级到 Node.js 22.15+（推荐 24 LTS）")
        if runtime.returncode:
            raise RuntimeError("缺少小红书签名依赖，请在 crawlers/xhs 目录执行 npm install")
        from loguru import logger
        logger.remove()  # 原客户端异常堆栈可能含鉴权字段，统一交由平台报告错误。
        import apis.xhs_pc_apis as xhs_api
        from xhs_utils.xhs_pc import XHSPcAuth
        from crop_output import make_image_output
        sys.path.insert(0, str(Path(__file__).parent / "crawlers" / "weibo"))
        from weibo_spider.image_output import ImageOutput

        xhs_api.REQUEST_TIMEOUT = config["timeout"]
        emit("log", message="正在验证小红书登录状态…", level="info")
        auth = XHSPcAuth.from_cookie(config["cookie"])
        api = xhs_api.XHS_Apis(auth)
        image_output = make_image_output(ImageOutput, config["filename_prefix"], config["image_long_edge"],
                                         config["crop_bottom"], config["resize_enabled"])
        emit("log", message="登录成功，开始采集图文笔记（视频笔记自动跳过）", level="info")
        XhsCrawler(api, config, image_output).run()
        return 0
    except ModuleNotFoundError as error:
        emit("error", message=f"缺少小红书运行依赖 {error.name}，请在平台目录执行 python -m pip install -r requirements.txt")
    except Exception as error:
        message = str(error).replace(config.get("cookie", ""), "[Cookie]")
        emit("error", message=f"{type(error).__name__}: {message}")
    finally:
        if auth:
            auth.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
