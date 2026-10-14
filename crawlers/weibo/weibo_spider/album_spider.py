"""通过 weibo.cn 相册页面下载图片，不启动微博正文爬虫。"""

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from queue import Queue
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from lxml import etree
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .browser_login import load_browser_cookies
from .image_output import ImageOutput


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:80]
    if not name or name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
        r"(?:COM|LPT)[1-9]", name, re.IGNORECASE
    ):
        name = "album_" + name
    return name


def image_url(source, original_size):
    parts = urlsplit(urljoin("https://weibo.cn", source))
    if not (parts.hostname or "").endswith(".sinaimg.cn"):
        return None
    path = parts.path
    if original_size:
        path = re.sub(r"^/(?:wap\d+|thumb\d+|small|bmiddle|mw\d+|orj\d+)/", "/large/", path)
    return urlunsplit(("https", parts.netloc, path, parts.query, ""))


class AlbumSpider:
    def __init__(self, config, base_dir):
        self.config = config
        self.validate()
        self.image_output = ImageOutput(config.get("filename_prefix", "weibo"), config.get("image_long_edge", 1024))
        output = Path(config["output_dir"]).expanduser()
        self.output = output if output.is_absolute() else Path(base_dir) / output
        cookie = config.get("cookie", "").strip()
        manual_cookies = SimpleCookie()
        if cookie:
            try:
                manual_cookies.load(cookie)
            except CookieError:
                raise ValueError("Cookie 格式不正确，请只粘贴 Cookie 的值。") from None
            if not manual_cookies:
                raise ValueError("Cookie 格式不正确，请只粘贴 Cookie 的值。")
        browser_cookies = None if cookie else load_browser_cookies(
            config.get("cookie_browser", "auto"), config.get("cookie_file", "")
        )
        self.session = self.make_session()
        self.media_session = self.make_session()
        if cookie:
            for name, morsel in manual_cookies.items():
                self.session.cookies.set(name, morsel.value, domain=".weibo.cn", path="/")
        else:
            self.session.cookies.update(browser_cookies)
        self.page_count = 0
        self.last_request = None

    def validate(self):
        config = self.config
        if not isinstance(config.get("cookie", ""), str):
            raise ValueError("cookie 必须是字符串；留空时自动读取浏览器。")
        if config.get("cookie_browser", "auto") not in ("auto", "chrome", "edge", "firefox"):
            raise ValueError("cookie_browser 必须为 auto、chrome、edge 或 firefox。")
        if not isinstance(config.get("cookie_file", ""), str):
            raise ValueError("cookie_file 必须是路径字符串。")
        for key in ("user_ids", "album_urls", "album_names"):
            if not isinstance(config.get(key), list):
                raise ValueError(f"{key} 必须是列表。")
        if not config["album_urls"] and (
            not config["user_ids"] or any(not str(uid).isdigit() for uid in config["user_ids"])
        ):
            raise ValueError("请将 user_ids 改成用户数字 UID，或填写 album_urls。")
        for url in config["album_urls"]:
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.netloc != "weibo.cn" or not parts.path.startswith("/album/"):
                raise ValueError("album_urls 必须是 https://weibo.cn/album/... 相册链接。")
        for key in ("max_images", "max_pages", "retries"):
            if type(config.get(key)) is not int or config[key] < (1 if key == "max_pages" else 0):
                raise ValueError(f"{key} 必须是{'正' if key == 'max_pages' else '非负'}整数。")
        workers = config.get("download_workers", 6)
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError("download_workers 必须是 1 到 32 的整数。")
        prefix = config.get("filename_prefix", "weibo")
        if not isinstance(prefix, str) or not prefix.strip() or safe_name(prefix) != prefix:
            raise ValueError("filename_prefix 必须是有效的非空文件名前缀。")
        long_edge = config.get("image_long_edge", 1024)
        if type(long_edge) is not int or long_edge < 1:
            raise ValueError("image_long_edge 必须是正整数。")
        for key in ("request_interval", "timeout"):
            if not isinstance(config.get(key), (int, float)) or config[key] < 0 or (key == "timeout" and config[key] == 0):
                raise ValueError(f"{key} 必须是有效秒数，timeout 必须大于 0。")
        if not isinstance(config.get("output_dir"), str) or not config["output_dir"].strip():
            raise ValueError("output_dir 不能为空。")
        if any(not isinstance(name, str) for name in config["album_names"]):
            raise ValueError("album_names 中必须填写文字。")

    def make_session(self):
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"})
        retry = Retry(total=self.config["retries"], backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def pause(self):
        if self.last_request is not None:
            time.sleep(max(0, self.config["request_interval"] - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()

    def get_page(self, url):
        if self.page_count >= self.config["max_pages"]:
            raise RuntimeError("已达到 max_pages 页面上限；可在 main.py 中调大后重试。")
        self.page_count += 1
        self.pause()
        try:
            with self.session.get(url, timeout=self.config["timeout"]) as response:
                response.raise_for_status()
                selector = etree.HTML(response.content, parser=etree.HTMLParser(encoding="utf-8"))
                final_url = urlsplit(response.url)
        except requests.RequestException as error:
            raise RuntimeError(f"相册页面请求失败：{url}（{type(error).__name__}）") from None
        if selector is None:
            raise RuntimeError("微博返回了空页面。")
        text = "".join(selector.xpath("//body//text()"))
        context = f"请求：{urlsplit(url).path}；返回：{final_url.scheme}://{final_url.hostname}{final_url.path}。"
        if any(word in text for word in ("访问频次过高", "请输入验证码")):
            raise RuntimeError(context + "微博要求验证或限制访问，请在浏览器打开相册完成验证后重试。")
        if selector.xpath('//input[@type="password"]') or (
            final_url.hostname in {"passport.weibo.cn", "passport.sina.cn", "login.sina.com.cn"}
            and "login" in final_url.path.lower()
        ):
            raise RuntimeError(context + "服务器返回登录页，请从浏览器中能正常显示图片的相册请求获取 Cookie。")
        if final_url.hostname != "weibo.cn":
            raise RuntimeError(context + "相册跳转到当前程序不支持的站点，并不能判定 Cookie 无效。")
        if any(word in text for word in ("帐号登录", "账号登录")) and not selector.xpath(
            '//a[contains(@href,"logout")] | //img[contains(@src,"sinaimg.cn")]'
        ):
            raise RuntimeError(context + "页面提示登录，请从正常显示图片的相册请求获取 Cookie。")
        return selector

    def pages(self, first_url):
        url = first_url
        visited = set()
        while url and url not in visited:
            visited.add(url)
            selector = self.get_page(url)
            yield url, selector
            links = selector.xpath('//a[normalize-space(.)="下页" or normalize-space(.)="下一页"]/@href')
            next_url = urljoin(url, links[0]) if links else None
            url = next_url if next_url and urlsplit(next_url).netloc == "weibo.cn" else None

    def albums(self, uid):
        found = set()
        for page_url, selector in self.pages(f"https://weibo.cn/{uid}/photo"):
            for link in selector.xpath('//a[contains(@href,"/album/")]'):
                url = urljoin(page_url, link.get("href"))
                parts = urlsplit(url)
                if parts.netloc != "weibo.cn" or not re.fullmatch(r"/album/(?:\d+|albummblog)/?", parts.path):
                    continue
                title = "".join(link.itertext()).strip()
                title = title or "".join(link.xpath(".//img/@alt"))
                if not title or (not self.config["include_avatar"] and "头像" in title):
                    continue
                if self.config["album_names"] and not any(name in title for name in self.config["album_names"]):
                    continue
                identity = parts.path.rstrip("/")
                if identity not in found:
                    found.add(identity)
                    yield url, title

    def download(self, source, folder, referer, session=None):
        session = self.media_session if session is None else session
        original = image_url(source, self.config["original_size"])
        fallback = image_url(source, False)
        if not original:
            return None
        parts = urlsplit(original)
        identity = Path(parts.path).name
        if self.image_output.existing(folder, identity):
            return "existing"
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / (hashlib.sha256(identity.encode()).hexdigest()[:24] + ".download.part")
        for url in dict.fromkeys([original, fallback]):
            try:
                with session.get(url, headers={"Referer": referer}, stream=True,
                                            timeout=self.config["timeout"]) as response:
                    response.raise_for_status()
                    if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                        raise ValueError("服务器未返回图片")
                    with temporary.open("wb") as output:
                        for chunk in response.iter_content(64 * 1024):
                            output.write(chunk)
                if temporary.stat().st_size == 0:
                    raise ValueError("图片内容为空")
                try:
                    target = self.image_output.save(temporary, folder, identity)
                except (OSError, ValueError) as error:
                    print(f"图片转换失败：{identity}（{type(error).__name__}），尝试备用地址。")
                    continue
                print(f"已保存：{target}")
                return "downloaded"
            except (requests.RequestException, ValueError):
                continue
            finally:
                temporary.unlink(missing_ok=True)
        print(f"图片下载失败：{identity}")
        return "failed"

    def crawl(self, albums, folder):
        workers = self.config.get("download_workers", 6)
        with ExitStack() as stack:
            sessions = Queue()
            for worker in range(workers):
                sessions.put(stack.enter_context(self.make_session()))
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=workers))
            self.crawl_albums(albums, folder, executor, sessions, workers)

    def download_with_session(self, source, folder, referer, sessions):
        session = sessions.get()
        try:
            return self.download(source, folder, referer, session=session)
        finally:
            sessions.put(session)

    def crawl_albums(self, albums, folder, executor, sessions, workers):
        seen = set()
        completed = downloaded = failed = 0
        for album_url, title in albums:
            album_id = urlsplit(album_url).path.strip("/").replace("/", "_")
            destination = folder / (safe_name(title) + "_" + safe_name(album_id))
            print(f"正在读取相册：{title}")
            for page_url, selector in self.pages(album_url):
                sources = selector.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," c ")]//img/@src')
                unique_sources = []
                for source in sources:
                    normalized = image_url(source, True)
                    if not normalized:
                        continue
                    identity = Path(urlsplit(normalized).path).name
                    if identity in seen:
                        continue
                    seen.add(identity)
                    unique_sources.append(source)
                offset = 0
                while offset < len(unique_sources):
                    batch_size = workers
                    if self.config["max_images"]:
                        batch_size = min(batch_size, self.config["max_images"] - completed)
                    batch = unique_sources[offset:offset + batch_size]
                    futures = [executor.submit(self.download_with_session, source, destination,
                                               page_url, sessions) for source in batch]
                    offset += len(batch)
                    for future in futures:
                        status = future.result()
                        if status in {"downloaded", "existing"}:
                            completed += 1
                            downloaded += status == "downloaded"
                        elif status == "failed":
                            failed += 1
                    if self.config["max_images"] and completed >= self.config["max_images"]:
                        print(f"达到图片上限：{completed} 张，本次新下载 {downloaded} 张，失败 {failed} 张。")
                        return
        print(f"完成：有效图片 {completed} 张，本次新下载 {downloaded} 张，失败 {failed} 张。")
        if not completed:
            print("没有获取到图片，请检查 UID、相册筛选、访问权限和 Cookie。")

    def run(self):
        try:
            if self.config["album_urls"]:
                for url in dict.fromkeys(self.config["album_urls"]):
                    self.page_count = 0
                    self.crawl([(url, "指定相册")], self.output / "selected_albums")
            else:
                for uid in dict.fromkeys(str(value) for value in self.config["user_ids"]):
                    self.page_count = 0
                    print(f"正在获取用户 {uid} 的相册")
                    self.crawl(self.albums(uid), self.output / uid)
        finally:
            self.session.close()
            self.media_session.close()
