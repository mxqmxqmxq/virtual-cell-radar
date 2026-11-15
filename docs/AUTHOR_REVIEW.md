# 发布前待作者确认事项（授权与条款核实表）

> **先读这三条**
>
> 1. 本表**不是法律意见**。它只是把 `DATA_LICENSE.md` 里标为「待核实」的点整理成一张待办清单，并记录我在网上能读到的官方依据。
> 2. **核实日期：2026-11-15**。表中每个标「已核实 / 部分核实」的结论，都对应本次实际打开过的页面（URL 写在同一行，访问日期均为 2026-11-15）。打不开的页面写明状态码，结论保持「未能核实」。条款会变，发布前如隔了较久请重读。
> 3. **`DATA_LICENSE.md` 才是正式声明**，本表只是待办清单；作者逐项回答后，再把结论回写到 `DATA_LICENSE.md`（本表不改动它）。
>
> 读法：「核实结果」三档——已核实（读到了明确条款）/ 部分核实（读到一部分，仍有缺口）/ 未能核实（页面打不开或没有相关文字）。「建议」只是我的倾向，决定权在作者；凡建议「停用」的，都是因为条款里读到了对自动访问、存储或缓存的明文限制，或读不到条款又无法排除风险。

## 0. 速览与对照计数

| 对照项 | DATA_LICENSE.md 中的数量 | 本表对应 |
|---|---|---|
| 第 8 节「待核实清单」 | 16 条 | 全部有对应行，见下方对照表 |
| 第 6 节新闻表格里带「待核实」的源 | 19 行（另有 2026-11-15 新增的 4 行 Valence、Enveda、Dyno、Noetik，对应 C24–C27）（表格共 21 行，MIT News 与 EMBL-EBI 两行无此标记，本表仍列为 C15、C21 供参考）；之后 S5 补充核对又新增 NYGC、Whitehead 两行（C28、C29），并以 C30 汇总未启用的候选 | C1–C14、C16–C20，共 19 行 |
| 第 7 节资源表里带「待核实」的数据门户/基准 | `data/resources/data.yaml` 中 14 条（20 条里 14 条 note 带「待核实」） | D2–D15，共 14 行 |
| 第 6 节「未启用」段落里带「待核实」的 Science、OpenAI | 2 处 | C23 |
| 第 3、4、7 节正文里的「待核实」（bioRxiv、OpenReview/ICLR、Anthropic、Geist、GitHub） | 与第 8 节重复 | A1–A5、B1–B2、D1 |
| 本次核实中新发现的问题 | – | A7（arXiv 致谢语）、C2（STAT）、C6（Genentech）、C9、C10（条款明文禁止缓存/存储）、A5（OpenReview 匿名访问被挑战拦截），以及第 E 节 |

### 第 8 节 16 条与本表行号对照

| 第 8 节 | 内容 | 本表行 |
|---|---|---|
| 1 | bioRxiv `license` 字段取值 | A1 |
| 2 | bioRxiv 无 CC 许可的摘要能否再发布 | A2 |
| 3 | OpenReview 条款对摘要再发布 | A3 |
| 4 | ICLR 对在审/被拒/撤稿可见性 | A4 |
| 5 | OpenReview API 访问规则 | A5 |
| 6 | Anthropic 适用条款与 Outputs 再分发 | B1 |
| 7 | Google Fonts 上 Geist 的许可 | B2 |
| 8 | User-Agent 联系方式要求 | A6 |
| 9 | arXiv 致谢措辞 | A7 |
| 10 | TechCrunch / STAT / GEN 的摘要与存储 | C1、C2、C3 |
| 11 | Labiotech / CZI / Genentech / XtalPi 条款读不到 | C4、C5、C6、C7 |
| 12 | GlobeNewswire / FierceBiotech / Endpoints / Recursion IR 403 | C22 |
| 13 | 2026-11 新增的一批 feed | C8、C9、C10、C11、C12、C13、C14、C16、C17（MIT 为 C15） |
| 14 | Arc / insitro / Sanger / EMBL-EBI 列表页 | C18、C19、C20、C21 |
| 15 | Science 与 OpenAI 条款读不到 | C23 |
| 16 | 知识资源许可与 GitHub 条款 | D1（GitHub）、D2–D15（14 个门户/基准） |
| 17 | 2026-11-15 新增的中文媒体、Biohub 新闻稿栏目、播客与 newsletter | C31–C38 |

---

## A. 论文元数据

| 编号 | 来源或事项 | 当前处理方式 | 核实结果 | 需作者确认的问题 | 建议 |
|---|---|---|---|---|---|
| A1 | bioRxiv `license` 字段的取值（DATA_LICENSE §8.1） | 存标题、摘要、作者、DOI、链接；不重新授权，条目仍受原许可约束 | **部分核实**。bioRxiv API 页（https://api.biorxiv.org/ ，200）把 `license` 列为返回字段，但没有定义可取值。FAQ（https://www.biorxiv.org/about/FAQ ，200）列出作者可选的许可：CC BY、CC BY-NC、CC BY-ND、CC BY-NC-ND、CC0，以及不带 CC 许可（需向通讯作者取得许可）。TDM 页 https://www.biorxiv.org/tdm 连续三次返回 HTTP 429，未能读到。 | 站点是否要把 bioRxiv 条目的原始 `license` 字符串原样显示在卡片上（是/否）？ | 若数据里本来就有该字段，原样显示、不做映射；等 TDM 页能打开后再补读一次。 |
| A2 | bioRxiv 无 CC 许可的预印本，摘要能否放进衍生索引（§8.2） | 公开库保存标题和摘要，保留下架通道（DATA_LICENSE §5） | **部分核实**。FAQ（https://www.biorxiv.org/about/FAQ ，200）说无 CC 许可的预印本，要重用须先取得通讯作者许可；同页又说 bioRxiv 为所有文章提供免费且不受限的访问，并明确包含机器分析，称已有 API 与 feed 供此用。两处都没有回答「在衍生数据库里再发布摘要」是否被允许。 | 对 bioRxiv 条目，公开库是 (A) 继续保留摘要，还是 (B) 只留标题、DOI 链接与本项目自有标注，摘要只在本地保存？ | 倾向 (A)：摘要属于描述性元数据，且 bioRxiv 公开鼓励机器分析；同时在「关于」页写明摘要版权归原作者并保留下架通道。若想最保守，选 (B)。 |
| A3 | OpenReview 条款能否允许再发布投稿摘要（§8.3） | 保存标题、摘要、作者、关键词、链接 | **部分核实**。条款页（https://openreview.net/legal/terms ，200，页面显示 Last updated: November 5, 2026）里，「Metadata」的定义明确包含标题、作者、摘要、许可等；并写明「在提交者或 OpenReview 对元数据享有版权利益的范围内，适用 CC0 1.0」。同页另说：对下载的投稿，用户只能用于提供该投稿的目的，且不得宣称对投稿享有条款之外的权利。所以摘要作为元数据适用 CC0 较为清楚，但「用于提供目的」是否覆盖衍生索引仍不明确。 | 是否接受以「元数据 CC0」条款作为依据，继续在公开库保留 OpenReview 条目的标题、摘要、关键词和链接（是/否）？ | 保留，并保留下架通道；全文与 PDF 不存。第 E 节 (a) 另有具体两条记录需要作者决定。 |
| A4 | ICLR 对在审、被拒、撤稿投稿的公开可见性（§8.4） | DATA_LICENSE §2 第 3 点已声明「在审/被拒/撤稿，非正式发表」 | **部分核实**。作者指南（https://iclr.cc/Conferences/2026/AuthorGuide ，200）写明：官方评审在 OpenReview 上匿名且公开；双盲评审，作者姓名在评审期结束时才揭晓；截止日期之后撤稿的论文会留在公开的「撤稿」栏目，且论文视为存档、不能删除。征稿页（https://iclr.cc/Conferences/2026/CallForPapers ，200）只说用 OpenReview 托管论文并支持公开评审与讨论。指南没有写被拒论文的默认可见性。我猜的 https://iclr.cc/Conferences/2026/Policies 返回 404。 | 评审期内的条目，作者字段是否在公开库里隐去（是/否）？被拒论文是否继续保留（是/否）？ | 作者字段对「评审期内、尚未揭晓作者」的条目隐去或留空；其余保留并保留声明。 |
| A5 | OpenReview API 访问规则（匿名访问、速率限制）（§8.5） | `refresh-openreview` 命令按官方 API v2 逐条核验，请求间隔不低于 1 秒；`config/sources.yaml` 里 `openreview.enabled` 为 false | **部分核实**。文档「Using the API」页（https://docs.openreview.net/getting-started/using-the-api ，200）写明使用 API 需要 OpenReview 账号，且 API 的权限与网站一致；API V2 参考页（https://docs.openreview.net/reference/api-v2 ，200）只是目录页，没有速率限制说明。我用通用客户端标识对 `api2.openreview.net/notes` 做了一次只读 GET，返回 HTTP 403（ChallengeRequiredError，要求验证），未尝试绕过。也就是说，匿名脚本访问很可能走不通。 | 是否愿意用自己的 OpenReview 账号（凭证只放本地，不入库、不进 Actions）运行该命令（是/否）？不愿意的话，是否把它从 README 里标注为「需账号，默认不用」（是/否）？ | 保持 `openreview.enabled: false`；不要把 refresh-openreview 放进定时任务；README 标注需账号。 |
| A6 | User-Agent 是否要求联系方式（§8.8） | UA 为 `virtual-cell-radar/0.1 (+仓库地址)`，仓库地址充当联系方式，无邮箱 | **部分核实**。arXiv API 使用条款页（https://info.arxiv.org/help/api/tou.html ，200）没有 UA 或联系方式要求；它要求对 legacy API 每 3 秒不超过 1 次请求、同时只开 1 个连接（项目 arXiv 间隔为 3.0 秒，符合）。bioRxiv API 页（https://api.biorxiv.org/ ，200）也没有此类要求。OpenReview 本次未单独查。没有写出要求不等于确认没有。 | 是否接受以仓库 URL 作为联系方式、不写邮箱（是/否）？ | 保持现状。 |
| A7 | arXiv 致谢措辞（§8.9）及不得暗示背书 | `NOTICE.md` 已写出该致谢语，但注释称条款页未写明必须用语 | **已核实，并发现注释过期**。arXiv API 总览页（https://info.arxiv.org/help/api/index.html ，200）写明请在产品上标注致谢语 “Thank you to arXiv for use of its open access interoperability.”，并且不要用 arXiv 名称或标志，以免暗示 arXiv 背书；使用条款页（https://info.arxiv.org/help/api/tou.html ，200）把「宣称项目得到 arXiv 支持或背书」列入禁止事项，把「存储并提供 e-print 的 PDF 或源文件」也列入禁止事项（本项目不存）。我猜的 https://info.arxiv.org/help/policies/thank_you.html 返回 404，致谢语出处就是上面的总览页。 | 是否同意把该致谢语也放到网站「关于」页或页脚（是/否）？ | 放到「关于」页；并让 `NOTICE.md` 里「条款页未写明措辞」的注释改为指向 API 总览页（本任务未改动该文件）。 |

---

## B. LLM 与字体

| 编号 | 来源或事项 | 当前处理方式 | 核实结果 | 需作者确认的问题 | 建议 |
|---|---|---|---|---|---|
| B1 | Anthropic：适用哪套条款、Outputs 能否再分发（§8.6，另见 §4） | 打分结果（`summary_zh`、分数等）作为自有标注以 CC BY 4.0 发布，并带「机器生成」声明；打分可经 API key，或经本机 Claude Code 命令行（`src/aivc/claude_cli.py`，走本机登录） | **部分核实**。消费者条款（https://www.anthropic.com/legal/consumer-terms ，200）：在遵守条款的前提下，Anthropic 把其在 Outputs 上的权利（如有）转让给用户；同页禁止用机器人或脚本等自动化方式访问服务，除非通过 Anthropic API key 或另行明确允许。商业条款（https://www.anthropic.com/legal/commercial-terms ，200）：客户拥有 Outputs，转让以遵守条款为前提；D.3 要求客户提示用户事实性陈述需独立核对。两页都没有单独的「Outputs 再分发」限制。**用本机 Claude Code 登录跑打分属于哪一套条款，我没能确定。** | (1) 公开发布前，你自己跑打分用的是 API key 还是订阅登录的命令行？(2) GitHub Actions 里的打分是否只用 API key（建议是）？ | 公开数据的打分只用 API key 跑，或你自行向 Anthropic 确认订阅登录用于批量打分是否合规；保留「机器生成」声明。 |
| B2 | Geist 与 Geist Mono 字体在 Google Fonts 上的许可（§8.7） | 页面在浏览时从 Google Fonts 加载，仓库内无字体文件 | **已核实**。Google Fonts 目录仓库里的许可文件（https://raw.githubusercontent.com/google/fonts/main/ofl/geist/OFL.txt ，200）写明采用 SIL Open Font License 1.1；同仓库 Geist 与 Geist Mono 的元数据（https://raw.githubusercontent.com/google/fonts/main/ofl/geist/METADATA.pb 与 https://raw.githubusercontent.com/google/fonts/main/ofl/geistmono/METADATA.pb ，均 200）的 license 字段均为 OFL。上游仓库（https://raw.githubusercontent.com/vercel/geist-font/main/OFL.txt ，200）同样是 OFL 1.1。fonts.google.com 的字体页是前端渲染，页面源码里没有许可文字，所以没有直接读到 Google 网页上的展示。 | 无需作者回答；是否同意把 §8.7 关闭并在 `DATA_LICENSE.md` 引用上述链接（是/否）？ | 关闭该条，改引用目录仓库的 OFL 文件。 |

---

## C. 新闻源

通用说明：站点只存标题、链接、来源名、发布时间，以及 LLM 用自己的话写的中文摘要。「当前处理方式」里不再重复。下表「建议」中的「仅标题+链接」指：保留来源，但站点上不展示 LLM 摘要。

| 编号 | 来源或事项 | 当前处理方式 | 核实结果 | 需作者确认的问题 | 建议 |
|---|---|---|---|---|---|
| C1 | TechCrunch Biotech & Health（§6；§8.10） | RSS，标题+链接+日期+LLM 摘要 | **部分核实**。RSS 条款页（https://techcrunch.com/rss-terms-of-use/ ，200）：只允许展示 feed 提供的内容并署名 TechCrunch，须链接到原文；不得去掉署名或链接，也不得「modify our feed content」；TechCrunch 可随时要求停止使用 feed。LLM 摘要是否算修改 feed 内容，条款没有说。 | 该来源是否只显示标题+链接+来源，不显示 LLM 摘要（是/否）？ | 保留，仅标题+链接。 |
| C2 | STAT（§6；§8.10） | RSS，标题+链接+日期 | **部分核实，条款读起来比 DATA_LICENSE 原先写的更严**。条款页（https://www.statnews.com/terms-conditions/ ，200）一方面把 RSS 列在免费访问的功能里；另一方面禁止使用机器人、爬虫、脚本等任何自动化手段抓取或收集内容，禁止把内容用于训练机器学习或人工智能系统，也禁止提供「archived cached data sets」；内容仅供个人非商业使用。RSS 与这些限制的关系，条款没有讲清。 | 是否停用 STAT（是/否）？ | **已处理：2026-11-15 停用，库内条目已移除** |
| C3 | GEN（Genetic Engineering & Biotechnology News）（§6；§8.10） | RSS，标题+链接+日期 | **部分核实**。条款页（https://www.genengnews.com/legal/ ，200）：材料不得未经书面许可复制、再发布、镜像；许可显示和下载仅限个人、非商业、信息性用途且不得修改。页面文字里没有提到 RSS 或机器人。DATA_LICENSE 原写的「页脚保留文本与数据挖掘权利」本次未再复核。 | 是否保留 GEN，仅显示标题+链接（是/否）？ | 保留，仅标题+链接；或先申请许可。 |
| C4 | Labiotech（§6；§8.11） | RSS，标题+链接+日期 | **未能核实**。版权说明页 https://www.labiotech.eu/imprint/ 与猜测的 https://www.labiotech.eu/terms-and-conditions/ 均返回 HTTP 403。 | 请你在浏览器里打开 Labiotech 页脚的 Imprint 与 Terms，确认是否限制 RSS 或自动读取（是/否有限制）？ | 在核实前保留，仅标题+链接。 |
| C5 | Chan Zuckerberg Initiative（公司 feed）（§6；§8.11） | RSS，标题+链接+日期 | **未能核实**。https://chanzuckerberg.com/privacy/ 与其网站隐私政策页 https://chanzuckerberg.com/privacy/czi-website-privacy-policy/ 均为 200，但我在其中没有找到关于内容使用、RSS 或自动访问的条款；我猜的 https://chanzuckerberg.com/terms-of-use/ 和 https://chanzuckerberg.com/terms/ 均 404。 | 你能否在其网站页脚找到使用条款页并发来链接（能/不能）？ | 保留，仅标题+链接。 |
| C6 | Genentech Stories（公司 feed，经 Feedburner 发布）（§6；§8.11） | RSS，标题+链接+日期 | **已核实，且是不利条款**。网站条款页（https://www.gene.com/terms-conditions ，200）列出禁止行为，其中包括「use any data mining, robots, or similar data gathering or extraction methods in connection with this Website」，并禁止未经许可修改、公开展示、复制或分发网站内容。条款没有提到 RSS；feed 本身托管在 Feedburner。此前猜的 gene.com 条款路径（/terms-of-use、/legal 等）均 404。 | 是否停用 Genentech feed（是/否）？ | **已处理：2026-11-15 停用，库内条目已移除** |
| C7 | XtalPi（公司 feed）（§6；§8.11） | RSS，标题+链接+日期 | **未能核实**。官网首页（https://www.xtalpi.com/en ，200）页脚有「Privacy Policy & Legal Statement」，但 https://www.xtalpi.com/privacy-policy/ 请求没有响应（连接失败），https://www.xtalpi.com/en/privacy-policy/ 返回的仍是首页内容，读不到法律声明正文。 | 你能否在浏览器里打开页脚的 Legal Statement 并确认是否限制自动读取（是/否有限制）？ | 在核实前保留，仅标题+链接。 |
| C8 | BiopharmaTrend（§6；§8.13） | RSS，标题+链接+日期；条目无 pubDate，已设 `require_date: true` 与 `date_from_article: true`：对无日期条目只请求文章页读取发布日期，不存正文 | **部分核实**。条款页（https://wtmbgroup.com/terms-of-use ，200）：不得未经书面许可复制、分发或商业利用内容；公司保留以摘要形式展示投稿内容并附链接的权利；没有提到 RSS 或自动访问。2026-11-15 实测：文章页 `/post/<id>-<slug>/` 301 到同站 `/<category>/<slug>/`，页内带 article:published_time、JSON-LD datePublished、time datetime；robots.txt 只禁 /admin/ 等，不禁文章页。请求文章页取日期是否在条款允许范围内，条款没有讲。 | 是否保留该源并允许只为取日期请求文章页（是/否）？ | 作者判断。保留则只存标题+链接+日期；不放心可改 `enabled: false`（E5 曾建议停用，本次未自行改动）。 |
| C9 | Drug Discovery Trends（§6；§8.13） | RSS，标题+链接 | **已核实，且是不利条款**。没有单独条款页（https://www.drugdiscoverytrends.com/privacy-policy/ 404）；首页（https://www.drugdiscoverytrends.com/ ，200）页脚写明：材料未经 Arrowfly 书面许可，不得复制、分发、传输、缓存或另作使用。运营方 Arrowfly 的隐私政策页（https://www.arrowfly.com/privacy-policy/ ，200）没有 RSS 条款。其 feed（https://www.drugdiscoverytrends.com/feed/ ）本身可访问（200）。 | 是否停用（是/否）？ | **已处理：2026-11-15 停用，库内条目已移除** |
| C10 | MedCity News（§6；§8.13） | RSS，标题+链接 | **已核实，且是不利条款**。服务条款（https://medcitynews.com/medcitizen-terms-of-service/ ，200，Last Modified: August 23, 2024）：未经公司书面同意，不得复制、修改、公开展示、再发布、下载或存储、传输网站上的任何材料（仅列出少数例外，如浏览器缓存和合理打印）；没有提到 RSS 或机器人。 | 是否停用（是/否）？ | **已处理：2026-11-15 停用，库内条目已移除** |
| C11 | FDA Press Releases（§6；§8.13） | RSS（官方「Stay Informed」feed），标题+链接+日期 | **已核实**。FDA 网站政策页（https://www.fda.gov/about-fda/about-website/website-policies ，200）写明：除另有说明外，fda.gov 的文字和图形不受版权保护，属于公共领域，可免许可自由再发布；署名为来源受欢迎但非必需，并更希望人们链接回 FDA 页面。美国政府门户的说明（https://www.usa.gov/government-copyright ，200，由 /government-works 跳转而来）提醒：联邦网站上的文字、图片和标志可能受版权或其他限制，所以仍以「另有说明除外」为界。免责声明页（https://www.fda.gov/about-fda/website-policies/website-disclaimer ，200）没有关于 RSS 的限制。 | 无需回答。是否同意在该条目处注明「来源 FDA，公共领域（另有说明除外）」（是/否）？ | 保留。 |
| C12 | EMBL News（机构 feed）（§6；§8.13） | RSS，标题+链接+日期 | **未能核实**。我猜的 https://www.embl.org/legal-notice/、https://www.embl.org/about/info/legal-notice/、https://www.embl.org/about/info/terms-of-use/、https://www.embl.org/terms-of-use/、https://www.embl.org/legal/ 全部 404，没有读到任何 EMBL 站点条款。 | 你能否在 embl.org 页脚找到法律声明或使用条款页并发来链接（能/不能）？ | 在核实前保留，仅标题+链接。 |
| C13 | Wyss Institute（机构 feed）（§6；§8.13） | RSS，标题+链接+日期 | **部分核实**。使用条款页（https://wyss.harvard.edu/terms-of-use/ ，200）只有一句版权声明：网站所有材料由哈佛学院院长及研究员版权所有（或原作者所有），未经版权所有者许可，不得以任何目的复制；没有提到 RSS 或自动访问。 | 是否保留，仅显示标题+链接（是/否）？ | 保留，仅标题+链接；或停用。 |
| C14 | Biohub（机构 feed）（§6；§8.13） | RSS，标题+链接+日期 | **部分核实**。使用条款（https://biohub.org/terms-of-use/ ，200，Last Updated: May 27, 2026）是针对其「服务」的通用条款；禁止以条款未授权的方式访问服务；我在全文里没有搜到 RSS、爬取、机器人等字样。 | 是否接受保留，仅标题+链接（是/否）？ | 保留，仅标题+链接。 |
| C15 | MIT News（生物工程、人工智能两个 feed）（§6，该行无「待核实」标记，列出供参考） | RSS，标题+链接+日期 | **已核实**。使用条款页（https://news.mit.edu/terms-of-use ，200）：MIT News 提供 RSS feed 用于内容分发（syndication）；转载故事须在顶部给出标题、作者署名并链接到原文、底部注明「Reprinted with permission of MIT News」。本项目只展示标题+链接+来源，不转载故事正文。 | 无需回答。 | 保留。 |
| C16 | Google Research / Google Health / Google DeepMind 博客（§6；§8.13） | RSS，标题+链接+日期 | **部分核实**。Google 服务条款（https://policies.google.com/terms ，200）禁止违反网页上机器可读指令（例如 robots.txt 里禁止抓取、训练等）而用自动化方式访问内容。项目默认遵守 robots.txt（`respect_robots: true`）。三个博客自己的站点条款没找到。 | 无需回答；是否接受「遵守 robots.txt 即可」这一判断（是/否）？ | 保留，保持 robots 检查。 |
| C17 | Microsoft Research Blog（§6；§8.13） | RSS，标题+链接+日期 | **部分核实**。使用条款页（https://www.microsoft.com/en-us/legal/terms-of-use ，200，页面显示 Last Updated: February 7, 2022）：我在正文里没有搜到 RSS、爬取、机器人、自动访问等字样。 | 无需回答。 | 保留。 |
| C18 | Arc Institute（HTML 列表页）（§6；§8.14） | 只读列表页的标题、链接、日期，从不请求文章页 | **未能核实条款**。https://arcinstitute.org/terms 与 https://arcinstitute.org/privacy 均 404；首页（https://arcinstitute.org/ ，200）没有找到条款链接。robots.txt（https://arcinstitute.org/robots.txt ，200）对所有机器人只禁止 `/api/`。 | 你能否在浏览器里确认 Arc 页脚有无使用条款（有/无）？ | 保留（robots 允许），仅标题+链接。 |
| C19 | Wellcome Sanger Institute（HTML 列表页）（§6；§8.14） | 只读列表页的标题、链接、日期 | **已核实许可，衍生问题未决**。条款页（https://www.sanger.ac.uk/terms-of-use/ ，200）：除另有说明外，网站在线内容采用 CC BY-NC-ND 4.0，须署名 Genome Research Limited 并链接到来源网站。NC（非商业）与 ND（禁止演绎）：LLM 中文摘要是否算演绎，条款没有讲。 | (1) 本项目站点是否保持纯非商业（无广告、无付费）（是/否）？(2) Sanger 条目是否不展示 LLM 摘要（是/否）？ | 仅标题+链接+日期，并在条目注明来源与 CC BY-NC-ND 4.0。 |
| C20 | insitro（HTML 列表页）（§6；§8.14） | 只读列表页；日期取自每条的摘要文字，无日期的条目丢弃 | **未能核实条款**。robots.txt（https://www.insitro.com/robots.txt ，200）对所有机器人不设限制（`Disallow:` 为空）。我猜的 https://www.insitro.com/terms 404；隐私政策（https://www.insitro.com/privacy-policy ，200）只有隐私内容。 | 无需回答。 | 保留，仅标题+链接+日期。 |
| C21 | EMBL-EBI（HTML 列表页）（§6，该行无「待核实」标记，列出供参考） | 只读列表页的标题、链接、日期 | **部分核实**。使用条款页（https://www.ebi.ac.uk/about/terms-of-use/ ，200）没有给出新闻内容的再使用许可；DATA_LICENSE 原写「仅禁止干扰服务的过度访问」，本次没有在该页里再次读到对应原文，所以这个说法暂记为「未复核」。 | 无需回答。 | 保留，仅标题+链接+日期。 |
| C22 | GlobeNewswire、FierceBiotech、Endpoints News、Recursion 投资者新闻 feed（未启用）（§6 末段；§8.12） | 未启用 | **未能核实**。四个 feed 地址本次再次请求均返回 HTTP 403：https://www.fiercebiotech.com/rss/xml 、https://www.endpts.com/feed/ 、https://ir.recursion.com/rss/news-releases.xml 、https://www.globenewswire.com/RssFeed/subjectcode/22-Biotechnology/feedTitle/GlobeNewswire 。 | 无需回答。 | 保持未启用；Recursion 等公司新闻改走其官网列表页（若条款允许）。 |
| C23 | Science 与 OpenAI（未启用）（§6 末段；§8.15） | 未启用 | **未能核实**。Science 条款页 https://www.science.org/content/page/terms-service 返回 403；OpenAI 条款页 https://openai.com/policies/terms-of-use/ 返回 403。 | 无需回答。 | 保持未启用。 |
| C24 | Valence Labs（Recursion 旗下，RSS https://www.valencelabs.com/feed/ ；§6；§8.16） | RSS，标题+链接+日期，LLM 摘要待打分后生成 | **部分核实**（2026-11-15）。站点页脚没有条款页，robots.txt 为默认（Disallow 为空）；母公司条款 https://www.recursion.com/terms-of-use 禁止 robot/spider，但写明适用于 www.recursion.com，不是 valencelabs.com。最新一条为 2025-05，近 30 天几乎没有条目。 | 母公司条款是否延伸到 valencelabs.com？ | 条目极少，若不放心可停用（`enabled: false`）。 |
| C25 | Enveda（RSS https://enveda.com/feed/ ；§6；§8.16） | RSS，标题+链接+日期 | **部分核实**。https://enveda.com/terms-of-service/ 只是短信服务条款，没找到网站使用条款；robots.txt 仅禁 /wp-admin/，并明确允许若干 AI 爬虫。 | 是否可接受「没有网站条款、robots 允许」作为依据？ | 保持启用，仅存标题+链接。 |
| C26 | Dyno Therapeutics（HTML 列表页 https://www.dynotx.com/news ；§6；§8.16） | 通用 HTML 解析器，只存同站链接的标题、链接、日期 | **部分核实**。条款 https://www.dynotx.com/legal/terms-and-conditions 明文 "You must not: Republish material ... Reproduce, duplicate or copy material ... Redistribute content"，未提及 robots、抓取或 RSS；robots.txt 允许全部。**已在 sources.yaml 设为 enabled: false（默认停用，与 Cradle 同样保守处理）。**第三方媒体链接已丢弃（`same_host_only`）。 | 只存标题+链接+日期是否算「复制材料」？ | 作者判断；确认允许后再改 enabled: true。 |
| C27 | Noetik（HTML 列表页 https://www.noetik.ai/news ；§6；§8.16） | 通用 HTML 解析器，只存同站链接的标题、链接、日期，标题经 `title_strip_patterns` 清洗 | **部分核实**。条款 https://www.noetik.ai/terms-of-use 原文 "may not ... reproduce, display, ... distribute, or otherwise use the Content in any way for any public or commercial purpose"，即限制公开展示；未提及 robots、抓取或 RSS；robots.txt 为空。**已在 sources.yaml 设为 enabled: false（默认停用）。**第三方新闻稿站点链接已丢弃。 | 同 C26。 | 作者判断；确认允许后再改 enabled: true。 |
| C28 | New York Genome Center（RSS https://www.nygenome.org/feed/ ；§6；§8.16） | RSS，标题+链接+日期 | **部分核实**（2026-11-15）。HTTP 200，10 条，最新 2026-11-12；robots.txt 仅禁 /wp-content/plugins/、/wp-admin/、/readme.html，未点名 AI 爬虫；条款页 https://www.nygenome.org/terms-conditions/（200）正文主要是基因组测序服务条款，网站部分只有修改与终止声明，未提 RSS、抓取或自动访问。最近 10 条里约 2–3 条对口（脑细胞类型图谱、基因调控图谱），其余为人事与奖项，相关性中等偏弱，由打分层把关。 | 是否接受「条款未禁止、robots 允许」作为依据（是/否）？ | 保留，仅标题+链接+日期；展示后若噪声多可停用。news-v6.1 重打后：NYGC 在核心档 2 条（含 RNA fingerprinting 扰动方法，7 分）、相关档 2 条（脑图谱，5 分），不再占据核心档多数；它的内容多为基础图谱，若作者希望新闻页更聚焦，可停用本源或只保留进相关档。 |
| C29 | Whitehead Institute（RSS https://wi.mit.edu/rss.xml ；§6；§8.16） | RSS，标题+链接+日期 | **未能核实条款**（2026-11-15）。HTTP 200，10 条，最新 2026-11-02（另有 1 条 2016 年旧稿，被 90 天窗口滤掉）；robots.txt 为 Drupal 默认，未点名 AI 爬虫；我试的 /terms、/terms-of-use、/legal、/privacy、/copyright 均 404，首页无条款链接。最近 10 条里约 2 条对口（全身单细胞图谱、胚胎发育），其余为奖项与研讨会。 | 你能否在浏览器里确认 wi.mit.edu 页脚有无使用条款（有/无）？ | 在核实前保留，仅标题+链接+日期。 |
| C30 | S5 补充核对后未启用的候选（§6 末段；§8.16） | 未启用 | **已核实，不启用**（2026-11-15，项目 UA）。GenomeWeb（https://www.genomeweb.com/section/rss/news ，200，但 robots 对 ClaudeBot、GPTBot、CCBot 等全站 Disallow 且 Crawl-delay: 10）；Google News RSS（robots 对 * 为 Disallow: /）；Parse Biosciences（https://www.parsebiosciences.com/feed/ ，200，产品营销博客，条款 https://www.parsebiosciences.com/legal/terms-of-service/ 禁止 copy 与 mirror，robots 声明 Crawl-delay: 10；本次对该主机前几次请求按 1 秒间隔发出，未满足该声明）；Gladstone（https://gladstone.org/feed/ ，200，内容与虚拟细胞关联弱）；Human Cell Atlas、Turbine、Novo Nordisk Foundation（feed 200 但 0 条）；Wellcome Leap（200，无关内容）；Synthesize Bio、Ginkgo、CytoReason、Deepcell、EurekAlert、BioSpace、Tahoe、Xaira（无可用 feed 或页面 404）。Helmholtz Munich、JAX、Business Wire 的 robots.txt 本次仍读不到（本机证书校验失败或超时），未判定。 | 无需回答。 | 保持未启用。 |
| C31 | 量子位（RSS https://www.qbitai.com/feed ；§6） | RSS，标题+链接+日期；严格预筛（只有命中严格词的条目才入库） | **未能核实条款**（2026-11-15，项目 UA）。HTTP 200，10 条，最新 2026-11-14，约覆盖 1.1 天；robots.txt 仅禁 /wp-admin/，未点名 AI 爬虫；「关于」页（https://www.qbitai.com/?page_id=99 ）没有条款，页脚只有版权所有。近 10 条里对口约 0-2 条。 | 条款找不到的中文媒体 RSS 是否保留（是/否）？ | 保留，只存标题+链接+日期；如你更稳妥，可停用。 |
| C32 | IT之家（RSS https://www.ithome.com/rss/ ；§6） | RSS，标题+链接+日期；严格预筛 | **部分核实**。HTTP 200，60 条，但只覆盖约 5.75 小时（2026-11-14 23:35 至 11-15 05:20）；robots.txt 不禁 /rss/，未点名 AI 爬虫；https://www.ithome.com/about.htm 只有简介与版权所有，没有抓取或转载条款；首页提供「RSS订阅」入口，即官方提供 RSS。近 20 条对口 0。 | (1) 条款待核实的中文源是否保留（是/否）？(2) feed 只覆盖几小时，每天一次的定时运行只能看到当天一部分稿件，是否仍保留（是/否）？ | 价值有限，建议在你确认前可停用；若保留，只存标题+链接+日期。 |
| C33 | 雷峰网（RSS https://www.leiphone.com/feed ；§6） | RSS，标题+链接+日期；严格预筛 | **未能核实条款**。HTTP 200（响应慢），20 条，最新 2026-11-13，约覆盖 1.1 天；robots.txt 不禁 /feed，未点名 AI 爬虫；https://www.leiphone.com/us/index 无条款，页脚版权所有，有 RSS 订阅入口。近 20 条对口近 0。 | 同 C31（条款待核实的中文源是否保留）。 | 保留，只存标题+链接+日期。 |
| C34 | 36氪（RSS https://www.36kr.com/feed ；§6） | RSS，标题+链接+日期；严格预筛 | **未能核实条款**。HTTP 200（响应慢），30 条，最新 2026-11-15，文章与快讯（/newsflashes/）混排；robots.txt 禁 /detail/、/api/ 等，不禁 /feed 与 /p/；条款页没找到（首页为前端壳，/about-us 404）；早先用通用 UA 见过反爬挑战页，项目 UA 本次拿到了 RSS XML。近 30 条约 1 条对口。 | (1) 条款读不到且曾遇反爬的源是否保留（是/否）？ | 建议停用；若保留，只存标题+链接+日期。 |
| C35 | Biohub News（HTML 列表页 https://biohub.org/news/ ；§6） | 只读列表页的标题、链接、日期（新闻稿，路径 /news/；/blog/ 已由 Biohub RSS 覆盖），日期取锚点之前最近一个（`date_scope: before_nearest`）；该源使用 `prefilter: none`（不预筛、直接交 LLM 打分），因为列表页没有摘要、标题常不含领域关键词，而其产量低、对口度高 | **未发现禁止条文，仍标待核实**（2026-11-15）。robots.txt 对所有机器人 `Disallow` 为空；使用条款（https://biohub.org/terms-of-use/ ）与可接受使用政策（https://biohub.org/acceptable-use-policy/ ）均 200，按 crawl、scrap、robot、spider、automat、data mining 等词检索没有禁止自动访问或转载的明文；条款里 reproduce 一句是对用户内容的授权，不是禁令。 | 条款没有明文谈自动访问，是否按此保留（是/否）？ | 保留；这是本轮漏掉的 18 亿美元虚拟生物学计划新闻稿所在栏目。 |
| C36 | Latent Space（RSS https://latent.space/feed ；播客 feed https://podcast.latent.space/feed.xml ；§6）。**作者已确认启用（2026-11-15）**：`latent.space/feed` 为 `enabled: true`，播客 feed 保持停用以免重复 | 无（已启用） | **条款含糊**（2026-11-15）。站点没有条款页（/terms、/tos 均 404）；页脚 Terms 链接指向 https://www.beehiiv.com/tou ，该条款禁止「data mining, robots, crawling, scraping or data gathering or extraction methods」，其「Services」指 beehiiv 提供的服务，是否覆盖 latent.space 的公开 RSS 原文没有明说。robots.txt 为 `Allow: /`，仅禁 /api/，未点名 AI 爬虫。本项目只请求 latent.space（每次运行 1 次），不请求 beehiiv 域名，只存标题+链接+日期。主 feed 20 条约覆盖 12 天，其中 2026-11-06 的 Fabian Theis 访谈对口，但标题与一句话简介里只有 biology 一类词，LLM 只看这些，分数可能低于 5；播客 feed（297 条）60 天内 31 条。 | 已决定：作者知情 beehiiv 条款适用范围不明后，确认只读 latent.space 公开 RSS 并启用（若日后收到异议则停用）。 | 已启用 `latent.space/feed`；播客 feed 保持停用以免重复。 |
| C37 | 钛媒体（RSS）、生物谷（HTML 列表页）。**已配置，默认停用**（§6） | 无（`enabled: false`） | **条款读不到 / 有明文限制**。钛媒体：feed HTTP 200，17 条，约覆盖 8.5 小时，17 条 0 条对口，首页两次超时没读到条款；robots.txt 对 ClaudeBot 等只禁 *.js、*.css。生物谷：页脚写「版权所有 不得转载」，著作权声明页（https://www.bioon.com/info/protocol.shtml ）无实质文本；robots.txt 禁 /news/* 与 /article/0*.html；近 15 条对口约 0。 | 是否同意两者保持停用（是/否）？ | 保持停用（先例：Drug Discovery Trends）。 |
| C38 | 本轮中文源、播客、newsletter 核对后未启用的候选（§6 末段） | 未启用 | **已核实，不启用**（2026-11-15，项目 UA）。财联社（https://www.cls.cn/our?nav=copyright 声明未经书面授权不得转载、复制、使用，且无 RSS）；动脉网 vbdata.cn（无 RSS，列表由客户端加载）；机器之心（/rss 落到数据服务页）；DeepTech 深科技（已跳转到 mittrchina.com，壳页，该域名本轮未请求）；新浪科技（RSS 停在 2018-09）；凤凰网科技（无 RSS）；微信公众号（无公开接口、违反平台规则，不接）；The Bio Report（近 10 条对口 0，robots 声明 ai-train=no）；Timmerman Report / The Long Run（条款明禁 robot/spider/scraper，feed 500）；a16z Bio+Health（未公布 RSS）；BioWorld Insider（403，robots 禁 ClaudeBot）；Where Tech Meets Bio（未找到）；Substack 类 newsletter（Substack 条款禁止 crawl/scrape，沿用既有结论）。 | 无需回答；若你想接入其中某家，请告知。 | 不启用。 |

---

## D. 知识资源

「当前处理方式」对所有 D 行相同：资源库只存名称、链接和本项目自写的中文描述，不复制第三方内容，也不抓取这些站点；站点上给出的许可标识（若有）来自 GitHub REST API 并原样转述。「仅链接」的资源，建议卡片不写具体许可，写「许可以原站为准」。

| 编号 | 来源或事项 | 当前处理方式 | 核实结果 | 需作者确认的问题 | 建议 |
|---|---|---|---|---|---|
| D1 | GitHub 服务条款与 API 条款对「抓取仓库指标」的限制（§7；§8.16 后半） | `aivc resources refresh` 用 GitHub REST API，匿名（每小时 60 次）或用可选 `GITHUB_TOKEN`，请求间隔不少于 1 秒；只存 stars、最近推送时间、是否归档、许可标识、主语言 | **已核实**。服务条款 H 节（https://docs.github.com/en/site-policy/github-terms/github-terms-of-service ，200）：滥用或过于频繁的 API 请求可能导致 API 访问被暂停；不得共享令牌以突破速率限制；不得为垃圾信息目的下载数据。可接受使用政策（https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies ，200）第 7 节：研究者可把公开的非个人信息用于研究（成果须开放获取），归档者可用于存档；通过 API 采集不算「scraping」；不得用于垃圾邮件等。速率限制页（https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api ，200）：未认证请求每小时 60 次。本项目只存上述少量非个人字段，符合这些条款的字面要求。 | 是否确认定时任务里不使用个人令牌共享、也不存任何用户个人信息（是/否）？ | 关闭 §8.16 的 GitHub 部分。 |
| D2 | CZ CELLxGENE Discover（`cellxgene-discover`，https://cellxgene.cziscience.com/ ） | 仅链接 | **未能核实**。官网首页（200）页脚有 Terms，隐私页（https://cellxgene.cziscience.com/privacy/ ，200）说使用须同时遵守其使用条款；但使用条款页本身没读到，我猜的两个文档路径均 404，数据集许可按集合分别标注，也没读到。 | 无需回答。 | 保留，仅链接，不写许可。 |
| D3 | scPerturb（`scperturb`，https://projects.sanderlab.org/scperturb/ ） | 仅链接 | **已核实（针对数据记录）**。Zenodo 记录（https://zenodo.org/api/records/7041849 ，200）标题为「scPerturb Single-Cell Perturbation Data: RNA and protein h5ad files」，许可 id 为 cc-by-4.0。门户页（200）本身没有许可文字。记录里的各子数据集是否另有来源许可，未核实。 | 卡片是否写「Zenodo 记录标注 CC BY 4.0」（是/否）？ | 保留，可注明 Zenodo 记录的 CC BY 4.0，并写「各子数据集以原来源为准」。 |
| D4 | CLUE / Connectivity Map（`clue-cmap`，https://clue.io/ ） | 仅链接 | **已核实（限制性条款）**。数据许可说明页（https://clue.io/connectopedia/data_license ，200）：访问密钥、代码和数据文件仅限单用户，不得再分发，限本研究组内使用；若有显著不同的衍生作品想分发，需联系 Broad。注册页（https://clue.io/terms ，200）提示：学术和非营利机构免费使用，商业使用需联系。 | 无需回答。 | 保留，仅链接，描述里写明「需注册，学术非营利免费」。 |
| D5 | Human Cell Atlas Data Portal（`hca-data-portal`，https://data.humancellatlas.org/ ） | 仅链接 | **部分核实**。数据使用协议页（https://data.humancellatlas.org/about/data-use-agreement ，200）写明：该发布中的每个数据集采用 CC BY 4.0；未来其他 HCA 数据的许可可能不同。 | 无需回答。 | 保留，可注明「该次发布的数据集为 CC BY 4.0，以数据使用协议为准」。 |
| D6 | Arc Virtual Cell Atlas（`arc-virtual-cell-atlas`，https://github.com/ArcInstitute/arc-virtual-cell-atlas ） | 仅链接 | **未能核实**。GitHub 许可接口（https://api.github.com/repos/ArcInstitute/arc-virtual-cell-atlas/license ，404）表示仓库没有识别出许可文件（仓库本身 200 存在）；README（raw 文件）中没有搜到许可或使用条款字样。 | 无需回答。 | 保留，仅链接，不写许可。 |
| D7 | Tabula Sapiens（`tabula-sapiens`，https://tabula-sapiens.sf.czbiohub.org/ ） | 仅链接 | **未能核实**。门户页（200）源码里没有许可文字（页面内容很少）。 | 无需回答。 | 保留，仅链接，不写许可。 |
| D8 | Broad Single Cell Portal（`broad-single-cell-portal`，https://singlecell.broadinstitute.org/single_cell ） | 仅链接 | **部分核实**。服务条款（https://singlecell.broadinstitute.org/single_cell/terms_of_service ，200）是针对门户的通用行为条款（例如禁止建立未经授权的接口、禁止上传或使用侵犯他人知识产权的内容），没有给出各研究数据的统一许可；各研究许可需看研究页。 | 无需回答。 | 保留，仅链接。 |
| D9 | DepMap Portal（`depmap-portal`，https://depmap.org/portal/ ） | 仅链接 | **未能核实**。门户页（200）是人机验证页，写着需要批量数据请走下载区、不要抓取门户；条款页（https://depmap.org/portal/terms-and-conditions/ ）同样返回这个验证页，读不到条款。项目从不请求该门户，符合其要求。 | 无需回答。 | 保留，仅链接；不要把该站加入任何自动抓取。 |
| D10 | JUMP Cell Painting 数据集（`jump-cell-painting`，https://github.com/jump-cellpainting/datasets ） | 仅链接 | **已核实**。GitHub 许可接口（https://api.github.com/repos/jump-cellpainting/datasets/license ，200）：仓库为 BSD-3-Clause。数据存放的 Cell Painting Gallery 登记页（https://registry.opendata.aws/cellpainting-gallery/ ，200）写明许可为 CC0 1.0 Universal，同时请引用对应论文。 | 无需回答。 | 保留，可写「仓库 BSD-3-Clause；Cell Painting Gallery 数据 CC0 1.0」。 |
| D11 | RxRx3（`rxrx3`，https://www.rxrx.ai/rxrx3 ） | 仅链接 | **部分核实**。页面（200）的「Citation and License」写明该工作按 Recursion 的非商业最终用户许可协议（Non-Commercial End User License Agreement）授权；数据集对比表里列出了 CC-BY 与 CC-BY-NC-SA 等标识，但各列对应哪个数据集，从文字抽取里无法确定。 | 无需回答。 | 保留，仅链接，描述写明「非商业许可，以原页为准」。 |
| D12 | Open Problems – Single-Cell Perturbations（Kaggle）（`kaggle-open-problems-perturbations`，https://www.kaggle.com/competitions/open-problems-single-cell-perturbations ） | 仅链接 | **未能核实**。竞赛页是前端渲染，本次只读到标题，规则页（/rules）同样读不到正文；Kaggle 参赛数据需登录并接受规则。 | 无需回答。 | 保留，仅链接，不写许可。 |
| D13 | PerturBench（`perturbench`，https://github.com/altoslabs/perturbench ） | 仅链接 | **已核实（代码许可）**。GitHub 许可接口（https://api.github.com/repos/altoslabs/perturbench/license ，200）把许可归为「Other」（NOASSERTION），文件 LICENSE.txt 的正文为 BSD 3-Clause（Altos Labs，2024）。这是代码许可，不涉及数据。 | 无需回答。 | 保留；卡片上的许可标识若显示 NOASSERTION，属于 GitHub 的识别结果，可在描述里补一句「LICENSE 文件为 BSD-3-Clause」。 |
| D14 | PertEval-scFM（`perteval-scfm`，https://github.com/aaronwtr/PertEval ） | 仅链接 | **已核实（代码许可）**。GitHub 许可接口（https://api.github.com/repos/aaronwtr/PertEval/license ，200）：MIT。 | 无需回答。 | 保留。 |
| D15 | scPerturBench（`scperturbench`，https://github.com/bm2-lab/scPerturBench ） | 仅链接 | **已核实（代码许可）**。GitHub 许可接口（https://api.github.com/repos/bm2-lab/scPerturBench/license ，200）：GPL-3.0。这是代码许可，只链接不影响本项目的 MIT 许可。 | 无需回答。 | 保留。 |

---

## E. 其他需作者决定的事项

| 编号 | 事项 | 现状与背景 | 需作者确认的问题 | 建议 |
|---|---|---|---|---|
| E1 | 公开论文库中两条在审的 OpenReview 双盲投稿记录 | 已处理：2026-11-15 从公开数据中移除（论文月度数据与打分缓存），不在网站展示 | 无需确认 | 已移除 |
| E2 | `daily.yml` 的定时触发 | 默认关闭（`schedule` 为注释状态），只能手动触发。启用后每天会跑抓取与 LLM 打分，**产生 LLM API 费用**，且需要在仓库里配置 `ANTHROPIC_API_KEY` secret。 | 是否启用定时更新（是/否）？启用的话，每天预算上限是多少？ | 先手动跑几次、看清费用，再决定是否启用。 |
| E3 | 新接入的新闻源在打分之前不会展示 | 站点只展示「有日期、已打分、相关度不低于 6」的新闻，且每家公司每周最多 3 条、30 天最多 4 条。新源条目以未打分形式入库，前端默认隐藏。 | 是否由你运行一次 `uv run python -m aivc news --days 30` 完成打分（是/否）？ | 发布前由你跑一次，再看展示条数和公司分布是否满意。 |
| E4 | 中文媒体源接入现状 | 已接入（RSS，严格预筛）：量子位、IT之家、雷峰网、36氪，条款均未找到明文，标待核实（C31–C34）。已配置但停用：钛媒体、生物谷（C37）。未接入：财联社（条款明禁转载、复制、使用，无 RSS）、动脉网（无 RSS，列表由客户端加载）、机器之心、DeepTech 深科技、新浪科技、凤凰网科技（无可用 feed）、微信公众号（无公开接口、违反平台规则）（C38）。中文 RSS 只保留几小时到一两天的条目，60 天回填基本无效，主要靠定时运行积累。ProtCell 与摩尔线程那条报道的原稿没有查到，回填大概率抓不到。 | (1) 条款待核实的四家中文源是否保留（是/否）？36氪条款页找不到且曾遇反爬，是否停用？(2) IT之家 feed 只覆盖约 5.75 小时，每天一次的定时运行只能覆盖当天一部分稿件，是否保留（是/否）？ | 保留量子位和雷峰网；IT之家与 36氪价值有限，建议你确认前可停用。 |
| E5 | 本表建议停用的新闻源汇总 | C2 STAT、C6 Genentech、C9 Drug Discovery Trends、C10 MedCity News 的条款有明文限制（自动访问、数据挖掘、缓存或存储）；C8 BiopharmaTrend 当时没有任何展示产出。 | 这五个源是否全部停用（是/否），或指定哪几个保留？ | **部分已处理**：STAT、Genentech、Drug Discovery Trends、MedCity News 已于 2026-11-15 停用，库内条目已移除；**BiopharmaTrend 目前仍为 `enabled: true`（sources.yaml），并新增 `date_from_article: true` 从文章页取发布日期，尚未停用，是否保留留给作者判断（见 C8）。** |
| E6 | 条款核实的时效 | `DATA_LICENSE.md` 开头写的核实日期是 2026-11-14，新闻部分写 2026-11-15；本表核实日期为 2026-11-15。 | 回写结论后，是否统一把核实日期更新为发布当天（是/否）？ | 回写时统一更新。 |
| E7 | 下架通道 | 当前只有仓库 issue 一个入口（DATA_LICENSE §5），没有邮箱。 | 是否接受只用 GitHub issue 作为下架联系渠道（是/否）？ | 接受；如需更稳妥，可日后在仓库 Settings 里开启 Security 联系渠道或专用说明页。 |

---

## 附录：本次未能打开或读不到正文的页面

访问日期均为 2026-11-15，使用通用客户端标识；未尝试绕过任何拦截。

| 页面 | 结果 | 影响的行 |
|---|---|---|
| https://www.biorxiv.org/tdm | HTTP 429（试了三次） | A1 |
| https://iclr.cc/Conferences/2026/Policies（我猜的路径） | 404 | A4 |
| https://info.arxiv.org/help/policies/thank_you.html（我猜的路径） | 404 | A7 |
| https://api2.openreview.net/notes（匿名一次只读请求） | 403，ChallengeRequiredError | A5 |
| https://www.labiotech.eu/imprint/ 、https://www.labiotech.eu/terms-and-conditions/ | 403 | C4 |
| https://chanzuckerberg.com/terms-of-use/ 、https://chanzuckerberg.com/terms/（我猜的路径） | 404 | C5 |
| https://www.xtalpi.com/privacy-policy/ | 无响应；/en/privacy-policy/ 返回首页内容 | C7 |
| https://www.drugdiscoverytrends.com/privacy-policy/ | 404 | C9 |
| https://www.embl.org/legal-notice/ 等 5 个猜测路径 | 404 | C12 |
| https://arcinstitute.org/terms 、https://arcinstitute.org/privacy | 404 | C18 |
| https://www.insitro.com/terms | 404 | C20 |
| https://www.science.org/content/page/terms-service 、https://openai.com/policies/terms-of-use/ | 403 | C23 |
| FierceBiotech、Endpoints、Recursion IR、GlobeNewswire 的 feed 地址 | 403 | C22 |
| https://cellxgene.cziscience.com/docs/ 下两个猜测路径 | 404 | D2 |
| DepMap 门户与条款页、Kaggle 竞赛页、Tabula Sapiens 门户、fonts.google.com 字体页 | 200，但内容为验证页或前端渲染，读不到条款正文 | D9、D12、D7、B2 |
| https://www.tmtpost.com/ （钛媒体首页） | 两次请求均超时 | C37 |
| https://www.36kr.com/about-us（猜测路径） | 404 | C34 |
| https://latent.space/terms 、https://latent.space/tos | 404 | C36 |
| https://www.podbean.com/terms-of-service | 404 | C38 |
| https://www.bioworld.com/media/podcasts | 403 | C38 |
| https://a16z.com/podcast/feed/ 、https://a16z.com/feed/（猜测路径） | 404 | C38 |
| https://www.wheretechmeetsbio.com/ 、thebiotechpodcast.com、thelongrun.libsyn.com（猜测地址） | 连接失败 | C38 |
