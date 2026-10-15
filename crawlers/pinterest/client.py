"""HTTP bootstrap / cursor resources, with a browser fallback for changed pages."""
import asyncio
import json
import time
from http.cookies import SimpleCookie
from urllib.parse import unquote, urlsplit

import requests

from crawlers.google_images.browser import GoogleBrowser, StopRequested
from .config import normalize_target
from .extract import states_from_html, resource_entries

BASE = 'https://www.pinterest.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'


class PinterestBrowser(GoogleBrowser):
    def __init__(self, config, emit, stopped):
        def browser_emit(kind, **values):
            if kind == 'log':
                values['message'] = values.get('message','').replace('Google 图片搜索','Pinterest 页面')
            emit(kind, **values)
        super().__init__({**config, 'search_language':'en-US'}, browser_emit, stopped)
        self.responses = []
        self.expected = None

    async def __aenter__(self):
        await super().__aenter__()
        cookie = SimpleCookie()
        cookie.load(self.config.get('cookie',''))
        if cookie:
            await self.context.add_cookies([{'name':k,'value':v.value,'domain':'.pinterest.com','path':'/','secure':True} for k,v in cookie.items()])
        return self

    async def on_response(self, response):
        if not self.expected or len(self.responses) >= 100:
            return
        name, criteria = self.expected
        parts = urlsplit(response.url)
        if parts.hostname != 'www.pinterest.com' or parts.path != f'/resource/{name}/get/':
            return
        try:
            from urllib.parse import parse_qs
            args = json.loads(parse_qs(parts.query).get('data',['{}'])[0]).get('options',{})
            if not all(str(args.get(k,'')).casefold() == str(v).casefold() for k,v in criteria.items()):
                return
            data = await response.json()
            if isinstance(data.get('resource_response',{}).get('data'), (list,dict)):
                self.responses.append(data['resource_response']['data'])
        except Exception:
            pass

    async def check_page(self):
        self.check_stop()
        path = urlsplit(self.page.url).path
        if path.startswith(('/login','/challenge','/suspended')):
            raise RuntimeError('Pinterest 要求登录或验证，请填写有效 Cookie，或检查当前网络后重试')

    async def load(self, url):
        self.check_stop()
        await self.page.goto(url, wait_until='domcontentloaded', timeout=self.config['timeout']*1000)
        await self.pause(max(.5, self.config['request_interval']))
        await self.check_page()
        return states_from_html(await self.page.content())

    async def resource_batches(self, url, name, criteria, rounds, options=None):
        self.expected = (name, criteria)
        self.responses.clear()
        stale = 0
        previous = None
        consumed = 0
        try:
            states = await self.load(url)
            # Retry the exact resource in the browser's first-party context before scrolling.
            # This also respects filters that differ from the page's default feed.
            args = dict(options or criteria)
            cursors = set()
            for _ in range(rounds):
                self.check_stop()
                response = await self.page.evaluate("""async ({name,args,path}) => {
                  const q = new URLSearchParams({source_url:path,data:JSON.stringify({options:args,context:{}})});
                  const r = await fetch('/resource/'+name+'/get/?'+q,{headers:{'X-Requested-With':'XMLHttpRequest','X-Pinterest-AppState':'active'},credentials:'same-origin'});
                  if (!r.ok) return null;
                  try { return await r.json(); } catch { return null; }
                }""", {'name':name,'args':args,'path':urlsplit(url).path})
                if not isinstance(response,dict):
                    break
                data = response.get('resource_response',{}).get('data')
                if not isinstance(data,(list,dict)):
                    break
                self.emit('page')
                if not data:
                    return
                yield data if isinstance(data,list) else [data]
                consumed += 1
                bookmark = response.get('resource',{}).get('options',{}).get('bookmarks')
                if not bookmark or bookmark in (['-end-'],'-end-'):
                    return
                cursor = json.dumps(bookmark)
                if cursor in cursors:
                    return
                cursors.add(cursor)
                args['bookmarks'] = bookmark if isinstance(bookmark,list) else [bookmark]
                await self.pause(self.config['request_interval'])
            for _ in range(max(0, rounds-consumed)):
                self.check_stop()
                values = []
                for state in states:
                    for _, value in resource_entries(state, name, **criteria):
                        data = value.get('data')
                        if isinstance(data, list):
                            values.extend(data)
                for data in self.responses:
                    values.extend(data if isinstance(data,list) else [data])
                self.responses.clear()
                signature = tuple(str(i.get('id','')) for i in values if isinstance(i,dict))
                self.emit('page')
                if signature and signature != previous:
                    yield values
                    stale = 0
                else:
                    stale += 1
                    if stale >= 3:
                        break
                previous = signature
                await self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
                await self.pause(max(1, self.config['request_interval']))
                await self.check_page()
                states = states_from_html(await self.page.content())
        finally:
            self.expected = None
            self.responses.clear()


class PinterestClient:
    def __init__(self, config, emit, stopped=lambda:False):
        self.config, self.emit, self.stopped = config, emit, stopped
        self.session = requests.Session()
        self.session.headers.update({'User-Agent':UA,'Accept-Language':'en-US,en;q=0.9'})
        cookie = SimpleCookie()
        cookie.load(config.get('cookie',''))
        for key, value in cookie.items():
            self.session.cookies.set(key, value.value, domain='.pinterest.com', path='/')
        if config['proxy']:
            self.session.trust_env = False
            self.session.proxies.update(http=config['proxy'], https=config['proxy'])
        self.browser = None
        self.last_request = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            self.session.close()

    def check_stop(self):
        if self.stopped():
            raise StopRequested()

    async def pause(self, seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            self.check_stop()
            await asyncio.sleep(min(.1, max(0, deadline-time.monotonic())))

    async def get_browser(self):
        if self.browser is None:
            browser = PinterestBrowser(self.config, self.emit, self.stopped)
            await browser.__aenter__()
            self.browser = browser
        return self.browser

    async def request(self, url, **kwargs):
        for attempt in range(self.config['retries']+1):
            self.check_stop()
            await self.pause(max(0,self.config['request_interval']-(time.monotonic()-self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = await asyncio.to_thread(self.session.get, url, timeout=self.config['timeout'], **kwargs)
                self.emit('page')
                self.check_stop()
                if response.status_code in (429,500,502,503,504) and attempt < self.config['retries']:
                    response.close()
                    await self.pause(min(2**attempt,8))
                    continue
                response.raise_for_status()
                return response
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.config['retries']:
                    raise RuntimeError('Pinterest 请求超时或网络不可达，请检查代理 / 网络') from None
                await self.pause(min(2**attempt,8))

    async def state(self, url):
        try:
            response = await self.request(url)
            try:
                final = normalize_target(response.url)
                if urlsplit(final).hostname == 'pin.it':
                    raise RuntimeError('pin.it 短链接未能解析，请改用完整 Pinterest 链接')
                states = states_from_html(response.text)
            finally:
                response.close()
            if self.target_ready(final, states):
                return final, states
        except StopRequested:
            raise
        except Exception:
            pass
        self.emit('log',message='普通请求未取得有效页面，正在尝试浏览器加载',level='warning')
        browser = await self.get_browser()
        states = await browser.load(url)
        final = normalize_target(browser.page.url)
        if not self.target_ready(final, states):
            raise RuntimeError('Pinterest 未返回有效采集数据，可能需要 Cookie、网络验证或页面结构已变化')
        return final, states

    @staticmethod
    def target_ready(url, states):
        segments = urlsplit(url).path.strip('/').split('/')
        if segments[0] == 'pin':
            return any(isinstance(pin,dict) and pin.get('images') for state in states
                       for pin in [(state.get('pins') or {}).get(segments[1])])
        if len(segments) >= 2:
            board_path = unquote('/'+'/'.join(segments[:2])).casefold()
            return any(isinstance(board,dict) and board.get('id') and
                       unquote(urlsplit(board.get('url','')).path).rstrip('/').casefold() == board_path
                       for state in states for board in (state.get('boards') or {}).values())
        return any(resource_entries(state,'BoardsFeedResource',username=segments[0]) or
                   any(isinstance(user,dict) and str(user.get('username','')).casefold() == segments[0].casefold()
                       for user in (state.get('users') or {}).values()) for state in states)

    async def batches(self, url, name, criteria, states, options=None):
        entries = [entry for state in states for entry in resource_entries(state, name, **criteria)]
        requested = options
        options, cached = entries[0] if entries else (options or {**criteria,'page_size':25,'field_set_key':'react_grid_pin'}, {})
        if requested and any(key in requested and options.get(key) != requested[key] for key in ('filter_section_pins',)):
            entries, cached, options = [], {}, requested
        data, bookmark = cached.get('data'), cached.get('nextBookmark')
        consumed = 0
        if isinstance(data,(list,dict)):
            consumed += 1
            yield data if isinstance(data,list) else [data]
        if entries and (not bookmark or bookmark in ('-end-','-end')):
            return
        seen = set()
        # Initial cached feed is page one; never gather all pages before yielding.
        for _ in range(max(0,self.config['max_pages']-consumed)):
            self.check_stop()
            cursor = json.dumps(bookmark,sort_keys=True)
            if cursor in seen:
                return
            seen.add(cursor)
            args = dict(options)
            if bookmark:
                args['bookmarks'] = bookmark if isinstance(bookmark,list) else [bookmark]
            try:
                response = await self.request(BASE+f'/resource/{name}/get/', params={'source_url':urlsplit(url).path,'data':json.dumps({'options':args,'context':{}},separators=(',',':'))}, headers={'X-Requested-With':'XMLHttpRequest','X-Pinterest-AppState':'active','Referer':url})
                try:
                    payload = response.json()
                finally:
                    response.close()
                result = payload.get('resource_response',{})
                if result.get('status') == 'failure' or not isinstance(result.get('data'), (list,dict)):
                    raise RuntimeError('Pinterest 分页数据不可用')
                data = result['data']
                if not data:
                    return
                consumed += 1
                yield data if isinstance(data,list) else [data]
                bookmark = payload.get('resource',{}).get('options',{}).get('bookmarks')
                if isinstance(bookmark,list) and len(bookmark)==1:
                    bookmark = bookmark[0]
                if not bookmark or bookmark in ('-end-','-end'):
                    return
            except StopRequested:
                raise
            except Exception:
                self.emit('log',message='Pinterest 分页请求被限制，切换浏览器继续采集',level='warning')
                browser = await self.get_browser()
                fallback_options = dict(args)
                fallback_criteria = dict(criteria)
                if 'filter_section_pins' in options:
                    fallback_criteria['filter_section_pins'] = options['filter_section_pins']
                async for batch in browser.resource_batches(url, name, fallback_criteria, self.config['max_pages']-consumed, fallback_options):
                    yield batch
                return
