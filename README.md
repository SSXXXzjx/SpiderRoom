# SpiderRoom

本地爬虫管理控制台。原生 HTML / CSS / JavaScript + Python，黑、灰、荧光绿界面，固定侧边栏。目前接入微博相册图片爬虫。

## 功能

- 配置采集目标、下载并发、保存目录与 Cookie。
- 启动 / 停止采集，查看真实进度、实时日志和运行记录。
- 默认保存原图，长边缩放可选、默认关闭。
- 输入底部裁剪像素，或打开图片拖动裁剪线，实时预览并显示像素数。

## 启动

1. 安装 Python 3.10+，在本项目根目录执行 `python -m pip install -r requirements.txt`。
2. 微博爬虫已内置于 `crawlers/weibo/`，`tools.json` 默认使用项目内的相对路径。
3. Windows 双击 `start.bat`，或执行：

   ```bash
   python server.py
   ```

4. 打开 `http://127.0.0.1:8765`，填写目标和登录 `weibo.cn` 后的 Cookie，开始采集。

使用独立 Python 环境时，在 `tools.json` 的 `python` 字段填写解释器路径。更换端口可执行 `python server.py --port 8888`。

## 图片处理

关闭缩放且裁剪为 0 时保留下载文件的原始尺寸、格式和字节。底部裁剪按输出像素计算：缩放关闭时按原图尺寸裁剪，开启时先缩放再裁剪。裁剪后的图片保存为 JPG。

点击「裁剪预览」，打开本地图片，拖动绿线后点击「应用裁剪」，再保存设置或开始采集。选图不会上传或修改原文件。处理参数改变后会生成新的图片，保留之前的文件。

## 本地数据

配置和运行记录位于 `data/`，不提交到仓库。Cookie 仅用于当次运行，不写入文件。服务仅监听本机；终端按 Ctrl+C 停止服务和采集。

本仓库包含管理页面、运行服务，以及从本地 `weiboSpider` 整合的完整爬虫源码、依赖、文档和测试。原爬虫基于 [dataabc/weiboSpider](https://github.com/dataabc/weiboSpider)，原项目说明保留于 `crawlers/weibo/README.md`。新增其他爬虫需要添加对应适配器与设置表单。
