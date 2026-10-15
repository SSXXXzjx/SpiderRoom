"""Read scoped Pinterest resources; do not mix in recommendation stores."""
import json
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PinterestImage:
    url: str
    source_url: str
    title: str = ''
    width: int = 0
    height: int = 0
    pin_id: str = ''
    variants: tuple = ()

    def to_dict(self):
        return asdict(self)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_script, self.parts = True, []

    def handle_data(self, data):
        if self.in_script:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_script:
            self.documents.append(''.join(self.parts))
            self.in_script = False


def states_from_html(document):
    parser = Scripts()
    parser.feed(document)
    states = []
    for source in parser.documents:
        try:
            value = json.loads(source)
            if not isinstance(value, dict):
                continue
            state = value.get('initialReduxState') or value.get('props', {}).get('initialReduxState')
            if isinstance(state, dict):
                states.append(state)
        except (ValueError, TypeError, AttributeError):
            continue
    return states


def resource_entries(state, name, **criteria):
    result = []
    entries = state.get('resources', {}).get(name, {})
    if not isinstance(entries, dict):
        return result
    for key, value in entries.items():
        try:
            options = dict(json.loads(key))
            if all(str(options.get(k, '')).casefold() == str(v).casefold() for k,v in criteria.items()) and isinstance(value, dict):
                result.append((options, value))
        except (ValueError, TypeError):
            continue
    return result


def image_url(url):
    if not isinstance(url, str):
        return ''
    try:
        parts = urlsplit(url)
        if parts.scheme == 'https' and (parts.hostname == 'pinimg.com' or (parts.hostname or '').endswith('.pinimg.com')) and not parts.username:
            return url
    except ValueError:
        pass
    return ''


def images_from_pins(pins):
    if isinstance(pins, dict):
        pins = [pins]
    result = []
    for pin in pins if isinstance(pins, list) else []:
        if not isinstance(pin, dict) or pin.get('type', 'pin') not in ('pin',''):
            continue
        pin_id = str(pin.get('id', ''))
        if not pin_id.isascii() or not pin_id.isdigit():
            continue
        variants = pin.get('images') or {}
        if not isinstance(variants, dict):
            continue
        def size(item):
            try:
                return int(item.get('width') or 0) * int(item.get('height') or 0)
            except (ValueError, TypeError, AttributeError):
                return 0
        ordered = ([variants['orig']] if isinstance(variants.get('orig'), dict) else []) + sorted((v for k,v in variants.items() if k != 'orig' and isinstance(v,dict)), key=size, reverse=True)
        urls = list(dict.fromkeys(image_url(v.get('url')) for v in ordered if image_url(v.get('url'))))
        if not urls:
            continue
        # Actual dimensions are verified after fallback; an original's dimensions may not match a smaller fallback.
        result.append(PinterestImage(urls[0], f'https://www.pinterest.com/pin/{pin_id}/', str(pin.get('title') or pin.get('grid_title') or pin.get('description') or '')[:1000], pin_id=pin_id, variants=tuple(urls)))
    return result
