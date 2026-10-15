# Google 图片采集方案与参考项目分析

分析日期：2026-10-15。阅读了三份参考的 README / SKILL 及实际核心源码；只借鉴方法，新模块自行实现。

| 参考 | 实际方法 | 可借鉴点 | 当前实现的不足 |
| --- | --- | --- | --- |
| [GoogleCommonSpider](https://github.com/Vaskka/GoogleCommonSpider/blob/master/main.py) | Selenium 滚动结果页，从锚点 imgurl 取原图，requests 下载 | 浏览器和下载器分离、代理 | Chrome 75 和已淘汰 Selenium API；硬编码 rg_s/smb；URL 正则贪婪；下载类型误调用 get_real_url；缺少图片校验和可靠去重 |
| [google_HDImage_crawler](https://github.com/tata24/google_HDImage_crawler/blob/master/google_HDImage_crawler.py) | Selenium 滚动，点击每个缩略图，再取预览高清 src | 不能直接把缩略图当原图；点击预览作为补充 | 硬编码绝对 XPath、旧 API；关闭 TLS 验证；串行下载；强制 .jpg；先滚动再持有元素易失效 |
| [Smithery 页面](https://smithery.ai/skills/xdrshjr/google-images-crawler)及[实际核心](https://github.com/xdrshjr/JR-Agent-Skills/blob/main/google-images-crawler/core/crawler.py) | Playwright 滚动；解析 /imgres 的 imgurl、尺寸；异步下载或 requests 线程池 | 结构化结果、上下文释放、超时、代理、分离下载器 | 仍依赖 /imgres；部分版本的文档文件名与仓库不一致；初始 consent 在空白页执行；下载仅检查 HTTP、尺寸依赖页面声明，不验证真实图片；线程共用 session；下载失败不持续补足数量 |

## 本平台实现

采用真实浏览器加载，不使用收费搜索 API，不要求 Cookie。默认复用系统 Chrome / Edge，也支持 Playwright Chromium。多关键词逐项采集；页面数据中的原图元组、imgurl / data-ou / data-iurl 属性共同提取，必要时点击缩略图提取预览原图。过滤 Google 缓存缩略图和 data URL，不把缩略图冒充原图。

下载端独立线程会话、限并发、超时、有限指数退避、单文件体积限制、真实图像解码与尺寸校验；有效图片 / 已存在图片才消耗图片上限，失败与过滤继续补足。按关键词建目录，保存来源记录，URL 与文件内容双重去重，支持重新运行跳过已完成文件；继续复用平台图片裁剪、长边缩放、相册预览。

高级配置：浏览器模式、HTTP / SOCKS 代理、搜索语言、安全搜索、大小 / 类型 / 色彩筛选、最小宽高、滚动次数、下载大小、并发 / 超时 / 重试。遇到验证码或访问限制明确报告，不尝试自动破解。Google 改版、网络地区限制和来源站拒绝下载仍会影响结果，不能保证达到任意数量。

logo 使用 resources/icons/微博.png、小红书.png、谷歌.png，裁剪 logo 保留。服务端只开放现有 PNG 图标文件，前端保持相对 URL；白名单已更新为当前四个图标，缺失时返回 404。
