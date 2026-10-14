# 在 VS Code 中只下载微博相册图片

使用 Python 3.10 或更高版本。用 VS Code 打开项目文件夹
`E:\MyWorls\weiboSpider`，在终端中执行一次：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

按 `Ctrl+Shift+P`，选择 `Python: Select Interpreter`，选择项目内
`.venv\Scripts\python.exe`，然后新建终端。需要安装 VS Code 的 Python 扩展。
如果 PowerShell 阻止虚拟环境激活，可以直接使用
`.\.venv\Scripts\python.exe main.py`，无须修改执行策略。

## 修改 main.py 顶部的 CONFIG

| 参数 | 用法 |
| --- | --- |
| `user_ids` | 如 `["1980768563"]`，从用户主页地址获得数字 UID，详见 `docs/userid.md` |
| `output_dir` | 如 `r"D:\微博图片"`；相对路径以 main.py 所在目录为准 |
| `max_images` | 每个用户最多保存的图片张数，默认 100；0 表示不限，实际数量取决于可访问图片数量 |
| `album_names` | 如 `["微博配图"]` 或 `["旅行", "日常"]`，按相册名称包含文字筛选；`[]` 表示全部 |
| `include_avatar` | 默认 `False`，需要头像相册时改为 `True` |
| `album_urls` | 指定 `https://weibo.cn/album/...` 链接列表；非空时优先下载这些相册，每个相册独立限制张数 |
| `original_size` | 默认 `True`，对常见缩略图地址尝试大图，失败时回退到原页面图片 |
| `max_pages` | 每个用户或指定相册的总页面请求上限，包含相册列表页和相册内容页，默认 500 |
| `request_interval` | 相册页面请求间隔，默认 1 秒；图片下载不逐张等待 |
| `download_workers` | 同时下载图片数，默认 6；可设置 1 到 32，设为 1 为顺序下载 |
| `filename_prefix` | 文件名前缀，默认 `weibo`，输出如 `weibo_0001.jpg` |
| `image_long_edge` | 输出长边像素数，默认 1024，短边等比例缩放；小图也会放大 |
| `timeout` / `retries` | 请求超时秒数和连接/HTTP 错误重试次数，默认 30 秒 / 3 次 |

配置完成后运行：

先在浏览器打开 `https://weibo.cn` 并完成登录、验证，按 `docs/cookie.md` 获取 Cookie。
每次启动后，在终端提示处粘贴 Cookie 的值并按回车，不要带 `Cookie:` 前缀。
输入不能为空；按 Ctrl+C 可以取消。Cookie 仅用于本次运行，不保存到配置或文件。
main.py 不读取浏览器 Cookie 或 WEIBO_COOKIE 环境变量。
当前 main.py 保留已设置的 UID 和 1000 张图片上限。

```powershell
python main.py
```

图片按用户 UID 和相册分别保存，只下载图片，不输出微博正文或视频。
新下载图片统一转换为 JPG，每个相册单独连续编号，并发时按转换完成顺序编号。
透明区域使用白底，动图只保留第一帧，按 EXIF 方向旋转后缩放。
再次运行会继续已有最大编号；保留 `.image_index.json` 才能按来源跳过已处理图片。
此前已下载的原始文件不会批量修改；再次遇到时会生成新的编号缩放版本。
重复运行会跳过已有图片，已有图片也计入 `max_images`，需要继续下载时调大上限。
下载失败的图片不计入成功数量；未完成下载使用 `.part` 文件，不会被当作完整图片。
达到上限即停止；按 `Ctrl+C` 可停止并保留已下载图片。

Cookie 属于登录凭据，请勿分享或提交。只能下载当前登录账号有权访问的相册。
该入口沿用项目的 weibo.cn 相册页面来源，页面变化、相册不可见或验证要求可能导致无法获取；
出现登录或验证提示时，先在浏览器完成登录和验证，再重新运行。
重新运行时粘贴最新 Cookie。
