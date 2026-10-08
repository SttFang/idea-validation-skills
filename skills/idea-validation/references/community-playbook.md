# 社区取数手册（第 3 步）

目标不是「搜到几条帖子」，而是从**不同类型的平台**拿到足够多**不同作者**的原话，覆盖痛点、换用、付费和反对四类声音。接口单价见 `../data-sources.md`，调用前先 `python3 scripts/ar.py describe <接口>` 看必填字段和可选值。

## 选平台：至少三类，且至少一类是交易类

| 类型 | 平台 | 擅长回答 | 默认用法 |
|---|---|---|---|
| 讨论类 | Reddit、Hacker News、X、知乎 | 痛点、情境、换用经历、反对意见 | 深挖主力；HN 免费，Reddit、X 走 APIsRouter |
| 教程与评论类 | YouTube 评论、B站评论、小红书笔记评论 | 新手卡点、大众用户的说法 | 「<任务> tutorial」「<竞品> review」视频下的评论 |
| 开发者类 | GitHub issues 与讨论、HN 的 Show HN | 功能请求热度、官方不打算做的缺口、自建方案 | 免费接口，开发者产品必做 |
| 交易类 | 竞品评价（G2 认证用户、应用商店、Chrome 商店）、TrustMRR、外包平台、GitHub 悬赏与赞助 | 有没有人已经在付钱、为什么不满意、付多少 | 付费意愿专轮（见 `wtp.md`），每轮必做 |

平台人群不同，选平台要看目标人群：Reddit 偏年轻、高学历、高收入（美国 18–29 岁 48% 在用，65 岁以上 6%）；YouTube 覆盖面最广（美国成年人 84%）；HN 偏开发者和自托管；小红书偏年轻女性和消费决策；知乎高赞回答常带货；B站偏年轻。面向大众消费者的想法，主证据不能只来自 Reddit 和 HN。

## 选场与沉浸

每个平台选 2–4 个社区（子版块、话题、频道），优先中等规模、讨论具体工作的社区，不选泛娱乐大版块。编码前先读每个社区的规则、置顶帖和常见问题，写 5–15 条沉浸日志：行话和缩写、是否禁止推广、哪些帖子类型是抱怨、哪些是玩笑或炫耀、社区里公认的「标准解法」是什么。没写日志的社区，数据不进入编码。我们只能读、不能发帖追问，这一点在报告局限里写明，并用三种办法代替：专门搜相反观点；看社区汇总帖或常见问题有没有同一痛点；把结论标为「待访谈确认」。

## 查询模板

每个意图簇至少跑四类查询，每类至少 2 个说法。英文平台用左列，中文平台用右列。

| 查询类 | 英文 | 中文 |
|---|---|---|
| 痛点 | `"I hate" <任务>`、`"so annoying" <任务>`、`"how do you handle" <任务>`、`"spreadsheet" <任务>` | `<任务> 太麻烦`、`<任务> 踩坑`、`<任务> 怎么解决`、`有没有办法 <任务>` |
| 找方案 | `"is there a tool"`、`"alternative to" <竞品>`、`<竞品> vs`、`"looking for" <品类>` | `求推荐 <品类>`、`<竞品> 平替`、`<竞品> 和 <竞品> 哪个好` |
| 换用与花钱 | `"switched from" <竞品>`、`"I pay" <品类>`、`"worth it" <竞品>`、`"cancelled" <竞品>` | `从 <竞品> 换到`、`<竞品> 值不值`、`<竞品> 退订`、`<竞品> 智商税` |
| 反对 | `"just use" <免费方案>`、`"don't need" <品类>`、`"overkill"`、`"git is enough"` 一类 | `<任务> 用 <免费方案> 就够了`、`没必要`、`白嫖`、`有没有免费的` |

中文用户很少直接抱怨，更多是「求推荐」「避雷」；价格敏感表现为找「平替」「白嫖」「学生党」。「值不值」说明在权衡价格，「有没有免费的」说明拒绝付费，两类提问的比例可以作为中文付费意愿的旁证。

## 各平台怎么取

| 平台 | 入口 | 做法 | 注意 |
|---|---|---|---|
| Reddit | `reddit-dynamic-search.search.v1`（只支持 `sort=RELEVANCE`、`time_range=all`）、`reddit-subreddit-feed.read.v1`（按版块浏览，可选排序）、`reddit-post-comments.read.v1`、`reddit-comment-replies.read.v1` | 搜索只按相关度，所以要靠**多换说法**和**按版块浏览**补足不同排序；高信号帖子读完整评论树，含二级评论 | 分数受早期投票影响大，只在版块内按百分位比较；GummySearch 已停服，官方接口需审批，批量抓取走 APIsRouter |
| Hacker News | 免费：`https://hn.algolia.com/api/v1/search?query=<词>&tags=comment`（或 `story`、`ask_hn`、`show_hn`），`numericFilters=created_at_i>时间戳`；整棵评论树 `https://hn.algolia.com/api/v1/items/<id>` | 竞品的 Show HN 帖评论区是最集中的反对意见来源；`"is there a tool"` 搜评论 | 偏开发者，「写个脚本就行」会高估自建比例 |
| GitHub | 免费：`https://api.github.com/search/issues?q=repo:<仓库>+is:issue+<词>&sort=reactions-%2B1&order=desc`；加 `reason:"not planned"` 找官方不打算做的请求 | 点赞多又被关掉的功能请求，就是现有产品不打算补的缺口；开源替代品的星标增速是需求代理 | 未登录每分钟约 10 次搜索；点赞数按开放月数归一；星标可被刷 |
| X | `twitter-search-timeline.search.v1`（`search_type` 选 Top 或 Latest）、`twitter-post-comments.read.v1`、`twitter-latest-post-comments.read.v1`、`twitter-tweet-detail.read.v1` | 竞品官方账号和大号的产品发布推文，读回复里的抱怨、求功能和换用 | 噪声大，只做定向抽样；剔除营销号和疑似机器账号 |
| YouTube | `youtube-videos.search.v1`、`youtube-video-comments.read.v1`、`youtube-video-comment-replies.read.v1` | 「<任务> tutorial」「<竞品> review」视频下的评论；多人追问却没人给出解法的问题是最强卡点信号 | 评论偏新手 |
| 中文平台 | 见下方「中文市场」 | — | — |
| Discord | 无 | 官方禁止抓取，只能人工观察，不作批量数据源 | — |

## 中文市场

国内用户主要在抖音、小红书、知乎、微博、微信、B站表达需求，谷歌搜索量覆盖不到大陆用户。中文市场单独按下面做，和英文市场分开计数、分开下结论，不把两边的数字相加。

**搜索需求的替代指标**（第 2 步核量用）：

| 用途 | 接口 | 说明 |
|---|---|---|
| 关键词热度走势 | `douyin-multi-keyword-hot-trend.read.v1`（巨量算数，$0.0075） | 是热度指数，不是搜索次数；只在同一批词之间比高低、看走势，不能换算成月搜索量。实测出现过整批返回全零：先用确定有热度的大词试一次，为 0 就当天不可用，改用联想词、各平台搜索结果数和社区作者数判断 |
| 关联词 | `douyin-relation-word.read.v1` | 用户搜这个词时还在搜什么，用来补种子 |
| 搜这个词的人群画像 | `douyin-portrait.read.v1` | 年龄、性别、地域，建模结果，只作定向参考 |
| 联想词 | `douyin-search-suggest.search.v1`、`xiaohongshu-search-suggest.search.v1`、`zhihu-search-suggestions.read.v1`、`weibo-similar-search.search.v1` | 中文的「自动补全」，只说明有人这样搜过 |

收入模型里中文市场单独建一个模型文件，`monthly_searches` 写成 `{"missing": true}`（脚本按 0 计入并在输出里标出缺数据）；中文市场的需求规模主要靠社区作者数、竞品流量和付费证据来判断。

**社区原话**（第 3 步）：

| 平台 | 接口 | 适合找什么 |
|---|---|---|
| 小红书 | `xiaohongshu-note-search.search.v1`、`xiaohongshu-note-comments.read.v1`、`xiaohongshu-note-sub-comments.read.v1`（各 $0.025） | 学生、职场人的效率工具经验和「避雷」；收藏多的笔记对应正在做决定的人 |
| 抖音 | `douyin-general-search.search.v1`、`douyin-experience-posts.search.v1`、`douyin-discussion-search.search.v1`（各 $0.025）、`douyin-video-comments.read.v1`、`douyin-video-comment-replies.read.v1` | 教程视频下的追问和抱怨；经验类内容 |
| 知乎 | `zhihu-answer-search.search.v1`、`zhihu-comment.search.v1`、`zhihu-sub-comment.search.v1` | 问题标题就是需求表述；高赞回答下的反驳 |
| 微博 | `weibo-realtime-search.search.v1`、`weibo-status-comments.read.v1` | 产品发布、出故障时的即时反应 |
| 微信 | `wechat_search-search.read.v1`（搜一搜）、`wechat_mp-article-comments.read.v1`（公众号评论，各 $0.025） | 职场和行业人群的长文与评论 |
| B站 | `bilibili-search-by-type.search.v1`、`bilibili-video-comments.read.v1` | 教程和测评视频的评论 |
| 快手 | `kuaishou-search-comprehensive.search.v1`、`kuaishou-one-video-comment.read.v1` | 下沉人群，按想法决定要不要查 |

中文查询词用「求推荐」「避雷」「踩坑」「平替」「值不值」「有没有免费的」「换电脑后……」「从 A 换到 B」这类说法；国内产品名（如 WorkBuddy、豆包、Kimi、元宝、通义）要和英文产品名一起进竞品清单。中文平台软广和种草多，作者是否在推广要逐条判断。

## 每条原话要记下什么

平台、社区、链接、作者、日期、互动数（赞、回复）、原文（不改写）、原始数据文件路径。写进 `evidence.jsonl`，字段和编码规则见 `coding.md`。每次查询都追加一行到 `query-log.md`：平台、查询串、排序、时间窗、返回条数、日期。查询日志是报告附录的一部分。

## 加权与偏差

- **数人，不数帖**：所有计数按独立作者；同一人发十条只算一次。前 5% 作者贡献超过约 30% 的片段时，一律每人一票（`evidence.py stats` 会提示）。
- **互动数只在社区内比较**：换算成该社区内的百分位，不跨社区比原始赞数。
- **剔除**：产品方和竞品方的推广帖、创始人「你们会用这个吗」的征询帖（诱导性提问）、机器账号（账号新、只发同一链接、内容模板化，多个信号合起来判断）。
- **按年份拆分**：看痛点是否已被现有产品解决、讨论是否在退潮。
- **帖子数不能外推成市场规模**：社区证据只证明需求存在和它的样子，规模由搜索量、竞品流量和付费数据回答。
