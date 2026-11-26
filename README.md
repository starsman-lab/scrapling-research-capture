# Scrapling Research Capture

一个面向 Codex 的科研网页采集 skill，配有可运行的 Python 辅助脚本。它借鉴 [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling) 的公开 API，用于对已知的公开网页做有边界、可追溯的采集。

## 文件

- `SKILL.md`：何时使用 Scrapling、如何选择抓取方式，以及权限和证据边界。
- `scripts/capture_public_pages.py`：按用户提供的 URL 清单串行采集静态页面，检查 `robots.txt`，限制请求速率并输出 JSONL。
- `scripts/probe_selectors.py`：对本地 HTML 检查 CSS selector 的匹配数和样例，不发起网络请求。
- `references/project-analysis.md`：Scrapling 架构、API 选择、适用边界和上游资料。
- `requirements.txt`：固定到本次核对的 Scrapling 版本与 HTTP fetcher extra。

脚本使用 Scrapling 的 `FetcherSession` 和 `Selector` 文档化 API；它们是为本 skill 编写的采集助手，不重新打包 Scrapling 的内部实现。

## 安装与运行

在需要运行脚本的项目虚拟环境中安装依赖：

```bash
python -m pip install -r requirements.txt
```

上游目前将普通 HTTP Fetcher 与浏览器支持放在同一个 `fetchers` extra 中，因此依赖会安装浏览器相关 Python 包；本仓库脚本只使用普通 HTTP，不会下载或启动浏览器。

准备一个只包含目标页面的 `urls.txt`，每行一个 URL，然后运行：

```bash
python scripts/capture_public_pages.py \
  --urls urls.txt \
  --output captures/research.jsonl \
  --allow-domain example.org \
  --max-pages 10 \
  --delay-seconds 2
```

采集成功后，JSONL 每行包含来源 URL、UTC 获取时间、HTTP 状态、页面标题、可用的作者/日期/站点元数据和正文文本。可用 `--save-html-dir captures/html` 保存成功响应的 HTML。默认不覆盖已有 JSONL；追加时显式传入 `--append`。

检查本地保存的 HTML 和候选 selector：

```bash
python scripts/probe_selectors.py page.html \
  --css 'h1::text' \
  --css 'article p::text'
```

## 边界

运行前确认网站条款允许采集。采集器只请求清单中的 URL，不发现或跟进链接；默认每页之间至少间隔 2 秒，每个 host 遵守 robots 规则和更严格的 crawl delay，并将目标页上限限制为 50。遇到拒绝访问、限流或挑战页时停止，不重试、不绕过。脚本只用普通 HTTP，不启用浏览器伪装、代理轮换或 challenge-solving。

如需 JavaScript 渲染或真正的多页 Spider，请先阅读 `SKILL.md` 和上游对应文档，再按目标站点规则单独配置。

