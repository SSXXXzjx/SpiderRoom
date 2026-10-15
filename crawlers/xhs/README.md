# Spider_XHS 平台采集模块

源自 `D:\我的学习文件\爬虫\Spider_XHS` 的 PC API、鉴权及 Node.js 签名运行时，由平台根目录 `xhs_worker.py` 调用。

保留原模块实现。包入口 `apis/__init__.py` 仅加载采集模块，避免额外引入直播和消息依赖。未复制原项目 Cookie、.env、采集数据或登录状态。

需要 Python 依赖 `requirements.txt`，以及 Node.js 22.15+（推荐 24 LTS）。本目录执行 `npm install` 安装 `crypto-js`。在平台侧栏选择「小红书图集」，使用 Cookie 采集笔记链接、主页或关键词图片。使用说明见平台根目录 README.md。
