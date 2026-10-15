"""Focused Google checks; exactly two local source images, no external crawl."""
import asyncio
import copy
import functools
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch, AsyncMock
from urllib.parse import urlencode

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import GOOGLE_DEFAULTS, validate_config, target_count
from crop_output import make_image_output
from crawlers.weibo.weibo_spider.image_output import ImageOutput
from crawlers.google_images.extract import ImageCandidate, extract_candidates, search_url
from crawlers.google_images.downloader import ImageDownloader
from crawlers.google_images.crawler import GoogleCrawler
from crawlers.google_images.browser import GoogleBrowser, StopRequested


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class GoogleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="google-two-images-")
        cls.root = Path(cls.temporary.name)
        cls.source = cls.root / "source"
        cls.source.mkdir()
        Image.new("RGB", (960, 640), "#709b61").save(cls.source / "one.jpg")
        Image.new("RGB", (640, 960), "#688cb5").save(cls.source / "two.png")
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(cls.source)))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.temporary.cleanup()

    def config(self, **values):
        return validate_config({**copy.deepcopy(GOOGLE_DEFAULTS), "keywords": ["风景"], "output_dir": str(self.root / self._testMethodName), "max_images": 2, "retries": 0, **values}, "google")

    def test_extract_signed_urls_metadata_and_filters(self):
        signed = "https://images.example/a.jpg?token=a%2Fb%2Bc&x=1"
        document = '<a href="/imgres?' + urlencode({"imgurl": signed}) + '">a</a>'
        document += '<script>["https://example.org/original.png",960,640],["https://encrypted-tbn0.gstatic.com/thumbnail",100,100]</script>'
        items = extract_candidates(document)
        self.assertEqual([i.url for i in items], [signed, "https://example.org/original.png"])
        self.assertEqual((items[1].width, items[1].height), (640, 960))
        config = self.config(google_size="large", min_width=800)
        self.assertIn("isz%3Al", search_url("a & b", config))
        self.assertEqual(target_count(config, "google"), 1)
        with self.assertRaises(ValueError):
            self.config(proxy="file:///bad")

    def test_two_images_validation_dedup_and_resume(self):
        config = self.config()
        folder = Path(config["output_dir"])
        folder.mkdir()
        output = make_image_output(ImageOutput, "google", 1024, 0, False)
        downloader = ImageDownloader(config, output, lambda *a, **k: None)
        try:
            first = ImageCandidate(self.base + "/one.jpg")
            second = ImageCandidate(self.base + "/two.png")
            self.assertEqual(downloader.download(first, folder), "downloaded")
            self.assertEqual(downloader.download(ImageCandidate(first.url + "?same-content"), folder), "duplicate")
            self.assertEqual(downloader.download(second, folder), "downloaded")
            self.assertEqual(downloader.download(first, folder), "existing")
            self.assertEqual(downloader.download(ImageCandidate(first.url + "?same-content"), folder), "duplicate")
            files = [p for p in folder.iterdir() if p.suffix in (".jpg", ".png")]
            self.assertEqual(len(files), 2)
            self.assertEqual((folder / "google_0002.png").read_bytes(), (self.source / "two.png").read_bytes())
            self.assertEqual(len((folder / ".sources.jsonl").read_text(encoding="utf-8").splitlines()), 2)
            self.assertFalse(list(folder.glob("*.part")))
        finally:
            downloader.close()

    def test_pipeline_failed_and_filtered_candidates_do_not_consume_limit(self):
        config = self.config(min_width=600, min_height=600)
        events = []
        output = make_image_output(ImageOutput, "google", 1024, 0, False)
        base = self.base

        class FakePage:
            async def evaluate(self, script):
                return "fixture"

        class FakeBrowser:
            def __init__(self, *args):
                self.page = FakePage()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def check_stop(self):
                pass
            async def batches(self, keyword):
                yield [ImageCandidate(base + "/missing.jpg"), ImageCandidate(base + "/one.jpg", width=10), ImageCandidate(base + "/one.jpg"), ImageCandidate(base + "/two.png")]
                raise AssertionError("must stop at two valid results")

        with patch("crawlers.google_images.crawler.GoogleBrowser", FakeBrowser):
            asyncio.run(GoogleCrawler(config, output, lambda kind, **kw: events.append((kind, kw))).run())
        results = [e[1]["result"] for e in events if e[0] == "image"]
        self.assertEqual(results.count("downloaded"), 2)
        self.assertEqual(results.count("failed"), 1)
        self.assertIn(("target_done", {}), events)

    def test_actual_dimensions_and_stop(self):
        config = self.config(min_width=1000)
        folder = Path(config["output_dir"])
        folder.mkdir()
        output = make_image_output(ImageOutput, "google", 1024, 0, False)
        downloader = ImageDownloader(config, output, lambda *a, **k: None)
        try:
            self.assertEqual(downloader.download(ImageCandidate(self.base + "/one.jpg", width=2000), folder), "filtered")
            downloader.stopped = lambda: True
            with self.assertRaises(StopRequested):
                downloader.download(ImageCandidate(self.base + "/two.png"), folder)
        finally:
            downloader.close()

    def test_captcha_reports_proxy_error_without_opening_or_waiting(self):
        for headless in (True,False):
            browser = GoogleBrowser(self.config(headless=headless,proxy='http://127.0.0.1:7890'),lambda *a,**kw:None)
            browser.page = type('Page',(),{'url':'https://www.google.com/sorry/index'})()
            with patch.object(browser,'blocked',new=AsyncMock(return_value=True)), \
                 patch.object(browser,'open_browser',new=AsyncMock()) as opened, \
                 patch.object(browser,'pause',new=AsyncMock()) as waited:
                with self.assertRaisesRegex(RuntimeError,'代理'):
                    asyncio.run(browser.check_page())
                opened.assert_not_awaited()
                waited.assert_not_awaited()

    def test_consent_reports_error_without_manual_wait(self):
        browser = GoogleBrowser(self.config(),lambda *a,**kw:None)
        browser.page = type('Page',(),{'url':'https://consent.google.com/m'})()
        with patch.object(browser,'blocked',new=AsyncMock(return_value=False)), \
             patch.object(browser,'open_browser',new=AsyncMock()) as opened, \
             patch.object(browser,'pause',new=AsyncMock()) as waited:
            with self.assertRaisesRegex(RuntimeError,'隐私确认'):
                asyncio.run(browser.check_page())
            opened.assert_not_awaited()
            waited.assert_not_awaited()

    def test_search_result_text_does_not_trigger_captcha(self):
        browser = GoogleBrowser(self.config(), lambda *a, **kw: None)
        class Element:
            first = property(lambda element:element)
            async def is_visible(self):
                return False
            async def count(self):
                return 0
            async def inner_text(self,**kwargs):
                return 'Search results: unusual traffic / 异常流量'
        class Page:
            url = 'https://www.google.com/search?q=unusual+traffic'
            def locator(self,selector):
                return Element()
        browser.page = Page()
        asyncio.run(browser.check_page())


if __name__ == "__main__":
    unittest.main()
