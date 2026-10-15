"""pinterest-dl API engine with SpiderRoom transport, limits and browser recovery."""
import asyncio
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests

from .backend import Api, PinResponse, PinterestDL, build_get
from .client import PinterestClient as BrowserRecoveryClient
from .config import normalize_target
from crawlers.google_images.browser import StopRequested


class ManagedApi(Api):
    @staticmethod
    def _get_default_cookies(url):
        return {}

    @classmethod
    def _prepare_url(cls, url, timeout):
        return normalize_target(url)


class ApiSession:
    def __init__(self, client):
        self.client = client

    def pause(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.client.check_stop()
            time.sleep(min(.1, max(0, deadline-time.monotonic())))

    def get(self, url, **kwargs):
        parts = urlsplit(url)
        if parts.path == '/resource/BoardFeedResource/get/':
            query = parse_qs(parts.query)
            payload = json.loads(query.get('data',['{}'])[0])
            payload.setdefault('options',{})['filter_section_pins'] = not self.client.config['include_sections']
            query['data'] = [json.dumps(payload)]
            url = urlunsplit(parts._replace(query=urlencode(query,doseq=True)))
        elif parts.path == '/resource/BaseSearchResource/get/':
            query = parse_qs(parts.query)
            payload = json.loads(query.get('data',['{}'])[0])
            keyword = payload.get('options',{}).get('query','')
            source_url = '/search/pins/?'+urlencode({'q':keyword,'rs':'typed'})
            payload.setdefault('options',{})['source_url'] = source_url
            query.update(source_url=[source_url],data=[json.dumps(payload)])
            url = urlunsplit(parts._replace(query=urlencode(query,doseq=True)))
        for attempt in range(self.client.config['retries']+1):
            self.client.check_stop()
            self.pause(max(0,self.client.config['request_interval']-(time.monotonic()-self.client.last_request)))
            self.client.last_request = time.monotonic()
            response = None
            try:
                response = self.client.session.get(url,**{**kwargs,'timeout':self.client.config['timeout']})
                self.client.emit('page')
                self.client.check_stop()
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == self.client.config['retries'] or (response is not None and response.status_code not in (429,500,502,503,504)):
                    raise
                self.pause(min(2**attempt,8))
            finally:
                if response is not None:
                    response.close()


class PinterestClient(BrowserRecoveryClient):
    def __init__(self, config, emit, stopped=lambda:False):
        super().__init__(config,emit,stopped)
        self.session.headers.update({'x-pinterest-pws-handler':'www/pin/[id].js',
                                     'X-Requested-With':'XMLHttpRequest','X-Pinterest-AppState':'active'})

    def make_api(self, url):
        api = ManagedApi(url,timeout=self.config['timeout'])
        api._session.close()
        api._session = ApiSession(self)
        api.cookies = self.session.cookies
        return api

    async def state(self, url):
        self.check_stop()
        if urlsplit(url).hostname == 'pin.it':
            response = await self.request(url)
            try:
                url = normalize_target(response.url)
            finally:
                response.close()
            if urlsplit(url).hostname == 'pin.it':
                raise RuntimeError('短链接未解析，请使用完整 Pinterest 链接')
        segments = urlsplit(url).path.strip('/').split('/')
        if segments == ['search','pins']:
            return normalize_target(url),[{}]
        if len(segments) == 1:
            return url,[{}]
        try:
            api = self.make_api(url)
            if api.is_pin:
                scraper = PinterestDL.with_api(timeout=self.config['timeout'],max_retries=self.config['retries'])
                scraper._create_api = lambda target: api
                media = await asyncio.to_thread(scraper.scrape,url,1)
                pins = {str(item.id):{'id':str(item.id),'title':item.alt or '',
                        'images':{'orig':{'url':item.src,'width':item.resolution[0],'height':item.resolution[1]}}}
                        for item in media if str(item.id) == str(api.pin_id)}
                if not pins:
                    raise RuntimeError('pinterest-dl 未返回目标 Pin')
                return api.url,[{'pins':pins}]
            response = await asyncio.to_thread(api.get_board)
            board = response.data
            board_id = response.get_board_id()
            board_url = 'https://www.pinterest.com/'+'/'.join(segments[:2])+'/'
            board = {**board,'url':board_url}
            return api.url,[{'boards':{board_id:board}}]
        except StopRequested:
            raise
        except Exception as error:
            self.emit('log',message=f'pinterest-dl 接口暂不可用（{type(error).__name__}），尝试页面加载',level='warning')
            return await super().state(url)

    async def resource(self, api, name, criteria, args):
        if name == 'BaseSearchResource':
            response = await asyncio.to_thread(api.get_search,25,args.get('bookmarks',[]))
            if isinstance(response.data,dict):
                response.data = response.data.get('results')
            return response
        if name == 'BoardFeedResource':
            return await asyncio.to_thread(api.get_board_pins,str(criteria['board_id']),25,args.get('bookmarks',[]))
        if name == 'BoardSectionPinsResource':
            return await asyncio.to_thread(api.get_board_section_pins,str(criteria['section_id']),25,args.get('bookmarks',[]))
        if name == 'BoardSectionsResource':
            return await asyncio.to_thread(api.get_board_sections,str(criteria['board_id']))
        url = build_get(api.endpoint._BASE+f'/resource/{name}/get/',args,urlsplit(api.url).path)
        def fetch():
            response = api._session.get(url)
            return PinResponse(url,response.json())
        return await asyncio.to_thread(fetch)

    async def batches(self, url, name, criteria, states, options=None):
        api = self.make_api(url)
        args = dict(options or {**criteria,'page_size':25,'field_set_key':'react_grid_pin'})
        seen = set()
        consumed = 0
        try:
            for _ in range(self.config['max_pages']):
                self.check_stop()
                response = await self.resource(api,name,criteria,args)
                data = response.data
                if not isinstance(data,(list,dict)):
                    raise RuntimeError('pinterest-dl 返回的资源结构无效')
                if not data:
                    return
                consumed += 1
                yield data if isinstance(data,list) else [data]
                if name == 'BoardSectionsResource':
                    return
                bookmarks = response.resource.get('options',{}).get('bookmarks')
                if not bookmarks or bookmarks in ('-end-',['-end-']):
                    return
                signature = json.dumps(bookmarks,sort_keys=True)
                if signature in seen:
                    return
                seen.add(signature)
                args['bookmarks'] = bookmarks if isinstance(bookmarks,list) else [bookmarks]
        except StopRequested:
            raise
        except Exception as error:
            remaining = self.config['max_pages']-consumed
            if not remaining:
                return
            self.emit('log',message=f'pinterest-dl 分页暂不可用（{type(error).__name__}），继续页面 / 浏览器回退',level='warning')
            recovery = BrowserRecoveryClient({**self.config,'max_pages':remaining},self.emit,self.stopped)
            async with recovery:
                async for batch in recovery.batches(url,name,criteria,states if consumed == 0 else [],args):
                    yield batch
