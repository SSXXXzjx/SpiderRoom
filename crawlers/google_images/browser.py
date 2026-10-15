"""Browser discovery with bounded scrolling and a preview fallback."""

import asyncio
import re
from urllib.parse import urlsplit, unquote

from .extract import ImageCandidate, extract_candidates, original_url, search_url


class StopRequested(Exception):
    pass


class GoogleVerificationError(RuntimeError):
    pass


class GoogleBrowser:
    def __init__(self, config, emit, stopped=lambda: False):
        self.config, self.emit, self.stopped = config, emit, stopped
        self.playwright = self.browser = self.context = self.page = None
        self.network_results = []

    def check_stop(self):
        if self.stopped():
            raise StopRequested()

    async def pause(self, seconds):
        for _ in range(max(1, int(seconds * 10))):
            self.check_stop()
            await asyncio.sleep(min(.1, max(0, seconds)))

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        options = {"headless": self.config["headless"]}
        if proxy := self.config["proxy"]:
            parts = urlsplit(proxy)
            host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
            options["proxy"] = {"server": f"{parts.scheme}://{host}:{parts.port}"}
            if parts.username:
                options["proxy"].update(username=unquote(parts.username), password=unquote(parts.password or ""))
        try:
            await self.open_browser(options)
            return self
        except BaseException:
            await self.close()
            raise

    async def open_browser(self, options):
        channel = self.config["browser_channel"]
        channels = ("chrome", "msedge", "chromium") if channel == "auto" else (channel,)
        for name in channels:
            try:
                self.browser = await self.playwright.chromium.launch(**options, **({"channel": name} if name != "chromium" else {}))
                self.emit("log", message=f"已启动 {name}，开始加载 Google 图片搜索", level="info")
                break
            except Exception:
                if name == channels[-1]:
                    raise RuntimeError("浏览器启动失败。请安装 Chrome / Edge，或执行 python -m playwright install chromium；同时检查代理设置") from None
        context_options = {"viewport":{"width":1440,"height":1000},"locale":self.config["search_language"]}
        self.context = await self.browser.new_context(**context_options)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(min(self.config["timeout"], 15) * 1000)
        self.page.on("response", self.on_response)

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def on_response(self, response):
        parts = urlsplit(response.url)
        if parts.hostname != "www.google.com" or not parts.path.startswith(("/search", "/_/")) or len(self.network_results) >= 10000:
            return
        if response.request.resource_type not in ("document", "xhr", "fetch"):
            return
        try:
            body = await response.text()
            if len(body) < 10_000_000:
                self.network_results.extend(extract_candidates(body))
        except Exception:
            pass  # Navigation and context shutdown can cancel an in-flight response.

    async def blocked(self):
        path = urlsplit(self.page.url).path
        return path.startswith("/sorry") or bool(await self.page.locator('#captcha-form:visible, iframe[src*="recaptcha"]:visible, input[name="captcha"]:visible').count())

    async def check_page(self):
        self.check_stop()
        if await self.blocked():
            raise GoogleVerificationError("Google 限制了当前网络出口，请检查代理连接或更换代理节点后重试")
        if urlsplit(self.page.url).hostname == "consent.google.com":
            raise GoogleVerificationError("Google 隐私确认未能自动完成，请更换代理节点后重试")

    async def consent(self):
        pattern = re.compile(r"^(Reject all|Accept all|I agree|全部拒绝|全部接受|接受全部|同意|すべて拒否|すべて同意する)$", re.I)
        buttons = self.page.get_by_role("button", name=pattern)
        for button in await buttons.all():
            if await button.is_visible():
                await button.click(timeout=3000)
                await self.pause(.7)
                break

    async def preview_candidates(self):
        # Keep current DOM locators; old retained Selenium elements go stale after scrolling.
        cards = self.page.locator('a[href*="/imgres"]:has(img), [role="button"]:has(img), [data-ri]:has(img)')
        result = []
        attempted = 0
        for card in (await cards.all())[:500]:
            self.check_stop()
            if attempted >= self.config["download_workers"] * 2:
                break
            try:
                if await card.get_attribute("data-spiderroom-preview"):
                    continue
                if not await card.is_visible():
                    continue
                href = await card.get_attribute("href")
                if href:
                    parts = urlsplit(href)
                    if parts.netloc and parts.hostname != "www.google.com":
                        continue
                    if not parts.path.endswith("/imgres"):
                        continue
                attempted += 1
                await card.evaluate("el => el.setAttribute('data-spiderroom-preview', '1')")
                # Only open image result previews, never arbitrary external source links.
                await card.click(timeout=2000, no_wait_after=True)
                await self.pause(.4)
                await self.check_page()
                values = await self.page.locator('img[src^="http"]').evaluate_all("els => els.filter(e=>e.naturalWidth>=300 || e.closest('[role=dialog]')).map(e=>({url:e.currentSrc||e.src,title:e.alt,width:e.naturalWidth,height:e.naturalHeight}))")
                for item in values:
                    if url := original_url(item["url"]):
                        result.append(ImageCandidate(url, title=item["title"], width=item["width"], height=item["height"]))
            except (StopRequested, RuntimeError):
                raise
            except Exception:
                continue
        return result

    async def batches(self, keyword):
        self.network_results.clear()
        url = search_url(keyword, self.config)
        for attempt in range(self.config["retries"] + 1):
            self.check_stop()
            try:
                response = await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config["timeout"] * 1000)
                await self.consent()
                await self.pause(max(.5, self.config["request_interval"]))
                await self.check_page()
                if response and response.status >= 400:
                    raise RuntimeError(f"Google 搜索返回 HTTP {response.status}，请检查网络或稍后重试")
                break
            except RuntimeError:
                raise
            except StopRequested:
                raise
            except Exception:
                if attempt == self.config["retries"]:
                    raise RuntimeError("Google 搜索加载超时或网络不可达，请检查能否在浏览器打开 Google，必要时填写代理地址") from None
                await self.pause(min(2 ** attempt, 8))
        seen, unchanged = set(), 0
        for round_index in range(self.config["max_pages"]):
            await self.check_page()
            candidates = extract_candidates(await self.page.content()) + self.network_results
            self.network_results = []
            fresh = [item for item in candidates if item.url not in seen]
            if not fresh:
                fresh = [item for item in await self.preview_candidates() if item.url not in seen]
            batch = []
            for item in fresh:
                if item.url not in seen:
                    seen.add(item.url)
                    batch.append(item)
            self.emit("page")
            self.emit("album", title=f"搜索结果 · 第 {round_index + 1} 轮")
            if batch:
                unchanged = 0
                self.emit("log", message=f"第 {round_index + 1} 轮发现 {len(batch)} 个原图地址", level="info")
                yield batch
            else:
                unchanged += 1
                if unchanged >= 3:
                    self.emit("log", message="连续三轮没有新原图，结束当前关键词", level="info")
                    break
            await self.page.evaluate("window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))")
            more = self.page.get_by_role("button", name=re.compile(r"^(Show more results|More results|显示更多结果|更多结果|查看更多)$", re.I))
            for button in await more.all():
                if await button.is_visible():
                    try:
                        await button.click(timeout=2000)
                    except Exception:
                        pass
                    break
            await self.pause(max(.5, self.config["request_interval"]))
        if not seen:
            raise RuntimeError("未找到可用原图：可能没有匹配结果，或 Google 页面结构已变化。可尝试调整关键词 / 筛选条件")
