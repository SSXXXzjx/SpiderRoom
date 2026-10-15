"""Google-specific defaults and validation, shared by the CLI and platform."""

import re
from urllib.parse import urlsplit


OPTIONS = {
    "keywords": [], "min_width": 0, "min_height": 0, "google_size": "any",
    "google_type": "any", "google_color": "any", "safe_search": True,
    "search_language": "zh-CN", "proxy": "", "browser_channel": "auto",
    "headless": True, "deduplicate": True, "max_file_mb": 30,
}
ENUMS = {
    "google_size": ("any", "large", "medium", "icon"),
    "google_type": ("any", "photo", "clipart", "lineart", "animated"),
    "google_color": ("any", "color", "gray", "trans"),
    "browser_channel": ("auto", "chrome", "msedge", "chromium"),
}


def validate_google(config, require_target=True):
    if not isinstance(config["keywords"], list) or any(not isinstance(k, str) for k in config["keywords"]):
        raise ValueError("关键词必须是文本列表")
    config["keywords"] = list(dict.fromkeys(k.strip() for k in config["keywords"] if k.strip()))
    if require_target and not config["keywords"]:
        raise ValueError("请填写至少一个谷歌图片搜索关键词")
    if len(config["keywords"]) > 200 or any(len(k) > 500 for k in config["keywords"]):
        raise ValueError("最多支持 200 个关键词，每个关键词最多 500 字")
    for key, allowed in ENUMS.items():
        if config[key] not in allowed:
            raise ValueError(f"{key} 选项无效")
    for key, low, high in (("min_width", 0, 50000), ("min_height", 0, 50000), ("max_file_mb", 1, 100)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} 范围：{low}–{high}")
    for key in ("safe_search", "headless", "deduplicate"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
    if not isinstance(config["search_language"], str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", config["search_language"]):
        raise ValueError("搜索语言请填写 zh-CN、en 等语言代码")
    if not isinstance(config["proxy"], str):
        raise ValueError("代理地址必须是文本")
    config["proxy"] = config["proxy"].strip()
    if config["proxy"]:
        try:
            parts = urlsplit(config["proxy"])
            valid = parts.scheme in ("http", "https", "socks5") and parts.hostname and parts.port and parts.path in ("", "/") and not parts.query and not parts.fragment
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("代理地址格式：http://127.0.0.1:7890 或 socks5://127.0.0.1:1080")
        if parts.scheme == "socks5" and parts.username:
            raise ValueError("浏览器不支持需要账号密码的 SOCKS5 代理，请改用 HTTP 代理")
    return config
