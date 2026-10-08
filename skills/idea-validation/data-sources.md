# 数据源与花费规则

单价核对于 2026-10-08，会变：报价前用 `python3 scripts/ar.py describe <接口>` 读最新单价和必填字段。`location_code` 是 DataForSEO 的地区代码（美国 2840、英国 2826、加拿大 2124、澳大利亚 2036、新加坡 2702）。

## 搜索需求（第 1、2 步）

| 用途 | 接口 | 单价 | 规则 |
|---|---|---|---|
| 关键词月量、单次点击价、竞争、12 个月走势 | `search.google-ads.keyword-volume.v1` | $0.225 / 批（≤1000 词） | 一个国家一批。月量是过去 12 个月平均、四舍五入、已合并近似变体；单个词最长 80 个字符、10 个词；`monthly_searches` 最新月份在前、不含当月，画走势前按时间正序重排 |
| 从种子词拓展 | `search.google-ads.related-keyword-ideas.v1` | $0.225 / 批 | 结果量大，按意图正则筛 |
| 从竞品网址拓展 | `search.google-ads.site-keyword-ideas.v1` | $0.225 / 批 | 第 4 步找到竞品后补词用 |
| 真实长句 | `search.google.autocomplete.v1` | $0.005 / 次 | 必填 `client`（用 `gws-wiz-serp`）、`keyword`、`language_code`、`location_code`。种子写成问句开头。只作措辞证据，不作频次证据；每个国家分别取；超过 10 个词或 80 个字符的长句查不到量，只用于文案 |
| 广告流量预测 | `ads.google.keyword-traffic-forecast.v1` | $0.225 / 次 | 给定出价下的预计点击、花费，可用来估 `click_share` 和 `cpc` 的区间 |
| 搜索趋势走势 | `search.search-trends.interest-history.v1` | $0.003 / 批（≤5 词） | 以时段内峰值为 100，多词同查共用一把尺子；小词看多周平滑，不看单点 |
| 年龄、性别 | `search.search-trends.demographic-interest.v1` | $0.006 / 批 | 来自 DataForSEO Trends 的建模结果，**不是谷歌官方数据**；值以该词最高的组为 100，只能比较同一个词在不同组之间的高低，不能跨词比较；只覆盖 18–64 岁；0 表示数据不足 |
| 搜索结果页、相关问题、相关搜索 | `search.google.result-page.v1` | $0.005 / 次 | 单个时刻的快照，默认电脑端；目标人群以手机为主时另抓手机端 |

## 社区原话（第 3 步）

| 用途 | 接口 | 单价 | 规则 |
|---|---|---|---|
| Reddit 搜帖 | `reddit-dynamic-search.search.v1` | $0.0025 / 次 | `query`、`search_type: post`、`sort`（RELEVANCE / TOP / NEW）、`time_range` |
| Reddit 评论区 | `reddit-post-comments.read.v1` | $0.0025 / 次 | `post_id`（形如 `t3_…`） |
| Reddit 帖子详情批量 | `reddit-post-details-batch.read.v1` | $0.0125 / 批（≤5） | 要看正文全文时用 |
| X 搜索 | `twitter-search-timeline.search.v1` | $0.0025 / 次 | `keyword`、`search_type`（Top / Latest） |
| X 评论区 | `twitter-post-comments.read.v1` | $0.0025 / 次 | `tweet_id`；找竞品官方账号和大号的产品推文，读回复里的抱怨和求推荐 |

本机网络不稳时把并发降到 2–3，失败的单条不影响整批。

## 竞品（第 4 步，Similarweb）

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

## 一轮典型花费

| 步 | 典型用量 | 约花费 |
|---|---|---|
| 1 拓词 | 2 批相关词 + 20 次补全 | $0.55 |
| 2 核量 | 每国 1–2 批 | $0.25–0.50 / 国 |
| 3 原话 | 4 簇 × (4 次搜索 + 10 个评论区) × 2 平台 | $0.30 |
| 4 竞品 | 5 次结果页 + 3 站 × (快照 + 渠道 + 带量词) | $8–9 |

不用 Similarweb 时一轮通常在 $2 以内。
