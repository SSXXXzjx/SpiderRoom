import tempfile
from io import BytesIO
from threading import Barrier
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from lxml import etree
from PIL import Image

from main import CONFIG
from weibo_spider.album_spider import AlbumSpider, image_url, safe_name


class AlbumSpiderTest(unittest.TestCase):
    def test_concurrent_downloads_do_not_exceed_image_limit(self):
        self.config["max_images"] = 2
        self.config["download_workers"] = 6
        barrier = Barrier(2)
        selector = etree.HTML('<div class="c">' + ''.join(
            f'<img src="https://wx1.sinaimg.cn/wap180/{number}.jpg">'
            for number in range(5)) + '</div>')

        def download(source, folder, referer, session=None):
            barrier.wait(timeout=5)
            return "downloaded"

        with patch.object(self.spider, "pages", return_value=iter([("https://weibo.cn/album/123", selector)])), patch.object(
            self.spider, "download", side_effect=download
        ) as mocked:
            self.spider.crawl([("https://weibo.cn/album/123", "测试")], Path(self.temporary.name))
        self.assertEqual(mocked.call_count, 2)
        self.assertIsNot(mocked.call_args_list[0].kwargs["session"], mocked.call_args_list[1].kwargs["session"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = dict(CONFIG, cookie="SUB=test-cookie", user_ids=["1980768563"],
                           output_dir=self.temporary.name, request_interval=0)
        self.spider = AlbumSpider(self.config, self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.spider.session.close)
        self.addCleanup(self.spider.media_session.close)

    def test_cookie_survives_redirect_without_leaking_to_other_domain(self):
        initial = self.spider.session.prepare_request(requests.Request("GET", "https://weibo.cn/album/123"))
        response = requests.Response()
        response.status_code = 302
        response.url = initial.url
        response.headers["Location"] = "https://weibo.cn/album/123/"
        response.request = initial
        response._content = b""
        redirected = next(self.spider.session.resolve_redirects(response, initial, yield_requests=True))
        self.assertIn("SUB=test-cookie", redirected.headers.get("Cookie", ""))
        unrelated = self.spider.session.prepare_request(requests.Request("GET", "https://example.com/"))
        self.assertNotIn("Cookie", unrelated.headers)

    def test_cross_domain_diagnostic(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.content = b"<html><body>album</body></html>"
        response.url = "https://weibo.com/photos/123?token=private"
        with patch.object(self.spider.session, "get", return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                self.spider.get_page("https://weibo.cn/album/123")
        self.assertIn("不支持的站点", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_fixture_album_discovery_excludes_avatar(self):
        fixture = Path(__file__).parent / "testdata" / "e4d541ecb02253c14abc1d52605fc00d91279df9ac4c1465c85b91b3.html"
        selector = etree.HTML(fixture.read_bytes(), parser=etree.HTMLParser(encoding="utf-8"))
        with patch.object(self.spider, "get_page", return_value=selector):
            albums = list(self.spider.albums("1980768563"))
        self.assertEqual(len(albums), 2)
        self.assertEqual(albums[0][1], "微博配图")
        self.assertTrue(all("头像" not in title for _, title in albums))

    def test_pagination_stops_cycles(self):
        first = "https://weibo.cn/album/123?page=1"
        second = "https://weibo.cn/album/123?page=2"
        selectors = [etree.HTML('<a href="?page=2">下页</a>'),
                     etree.HTML('<a href="?page=1">下页</a>')]
        with patch.object(self.spider, "get_page", side_effect=selectors):
            self.assertEqual([url for url, _ in self.spider.pages(first)], [first, second])

    def test_limit_counts_existing_but_not_failed_images(self):
        self.config["max_images"] = 2
        selector = etree.HTML('<div class="c">' + ''.join(
            f'<img src="https://wx1.sinaimg.cn/wap180/{number}.jpg">'
            for number in range(4)) + '</div>')
        with patch.object(self.spider, "pages", return_value=iter([("https://weibo.cn/album/123", selector)])), patch.object(
            self.spider, "download", side_effect=["failed", "existing", "downloaded"]
        ) as download:
            self.spider.crawl([("https://weibo.cn/album/123", "测试")], Path(self.temporary.name))
        self.assertEqual(download.call_count, 3)

    def test_original_fallback_and_skip_existing(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"Content-Type": "image/jpeg"}
        image_bytes = BytesIO()
        Image.new("RGB", (1600, 800), "red").save(image_bytes, format="PNG")
        response.iter_content.return_value = [image_bytes.getvalue()]
        source = "https://wx1.sinaimg.cn/wap180/example.jpg"
        folder = Path(self.temporary.name)
        with patch.object(self.spider.media_session, "get", side_effect=[
            requests.HTTPError("missing original"), response
        ]) as get:
            self.assertEqual(self.spider.download(source, folder, "https://weibo.cn/album/123"), "downloaded")
            self.assertEqual(self.spider.download(source, folder, "https://weibo.cn/album/123"), "existing")
        self.assertEqual(get.call_count, 2)
        self.assertFalse(list(folder.glob("*.part")))
        with Image.open(folder / "weibo_0001.jpg") as image:
            self.assertEqual(image.size, (1024, 512))
            self.assertEqual(image.format, "JPEG")

    def test_validation_and_image_filter(self):
        with self.assertRaises(ValueError):
            AlbumSpider(dict(self.config, cookie=None), self.temporary.name)
        self.assertIsNone(image_url("https://h5.sinaimg.cn.example.com/icon.png", True))
        self.assertEqual(image_url("http://wx1.sinaimg.cn/wap180/photo.gif", True),
                         "https://wx1.sinaimg.cn/large/photo.gif")
        self.assertEqual(safe_name("CON"), "album_CON")


if __name__ == "__main__":
    unittest.main()
