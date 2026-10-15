import asyncio
import copy
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from server import PINTEREST_DEFAULTS, validate_config
from crawlers.pinterest.crawler import PinterestCrawler
from crawlers.pinterest.config import normalize_target
from crawlers.pinterest.engine import PinterestClient
from crawlers.pinterest.backend import Api, PinResponse, VENDOR_ROOT


class PinterestEngineTests(unittest.TestCase):
    def test_search_normalization_preserves_query_and_validates_empty(self):
        url=normalize_target('https://www.pinterest.com/search/pins/?q=landscape+art&rs=typed')
        self.assertEqual(parse_qs(urlsplit(url).query)['q'],['landscape art'])
        config=validate_config({**copy.deepcopy(PINTEREST_DEFAULTS),'pinterest_urls':[url]},'pinterest')
        self.assertEqual(config['pinterest_urls'],[url])
        for invalid in ('https://www.pinterest.com/search/pins/?q=','https://evil.com/search/pins/?q=cat'):
            with self.assertRaises(ValueError):
                normalize_target(invalid)

    def test_search_calls_upstream_and_unwraps_results_with_page_limit(self):
        calls=[]
        def search(api,num,bookmarks):
            calls.append((api.query,num,bookmarks))
            return PinResponse('https://www.pinterest.com/',{'resource_response':{'data':{'results':[{'id':'123','images':{'orig':{'url':'https://i.pinimg.com/originals/a.jpg'}}}]}},'resource':{'options':{'bookmarks':['next']}}})
        async def run():
            config={**copy.deepcopy(PINTEREST_DEFAULTS),'request_interval':0,'max_pages':2}
            async with PinterestClient(config,lambda *a,**kw:None) as client:
                crawler=PinterestCrawler(config,None,lambda *a,**kw:None)
                return [batch async for folder,batch in crawler.target_batches(client,'https://www.pinterest.com/search/pins/?q=landscape+art')]
        with patch.object(Api,'get_search',search):
            batches=asyncio.run(run())
        self.assertEqual([batch[0]['id'] for batch in batches],['123','123'])
        self.assertEqual(calls,[('landscape art',25,[]),('landscape art',25,['next'])])

    def test_engine_is_bundled_project_and_board_pagination_is_bounded(self):
        calls = []
        def board(api):
            calls.append('board')
            return PinResponse('https://www.pinterest.com/',{'resource_response':{'data':{'id':'11','name':'Art'}},'resource':{}})
        def feed(api,board_id,num,bookmarks):
            calls.append(('feed',board_id,bookmarks))
            return PinResponse('https://www.pinterest.com/',{'resource_response':{'data':[{'id':'1','images':{'orig':{'url':'https://i.pinimg.com/originals/a.jpg'}}}]},'resource':{'options':{'bookmarks':['next']}}})
        async def run():
            config = {**copy.deepcopy(PINTEREST_DEFAULTS),'max_pages':2,'request_interval':0}
            async with PinterestClient(config,lambda *a,**kw:None) as client:
                url,states = await client.state('https://www.pinterest.com/user/art/')
                return [b async for b in client.batches(url,'BoardFeedResource',{'board_id':'11'},states)]
        with patch.object(Api,'get_board',board),patch.object(Api,'get_board_pins',feed):
            self.assertEqual(len(asyncio.run(run())),2)
        self.assertEqual(calls,['board',('feed','11',[]),('feed','11',['next'])])
        import pinterest_dl
        self.assertTrue(str(pinterest_dl.__file__).startswith(str(VENDOR_ROOT)))

    def test_session_adapter_preserves_proxy_cookie_and_section_option(self):
        class Response:
            def raise_for_status(self):
                pass
            def close(self):
                pass
        config = {**copy.deepcopy(PINTEREST_DEFAULTS),'proxy':'http://127.0.0.1:7890','cookie':'auth=fixture','include_sections':True}
        async def run():
            async with PinterestClient(config,lambda *a,**kw:None) as client:
                api=client.make_api('https://www.pinterest.com/user/art/')
                with patch.object(client.session,'get',return_value=Response()) as get:
                    api._session.get('https://www.pinterest.com/resource/BoardFeedResource/get/?data='+json.dumps({'options':{'filter_section_pins':True},'context':{}}))
                    options=json.loads(parse_qs(urlsplit(get.call_args.args[0]).query)['data'][0])['options']
                    self.assertFalse(options['filter_section_pins'])
                self.assertEqual(client.session.proxies['https'],config['proxy'])
                self.assertEqual(client.session.cookies.get('auth'),'fixture')
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
