# 虚拟细胞雷达（Virtual Cell Radar）

[English](README.md) | 简体中文

虚拟细胞雷达（Virtual Cell Radar）：AIVC（AI 虚拟细胞）方向的论文与行业动态实时知识库：自动抓取 arXiv、bioRxiv 的新论文，按关键词预筛，用 LLM 打分与归类，生成每日、每周 Markdown 报告，并发布为静态网站。另有一条并行的商业新闻线：从公开 RSS 与少数 HTML 新闻列表页收集融资、合作、并购、模型或数据发布、临床进展、重要人事等动态，结构化后附原文链接展示，见「商业新闻」。第三条是半静态的精选目录：GitHub 仓库、数据门户、基准、学习资源，见「知识资源」。

```
论文：  抓取  ->  关键词预筛  ->  LLM 打分与归类  ->  日报 / 周报  ->  静态站点
新闻：  RSS + HTML 列表页  ->  关键词 / 公司名预筛  ->  去重合并  ->  LLM 抽取与相关度  ->  事件合并  ->  报告「行业动态」  ->  静态站点
资源：  人工精选 YAML  ->  刷新 GitHub 指标  ->  从论文与新闻发现候选  ->  静态站点（#/resources）
```

网站是纯静态页面，没有 npm 构建步骤。每位访客都可以在浏览器里设置**自己的课题**（「我的课题」），看到哪些论文与之相关；课题内容不会发送到任何服务器。

- 仓库地址：https://github.com/mxqmxqmxq/virtual-cell-radar
- 网站：https://mxqmxqmxq.github.io/virtual-cell-radar/
- 许可：代码 MIT，本项目自有标注 CC BY 4.0，第三方元数据按各来源条款，见「数据与许可」。
- 路线图：[docs/ROADMAP.md](docs/ROADMAP.md)

> **分数不是质量评价。** `relevance`、`final_score`、`must_read` 只是「与 `config/` 中定义的 AIVC 研究范围的相关度」的自动标注，不代表论文的质量、正确性或价值。中文总结（`summary_zh`、`novelty_zh`）由 LLM 生成，可能有误，请以原论文为准。

## 环境要求

- [uv](https://docs.astral.sh/uv/)（Python 包与虚拟环境管理工具）
- Python 3.11，由 uv 自动安装和管理
- Node.js（可选，只用于运行 JavaScript 测试）

## 安装

```bash
git clone https://github.com/mxqmxqmxq/virtual-cell-radar.git
cd virtual-cell-radar
uv sync
```

可选：配置 LLM 打分所需的 API key。

```bash
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY=...
```

`.env` 已写入 `.gitignore`。不填 key 也能运行，见「没有可用打分后端时的行为」。

## 快速开始

```bash
# 抓取最近 3 天（默认值，取自 config/pipeline.yaml）的论文，打分并生成日报
uv run python -m aivc update

# 抓取最近 7 天
uv run python -m aivc update --days 7

# 只抓取和入库，跳过 LLM 打分（不需要 key）
uv run python -m aivc update --no-llm

# 建站并在 http://localhost:8000 预览
uv run python -m aivc build-site
uv run python -m aivc serve
```

## 命令一览

全局参数写在子命令之前，例如 `uv run python -m aivc -v update`。

| 命令 | 用途 | 参数 |
|---|---|---|
| `update` | 抓取最近几天的论文并生成日报（同一天多次运行，日报包含当天所有新入库论文）。默认会先顺带运行新闻步骤（由 `config/pipeline.yaml` 的 `news.run_with_update` 控制，默认开启）；新闻步骤失败只输出警告，当天的重大事件会置顶在日报里 | `--days N`：含今天在内的回看天数，默认取 `config/pipeline.yaml` 的 `update.default_days`（当前为 3，同时作为新闻窗口）；`--no-llm`：跳过 LLM 打分与新闻抽取；`--no-site`：完成后不自动重建网站；`--no-news`：本次不抓取行业新闻；`--refresh-resources`：论文更新后顺带刷新知识资源的 GitHub 指标（默认关；`pipeline.yaml` 的 `resources.run_with_update` 可设为常开） |
| `news` | 抓取并处理商业新闻：已启用的 RSS 与 HTML 列表页 -> 预筛 -> 去重合并 -> LLM 抽取 -> 写入 `data/news/YYYY-MM.jsonl`，然后重建网站，见「商业新闻」 | `--days N`：含今天在内的回看天数，默认取 `config/pipeline.yaml` 的 `news.default_days`（当前为 7）；`--no-llm`：只入库候选、不做 LLM 抽取（保持未打分）；`--limit N`：本次最多让 N 条候选进入 LLM 与入库（按来源轮转取，避免单一来源占满额度），其余本次不处理（汇总里会报数）；`--rescore-stale`：库内仍是旧提示词版本的新闻，若本次抓取又出现，就用当前提示词重打（独立预算 `news.rescore_stale_max_calls`，默认 40；重打失败保留旧分）；`--no-site`：完成后不自动重建网站 |
| `news-rescore` | 只重打库内已有条目：重新抓取启用的源以找回摘要片段（不入库新条目），找不回时用库内旧摘要作参考；真实 LLM 请求数有硬上限 | `--days N`、`--no-fetch`、`--ids`、`--include-unscored`、`--max-requests N`（含重试的真实请求数上限）、`--dry-run`、`--no-site` |
| `backfill` | 回填历史数据 | `--days N`：含今天在内的天数（必填）；`--no-llm`：跳过 LLM 打分 |
| `seed` | 导入你自己的 PDF 目录与 ICLR 摘要汇总。结果只写入私有的 `local/`，不写入公开的 `data/` | `--source-dir PATH`：种子目录（默认取 `AIVC_SEED_DIR`，否则取 `config/sources.yaml` 的 `seed.source_dir`）；`--iclr-md PATH`：ICLR 摘要汇总 markdown；`--no-llm`：跳过 LLM 打分 |
| `reindex` | 改了 `keywords.yaml` 后，按新规则重算库内条目的圈层与关键词分 | `--prune`：删除重算后不再相关的抓取条目（种子条目保留） |
| `score` | 对库内未打分的论文补打分 | `--limit N`：最多处理 N 篇 |
| `weekly` | 生成 `reports/weekly/YYYY-Www.md`，并按配置自动重建网站 | `--week YYYY-Www`：指定周，如 `2026-W45`；`--end YYYY-MM-DD`：取包含该日期的自然周（可用于进行中的本周）；`--week` 与 `--end` 互斥，都不指定时取最近一个完整的自然周；`--no-site`：完成后不自动重建网站 |
| `build-site` | 把数据导出到 `site/`（`data/papers.json`、`data/news.json`、`data/companies.json`、`data/resources.json` 等）并复制静态页面 | 无 |
| `serve` | 在 `127.0.0.1` 上预览静态网站；`site/index.html` 不存在时先建站 | `--port PORT`：端口，默认取 `config/pipeline.yaml` 的 `site.port`（当前为 8000） |
| `resources` | 知识资源板块，子命令 `list`、`check`、`refresh`、`discover`、`accept`、`archive`，见「知识资源」 | `list [--status accepted\|candidate\|archived] [--kind K]`；`check`：离线校验（有问题退出码 1）；`refresh [--force] [--limit N]`：拉取 GitHub 指标（需联网）；`discover [--dry-run]`：写入候选；`accept ID [--kind K] [--url U] [--name N] [--description D]`；`archive ID` |
| `check-public` | 发布前自查：只读地检查公开库（含 `data/news` 与 `data/resources`）、报告、网站与仓库是否含私有信息或过长引用；退出码 0 表示未发现问题 | 无 |
| `refresh-openreview` | 用 OpenReview 官方 API 重新获取 ICLR 条目并覆盖公开库中的副本，需要联网；取不回的条目移到 `local/` | `--dry-run`：只打印每条将发生的动作与汇总，不写任何文件；`--limit N`：最多处理 N 条 |

全局参数：

| 参数 | 说明 |
|---|---|
| `--config-dir DIR` | 配置目录，默认取 `AIVC_CONFIG_DIR`，否则为 `./config` |
| `--data-dir DIR` | 数据目录，默认取 `AIVC_DATA_DIR`，否则为 `./data` |
| `--reports-dir DIR` | 报告目录，默认 `./reports` |
| `--site-dir DIR` | 网站输出目录，默认取 `AIVC_SITE_DIR`，否则为 `./site` |
| `--local` | 本机私有视图，见「本机私有档」。也可设环境变量 `AIVC_LOCAL=1` |
| `--local-dir DIR` | 私有目录，默认取 `AIVC_LOCAL_DIR`，否则为 `./local` |
| `-v`, `--verbose` | 输出 DEBUG 日志 |

回填几百天会发出大量请求。建议先加 `--no-llm` 完成抓取，再分批打分：

```bash
uv run python -m aivc backfill --days 180 --no-llm
uv run python -m aivc score --limit 100
```

## 打分后端

LLM 打分在 `config/llm.yaml` 的 `backend` 里选择（默认 `auto`）：

| backend | 用什么 | 需要什么 | 适合 |
|---|---|---|---|
| `auto`（默认） | 有 `ANTHROPIC_API_KEY` 就用 API，否则用本机 Claude Code | 二者之一 | 日常本机使用 |
| `api` | Anthropic API，按调用量付费 | `.env` 或环境变量里的 `ANTHROPIC_API_KEY` | GitHub Actions 等服务器环境 |
| `claude_cli` | 本机 `claude -p`，使用你的 Claude Code 订阅额度 | 装好并登录 Claude Code | 本机，不想单独配 key |

用本机 Claude Code 时什么都不用配：终端里能运行 `claude` 且已登录，`update`、`score` 就会自动使用。每篇约 10 秒（默认 4 篇并发），调用时不给模型任何工具，不加载 hooks、插件或 MCP，也不保存会话。只能在你自己的电脑上使用。两种后端使用同一个模型 id，打分缓存共用，换后端不会重复打分。

当前打分设置（`config/llm.yaml`）：提示词版本 `v3`，模型 `claude-haiku-5-5`（请确认你的账号可用），`must_read_threshold: 9`。最终分 = `relevance` × 圈权重（核心圈 1.0，外围圈 0.6），`final_score` 达到 `must_read_threshold` 时标为必读。具体数值始终以 `config/llm.yaml` 为准。

## 没有可用打分后端时的行为

既没有 `ANTHROPIC_API_KEY`、也找不到 `claude` 命令，或者鉴权失败、模型 id 不存在时，流水线照常完成：

- 论文仍会抓取、预筛、去重并入库，`llm_status` 标记为 `unscored`；
- 日报和周报按关键词分排序，并注明未打分；
- 命令退出码为 0。

配置好 key 或登录 Claude Code 之后补打分：

```bash
uv run python -m aivc score
```

同一篇论文不会重复打分：打分成功的记录带有 `llm_status=scored`，结果同时追加写入 `data/cache/llm.jsonl`。

## 我的课题

网站导航栏里有「我的课题」入口。每位访客可以添加自己的课题（例如「空间组学」加几个关键词），标题、摘要或关键词命中的论文会被打上课题标签。

- **匹配规则（v1）。** 匹配文本为标题 + 摘要 + 关键词，课题的任一词项命中即命中。词项不区分大小写；空格与连字符等价（`single-cell` 匹配 `single cell`）；普通英文词自动匹配简单复数，全大写缩写不加复数（`UCE`）；命中处前面不能紧挨字母或连字符。结尾 `*` 为前缀匹配（`transcriptom*`），开头 `*` 允许前面接字母。含中日韩汉字的词项按字面子串、区分大小写匹配。用户输入一律先转义，绝不会被当作正则。同一套规则在 Python（`src/aivc/termmatch.py`）和 JavaScript（`src/aivc/web/assets/topics.js`）里各实现一份，用共享测试用例保持一致。
- **在哪里生效。** 匹配在浏览器里对已加载的论文现算：论文卡片与详情抽屉上的课题标签、论文流的课题筛选、周报页的「本周与我的课题相关」。
- **数据存在哪里。** 课题只保存在访客自己浏览器的 `localStorage`（键 `aivc.topics.v1`），清除站点数据即删除。
- **分享。** 课题可导出为链接（内容放在 URL 的 `#` 片段里，不会发送给服务器）或 JSON 文件。打开链接或导入文件只会弹出确认框，访客确认后才会合并。
- **部署者的预设。** 部署者可以在 `config/topics.yaml` 里提供公开预设课题（`id`、`name`、`terms`、`default_enabled`）。预设会作为可选课题出现在网站上。`default_enabled: true` 表示预设对访客默认启用（访客仍可关闭），并且会用于 Markdown 日报、周报里的「主题追踪」一节；`false` 表示只是可选预设，不会出现在这些报告里。仓库自带的示例预设都是 `false`。
- 上限：最多 20 个课题，每个课题最多 40 个词项。

## 本机私有档（`--local`）

维护者想在自己的电脑上使用私有课题或私有论文时，可用 `--local`（或 `AIVC_LOCAL=1`）在公开数据之上叠加一层私有数据：

- `local/topics.yaml` 提供私有课题（格式同 `config/topics.yaml`）；
- `local/data/` 存放私有论文，例如 `seed` 的导入结果；与公开库同 id 的论文只从 `local/` 取 `local_file` 与 `tags`，只存在于 `local/` 的论文整条追加；
- 报告与网站输出到 `local/reports`、`local/site`，不写 `reports/` 和 `site/`。

```bash
uv run python -m aivc --local build-site
uv run python -m aivc --local serve --port 8001
```

隔离保证：`local/` 写在 `.gitignore` 里，不进入仓库；`seed` 只写 `local/data`，种子内容不会混进公开数据；不带 `--local` 时完全不读取 `local/`；`check-public` 检查私有课题名、绝对路径和白名单之外的 tag 是否出现在公开数据、报告、网站或仓库里。

## 流水线说明

```
抓取（arXiv、bioRxiv）
  -> 关键词预筛（核心圈 / 外围圈）
  -> 去重（DOI / arXiv id / 标题模糊匹配）
  -> LLM 打分与字段抽取（模型在 config/llm.yaml 中配置）
  -> 按月 JSONL 入库（data/papers/YYYY-MM.jsonl）
  -> 每日 Markdown 日报（reports/daily/YYYY-MM-DD.md）
```

商业新闻有自己的流水线，见「商业新闻」一节。

- 只有通过关键词预筛（核心圈或外围圈）的条目才会入库，避免 bioRxiv 全类目把库撑爆。
- 去重键依次为 `arxiv:<无版本号 id>`、`biorxiv:<id>`、`doi:`、`openreview:`，再加归一化标题精确匹配和模糊匹配（阈值见 `config/pipeline.yaml` 的 `dedup.title_threshold`）。
- 最终分 `final_score = relevance × 圈权重`，核心圈权重 1.0，外围圈 0.6；`final_score` 达到 `config/llm.yaml` 的 `must_read_threshold`（当前为 9）时 `must_read` 为真。

## Phase 2：周报与本地网站

```bash
uv run python -m aivc weekly       # 最近一个完整自然周
uv run python -m aivc build-site   # 导出到 site/
uv run python -m aivc serve        # http://localhost:8000
```

`serve --port 9001` 可改用其他端口。

- **周的定义。** 自然周，周一到周日，周 id 形如 `2026-W45`（ISO 周编号）。论文按入库日期 `first_seen` 归周，不按发布日期。
- **周报**（`reports/weekly/YYYY-Www.md`）：本周概览（新增篇数、圈层分布、已打分与未打分数量）、按 `final_score` 排序的 Top 论文并附 `novelty_zh`、未打分论文（按关键词分排序，单独成节）、本周新发布的数据集或基准、范式与任务分布及与上一周的对比；只有 `default_enabled: true` 的课题才会生成「主题追踪」一节。本周有新闻数据时还有「行业动态」一节（只含已打分且相关度不低于 `news.site_min_relevance` 的条目）：重大事件标 [重大]，本周前 `news.weekly_top_n` 条重要事件（含摘要与来源链接），以及带合计的融资表。本周没有论文但有新闻时，仍会生成周报。
- **周报 LLM 综述（可选）。** 默认关闭；在 `config/pipeline.yaml` 把 `weekly.llm_overview` 改为 `true`，周报会多一段 3–5 句的综述。后端与打分相同，没有可用后端时自动跳过。
- **自动建站。** `site.auto_build` 为 `true` 时，`update` 与 `weekly` 结束后自动 `build-site`（`--no-site` 可跳过），建站失败只输出一行警告。`score`、`backfill`、`seed`、`reindex` 之后不会自动建站，需要手动运行 `build-site`。
- **网站页面。** 首页（`#/`）；论文流（搜索，按圈层、来源、是否已打分、是否必读、是否发布新数据集、任务、范式、课题、时间范围筛选，按最终分、关键词分或日期排序，详情抽屉；论文若提到所跟踪的公司或其模型，会链到该公司档案）；范式地图（范式 × 任务热力图）；数据集 / 基准；周报；我的课题；**商业新闻**（`#/news`，三个视图，见「商业新闻」）；**关于**（`#/about`：一篇论文的分步旅程、命令行四步终端演示与常见问题，从首页和页脚进入）；**知识资源**（`#/resources`，见「知识资源」）。
- **访问方式。** 必须通过 `serve` 访问；直接用 `file://` 打开 `site/index.html` 读不到 JSON 数据。`serve` 只监听 `127.0.0.1`。`site/` 是生成产物，已加入 `.gitignore`。

### 首页结构

首页有 5 个区块，每块回答一个不同的问题，呈现形式各不相同（不再连续出现同样的卡片网格）：

1. **首屏**：「这是什么、规模多大？」大标题与轮换高亮词（含「行业动态」）、入口按钮、关键数字（论文数、已打分数、必读数、近 7 天行业事件）、LLM 打分进度条；WebGL 隧道背景，不支持时回退为 CSS 渐变。
2. **本周速览**：「最近发生了什么？」首页主体，简报版式而非卡片。左侧是编号的论文排行（分数、标题、一句新意、课题标签），可在「全部 / 我的课题」间切换（有启用的课题时，命中论文排前并带标签）；右侧三段堆叠：行业动态（重大事件置顶，链到 `#/news`）、新数据集、范式趋势变化（本期对比上期）。时间窗是「近 7 天滚动」，所以周一也不会为空；标题处链到完整周报。
3. **领域脉搏**：「领域往哪走？」两张图：范式 × 任务热力图，以及季度融资柱状图（来自新闻数据，链到 `#/news?view=funding`）。
4. **它如何工作**：「数据从哪来，可信吗？」数据来源芯片加标签页式流水线演示，并链到 `#/about`。
5. **结尾**：「怎么让它为我所用？」一个打开「我的课题」面板的按钮，论文流、行业动态、周报的入口，以及数据生成时间。

原先在首页的「一篇论文的旅程」分步区块、命令行终端和常见问题已移到 `#/about`；同样内容的文字版见下面「命令行四步与常见问题」。

内容全部来自 `site/data/*.json`（包括 `build-site` 额外写出的 `home.json`、`news.json`、`companies.json`）。系统开启「减少动态效果」（`prefers-reduced-motion`）时，不播放循环和位移动画，直接显示终态；循环动画离开视口或页面隐藏时暂停。`home.json` 缺失或首页脚本出错时，网站回退到简版首页；缺少新闻数据时显示空状态并给出该运行的命令。前端不使用第三方库。

### 命令行四步与常见问题

首页原来用终端演示的流程，也是 `#/about` 页的内容：

```bash
uv run python -m aivc update       # 1. 抓取论文（及新闻）、预筛、去重、入库、打分、生成日报
uv run python -m aivc score        # 2. 对仍未打分的论文补打分（用过 --no-llm 或没有后端时）
uv run python -m aivc weekly       # 3. 生成最近一个完整自然周的周报
uv run python -m aivc build-site   # 4. 导出 site/；再用 serve 在 http://localhost:8000 预览
```

- *数据从哪来？* 每日抓取 arXiv 与 bioRxiv 并按关键词预筛；OpenReview（ICLR）条目作为投稿收录；商业新闻来自「商业新闻」一节列出的 RSS 与 HTML 列表页。medRxiv 与期刊 RSS 未启用。
- *分数和「必读」怎么来？* LLM 给每篇论文打相关度；`final_score = relevance × 圈权重`；`final_score >= must_read_threshold` 即必读。这些是相关度标注，不是质量评价。
- *未打分的论文怎么显示？* 照常入库并标为未打分，报告里按关键词分排序，网站上明确标出；之后用 `score` 补打分。
- *周报怎么生成？* 自然周（周一到周日，ISO 编号），按 `first_seen` 划分。
- *怎么本地预览？* 先 `build-site` 再 `serve`（`--port` 改端口）。
- *开启「减少动态效果」后首页什么样？* 不播放循环和位移动画，直接显示终态。
- *怎么只看和我课题相关的论文？* 用导航栏的「我的课题」；课题只保存在你的浏览器里。

## 商业新闻

商业新闻板块收集 AIVC 与 AI 药物发现领域的重要行业动态（融资、合作、并购、模型或数据发布、临床进展、重要人事），结构化后与论文库互相链接。

```bash
uv run python -m aivc news                  # 回看 news.default_days 天（7），真实 LLM 抽取
uv run python -m aivc news --days 14 --limit 15
uv run python -m aivc news --no-llm         # 只入库候选，不抽取
uv run python -m aivc update --no-news      # 只更新论文
```

**数据源。** 公开 RSS 加少数 HTML 新闻列表页，都配置在 `config/sources.yaml` 的 `news` 段。**共启用 23 个源**：19 个 RSS（5 个媒体、14 个机构或公司官方博客）与 4 个启用的 HTML 列表页（另有 4 个 RSS 源和 2 个 HTML 源已配置但停用，见下表）。均于 2026-11-15 用项目 User-Agent 实测：`robots.txt` 未禁止该路径、HTTP 200、可解析、条目带标题与链接。找得到条款页的已读过条款；没找到或条款没提自动访问的源仍启用，但在 [DATA_LICENSE.md](DATA_LICENSE.md) 中标「待核实」。

| 类别 | 已启用的源 |
|---|---|
| RSS，媒体（5） | TechCrunch Biotech & Health、GEN、Labiotech、BiopharmaTrend（设 `require_date`，目前不产出）、FDA Press Releases；因条款不利已停用：STAT、Drug Discovery Trends、MedCity News |
| RSS，机构或公司（14） | Chan Zuckerberg Initiative、Biohub、Absci、XtalPi（晶泰科技）、EMBL News、Wyss Institute、MIT News（Biological engineering 与 Artificial intelligence 两个）、Google Research Blog、Google Health Blog、Google DeepMind Blog、Microsoft Research Blog、Valence Labs（Recursion 旗下研究团队）、Enveda；因条款不利已停用：Genentech Stories |
| HTML 列表页（启用 4、停用 2） | Arc Institute、Wellcome Sanger Institute、insitro、EMBL-EBI；已配置但默认停用（条款限制公开展示/转载，待作者确认）：Dyno Therapeutics、Noetik |

**最近一次扩充（2026-11-15）。** 目标名单里的公司与机构都逐家查过：有没有官方 RSS，或可以读取的新闻列表页，并且先读 `robots.txt` 与站点条款再决定。新接入 4 个源：Valence Labs（RSS；属 Recursion 旗下研究团队，母公司条款禁止机器人访问，但写明适用范围只是 recursion.com，请作者确认）与 Enveda（RSS）已启用；Dyno Therapeutics（HTML）与 Noetik（HTML）已配置，但因条款限制公开展示和转载而默认停用，等作者确认。一律只存标题、链接和日期。

**查过但因条款禁止自动访问或抓取而未启用：** Recursion、Genentech / Prescient Design、Chai Discovery、Iambic、Owkin、BenevolentAI、Cellarity、Profluent、EvolutionaryScale、Cradle（条文针对欺骗性手段收集信息，出于保守仍不接）和 NewLimit（博客在 Substack）。此前已启用的 Genentech Stories feed 来自 gene.com，其条款禁止 data mining 与 robots；该源现已停用，STAT、Drug Discovery Trends、MedCity News 也已停用，库内相应条目已移除（[docs/AUTHOR_REVIEW.md](docs/AUTHOR_REVIEW.md) 中的相关条目可能仍写作待决）。2026-11-15 又复核了聚焦虚拟细胞的候选源（Xaira、Tahoe 没有新闻页，Bioptimus 没有条款页，Noetik 与 Dyno 限制公开展示），没有可新增的合规源。**因技术原因未启用：** Isomorphic Labs（卡片日期不可信）、Bioptimus、Deep Genomics、Lila Sciences、Formation Bio、Altos Labs、Genesis Molecular AI（取不到干净的标题或日期）、BioMap 与 Xaira（前端渲染，没有条目链接）、Allen Institute（前端渲染）、Tahoe Therapeutics（官网已跳转到 tahoelabs.ai，且没有新闻页；`config/companies.yaml` 里的条目暂不改，等作者确认是否更名）。每一条的原因和已读的条款页都写在 `config/sources.yaml` 的注释里。

**HTML 列表页抓取器**（`src/aivc/fetchers/news_html.py`，只用标准库 `html.parser`，不新增依赖）。用于没有 RSS、但 `robots.txt` 与条款不禁止自动读取列表页的站点。每个页面在 `html_sources` 里配置：`name`、`list_url`、`kind`、`company`、`link_pattern`（条目链接须匹配的正则）、可选的 `exclude_pattern`、`same_host_only`（默认 true）、`min_title_chars`（默认 15）、`max_items`（默认 30）、`require_date`（默认 true）、`date_scope`（日期相对链接的位置：`inside`、`after`、`before` 或 `auto`，默认 `auto`）和 `title_strip_patterns`（正则列表，在判断标题长度之前，从标题候选里删掉每个命中的部分，例如类别前缀或末尾署名；默认为空，即不清洗）。约束：只读**列表页**（从不请求文章页）；只取**标题、链接、日期**，片段恒为空，不存正文；解析不出日期的条目丢弃；遵守 `robots.txt`；请求间隔至少 1 秒；单个源失败不影响其他源。每个新增的 HTML 源在 `tests/test_news_html.py` 里都有离线 fixture 测试。

未启用的源及原因记录在 `config/sources.yaml` 的注释和 [DATA_LICENSE.md](DATA_LICENSE.md) 中：FierceBiotech、Endpoints News、Recursion 投资者新闻（HTTP 403）；BioPharma Dive、NVIDIA 博客、PR Newswire、Substack 类 newsletter（条款禁止自动访问）；Nature 系列（robots.txt 禁止 AI 爬虫，条款禁止用 feed 建库）；Science 新闻（robots.txt 禁止 AI 爬虫，条款页读不到）；MIT Technology Review、Pharmaceutical Technology、Insilico Medicine（robots.txt 限制）；GlobeNewswire（无法确认官方 feed 地址）。其余多数公司官网没有 RSS。BiopharmaTrend 目前没有产出，因为其 feed 不带日期。增删源请编辑 `feeds` 或 `html_sources`（RSS 的字段：`name`、`url`、`kind`＝`media` 或 `company`、`company`、`enabled`），事先核实 `robots.txt`、条款与 HTTP 状态。各来源条款摘要见 [DATA_LICENSE.md](DATA_LICENSE.md)，其中标「待核实」的尚未确认。

**流水线。**

```
RSS 与 HTML 列表页抓取（遵守 robots.txt，请求间隔 >= 1 秒）
  -> 预筛（pipeline.yaml 的 news.keywords 关键词，或命中所跟踪的公司名，另有组合规则：AI + 生物、融资 + AI、盘点汇总；公司官方源也必须命中）
  -> 去重（规范化 URL、标题模糊匹配；多家媒体报道同一件事时保留全部来源链接）
  -> LLM 结构化抽取与相关度 0-10（提示词 news-v5，独立缓存 data/cache/news_llm.jsonl）
  -> 事件合并（同事件类型 + 公司 + 日期窗 + 金额的跨媒体合并）
  -> 重大事件规则
  -> data/news/YYYY-MM.jsonl
  -> 日报（置顶重大事件）、周报「行业动态」、site/data/news.json 与 companies.json
```

LLM 后端的选择与论文打分完全一致（见「打分后端」）。没有后端或加了 `--no-llm` 时，条目以未打分状态入库，之后的 `news` 运行若 feed 仍列出该条就补打分（否则只凭标题）。`news.max_llm_calls_per_run` 限制单次运行的 LLM 调用数。提示词 `news-v5` 以虚拟细胞方向为核心，衡量与细胞层面 AI 建模的贴近程度，分四档：9-10 为虚拟细胞、细胞基础模型、扰动图谱或大规模扰动数据方面的重大事件；7-8 为核心业务是细胞建模、扰动筛选、单细胞/多组学/表型 AI 平台的公司的融资、合作与发布，以及大药企专门针对细胞层面建模或扰动数据的合作；5-6 为有明确细胞、组学或表型筛选成分的 AI 药物发现新闻；4 分及以下为一般 AI 药物发现（分子生成与设计、AI 药物的临床进展与临床获批）、通用 AI 产品、实验室自动化（除非直接产生扰动数据）等。交易金额、知名度与临床阶段本身不能把没有细胞成分的事件抬到 5 分以上，细胞治疗也不等于细胞建模。v4 的降分规则继续保留：泛科普、访谈与观点稿、盘点汇总、品牌活动、与生物医药无关的业务、非生物医药的框架协议、研究机构与科技公司博客中的泛稿，相关度一律打低；观点、访谈、盘点、分析稿即使提到交易也不超过 4 分，传闻或洽谈中的融资、公益/机构资助、VC 自身募资不算 `funding` 金额。加 `--rescore-stale` 时，库内仍是旧提示词版本的条目若在本次抓取里再次出现，会用当前提示词重打（库内不存片段，所以不再出现在任何源里的条目保留旧分）。`aivc news-rescore` 是另一条命令，只重打库内已有条目：会重新抓取启用的源，仅为找回每条的摘要片段（不入库新条目），找不回片段时用库内旧的中文摘要作参考，并对真实 LLM 请求数（含重试）设硬上限（`--max-requests`）。可用 `--ids` 指定条目，`--include-unscored` 纳入未打分条目，`--dry-run` 只列出计划，`--no-fetch` 跳过重新抓取，`--days` 设回看天数，`--no-site` 完成后不重建网站。

**展示限制。** 网站与周报共用同一筛选（`news.publishable`）：条目必须有发布日期，且 `relevance >= news.site_min_relevance`（默认 6；6 分起才是有明确细胞、组学或表型成分的新闻）。无日期的条目不展示、不进周报、不计入融资合计（源条目可设 `require_date: true` 在抓取时丢弃无日期条目；BiopharmaTrend 已设置，其 feed 不带日期，所以目前不产出；它此前留在库内的 7 条已标记为 `llm_status: unscored`、`llm_skip_reason: undated_excluded`，旧 v3 打分已清除）。此外，同一公司每周最多 `news.max_per_company_per_week`（默认 3）条，任意连续 30 天内最多 `news.max_per_company_per_30d`（默认 4）条（`<= 0` 表示不限），避免单一公司占满页面。新闻页的周内分组与首页速览按相关度排序，同分时按公司交替轮转，再按日期。

**最近一次真实运行**（2026-11-15）。4 个条款不利的源（STAT、Genentech Stories、Drug Discovery Trends、MedCity News）已停用，其条目已从 `data/news/` 移除。打分细则换成以虚拟细胞为核心的 `news-v5`，展示门槛由 5 提到 6，库内旧条目用 `aivc news-rescore` 重打；随后用 `aivc news --days 90` 回溯抓取 90 天。库内现共 54 条：47 条已打分，7 条因无发布日期未打分（BiopharmaTrend，`undated_excluded`，不展示）。**网站展示 3 条**：Arc Institute 的 Bolero（7 分）、Biohub/CZI 的肿瘤耐药 AI 研究（6 分）、MIT 的细胞通讯模型（6 分）。多数旧条目降分，是因为属于没有细胞层面成分的一般 AI 药物发现（例如晶泰的临床获批与合作稿由 6-7 分降到 1-3 分）；EMBL 单细胞胶质母细胞瘤研究与 Biohub 血细胞图像脓毒症平台为 5 分，恰在门槛之下。**虚拟细胞方向的新闻覆盖仍然偏薄**：聚焦该方向的合规源（Arc Institute、Biohub、CZI、EMBL、Sanger、insitro、Valence Labs）发稿很少，其余虚拟细胞公司的网站条款禁止抓取或没有可解析的新闻列表，见「已知限制」。

**存什么、不存什么。** 只保存并展示标题、链接、来源名、发布时间，以及 LLM 用自己的话写的中文摘要与要点。不存储、不展示原文全文或长段引用；RSS 自带的片段（HTML 列表页没有片段）只在内存里作为 LLM 输入，不写盘。站点上每条都链接回原文并标明来源。`check-public` 会检查这一点（见下文）。版权方可按 [DATA_LICENSE.md](DATA_LICENSE.md) 提出删除。

**字段**（`src/aivc/models.py` 的 `NewsItem`，每个事件一行 JSON）：

| 字段 | 含义 |
|---|---|
| `id`、`title`、`url`、`source`、`published` | 标识、标题、主链接、来源名、发布日期 |
| `sources` | 报道该事件的全部媒体（各自的名称与链接） |
| `companies` | 涉及公司的规范名（与 `config/companies.yaml` 匹配） |
| `event_type` | `funding`、`partnership`、`m_and_a`、`model_or_data_release`、`clinical`、`people` 或 `other` |
| `amount_usd`、`round`、`investors` | 已披露的金额、轮次与投资方（未披露时为 null 或空） |
| `summary_zh`、`significance_zh` | LLM 写的中文摘要与「意义」一句话 |
| `relevance` | 与虚拟细胞（细胞层面 AI 建模）方向的相关度 0-10（网站只展示不低于 `news.site_min_relevance` 的条目，默认 6） |
| `llm`、`llm_status`、`llm_skip_reason` | 模型、提示词版本与时间；`unscored`、`scored` 或 `error` |
| `major`、`major_reasons` | 是否命中重大事件规则及命中的规则 |
| `keyword_score`、`matched_terms` | 预筛结果 |
| `first_seen`、`schema_version` | 首次入库日期；模式版本 |

**重大事件。** 规则写在 `config/pipeline.yaml` 的 `news.major` 列表里；命中任一条的条目标为重大，当天置顶在日报里，并在周报「行业动态」里排在最前。每条规则有 `id`、`label`、`event_types`，以及可选的 `min_amount_usd` 与 `company_tier`（`top` 在 `companies.yaml` 里按公司设置）。自带规则：融资不低于 1 亿美元；头部公司的模型或数据发布。**推送通知没有实现**：只留了接口（`major` 标记与每次运行的重大条目列表），不会向任何外部服务发送内容。

**网站上**（`#/news`）：按周分组的**时间线**，可按事件类型、公司、金额筛选，支持搜索与「只看重大」（筛选条件会保存在 URL 里）；**融资汇总**（`#/news?view=funding`，季度柱状图与融资事件列表）；**公司列表**（`#/news?view=companies`）与**公司档案页**（`#/news/company/<id>`）：该公司的新闻、模型，以及论文库中提到该公司或其模型的论文（用 `companies.yaml` 的 `paper_terms` 做词边界匹配；论文详情抽屉可回到公司档案）。

**注意事项。**
- 公司页与融资汇总里的「累计融资」只是**本库已收录且披露了金额的融资事件之和**，不是公司的真实总融资额，也不是完整的融资数据库。
- 摘要与要点由 LLM 生成，可能有误或不完整，使用前请核对原文。相关度只是相关度标注，不是对公司或新闻的评价。
- 抓取礼仪：请求带 `sources.yaml` 里的 User-Agent（`virtual-cell-radar/0.1 (+https://github.com/mxqmxqmxq/virtual-cell-radar)`），按主机检查 `robots.txt`，请求之间至少间隔 1 秒。
- 单个源（RSS 或 HTML）失败不会中断运行；只有所有已启用的源都失败时才算运行失败。

## 知识资源

精选、半静态的领域入口目录，页面分五个板块：实验室与团队、代表性仓库（含少量基础工具）、重要数据集与基准、综述与博客（含讲座）、社区精选列表（awesome 合集，只收链接）。重在精选而非求全，按月维护，只收公开资源。

```bash
uv run python -m aivc resources list                  # 按类型分组，显示 star、最近推送、是否维护
uv run python -m aivc resources check                 # 离线校验，有问题退出码 1
uv run python -m aivc resources refresh               # 拉取 GitHub 指标（需联网）
uv run python -m aivc resources discover              # 从论文与新闻提取候选（不联网）
uv run python -m aivc resources accept ID --url URL   # 把候选移入精选清单
uv run python -m aivc resources archive ID
uv run python -m aivc update --refresh-resources      # 更新论文与新闻后顺带刷新指标（默认关）
```

**数据布局**（`data/resources/`）：

| 文件 | 内容 | 谁写 |
|---|---|---|
| `labs.yaml` | 实验室、研究团队与机构（`lab`），27 条（含 7 个国内团队） | 人工 |
| `repos.yaml` | 仓库（`repo`），22 条 | 人工 |
| `tools.yaml` | 基础工具（`tool`），8 条在展示，6 条通用工具已归档 | 人工 |
| `data.yaml` | 数据门户（`data_portal`）13 条与基准（`benchmark`）7 条 | 人工 |
| `learning.yaml` | 综述、博客与报告、讲座、课程、教程，28 条在展示 | 人工 |
| `community.yaml` | 社区维护的资源合集（`awesome_list`），3 条 | 人工 |
| `candidates.yaml` | 自动发现或待审核的条目（`candidate`、`archived`） | `aivc resources` |
| `github_metrics.json` | 各仓库的 GitHub 指标、抓取时间、月度 star 快照 | `aivc resources refresh` |

精选清单目前有 **108 条 accepted**（lab 27、repo 22、tool 8、data_portal 13、benchmark 7、review 16、blog 7、podcast 2、tutorial 2、course 1、awesome_list 3）。整理时每个 URL 都实际请求过，确认不了的没有收录；描述由本项目用自己的话写，只陈述可核实的事实。指标放在独立的 JSON 里，月度刷新不会打乱人工文件；只有 `accept` 把候选移入精选文件时才会重写该文件（文件里手写的注释不会保留，备注请写在 `note` 字段）。

**字段**（`src/aivc/models.py` 的 `Resource`）：

| 字段 | 含义 |
|---|---|
| `id`、`name`、`url` | slug id（小写字母、数字、连字符，全库唯一）、名称、链接（http 或 https） |
| `kind` | `repo`、`tool`、`data_portal`、`benchmark`、`course`、`review`、`tutorial`、`blog`、`podcast` 或 `other` |
| `description_zh` | 用自己的话写的中文描述（不超过 300 字，`check-public` 会检查） |
| `tags`、`related_tasks`、`related_paradigms` | 自由标签，以及 `config/scope.yaml` 分类表里的 id（`check` 会拒绝未知 id） |
| `status` | `accepted`（展示）、`candidate`（待审核）、`archived` |
| `added`、`source` | 收录日期；`manual` 或 `auto_discovered` |
| `github` | 用于抓指标的 `owner/repo`（`url` 是仓库地址时自动推出） |
| `paper_ids`、`dataset_names`、`discovered_from`、`note` | 关联的论文库 id、与数据集页互链用的数据集名或别名、发现来源、备注 |

**GitHub 指标。** `resources refresh` 对每个精选仓库调用 GitHub REST API，记录 star、最近推送时间、是否归档、许可证标识（原样转述 GitHub 返回值，不做解释）与主语言，并记录抓取时间和月度 star 快照（下一次月度刷新后才能显示 star 变化）。可选的环境变量 `GITHUB_TOKEN` 会作为 bearer token 发送；没有 token 时受匿名限额 **每小时 60 次** 约束，触顶后优雅停止并报告未处理的仓库数（不丢数据，稍后再跑即可）。距上次抓取不足 `resources.refresh_min_interval_days`（25）天的仓库会跳过，加 `--force` 可忽略。首次真实刷新用了 42 次请求，42 个仓库全部找到，无错误。

**「仍在维护」的判定。** 仓库未归档，且最近推送距今不超过 `resources.maintained_within_days`（默认 365）天，就算维护中；按建站当天的日期计算，不落盘。没有 GitHub 指标的资源（数据门户、课程等）没有维护标记，也不会被该筛选排除。这只是按推送日期的活跃度规则，不是质量评价：已经完成、稳定的仓库也可能被标为不活跃。

**自动发现。** `resources discover` 不联网。它从论文的 `code_url` 与摘要里提取 GitHub 仓库链接，从 `llm.datasets_used`（至少被 `resources.discover_min_dataset_mentions` 篇论文提到，默认 3）与 `llm.new_dataset_names` 提取数据集名，并从新闻里提取仓库链接。结果以 `status: candidate` 写入 `candidates.yaml`，不会自动进入精选。论文库里的仓库链接很少，所以仓库候选极少（目前 1 个），候选大多是数据集名，其中夹杂细胞系名之类的噪声，需要人工筛选。`accept` 把候选移入对应的精选文件（数据集候选没有 URL，必须加 `--url`）；`archive` 让候选不再被重新发现，并让精选条目不再在网站展示。

**网站上**（`#/resources`，数据来自 `site/data/resources.json`）：按类型分组，可按类型、任务、范式、维护状态、star 区间筛选并搜索，筛选条件保存在 URL 里（`#/resources?kind=&task=&paradigm=&maintained=&stars=&q=`）。仓库卡片显示 star、最近推送日期、主语言、许可证和归档或不活跃徽标。任务与范式标签链到论文流；数据集名链到论文搜索，数据集页里匹配到的数据集会链回对应资源（`#/resources?id=`）；设置了 `paper_ids` 的资源会出现在论文详情抽屉的「相关资源」里（目前还没有精选条目设置 `paper_ids`）。**候选与已归档条目不导出，也不在公开页展示。** 首页在结尾区有进入该页的入口。

资源的许可：本项目只存名称、链接和自己写的中文描述；每个资源的许可以其官网或仓库为准（卡片上显示的许可证标识转述自 GitHub API）。`data.yaml` 中多数数据门户的许可仍标「待核实」。见 [DATA_LICENSE.md](DATA_LICENSE.md)。

## 配置说明

所有配置都在 `config/*.yaml`，修改后下次运行即生效。

- **keywords.yaml**：关键词预筛。`groups` 中每个词组有 `circle`（`core` 或 `peripheral`）和 `weight`，命中词组的权重累加为 `keyword_score`，`circle` 取命中词组中优先级最高的一个。`min_keyword_score` 为入库的最低分，`exclude_terms` 为排除词。词组可以写 `all_of: [[A 类词], [B 类词]]`：每个子列表至少命中一个才算命中，用来收「方法词 + 生物语境」这类单独出现太泛的组合。词项匹配规则同「我的课题」一节。改完后运行 `reindex`。
- **sources.yaml**：各来源的开关与参数（`arxiv`、`biorxiv`、`medrxiv`、`journals_rss`、`openreview`、`news`、`http`、`seed`）。medRxiv 与期刊 RSS 未启用。`news` 段（取代原来的 `news_rss` 段）包含 `enabled`、`delay_seconds`、`user_agent`、`respect_robots`、`feeds` 列表（19 个已启用）和 `html_sources` 列表（HTML 列表页），见「商业新闻」。`openreview` 下还有 `refresh-openreview` 使用的 `api_base_url` 与 `delay_seconds`。`seed.source_dir` 为种子目录默认值。
- **scope.yaml**：核心圈与外围圈的权重（1.0 / 0.6），以及 LLM 抽取使用的任务 `tasks` 与范式 `paradigms` 分类表（`taxonomy`）。
- **llm.yaml**：`model`、`max_tokens`、`prompt_version`（`v3`）、`max_calls_per_run`、`concurrency`、`must_read_threshold`（9）、`abstract_max_chars`、`cache_file`、`backend`、`cli_command`、`cli_timeout`。
- **pipeline.yaml**：`update.default_days`、`backfill_chunk_days`、`dedup.title_threshold`、`report.max_items`、`weekly.*`（`top_n`、`max_unscored`、`max_per_topic`、`llm_overview`、`overview_model`、`overview_max_tokens`）、`site.*`（`auto_build`、`port`、`top_datasets_used`），`public.allowed_tags`（公开记录允许保留的 tag，其余视为私有），以及 `news` 段：`run_with_update`、`default_days`、`keywords`（预筛）、`title_threshold`（标题模糊去重）、`merge_window_days`（事件合并日期窗）、`company_feed_requires_match`、`max_per_company_per_week`、`max_per_company_per_30d`、`rescore_stale_max_calls`、`max_llm_calls_per_run`、`prompt_version`（`news-v5`）、`model`、`cache_file`、`site_min_relevance`（6）、`weekly_top_n` 与 `major` 规则，以及 `resources` 段：`maintained_within_days`、`refresh_min_interval_days`、`max_requests_per_run`、`request_delay_seconds`、`run_with_update`、`github_api_base`、`discover_min_dataset_mentions`。
- **topics.yaml**：给访客的公开预设课题，见「我的课题」。
- **companies.yaml**：所跟踪的公司名单，新闻流水线用它做匹配并生成公司档案。字段：`id`（公司档案页 URL 用）、`name`、`aliases`（匹配用的别名）、`url`、`category`（`research_institute`、`biotech`、`pharma`、`big_tech`、`philanthropy`、`other`）、`tier`（`top` 表示公认头部机构，供重大事件规则使用）、`models`（公开的模型或数据集，展示在档案页）、`paper_terms`（在论文标题与摘要里做词边界匹配、建立论文与公司互链的专名；不要写 Arc、State 之类的短词或普通词）。只写公开信息。

环境变量：`ANTHROPIC_API_KEY`、`GITHUB_TOKEN`（可选，仅 `resources refresh` 使用）、`AIVC_CONFIG_DIR`、`AIVC_DATA_DIR`、`AIVC_SITE_DIR`、`AIVC_LOCAL`、`AIVC_LOCAL_DIR`、`AIVC_SEED_DIR`（见 `.env.example`）。

## 数据格式

论文记录是 `data/papers/YYYY-MM.jsonl` 中的一行 JSON，字段由 `src/aivc/models.py` 的 `Paper` 模型定义。新闻条目与资源条目有各自的格式，见「商业新闻」与「知识资源」里的字段表。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 去重后的唯一 id，如 `arxiv:2606.01042` |
| `title` | str | 标题 |
| `abstract` | str 或 null | 摘要 |
| `authors` | list[str] | 作者 |
| `source` | str | `arxiv`、`biorxiv`、`medrxiv`、`openreview`、`journal`、`seed_pdf`、`other` |
| `published` | date 或 null | 发布日期 |
| `url`、`pdf_url` | str 或 null | 论文页与 PDF 链接 |
| `doi`、`arxiv_id`、`biorxiv_id`、`openreview_id` | str 或 null | 各类外部标识符 |
| `license` | str 或 null | 来源给出的许可标识，原样保存、不做解释（如 bioRxiv 的 `cc_by`）；来源没有给出时为 null（arXiv） |
| `categories` | list[str] | 类目 |
| `keyword_score` | float | 关键词预筛得分 |
| `matched_keywords` | list[str] | 命中的关键词 |
| `circle` | str | `core`（核心圈）、`peripheral`（外围圈）或 `none` |
| `llm_status` | str | `unscored`、`scored` 或 `error` |
| `llm_skip_reason` | str 或 null | 未打分的原因 |
| `llm` | 对象或 null | LLM 抽取结果：`tasks`、`paradigms`、`datasets_used`、`releases_new_dataset`、`new_dataset_names`、`relevance`（0–10）、`novelty_zh`、`summary_zh`、`model`、`prompt_version`、`scored_at` 等 |
| `final_score` | float 或 null | `relevance` × 圈权重，未打分时为 null |
| `must_read` | bool | `final_score` 达到 `must_read_threshold` 时为真 |
| `tags` | list[str] | 自由标签；公开记录只保留 `public.allowed_tags` 里的 tag |
| `first_seen` | date | 首次入库日期 |
| `local_file` | str 或 null | 仅本机私有档使用（相对种子目录的路径，不含绝对路径）；公开数据中恒为空 |
| `schema_version` | int | 当前为 1 |

`Paper` 模型还有一些可选字段（`venue`、`updated`、`version`、`keywords`、`affiliations`、`code_url`、`alt_ids` 等）。LLM 缓存为 `data/cache/llm.jsonl`（只追加）。

## 测试

```bash
env -u ANTHROPIC_API_KEY uv run pytest -q        # Python 测试，不联网
node --test "tests/js/*.test.mjs"                # JavaScript 测试（课题匹配、新闻与资源筛选）
```

撰写本文时：602 个 Python 测试与 87 个 JavaScript 测试全部通过。Python 测试用 `httpx.MockTransport` 加本地样例（含手写的 GitHub API 响应与 HTML 列表页），LLM 调用使用 mock，`conftest.py` 全局禁止联网。`node --test` 的通配符请像上面那样加引号；请写具体文件或通配符，直接传目录在部分 Node 版本下会报错。`scripts/site_shots.mjs` 是可选的、无依赖的 headless Chrome 网站回归脚本。

## 部署到 GitHub Pages

网站地址为 https://mxqmxqmxq.github.io/virtual-cell-radar/ （首次 `Pages` 工作流成功运行之前会返回 404）。仓库在 `.github/workflows/` 下带有三个 GitHub Actions 工作流：

- `ci.yml`：每次 push 和 pull request 都运行。先用 `uv sync --locked` 安装（所以必须提交 `uv.lock`，并与 `pyproject.toml` 保持一致），再运行 `uv run pytest -q`、`node --test "tests/js/*.test.mjs"`（Node 22）和 `uv run python -m aivc check-public`。不使用任何 secret。
- `pages.yml`：push 到 `main`、手动触发（**Actions -> Pages -> Run workflow**）、或 `Daily update` 成功完成后运行，执行 `uv run python -m aivc build-site` 并把 `site/` 部署到 GitHub Pages。构建只依赖已提交的 `data/`、`reports/`、`config/` 与资源数据（`site/` 和 `local/` 已被 `.gitignore` 忽略）。网站资源均为相对路径，因此也能部署在 `https://<用户>.github.io/<仓库>/` 这样的子路径下。
- `daily.yml`：只能手动触发（`workflow_dispatch`）；`schedule` 块处于注释状态，因为启用定时会产生 LLM API 费用。它运行 `uv run python -m aivc update`（默认也会抓取新闻）与 `check-public`，并把 `data/`（含 `data/news/`）与 `reports/` 的变更提交回仓库。打分需要下面的 secret，并且默认分支需允许机器人推送。用默认 `GITHUB_TOKEN` 推送不会触发 `push` 事件，所以 `pages.yml` 在它成功之后再重新部署。

步骤：

1. 把代码推送到 https://github.com/mxqmxqmxq/virtual-cell-radar 的 `main` 分支（若是你自己的 fork 或副本，就推到你自己的仓库，网站地址也会随之改变）。
2. 打开 **Actions** 页，如果 GitHub 提示，就启用工作流。
3. 在 **Settings -> Pages** 中把 **Source** 设为 **GitHub Actions**。
4. 触发首次部署：向 `main` 推送一次，或手动运行 `Pages` 工作流；`github-pages` 环境会显示站点地址。
5. 可选：要让 `daily.yml` 打分，在 Settings -> Secrets and variables -> Actions 添加 secret `ANTHROPIC_API_KEY`，API 费用由你自己承担。不配置时流水线跳过打分，照常完成。要定时运行，取消 `daily.yml` 里 `schedule` 几行的注释。

工作流固定使用 `actions/checkout@v7`、`astral-sh/setup-uv@v10.3.3`、`actions/setup-node@v7`、`actions/configure-pages@v6`、`actions/upload-pages-artifact@v5`、`actions/deploy-pages@v5`，版本于 2026-11 对照各 action 的 releases 核对过；之后请不定期复查。

## 发布前自查

```bash
uv run python -m aivc check-public
```

它是只读的，没有问题时返回 0。检查内容：公开数据记录中没有 `seed_pdf` 来源、没有 `local_file`、没有白名单（`public.allowed_tags`）之外的 tag；报告、网站数据和仓库文本文件中没有本机绝对路径（形如 `.../news/home/...` 的网址不会被误判为路径）；`data/resources/*.yaml` 的每个条目与 `github_metrics.json`、`site/data/resources.json` 的 `items` 链接为 http(s)、`description_zh` 不超过 300 字；`data/news` 的每条记录与 `site/data/news.json` 的 `items` 只使用允许的键、链接为 http(s)、标题不超过 300 字符、`summary_zh` 不超过 400 字、`significance_zh` 不超过 200 字，且不含连续 25 个及以上英文单词（疑似长段引用）；本机存在 `local/topics.yaml` 时，其中的私有课题名没有出现在公开位置（没有 `local/` 的环境，如 CI，会跳过这一步并给出说明）。发布前请逐项修复所报问题。

## 隐私

- 课题只保存在访客自己的浏览器（`localStorage`）里；网站没有账号系统，也没有服务端存储。
- 分享链接把课题内容放在 URL 的 `#` 片段中。浏览器不会把片段发给服务器，因此打开链接或加载页面时内容不会被传输。
- 页面会发出一类第三方请求：向 Google Fonts（`fonts.googleapis.com`、`fonts.gstatic.com`）请求字体，这会让 Google 看到访客的 IP 地址。离线或被屏蔽时回退到系统字体，页面照常可用。
- 静态网站不使用 cookie，也没有统计脚本。托管方（例如 GitHub Pages）有它自己的访问日志。

## 数据与许可

- **代码**：MIT，见 [LICENSE](LICENSE)。
- **本项目自有标注**（相关度、分数、分类、圈层、关键词命中、中文总结、报告）：CC BY 4.0。
- **第三方元数据**（标题、摘要、作者、关键词、链接）：不由本项目重新授权，每条记录仍受其来源（arXiv、bioRxiv、OpenReview）的条款约束。
- **知识资源**：名称、链接与本项目自写的中文描述（作为本项目自有标注，CC BY 4.0）；各资源自身的许可以其官网或仓库为准，GitHub 指标来自 GitHub REST API。
- 详情、来源条款与待核实事项见 [DATA_LICENSE.md](DATA_LICENSE.md)；数据来源与字体的致谢见 [NOTICE.md](NOTICE.md)。
- 来自 OpenReview 的条目是会议投稿：可能在审、被拒或撤稿，不是正式发表的论文，收录不代表任何背书。

## 已知限制

- 分数衡量的是与 AIVC 范围的相关度，不是论文质量；LLM 输出（分数、分类、中文总结）可能有误。
- 模型 id `claude-haiku-5-5` 需要你的账号可用，否则论文保持未打分。
- bioRxiv 的 `published` 是版本日期，较早的论文可能归入较早月份的文件。
- 种子条目没有摘要，不会被打分，并且只存在于本机私有档中。
- medRxiv、期刊 RSS 与更多会议尚未实现，见 [docs/ROADMAP.md](docs/ROADMAP.md)。
- 新闻覆盖取决于已启用的 23 个源与关键词预筛，并不是行业的完整记录；按虚拟细胞细则与门槛 6，90 天窗口里网站只展示 3 条（Arc Institute 1、Biohub/CZI 1、MIT 1），虚拟细胞方向的新闻覆盖偏薄：聚焦该方向的源产出很少，HTML 源的预筛只看得到标题，也没有接入中文媒体；[DATA_LICENSE.md](DATA_LICENSE.md) 中部分来源的条款仍标「待核实」。融资合计只统计库内事件，见「商业新闻」。
- 部分预印本的许可是否允许再分发其摘要仍在核实中，见 [DATA_LICENSE.md](DATA_LICENSE.md) 的待核实清单。
- 知识资源：`data/resources/data.yaml` 中多数数据许可仍标「待核实」；维护标记只按推送日期判断；star 增长要等第二次月度刷新才有；自动发现的仓库很少、数据集名噪声较多；目前没有精选条目关联 `paper_ids`。
