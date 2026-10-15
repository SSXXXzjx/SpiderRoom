"""Lazy target traversal and verified image downloads with original-first fallback."""
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs

import requests
from PIL import Image

from crawlers.google_images.browser import StopRequested
from crawlers.google_images.downloader import ImageDownloader, keyword_folder
from .engine import PinterestClient
from .config import normalize_target
from .extract import images_from_pins, resource_entries


def folder_name(title, identity):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title)).strip(' .')[:55] or 'Pinterest'
    return title + '_' + str(identity)


class PinterestDownloader(ImageDownloader):
    def fetch(self, item, source):
        variants = item.variants or (item.url,)
        for index, url in enumerate(variants):
            self.check_stop()
            try:
                super().fetch(replace(item, url=url), source)
                # An HTTP 200 response may still contain HTML or a corrupt image.
                with Image.open(source) as opened:
                    opened.verify()
                if index:
                    self.emit('log',message='原图不可用，已使用该 Pin 提供的较高分辨率图片',level='warning')
                return
            except (requests.RequestException, ValueError, OSError, SyntaxError) as error:
                if index == len(variants)-1:
                    raise


class PinterestCrawler:
    def __init__(self, config, output, emit, stopped=lambda:False):
        self.config, self.output, self.emit, self.stopped = config, output, emit, stopped
        self.root = Path(config['output_dir']).expanduser().resolve()

    async def sections(self, client, url, board, states):
        board_id = str(board['id'])
        seen = set()
        for state in states:
            for section in (state.get('boardsections') or {}).values():
                owner = section.get('board_id') or (section.get('board') or {}).get('id')
                if str(owner) == board_id and section.get('id'):
                    seen.add(str(section['id']))
                    yield section
        if not board.get('section_count'):
            return
        async with aclosing(client.batches(url,'BoardSectionsResource',{'board_id':board_id},states,
                                           {'board_id':board_id,'page_size':25,'isPrefetch':False})) as batches:
            async for batch in batches:
                for section in batch:
                    if isinstance(section,dict) and section.get('id') and str(section['id']) not in seen:
                        seen.add(str(section['id']))
                        yield section

    @staticmethod
    def find_board(states, url):
        path = unquote(urlsplit(url).path).rstrip('/').casefold()
        for state in states:
            for board in (state.get('boards') or {}).values():
                if isinstance(board,dict) and unquote(urlsplit(board.get('url','')).path).rstrip('/').casefold() == path and board.get('id'):
                    return board
        raise RuntimeError('未找到目标画板，请检查链接或填写登录 Cookie')

    async def board_batches(self, client, url, states, section_slug=None):
        parts = urlsplit(url)
        segments = parts.path.strip('/').split('/')
        board_url = parts._replace(path='/'+'/'.join(segments[:2])+'/', query='', fragment='').geturl()
        board = self.find_board(states, board_url)
        board_id = str(board['id'])
        folder = self.root / folder_name(segments[0], board_id) / folder_name(board.get('name') or segments[1], board_id)
        if not section_slug:
            self.emit('album',title=board.get('name') or segments[1])
            async with aclosing(client.batches(board_url,'BoardFeedResource',{'board_id':board_id},states,
                                               {'board_id':board_id,'board_url':urlsplit(board_url).path,'filter_section_pins':not self.config['include_sections'],'page_size':25,'field_set_key':'react_grid_pin'})) as batches:
                async for pins in batches:
                    yield folder, pins
            # With sections included, BoardFeedResource normally already contains their pins.
            # Explicit section traversal also catches boards whose main feed excludes them.
            if not self.config['include_sections']:
                return
        matched = False
        async with aclosing(self.sections(client,board_url,board,states)) as sections:
            async for section in sections:
                slug = str(section.get('slug') or '')
                if section_slug and unquote(slug).casefold() != unquote(section_slug).casefold():
                    continue
                matched = True
                section_id = str(section['id'])
                section_url = board_url+slug.strip('/')+'/'
                self.emit('album',title=f"{board.get('name',segments[1])} / {section.get('title') or slug}")
                async with aclosing(client.batches(section_url,'BoardSectionPinsResource',{'section_id':section_id},states,
                                                   {'section_id':section_id,'page_size':25,'field_set_key':'react_grid_pin'})) as batches:
                    async for pins in batches:
                        yield folder / folder_name(section.get('title') or slug,section_id), pins
        if section_slug and not matched:
            raise RuntimeError('未找到目标画板分区，请检查分区链接或登录 Cookie')

    async def target_batches(self, client, target):
        url, states = await client.state(target)
        segments = urlsplit(url).path.strip('/').split('/')
        if segments == ['search','pins']:
            query = parse_qs(urlsplit(url).query)['q'][0]
            self.emit('album',title='关键词搜索 · '+query)
            async with aclosing(client.batches(url,'BaseSearchResource',{'query':query,'scope':'pins'},states,
                                               {'query':query,'scope':'pins','page_size':25,'field_set_key':'react_grid_pin','rs':'typed'})) as batches:
                async for pins in batches:
                    values = []
                    for item in pins:
                        if isinstance(item,dict) and isinstance(item.get('results'),list):
                            values.extend(item['results'])
                        else:
                            values.append(item)
                    yield self.root / '关键词搜索' / keyword_folder(query), values
            return
        if segments[0] == 'pin':
            pin_id = segments[1]
            for state in states:
                pin = (state.get('pins') or {}).get(pin_id)
                if pin:
                    self.emit('album',title='Pin '+pin_id)
                    yield self.root / ('pin_'+pin_id), [pin]
                    return
            raise RuntimeError('未找到该 Pin 的图片，请检查链接或登录 Cookie')
        if len(segments) >= 2:
            async with aclosing(self.board_batches(client,url,states,segments[2] if len(segments)==3 else None)) as batches:
                async for batch in batches:
                    yield batch
            return
        username = segments[0]
        seen = set()
        async with aclosing(client.batches(url,'BoardsFeedResource',{'username':username},states,
                                           {'username':username,'page_size':25,'field_set_key':'profile_grid_item','sort':'last_pinned_to'})) as boards:
            async for batch in boards:
                for board in batch:
                    if not isinstance(board,dict) or not board.get('id') or str(board['id']) in seen:
                        continue
                    try:
                        board_url = normalize_target(board.get('url') or '')
                    except ValueError:
                        continue
                    board_segments = urlsplit(board_url).path.strip('/').split('/')
                    if len(board_segments)!=2 or board_segments[0].casefold()!=username.casefold():
                        continue
                    seen.add(str(board['id']))
                    try:
                        resolved, board_states = await client.state(board_url)
                        async with aclosing(self.board_batches(client,resolved,board_states)) as batches:
                            async for item in batches:
                                yield item
                    except StopRequested:
                        raise
                    except Exception:
                        self.emit('log',message=f"画板 {board.get('name') or board['id']} 采集失败，继续其他画板",level='warning')
        if not seen:
            raise RuntimeError('该用户没有可访问的画板，私密画板需要有效登录 Cookie')

    async def run(self):
        self.emit('log',message='Pinterest 采集引擎：pinterest-dl（本地内置源码）',level='info')
        errors = []
        downloader = PinterestDownloader(self.config,self.output,self.emit,self.stopped)
        pool = ThreadPoolExecutor(max_workers=self.config['download_workers'])
        try:
            async with PinterestClient(self.config,self.emit,self.stopped) as client:
                for target in self.config['pinterest_urls']:
                    client.check_stop()
                    self.emit('target',target=target)
                    used, failures, filtered = 0, 0, 0
                    limit = self.config['max_images']
                    seen = set()
                    try:
                        async with aclosing(self.target_batches(client,target)) as batches:
                            async for folder, pins in batches:
                                items = []
                                for item in images_from_pins(pins):
                                    if item.pin_id not in seen:
                                        seen.add(item.pin_id)
                                        items.append(item)
                                if not items:
                                    continue
                                folder.mkdir(parents=True,exist_ok=True)
                                offset = 0
                                while offset<len(items) and (not limit or used<limit):
                                    client.check_stop()
                                    count = min(self.config['download_workers'],limit-used if limit else len(items))
                                    group = items[offset:offset+count]
                                    offset += len(group)
                                    loop = asyncio.get_running_loop()
                                    results = await asyncio.gather(*(loop.run_in_executor(pool,downloader.download,i,folder) for i in group))
                                    used += sum(r in ('downloaded','existing') for r in results)
                                    failures += results.count('failed')
                                    filtered += sum(r in ('filtered','duplicate') for r in results)
                                if limit and used>=limit:
                                    break
                        if not used:
                            raise RuntimeError(f'当前目标未取得有效图片（过滤 / 重复 {filtered}，失败 {failures}）')
                        self.emit('log',message=f'目标完成：有效 {used}，过滤 / 重复 {filtered}，失败 {failures}',level='info')
                        if limit and used<limit:
                            self.emit('log',message='可访问的结果已结束，未达到图片上限，已下载文件保留',level='warning')
                        self.emit('target_done')
                    except StopRequested:
                        raise
                    except Exception as error:
                        errors.append(str(error))
                        self.emit('log',message=f'目标未完成：{error}',level='error')
        finally:
            pool.shutdown(wait=True,cancel_futures=True)
            downloader.close()
        if errors:
            raise RuntimeError(f'{len(errors)} 个目标未完成：{errors[-1]}')
