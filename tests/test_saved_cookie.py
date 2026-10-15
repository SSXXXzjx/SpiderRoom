import copy
import json
import tempfile
import unittest
from pathlib import Path

from server import ROOT, Room, defaults_for


class SavedCookieTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        tools = json.loads((ROOT / 'tools.json').read_text(encoding='utf-8'))
        for tool in tools['tools']:
            tool['path'] = str(ROOT / tool['path'])
        (self.root / 'tools.json').write_text(json.dumps(tools),encoding='utf-8')

    def tearDown(self):
        self.temporary.cleanup()

    def test_cookie_survives_reload_and_config_only_save(self):
        room = Room(self.root)
        room.save(copy.deepcopy(defaults_for('weibo')), 'fixture_cookie=only_test')
        reloaded = Room(self.root)
        self.assertEqual(reloaded.settings()['cookie'],'fixture_cookie=only_test')
        reloaded.save({**reloaded.config,'max_images':2})
        self.assertEqual(Room(self.root).settings()['cookie'],'fixture_cookie=only_test')
        self.assertNotIn('cookie',reloaded.config)
        self.assertNotIn('cookie',reloaded.snapshot())

    def test_cookie_is_per_tool_and_can_be_explicitly_cleared(self):
        weibo, pinterest = Room(self.root), Room(self.root,'pinterest')
        weibo.save(weibo.config,'weibo=fixture')
        pinterest.save({**pinterest.config,'pinterest_urls':['pin/123']},'pinterest=fixture')
        self.assertEqual(Room(self.root).settings()['cookie'],'weibo=fixture')
        self.assertEqual(Room(self.root,'pinterest').settings()['cookie'],'pinterest=fixture')
        weibo.save(weibo.config,'')
        self.assertEqual(Room(self.root).settings()['cookie'],'')


if __name__ == '__main__':
    unittest.main()
