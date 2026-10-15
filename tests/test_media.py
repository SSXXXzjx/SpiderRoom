import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="spiderroom-media-")
        cls.output = Path(cls.temporary.name) / "相册 图片"
        nested = cls.output / "用户 01" / "相册"
        nested.mkdir(parents=True)
        Image.new("RGB", (960, 640), "#709b61").save(cls.output / "图片 01.jpg")
        Image.new("RGB", (640, 960), "#688cb5").save(nested / "图片 02.png")
        cls.room = server.Room()
        cls.room.config = {**copy.deepcopy(cls.room.config), "output_dir": str(cls.output)}
        cls.room.run_config = copy.deepcopy(cls.room.config)
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.room))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.temporary.cleanup()

    def request_json(self, path, payload):
        request = Request(self.base + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)

    def test_two_images_list_thumbnail_and_original(self):
        page = self.request_json("/api/images", {"output_dir": str(self.output), "offset": 0, "limit": 1})
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["images"]), 1)
        # 在翻页之间改变另一张图的排序，分页仍应沿用同一份列表。
        other = next(path for path in self.output.rglob("*") if path.is_file() and path.name != page["images"][0]["name"])
        later = max(path.stat().st_mtime_ns for path in self.output.rglob("*") if path.is_file()) + 1000000000
        os.utime(other, ns=(later, later))
        next_page = self.request_json("/api/images", {"output_dir": str(self.output), "offset": 1, "limit": 1, "catalog": page["catalog"]})
        images = page["images"] + next_page["images"]
        self.assertEqual({image["name"] for image in images}, {"图片 01.jpg", "图片 02.png"})
        for image in images:
            with urlopen(self.base + image["thumbnail"]) as response:
                thumbnail = Image.open(io.BytesIO(response.read()))
                self.assertLessEqual(max(thumbnail.size), 480)
            with urlopen(self.base + image["url"]) as response:
                original = Image.open(io.BytesIO(response.read()))
                self.assertIn(original.size, {(960, 640), (640, 960)})

    def test_preview_rejects_paths_outside_directory(self):
        page = self.request_json("/api/images", {"output_dir": str(self.output)})
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(page["images"][0]["url"]).query)
        query["path"] = ["../outside.jpg"]
        with self.assertRaises(HTTPError) as failure:
            urlopen(self.base + "/api/image?" + urlencode(query, doseq=True))
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()

    def test_choose_directory_and_cancel(self):
        with patch("server.subprocess.run") as run:
            run.return_value.stdout = json.dumps({"path": str(self.output)})
            run.return_value.returncode = 0
            selected = self.request_json("/api/select-output", {"initial": str(self.output)})
            self.assertEqual(selected["path"], str(self.output))
            run.return_value.stdout = json.dumps({"path": None})
            cancelled = self.request_json("/api/select-output", {"initial": str(self.output)})
            self.assertIsNone(cancelled["path"])


if __name__ == "__main__":
    unittest.main()
