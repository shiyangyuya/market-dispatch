# Market Dispatch (`market-dispatch`)

自动采集 **Bloomberg · Reuters · WSJ** 公开渠道新闻元数据（标题、摘要、发布时间、链接），自动中英对照翻译、五栏主题分类、三信号要闻研判，并生成 **FT / WSJ 现代财经报刊风** 的自包含静态日报站点（首页即今日日报 + 近 3 天切换 + 历史归档）。

> 合规声明：仅采集公开 RSS 与公开 `<meta>` 摘要元数据，不抓取、不保存文章正文，不绕过任何付费墙；仅供个人学习研究。

## 核心特性

- **三源实时聚合**：Bloomberg 官方 RSS（4 频道）+ WSJ 官方实时 RSS（`feeds.content.dowjones.io`，3 频道）+ Reuters Google News RSS（24h 回看 + 限速 `og:description` 回退）。
- **双时间与按发布日归天**：数据库同时存储 `published_at`（新闻发布时间）与 `created_at`（数据创建时间），统一转 `Asia/Shanghai`；按 `COALESCE(published_at, created_at)` 归天并每小时重渲染近 3 天，页面卡片清晰标注双时间与跨零点补入。
- **五栏主题分类**：📈 市场与经济 / 💻 科技 / 🌍 政治与世界 / 🇺🇸 美国相关 / 🇨🇳 中国相关，配置化关键词词边界匹配，覆盖优先级 `china > us > 频道/关键词 > world`。
- **三信号要闻研判**：标题特征词（+2）、跨源同事件 Jaccard（+2）、Google News 同源头条（+3），近 3 天受影响日期自动重算。
- **双引擎翻译**：默认 Google 免费接口（`client=gtx`），配置或环境变量可一键切换 OpenAI 兼容 LLM（如智谱 `glm-4-flash`），失败自动降级并标注「未翻译」。
- **现代财经报刊风静态页**：零外部 CDN/字体依赖、可离线打开，内置栏目/来源筛选、仅看要闻、中英/仅中文切换、批量展开英文摘要与跨页同步的日间/夜间模式。
- **双存储与 FTS5 预留**：SQLite（主，内置 `articles_fts` 全文索引与触发器为搜索预留）+ MySQL（可选方言）。
- **云端自动化部署**：GitHub Actions 双分支流水线（`main` 存代码与 CI 门禁，`generated` 存数据库与静态站点并通过 GitHub Pages 发布），另附 macOS / Linux / Windows 本地可选定时脚本。

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖（Python 3.10+）
/opt/homebrew/bin/python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 2. 准备配置（默认 SQLite + Google 免费翻译，开箱即用）
cp config/config.example.yaml config/config.yaml

# 3. 初始化数据库（含 FTS5 索引与触发器）并执行一次抓取渲染
python -m src.main init-db
python -m src.main run

# 4. 浏览器打开生成的今日日报
open index.html
```

## 自动化测试

```bash
pytest -v
```

## 工程文档

- [01-需求文档.md](docs/01-需求文档.md)
- [02-实施方案.md](docs/02-实施方案.md)
- [03-测试计划与测试用例.md](docs/03-测试计划与测试用例.md)
- [04-部署与运维手册.md](docs/04-部署与运维手册.md)
- [静态交互原型](docs/prototype/index.html)
