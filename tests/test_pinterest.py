"""Focused Pinterest extraction checks; no external downloads here."""
import json
import sys
import unittest
import asyncio
import copy
import functools
import tempfile
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from unittest.mock import patch
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawlers.pinterest.config import normalize_target
from crawlers.pinterest.extract import states_from_html, resource_entries, images_from_pins
from crawlers.pinterest.extract import PinterestImage
from crawlers.pinterest.client import PinterestClient, PinterestBrowser
from crawlers.google_images.downloader import ImageDownloader
from crawlers.pinterest.crawler import PinterestDownloader, PinterestCrawler
from server import PINTEREST_DEFAULTS, validate_config
from crop_output import make_image_output
from crawlers.weibo.weibo_spider.image_output import ImageOutput


class PinterestParsingTests(unittest.TestCase):
    def test_target_normalization(self):
        self.assertEqual(normalize_target('artist/art/'), 'https://www.pinterest.com/artist/art/')
        self.assertEqual(normalize_target('https://www.pinterest.com/pin/123/?x=1'), 'https://www.pinterest.com/pin/123/')
        self.assertEqual(normalize_target('https://pin.it/abc123'), 'https://pin.it/abc123')
        for bad in ('https://evil.com/artist/art/', 'https://www.pinterest.com.evil.com/pin/123/', 'artist/../board', 'https://www.pinterest.com/search/pins/?q='):
            with self.assertRaises(ValueError, msg=bad):
                normalize_target(bad)

    def test_current_and_legacy_scoped_state(self):
        pin = {'id':'123','images':{'orig':{'url':'https://i.pinimg.com/originals/aa/bb/cc/img.png','width':960,'height':640},'736x':{'url':'https://i.pinimg.com/736x/aa/bb/cc/img.jpg','width':736,'height':490}}}
        state = {'pins':{'123':pin, '999':{'id':'999','images':{}}}, 'resources':{'BoardFeedResource':{json.dumps([['board_id','11']]):{'data':[pin], 'nextBookmark':'next'}}}}
        current = '<script id="__PWS_INITIAL_PROPS__" type="application/json">'+json.dumps({'initialReduxState':state})+'</script>'
        legacy = '<script>'+json.dumps({'props':{'initialReduxState':state}})+'</script>'
        for document in (current, legacy):
            parsed = states_from_html(document)[0]
            entries = resource_entries(parsed, 'BoardFeedResource', board_id='11')
            self.assertEqual(entries[0][1]['nextBookmark'],'next')
            self.assertEqual(resource_entries(parsed,'BoardFeedResource',board_id='22'),[])
            images = images_from_pins(entries[0][1]['data'])
            self.assertEqual(len(images),1)
            self.assertEqual(images[0].pin_id,'123')
            self.assertIn('/originals/',images[0].url)
            self.assertEqual(len(images[0].variants),2)
            self.assertEqual(images[0].source_url,'https://www.pinterest.com/pin/123/')

    def test_pagination_stops_on_repeated_bookmark(self):
        config = {**copy.deepcopy(PINTEREST_DEFAULTS),'request_interval':0,'max_pages':10}
        initial = {'resources':{'BoardFeedResource':{'[["board_id","11"]]':{'data':[{'id':'1'}],'nextBookmark':'same'}}}}
        calls = []
        class Response:
            def json(self):
                return {'resource_response':{'data':[{'id':'2'}]},'resource':{'options':{'bookmarks':['same']}}}
            def close(self):
                pass
        async def run():
            async with PinterestClient(config,lambda *a,**kw:None) as client:
                async def request(*args,**kw):
                    calls.append(kw)
                    return Response()
                client.request=request
                return [batch async for batch in client.batches('https://www.pinterest.com/a/b/','BoardFeedResource',{'board_id':'11'},[initial])]
        results=asyncio.run(run())
        self.assertEqual(len(results),2)
        self.assertEqual(len(calls),1)
        self.assertIn('"bookmarks":["same"]',calls[0]['params']['data'])

    def test_browser_fallback_respects_remaining_page_budget(self):
        config = {**copy.deepcopy(PINTEREST_DEFAULTS),'request_interval':0,'max_pages':2}
        initial = {'resources':{'BoardFeedResource':{'[["board_id","11"]]':{'data':[{'id':'1'}],'nextBookmark':'next'}}}}
        received = []
        class Browser:
            async def resource_batches(self, url, name, criteria, rounds, options):
                received.append(rounds)
                for index in range(rounds):
                    yield [{'id':str(index+2)}]
        async def run():
            async with PinterestClient(config,lambda *a,**kw:None) as client:
                async def request(*a,**kw):
                    raise RuntimeError('HTTP limited')
                async def browser():
                    return Browser()
                client.request, client.get_browser = request, browser
                return [b async for b in client.batches('https://www.pinterest.com/a/b/','BoardFeedResource',{'board_id':'11'},[initial])]
        self.assertEqual(len(asyncio.run(run())),2)
        self.assertEqual(received,[1])

    def test_browser_fetch_and_scroll_share_page_budget(self):
        config = {**copy.deepcopy(PINTEREST_DEFAULTS),'request_interval':0}
        class Page:
            url = 'https://www.pinterest.com/a/b/'
            calls = 0
            async def evaluate(self, script, args=None):
                if args:
                    self.calls += 1
                    if self.calls == 1:
                        return {'resource_response':{'data':[{'id':'2'}]},'resource':{'options':{'bookmarks':['next']}}}
                    return None
            async def content(self):
                return '<script>'+json.dumps({'initialReduxState':{'resources':{'BoardFeedResource':{'[["board_id","11"]]':{'data':[{'id':'3'}]}}}}})+'</script>'
        async def run():
            browser = PinterestBrowser(config,lambda *a,**kw:None,lambda:False)
            browser.page = Page()
            async def load(url):
                return [{'resources':{'BoardFeedResource':{'[["board_id","11"]]':{'data':[{'id':'1'}]}}}}]
            async def pause(seconds):
                pass
            browser.load, browser.pause = load, pause
            return [b async for b in browser.resource_batches(browser.page.url,'BoardFeedResource',{'board_id':'11'},2)]
        self.assertLessEqual(len(asyncio.run(run())),2)

    def test_incomplete_pin_state_uses_browser(self):
        config = {**copy.deepcopy(PINTEREST_DEFAULTS),'request_interval':0}
        target = 'https://www.pinterest.com/pin/123/'
        calls = []
        class Response:
            url = target
            text = '<script>'+json.dumps({'initialReduxState':{'pins':{},'resources':{}}})+'</script>'
            def close(self):
                pass
        class Browser:
            page = type('Page',(),{'url':target})()
            async def load(self,url):
                calls.append(url)
                return [{'pins':{'123':{'id':'123','images':{'orig':{}}}}}]
        async def run():
            async with PinterestClient(config,lambda *a,**kw:None) as client:
                async def request(*a,**kw):
                    return Response()
                async def browser():
                    return Browser()
                client.request, client.get_browser = request, browser
                return await client.state(target)
        _, states = asyncio.run(run())
        self.assertEqual(calls,[target])
        self.assertIn('123',states[0]['pins'])


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


class PinterestDownloadTests(unittest.TestCase):
    def test_original_size_limit_tries_known_smaller_variant(self):
        calls = []
        def fetch(downloader,item,source):
            calls.append(item.url)
            if 'originals' in item.url:
                raise ValueError('文件超过大小限制')
            source.write_bytes((self.source/'one.jpg').read_bytes())
        config=copy.deepcopy(PINTEREST_DEFAULTS)
        downloader=PinterestDownloader(config,None,lambda *a,**kw:None)
        item=PinterestImage('https://i.pinimg.com/originals/a.jpg','https://www.pinterest.com/pin/123/',variants=('https://i.pinimg.com/originals/a.jpg','https://i.pinimg.com/736x/a.jpg'))
        try:
            with tempfile.TemporaryDirectory() as temporary,patch.object(ImageDownloader,'fetch',fetch):
                downloader.fetch(item,Path(temporary)/'image.part')
        finally:
            downloader.close()
        self.assertEqual(calls,list(item.variants))

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='pinterest-two-images-')
        cls.root=Path(cls.temporary.name)
        cls.source=cls.root/'source'
        cls.source.mkdir()
        Image.new('RGB',(960,640),'#709b61').save(cls.source/'one.jpg')
        Image.new('RGB',(640,960),'#688cb5').save(cls.source/'two.png')
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietHandler,directory=str(cls.source)))
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.temporary.cleanup()

    def test_original_fallback_resume_and_atomic_output(self):
        config=validate_config({**copy.deepcopy(PINTEREST_DEFAULTS),'pinterest_urls':['pin/123'],'output_dir':str(self.root/'output'),'retries':0},'pinterest')
        folder=Path(config['output_dir'])
        folder.mkdir()
        events=[]
        downloader=PinterestDownloader(config,make_image_output(ImageOutput,'pinterest',1024,0,False),lambda kind,**kw:events.append((kind,kw)))
        item=PinterestImage(self.base+'/missing.jpg','https://www.pinterest.com/pin/123/',pin_id='123',variants=(self.base+'/missing.jpg',self.base+'/one.jpg'))
        try:
            self.assertEqual(downloader.download(item,folder),'downloaded')
            self.assertEqual(downloader.download(item,folder),'existing')
            self.assertEqual(downloader.download(replace(item,url=self.base+'/two.png',variants=(self.base+'/two.png',),pin_id='124'),folder),'downloaded')
        finally:
            downloader.close()
        self.assertEqual((folder/'pinterest_0002.png').read_bytes(),(self.source/'two.png').read_bytes())
        self.assertEqual(len([p for p in folder.iterdir() if p.suffix in ('.jpg','.png')]),2)
        self.assertFalse(list(folder.glob('*.part')))
        self.assertNotIn(('image',{'result':'failed','bytes':0}),events)

    def test_effective_cap_skips_failed_candidate(self):
        config=validate_config({**copy.deepcopy(PINTEREST_DEFAULTS),'pinterest_urls':['pin/123'],'output_dir':str(self.root/'pipeline'),'retries':0,'max_images':2},'pinterest')
        events=[]
        crawler=PinterestCrawler(config,make_image_output(ImageOutput,'pinterest',1024,0,False),lambda kind,**kw:events.append((kind,kw)))
        def pin(identity,path):
            return {'id':identity,'images':{'orig':{'url':'https://i.pinimg.com/'+path}}}
        async def batches(client,target):
            yield Path(config['output_dir']),[pin('1','missing.jpg'),pin('2','one.jpg'),pin('3','two.png')]
            raise AssertionError('must stop at two images')
        original_fetch=PinterestDownloader.fetch
        def fetch(downloader,item,source):
            local=replace(item,url=self.base+'/'+item.url.rsplit('/',1)[-1],variants=(self.base+'/'+item.url.rsplit('/',1)[-1],))
            return original_fetch(downloader,local,source)
        with patch.object(crawler,'target_batches',batches),patch.object(PinterestDownloader,'fetch',fetch):
            asyncio.run(crawler.run())
        results=[v['result'] for k,v in events if k=='image']
        self.assertEqual(results.count('downloaded'),2)
        self.assertEqual(results.count('failed'),1)
        self.assertIn(('target_done',{}),events)


if __name__ == '__main__':
    unittest.main()
