import re
from urllib.parse import unquote, quote, urlsplit, parse_qs, urlencode

OPTIONS = {'pinterest_urls': [], 'include_sections': True, 'min_width': 0, 'min_height': 0,
           'max_file_mb': 30, 'deduplicate': True, 'proxy': '', 'headless': True,
           'browser_channel': 'auto'}
HOSTS = {'pinterest.com', 'www.pinterest.com', 'uk.pinterest.com', 'www.pinterest.co.uk',
         'pinterest.co.uk', 'www.pinterest.jp', 'www.pinterest.fr', 'www.pinterest.de',
         'www.pinterest.ca', 'www.pinterest.com.au'}
RESERVED = {'search', 'ideas', 'login', 'logout', 'settings', 'business', 'today', 'explore', 'resource', '_'}


def normalize_target(value):
    value = value.strip()
    if not value or len(value) > 2048:
        raise ValueError('请填写 Pinterest 用户 / 画板 / 分区 / Pin 链接')
    if value.isascii() and value.isdigit():
        value = 'pin/' + value
    if '://' not in value:
        value = 'https://www.pinterest.com/' + value.lstrip('/')
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or parts.username or parts.password or parts.port not in (None, 80, 443):
            raise ValueError()
        segments = [unquote(p) for p in parts.path.strip('/').split('/')]
        if any(not p or p in ('.', '..') or any(c in p for c in '/\\?#') or any(ord(c) < 32 for c in p) for p in segments):
            raise ValueError()
        if parts.hostname == 'pin.it' and len(segments) == 1 and re.fullmatch(r'[A-Za-z0-9]+', segments[0]):
            return 'https://pin.it/' + segments[0]
        if parts.hostname in HOSTS and segments == ['search','pins']:
            query = parse_qs(parts.query).get('q',[''])[0].strip()
            if not query or len(query) > 500 or any(ord(character) < 32 for character in query):
                raise ValueError()
            return 'https://www.pinterest.com/search/pins/?'+urlencode({'q':query})
        if parts.hostname not in HOSTS or not 1 <= len(segments) <= 3 or segments[0].lower() in RESERVED:
            raise ValueError()
        if segments[0] == 'pin':
            if len(segments) != 2 or not segments[1].isascii() or not segments[1].isdigit():
                raise ValueError()
        elif not re.fullmatch(r'[A-Za-z0-9_\-.]+', segments[0]):
            raise ValueError()
        if len(segments) == 2 and segments[1] in ('_saved', '_created', 'pins', 'boards'):
            segments = segments[:1]
        return 'https://www.pinterest.com/' + '/'.join(quote(p, safe='-_~.') for p in segments) + '/'
    except ValueError:
        raise ValueError('支持 Pinterest 用户、画板、分区、Pin、搜索结果链接及 pin.it 短链接；搜索链接须包含非空 q 参数') from None


def validate_pinterest(config, require_target=True):
    config['pinterest_urls'] = list(dict.fromkeys(normalize_target(u) for u in config['pinterest_urls']))
    if require_target and not config['pinterest_urls']:
        raise ValueError('请填写至少一个 Pinterest 采集目标')
    if len(config['pinterest_urls']) > 200:
        raise ValueError('最多支持 200 个采集目标')
    for key, low, high in (('min_width', 0, 50000), ('min_height', 0, 50000), ('max_file_mb', 1, 100)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f'{key} 范围：{low}–{high}')
    for key in ('include_sections', 'deduplicate', 'headless'):
        if type(config[key]) is not bool:
            raise ValueError(f'{key} 必须是布尔值')
    if config['browser_channel'] not in ('auto', 'chrome', 'msedge', 'chromium'):
        raise ValueError('浏览器选项无效')
    if not isinstance(config['proxy'], str):
        raise ValueError('代理地址必须是文本')
    config['proxy'] = config['proxy'].strip()
    if config['proxy']:
        try:
            p = urlsplit(config['proxy'])
            if p.scheme not in ('http', 'https', 'socks5') or not p.hostname or not p.port or p.path not in ('','/') or p.query or p.fragment or (p.scheme == 'socks5' and p.username):
                raise ValueError()
        except ValueError:
            raise ValueError('代理格式：http://127.0.0.1:7890 或无认证 socks5://127.0.0.1:1080') from None
