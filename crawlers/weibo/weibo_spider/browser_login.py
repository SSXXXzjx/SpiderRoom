"""读取浏览器中 weibo.cn 的 Cookie，不保存或输出登录凭据。"""

from http.cookiejar import CookieJar


def load_browser_cookies(browser, cookie_file=""):
    supported = ("chrome", "edge", "firefox")
    if browser not in ("auto", *supported):
        raise ValueError("cookie_browser 必须为 auto、chrome、edge 或 firefox。")
    if cookie_file and browser == "auto":
        raise ValueError("填写 cookie_file 时，请明确指定 cookie_browser。")
    try:
        import browser_cookie3
    except ImportError:
        raise RuntimeError("请安装依赖：python -m pip install -r requirements.txt") from None
    failures = []
    for name in supported if browser == "auto" else (browser,):
        try:
            jar = getattr(browser_cookie3, name)(domain_name="weibo.cn", cookie_file=cookie_file or None)
            filtered = CookieJar()
            for cookie in jar:
                domain = cookie.domain.lstrip(".").lower()
                if (domain == "weibo.cn" or domain.endswith(".weibo.cn")) and not cookie.is_expired():
                    filtered.set_cookie(cookie)
            if not any(cookie.name == "SUB" and cookie.value for cookie in filtered):
                failures.append(f"{name}：没有未过期的微博登录 Cookie")
                continue
            print(f"已从 {name} 读取微博登录状态。")
            return filtered
        except Exception as error:
            failures.append(f"{name}：读取失败（{type(error).__name__}）")
    raise RuntimeError(
        "无法读取浏览器登录状态。" + "；".join(failures)
        + "。请在所选浏览器登录 https://weibo.cn，关闭浏览器后重试。"
        "多个用户配置时可指定 cookie_file；新版 Chrome/Edge 加密保护可能不受支持，"
        "可尝试 Firefox，或在 main.py 的 cookie 中手动填写 Cookie。"
    )
