# 数据源与花费规则

单价核对于 2026-10-08，会变：报价前用 `python3 scripts/ar.py describe <接口>` 读最新单价和必填字段。`location_code` 是 DataForSEO 的地区代码（美国 2840、英国 2826、加拿大 2124、澳大利亚 2036、新加坡 2702）。

## 搜索需求（第 1、2 步）

| 用途 | 接口 | 单价 | 规则 |
|---|---|---|---|
| 关键词月量、单次点击价、竞争、12 个月走势 | `search.google-ads.keyword-volume.v1` | $0.225 / 批（≤1000 词） | 一个国家一批。月量是过去 12 个月平均、四舍五入、已合并近似变体；单个词最长 80 个字符、10 个词；`monthly_searches` 最新月份在前、不含当月，画走势前按时间正序重排 |
| 从种子词拓展 | `search.google-ads.related-keyword-ideas.v1` | $0.225 / 批 | 只对已有成熟品类词的想法用。新品类常常只返回种子词本身（实测两次白花 $0.45），先买一批看有没有新词，没有就停，改用自动补全和社区原话 |
| 从竞品网址拓展 | `search.google-ads.site-keyword-ideas.v1` | $0.225 / 批 | 第 4 步找到竞品后补词用 |
| 真实长句 | `search.google.autocomplete.v1` | $0.005 / 次 | 必填 `client`（用 `gws-wiz-serp`）、`keyword`、`language_code`、`location_code`。种子写成问句开头。只作措辞证据，不作频次证据；每个国家分别取；超过 10 个词或 80 个字符的长句查不到量，只用于文案 |
| 广告流量预测 | `ads.google.keyword-traffic-forecast.v1` | $0.225 / 次 | 给定出价下的预计点击、花费，可用来估 `click_share` 和 `cpc` 的区间 |
| 搜索趋势走势 | `search.search-trends.interest-history.v1` | $0.003 / 批（≤5 词） | 以时段内峰值为 100，多词同查共用一把尺子；小词看多周平滑，不看单点 |
| 年龄、性别 | `search.search-trends.demographic-interest.v1` | $0.006 / 批 | 来自 DataForSEO Trends 的建模结果，**不是谷歌官方数据**；值以该词最高的组为 100，只能比较同一个词在不同组之间的高低，不能跨词比较；只覆盖 18–64 岁；0 表示数据不足 |
| 搜索结果页、相关问题、相关搜索 | `search.google.result-page.v1` | $0.005 / 次 | 单个时刻的快照，默认电脑端；目标人群以手机为主时另抓手机端 |

## 社区原话（第 3 步）

平台选择、查询模板、加权规则见 `references/community-playbook.md`。下面只列接口和单价。

| 平台 | 接口 | 单价 |
|---|---|---|
| Reddit | `reddit-dynamic-search.search.v1`（搜索）、`reddit-subreddit-feed.read.v1`（按版块浏览）、`reddit-post-comments.read.v1`、`reddit-comment-replies.read.v1`（二级评论）、`reddit-post-details-batch.read.v1`（≤5 帖正文） | $0.0025 / 次；批量详情 $0.0125 |
| X | `twitter-search-timeline.search.v1`、`twitter-post-comments.read.v1`、`twitter-latest-post-comments.read.v1`、`twitter-tweet-detail.read.v1` | $0.0025 / 次 |
| YouTube | `youtube-videos.search.v1`、`youtube-video-comments.read.v1`、`youtube-video-comment-replies.read.v1` | $0.0025 / 次 |
| 中文平台 | 抖音、小红书、知乎、微博、微信、B站、快手的搜索、评论、联想词，以及巨量算数关键词热度；接口清单见 `references/community-playbook.md`「中文市场」 | 评论、知乎、微博、B站多为 $0.0025 / 次；抖音搜索与联想词、小红书全部接口、微信为 $0.025 / 次（贵 10 倍，先算好次数） |
| Hacker News | 免费：`https://hn.algolia.com/api/v1/search?query=<词>&tags=comment`（或 `story`、`ask_hn`、`show_hn`），`numericFilters=created_at_i>时间戳`；评论树 `https://hn.algolia.com/api/v1/items/<id>` | 免费 |
| GitHub | 免费：`https://api.github.com/search/issues?q=repo:<仓库>+is:issue+<词>&sort=reactions-%2B1&order=desc`，可加 `reason:"not planned"` | 免费，未登录每分钟约 10 次搜索 |

本机网络不稳时把并发降到 2–3，失败的单条不影响整批。

## 付费证据（第 6 步）

见 `references/wtp.md`。主要是免费的公开网页：TrustMRR、Indie Hackers 收入页、G2 等评价站、外包平台；网页存档历史快照用免费接口 `https://web.archive.org/cdx/search/cdx?url=<域名>/pricing&output=json&fl=timestamp,statuscode&collapse=timestamp:6`。

## 竞品（第 5 步，Similarweb）

**调用前必须向用户报接口、竞品数和总价，等确认。** 数值是面板加建模的估计。

| 用途 | 接口 | 单价 |
|---|---|---|
| 最新流量快照 | `web.site-traffic-snapshot.read.v1` | $0.94 / 站 |
| 月度流量走势 | `web.site-traffic-trend.read.v1` | $1.41 / 站 |
| 流量渠道构成（看付费搜索占比） | `web.site-traffic-channels.read.v1` | $1.64 / 站 |
| 带量搜索关键词 | `web.site-search-keywords.read.v1` | $0.23 / 次 |
| 搜索竞品 | `web.site-search-competitors.read.v1` | $0.23 / 次 |
| 某个关键词各网站的点击份额 | `search.keyword-click-share.read.v1` | $0.23 / 次 |
| 相似网站 | `web.site-similar-sites.read.v1` | $1.17 / 站 |
| 主要国家 | `web.site-top-countries.read.v1` | $7.03 / 站（贵，按需） |

参数一般是 `domain`、`country`（`ww` 为全球）、`month`（如 `2026-08`）、`limit`。

## 已知参数限制（2026-10 实测）

`ar.py describe` 会显示目录里写明的可选值，下面是实测踩过的：

- Reddit 搜索：`sort` 只能是 `RELEVANCE`，`time_range` 只能是 `all`，`search_type` 只能是 `post`，`need_format` 填 `false`；评论区 `sort_type` 用 `CONFIDENCE`。要看最新或最热，改用按版块浏览。
- Similarweb 带量关键词、搜索竞品：`limit` 最大 10，`country` 只能是 `us` 或 `ww`。
- 自动补全：必填 `client`（`gws-wiz-serp`）。
- 知乎回答搜索：默认参数常返回空，按目录示例填 `search_source=Normal`、`sort=upvoted_count`、`time_interval`、`limit`、`offset` 等全部字段。
- 巨量算数关键词热度（`douyin-multi-keyword-hot-trend.read.v1`）：实测出现过整批返回全零（连「智能体」都是 0）且照常扣费。先用一个确定有热度的大词单独试一次，返回 0 就判为当天不可用，不要整批买。
- 微信搜一搜、公众号接口：实测多次失败，结果不稳定时跳过，在局限里写明。
- 相关词拓展：对新品类可能只返回种子词本身；多义词会带出无关结果（如 `claude md` 带出名叫 Claude 的医生），按意图正则过滤，见第 1 步撞词检查。
- 「全球英语市场」没有单一地区代码：用美国一批，加一批不填地区、只填 `language_code=en` 的全球批；小国的小词常落在最小分档（10），不要把多国相加。

## 耗时

每次付费调用通常要 20–40 秒（报价、下单、等结果、取结果）。一轮完整验证常有 150–250 次调用，按并发 2–3 算约 1.5–2.5 小时。时间紧时优先保证第 0 步和交易类证据，社区取数按饱和度停止，不要为了凑数量多买。

## 一轮典型花费

| 步 | 典型用量 | 约花费 |
|---|---|---|
| 1 拓词 | 2 批相关词 + 20 次补全 | $0.55 |
| 2 核量 | 每国 1–2 批 | $0.25–0.50 / 国 |
| 3 原话 | 4 簇 × 4 类查询 × 2 说法 × 3 个付费平台，加评论区与二级评论约 150 次 | $0.5–1（加小红书另计） |
| 4 竞品 | 5 次结果页 + 3 站 × (快照 + 渠道 + 带量词) | $8–9 |

不用 Similarweb 时一轮通常在 $2 以内。
