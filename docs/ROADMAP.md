# 路线图

虚拟细胞雷达（Virtual Cell Radar）是一个自动更新、可检索、会归纳趋势的 AI 虚拟细胞（AIVC）领域知识库，计划覆盖三个板块：论文、商业新闻、知识资源。本文说明项目的定位、已完成的阶段和后续规划。

## 项目定位

- **抓取要勤，推送要少，归纳要慢。** 领域内的信息更新节奏并不需要"实时"：预印本按天发布，公司新闻按周计。因此采用三档节奏：每日抓取、每周摘要、每月归纳。
- **本地优先。** 全部流程可在本机一条命令跑完，配置走 `config/*.yaml`，密钥走环境变量，不依赖本机路径；同一套脚本可直接放进 GitHub Actions 定时运行。
- **纯文本存储。** 数据以 JSONL / YAML 保存在仓库内，可 diff、可审阅，不依赖数据库。
- **核心圈满权重，外围圈降权收录。** 虚拟细胞、扰动建模、细胞基础模型为核心圈；表型药物发现、分子生成、蛋白设计等为外围圈。
- **静态站点。** 前端为无构建工具的静态页面，读取导出的 JSON，可部署到任意静态托管。

## 已完成

- **论文抓取与预筛**：从 arXiv、bioRxiv 等来源抓取新论文，去重，并按关键词配置做相关性预筛。
- **LLM 打分与抽取**：对论文给出相关性分数，抽取任务、建模范式、数据集、新意等结构化字段；带本地缓存，同一篇不重复调用。
- **日报与周报**：生成每日论文列表和每周摘要（Markdown）。
- **静态站点**：论文流、筛选与检索等视图。
- **「我的课题」**：访客可设置自己的课题关键词，在浏览器端匹配并高亮相关论文。
- **商业新闻板块（Phase 3）**：
  - 数据源：配置在 `config/sources.yaml` 的 `news` 段，已启用 23 个公开源：19 个 RSS（5 个媒体，14 个机构或公司官方博客，含 CZI、Biohub、Absci、晶泰、EMBL、Wyss、MIT News、Google Research / Health / DeepMind、Microsoft Research、Valence Labs、Enveda）与 4 个 HTML 新闻列表页（Arc Institute、Wellcome Sanger、insitro、EMBL-EBI）；遵守 robots.txt、请求间隔不低于 1 秒；条款不允许或不可用的源未启用，原因记录在配置注释与 `DATA_LICENSE.md`。
  - 新闻源扩充（2026-11）：新增配置化的 HTML 列表页抓取器（`fetchers/news_html.py`，标准库解析，只取标题、链接、日期，不请求文章页、不存正文，尊重 robots，间隔不低于 1 秒，缺日期的条目丢弃，单源失败不影响其他源）；预筛增加组合规则（AI + 生物、融资 + AI、盘点汇总），公司官方源也须命中；提示词升为 `news-v3`、再升 `news-v4`（压低泛科普、访谈观点稿、品牌活动、非生物医药业务与泛稿；v4 规定观点/访谈/盘点/分析稿即使提到交易也不超过 4 分，传闻或洽谈中的融资、公益/机构资助、VC 自身募资不算融资金额）；`NewsFeed.require_date` 可在抓取时丢弃无日期条目，网站与周报共用 `news.publishable`，无日期条目不展示、不进周报、不计融资（BiopharmaTrend 已设置，其 feed 无日期，目前不产出；库内 7 条旧条目标为 `undated_excluded`），新增 `aivc news --rescore-stale`（旧版本条目在新一轮抓取中再次出现时按当前提示词重打，独立预算），`--limit` 截断时按来源轮转；展示层除每公司每周 3 条外，增加任意连续 30 天同一公司不超过 4 条的滚动上限，首页速览与新闻页周内排序在同分时按公司轮转。扩充后真实运行两次（30 天窗口；第一次抓取 225 条、预筛后 39 条、LLM 调用 37 次，v3；第二次换 v4 并重打 30 条、抓取 215 条）：最终网站展示 8 条（扩充前 7 条；第一次运行后曾为 14 条，v4 与日期规则剔除了盘点、分析、访谈与无日期稿），晶泰 3 条为最高，占 3/8 = 37.5%（此前 71%），低于 40% 目标但余量不大；门槛未调整。
  - 新闻分层与日期补全（2026-11）：提示词升为 `news-v6.1`（5 与 6 分开定义，虚拟细胞方向的分析、追踪稿给 5 且封顶 5）；新增 `news.site_related_min_relevance`（默认 5），网站新闻页在核心动态（>= 6）之下增加可折叠的「相关动态」区，首页、统计、公司档案、融资汇总与周报仍只算核心；RSS 源可设 `date_from_article`，对无 pubDate 的条目请求文章页只读发布日期（遵守 robots、间隔 >= 1 秒、`max_date_lookups_per_run` 限额、缓存于 `data/cache/news_dates.json`、正文不落盘），BiopharmaTrend 已启用；新增 New York Genome Center 与 Whitehead Institute 两个 RSS 源。
  - 流水线（`aivc news`，`aivc update` 默认一并运行，可用 `--no-news` 关闭）：关键词与公司名预筛、URL 与标题模糊去重、LLM 结构化抽取与相关度打分（提示词 `news-v1`，现为 `news-v6.1`，独立缓存）、跨媒体事件合并、重大事件规则，按月写入 `data/news/YYYY-MM.jsonl`。
  - 版权口径：只存标题、链接、来源、时间与 LLM 自写的中文摘要和要点，不存原文，RSS 片段只在内存使用；`check-public` 已扩展检查 `data/news` 与 `site/data/news.json`。
  - 报告：日报置顶当天重大事件，周报新增「行业动态」一节（重要事件与融资汇总）。
  - 网站：新闻页含时间线（按周分组，按类型、公司、金额筛选与搜索）、季度融资汇总、公司列表与公司档案页（与论文库互链）；导出 `site/data/news.json` 与 `companies.json`；公司名单 `config/companies.yaml` 现已参与流水线。
  - 首页改版：由 11 个区块精简为 5 个（首屏、本周速览、领域脉搏、它如何工作、结尾），旅程分步、命令行终端与常见问题移到 `#/about`。
- **知识资源板块（Phase 4）**：
  - 数据：`data/resources/` 下四个人工精选文件（`repos.yaml`、`tools.yaml`、`data.yaml`、`learning.yaml`），共 69 条已确认资源（repo 22、tool 14、data_portal 12、benchmark 6、review 5、tutorial 5、blog 3、course 1、other 1）；整理时每个链接都实际请求验证，查不到的没有收录；描述只写可核实的事实。候选放在 `candidates.yaml`，GitHub 指标与月度 star 快照放在 `github_metrics.json`，二者由命令写入。`Resource` 模型含 id、类型、链接、中文描述、标签、关联任务与范式（复用 `config/scope.yaml`）、状态、收录日期与来源、`github`、关联论文与数据集名。
  - 命令：`aivc resources list|check|refresh|discover|accept|archive`；`refresh` 用 GitHub REST API 取 star、最近推送、是否归档、许可证标识与主语言，支持可选 `GITHUB_TOKEN`，匿名每小时 60 次，触顶优雅停止并报告；「仍在维护」按最近推送时间判断（`resources.maintained_within_days`，默认 365 天）；`aivc update --refresh-resources`（默认关）可挂到更新流程。首次真实刷新 42 次请求全部成功，其中 11 个仓库超过一年未推送、1 个已归档，条目均保留并如实标注。
  - 自动发现：`discover` 从论文库（`code_url`、摘要中的 GitHub 链接、`datasets_used` 与 `new_dataset_names`）和新闻中提取候选，写入 `status=candidate`，不自动进入精选；首次发现 24 个候选（1 个仓库，23 个数据集名，细胞系名噪声较多），尚未接受任何一个。
  - 网站：`#/resources` 为真实页面，按类型分组，可按类型、任务、范式、维护状态、star 区间筛选并搜索，筛选保存在 URL；仓库卡片显示 star、最近推送、许可证与归档徽标；与数据集页、论文页互链；候选与已归档条目不公开展示；导出 `site/data/resources.json`；首页结尾区有入口。
  - 检查与文档：`check-public` 覆盖 `data/resources` 与 `site/data/resources.json`；`DATA_LICENSE.md` 与 `NOTICE.md` 补充新增新闻源与资源板块的条款结论。
- **论文流水线 v2（2026-11）**：
  - 新增来源：OpenAlex（期刊 ISSN 与通用查询，默认开）、Europe PMC（默认开）、HF Daily Papers（默认开）、OpenReview v2 抓取器（默认关）。
  - 共享链接提取器 `fetchers/links.py`（代码仓库与权重/数据集链接）；arXiv 查询同时匹配标题与摘要，bioRxiv 回填已发表 DOI/期刊。
  - 数据模型新增 pmid/pmcid/openalex_id/published_doi/published_venue/weights_url/citation_count/hf_upvotes/github_stars/matched_orgs/semantic_score/sources 与 LLM 四维分；去重时预印本→已发表晋升与多源并集。
  - `aivc enrich`（离线机构匹配 + 可选 OpenAlex 引用刷新）。
  - 混合预筛：关键词 + 标准库 TF-IDF 语义捞回，`semantic_rescue_threshold`。
  - LLM：提示词 v4（新颖/严谨/影响四维分、锚点校准、推荐理由与局限）、复合排序与客观加分、openai_compat 后端（OpenAI/DeepSeek/OpenRouter/Ollama/vLLM）、API 错误指数退避。
  - 水位线 `data/state/fetch_state.json` 与追赶窗口、积压补打 `retry_backlog_per_run`。
  - `aivc stats`。

## 后续规划（均为计划中，尚未实现；商业新闻与知识资源板块的基础功能已在上文「已完成」中）

### 论文板块

- 更多来源（剩余缺口）：期刊已通过 OpenAlex 的 ISSN 过滤与 Europe PMC 接入，ISSN 与接口字段待线上核实；ChemRxiv 与 PubMed 直连仅预留 `source` 取值，尚未接入；更多会议（NeurIPS、ICML、MLCB、RECOMB 等）尚未接入。
- 补充信号（剩余缺口）：引用数、代码与权重链接已接入（OpenAlex、Europe PMC、链接提取器）；Semantic Scholar 未接，相似论文未做；`semantic_rescue_threshold`（初值 0.28）待用真实库校准。
- 范式地图：建模范式 × 任务的矩阵视图，按月观察趋势。
- 数据集与基准总表：名称、模态、规模、扰动类型、细胞系、来源论文、下载地址、许可。
- 对高分论文自动生成精读卡。

### 商业新闻板块（基础功能已完成，以下为尚未实现的部分）

- 中文媒体源：已接入量子位、IT之家、雷峰网、36氪（RSS，严格预筛，条款待核实）；钛媒体、生物谷已配置但停用。剩余缺口：财联社（条款明禁、无 RSS）、动脉网（列表由客户端加载）、机器之心、DeepTech、新浪科技、凤凰网科技（无可用 feed）未接，微信公众号不接；中文 RSS 只保留几小时到一两天的条目，回填无效，依赖定时运行逐日积累。
- 仍不可用或被禁止的来源：FierceBiotech、Endpoints、Recursion IR（HTTP 403）；BioPharma Dive、NVIDIA 博客、PR Newswire、Substack 类 newsletter、Nature 系列、Science 新闻（条款或 robots.txt 禁止自动访问）；GlobeNewswire（无法确认官方 feed）。如对方日后放开或提供授权接口，再评估接入。
- 更多公司官方源：Owkin、Bioptimus、Noetik、Chai Discovery、Isomorphic Labs、Lila Sciences、Cellarity、Allen Institute 等已实测但因标题不干净、日期格式不支持或前端渲染而未接入 HTML 抓取器；需要扩展解析器（例如支持 dd.mm.yyyy 日期、剥离类别标签）后再逐个核对。
- 来源覆盖与质量：30 天窗口里网站展示 8 条，仍偏少；CZI、Absci、insitro 在该窗口没有抓到新条目，insitro 的日期取自摘要文字、版式变化时可能漏条，需要定期复查；BiopharmaTrend feed 不带发布日期，已改为 `date_from_article` 从文章页补日期（条款仍待作者核实）。
- 外部推送通知：重大事件目前只在日报、周报与网站上置顶标注，代码里只留了接口，没有向任何外部服务发送。
- 融资数据库与新闻稿检索：「累计融资」目前只是库内已收录且披露了金额的事件之和，不是完整融资数据库；接入权威、许可明确的融资数据源后才能给出可靠的累计值。
- 部分来源的条款仍标「待核实」（见 `DATA_LICENSE.md`），其中多数是没有找到条款页或条款未提自动访问。

### 知识资源板块（基础功能已完成，以下为尚未实现的部分）

- star 增长趋势：`github_metrics.json` 已按月记录 star 快照，但目前只有一次快照，网站尚无增长数字，也没有趋势图或「近期增长最快」排行榜；至少要有两次月度刷新后才有数据。
- 排行榜与榜首：没有按 star、增长或维护状态排序的榜单，也没有「当前榜首」之类的展示。
- 关联论文：资源与论文的互链逻辑已实现，但目前没有精选条目填写 `paper_ids`，论文详情里的「相关资源」入口暂时不会出现；数据集页的互链已有数据（部分条目带 `dataset_names`）。
- 候选的人工审核：24 个候选尚未处理；数据集名候选中含 k562、hepg2、pbmc 这类细胞系名，需要人工筛选或调高 `resources.discover_min_dataset_mentions`；自动发现只覆盖论文库与新闻，没有主动搜索 GitHub。
- 许可信息：`data.yaml` 中多数数据门户的许可仍标「待核实」；GitHub REST API 条款是否对本用法有特别要求尚未核对。
- 月度维护：目前需要人工运行 `aivc resources refresh`（或 `aivc update --refresh-resources`），还没有挂到定时任务；「仍在维护」只按最近推送日期判断，不反映仓库质量。
- 播客：`podcast` 类型已在模型里，精选清单尚未收录。

### 检索与集成

- 语义检索与问答（例如"最近有哪些用 flow matching 做扰动预测的论文"），需要时引入 SQLite 与向量索引。
- 月度归纳：范式趋势、数据集总表与知识资源的定期复查。
- 与文献管理工具、笔记工具的可选同步。
- 质量控制：收集推送反馈，用于调整关键词与打分阈值，并抽查抽取字段的准确率。

## 欢迎贡献的方向

- 新的数据源抓取器（期刊 RSS、会议、新闻源），抓取器应能用录制的样例响应离线测试。
- 关键词与分类配置：覆盖更多细分方向（如空间组学、多组学整合、基因调控网络）的关键词表。
- 打分与抽取的提示词改进，以及字段准确率的评测方法。
- 前端：范式地图、数据集总表、可访问性与移动端体验。
- 数据集、基准、仓库与学习资源的整理与勘误。
- 文档、测试与多语言支持。
