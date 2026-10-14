import time
import unittest
from unittest.mock import Mock, patch

from requests.cookies import RequestsCookieJar

from main import CONFIG, main
from weibo_spider.album_spider import AlbumSpider
from weibo_spider.browser_login import load_browser_cookies


class BrowserLoginTest(unittest.TestCase):
    def test_main_requires_new_cookie_and_reprompts_empty(self):
        with patch("builtins.input", side_effect=["   ", " new-cookie=test "]) as prompt, patch(
            "main.AlbumSpider"
        ) as spider:
            self.assertEqual(main(), 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(spider.call_args.args[0]["cookie"], "new-cookie=test")
        self.assertNotIn("cookie", CONFIG)
        spider.return_value.run.assert_called_once()

    def test_main_eof_does_not_start_spider(self):
        with patch("builtins.input", side_effect=EOFError), patch("main.AlbumSpider") as spider:
            self.assertEqual(main(), 1)
        spider.assert_not_called()

    def cookies(self):
        jar = RequestsCookieJar()
        jar.set("SUB", "test-login", domain=".weibo.cn", path="/")
        jar.set("unrelated", "ignored", domain="other.example", path="/")
        jar.set("expired", "ignored", domain=".weibo.cn", path="/", expires=int(time.time()) - 10)
        return jar

    def test_auto_fallback_filters_cookies(self):
        module = Mock()
        module.chrome.side_effect = RuntimeError("private details")
        module.edge.return_value = self.cookies()
        with patch.dict("sys.modules", {"browser_cookie3": module}):
            jar = load_browser_cookies("auto")
        self.assertEqual([cookie.name for cookie in jar], ["SUB"])
        module.firefox.assert_not_called()

    def test_failure_does_not_expose_exception_details(self):
        module = Mock()
        module.chrome.side_effect = RuntimeError("private details")
        with patch.dict("sys.modules", {"browser_cookie3": module}):
            with self.assertRaises(RuntimeError) as caught:
                load_browser_cookies("chrome")
        self.assertNotIn("private details", str(caught.exception))

    def test_browser_cookie_jar_and_manual_priority(self):
        config = dict(CONFIG, cookie="", user_ids=["123"])
        with patch("weibo_spider.album_spider.load_browser_cookies", return_value=self.cookies()) as loader:
            spider = AlbumSpider(config, ".")
            self.addCleanup(spider.session.close)
            self.addCleanup(spider.media_session.close)
            self.assertNotIn("Cookie", spider.session.headers)
            self.assertEqual(spider.session.cookies.get("SUB"), "test-login")
            self.assertFalse(spider.media_session.cookies)
            loader.assert_called_once()
        with patch("weibo_spider.album_spider.load_browser_cookies") as loader:
            spider = AlbumSpider(dict(config, cookie="manual=test"), ".")
            self.addCleanup(spider.session.close)
            self.addCleanup(spider.media_session.close)
            self.assertNotIn("Cookie", spider.session.headers)
            self.assertEqual(spider.session.cookies.get("manual"), "test")
            loader.assert_not_called()

    def test_custom_file_requires_explicit_browser(self):
        with self.assertRaises(ValueError):
            load_browser_cookies("auto", "Cookies")
