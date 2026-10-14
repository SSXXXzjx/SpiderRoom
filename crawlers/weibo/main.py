"""修改 CONFIG 后执行 python main.py，每次启动在终端粘贴 Cookie。

user_ids: 用户数字 UID。album_urls: 指定相册链接，非空时优先使用。
max_images: 每个用户或指定相册图片上限，0 不限，已有图片计数。
output_dir: 保存目录。album_names: 相册名称筛选，空列表表示全部。
download_workers: 同时下载图片数。request_interval: 相册页面请求间隔秒数。
filename_prefix: JPG 文件名前缀。image_long_edge: 输出图片长边像素数。
先在浏览器登录 https://weibo.cn。程序只下载图片。
"""

import os

from weibo_spider.album_spider import AlbumSpider


CONFIG = {
    "user_ids": ["3576729842"],
    "album_urls": [],
    "output_dir": "downloads",
    "max_images": 1000,
    "album_names": [],
    "include_avatar": False,
    "original_size": True,
    "max_pages": 500,
    "request_interval": 1,
    "download_workers": 6,
    "filename_prefix": "weibo",
    "image_long_edge": 1024,
    "timeout": 30,
    "retries": 3,
}


def main():
    try:
        cookie = input("请粘贴登录 weibo.cn 后的 Cookie（只粘贴值），然后按回车：\n").strip()
        while not cookie:
            cookie = input("Cookie 不能为空，请重新粘贴（Ctrl+C 取消）：\n").strip()
        config = dict(CONFIG, cookie=cookie)
        AlbumSpider(config, base_dir=os.path.dirname(os.path.abspath(__file__))).run()
    except EOFError:
        print("无法读取 Cookie，请在 VS Code 终端中执行 python main.py。")
        return 1
    except (ValueError, RuntimeError, OSError) as error:
        print(f"停止：{error}")
        return 1
    except KeyboardInterrupt:
        print("已手动停止，已下载图片保留。")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
