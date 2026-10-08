# 想法验证：你的想法能赚多少钱

给 Claude Code 和 Codex 用的三个技能，把「这个想法有没有人要、能赚多少钱」从拍脑袋变成一套可复查的流程。

| 技能 | 做什么 | 产出 |
|---|---|---|
| `idea-validation` 想法验证 | 先访谈提出者、把想法拆成问题树和多个有证据的场景 → 从场景设计关键词并用结果页验证意图 → 中英文社区多平台取原话（Reddit、X、Hacker News、GitHub、YouTube、小红书、抖音、知乎等）并按人编码 → 竞品与付费证据阶梯 → 反证与竞争假设 → 按低、中、高三档估算收入 | 中英文分开的结论、收入区间、证据库、**最该先验证的假设**和带门槛的验证实验 |
| `ad-campaign-build` 投放及素材搭建 | 想用真金白银验证诉求时：复盘上一轮、确认人群与设备、写假设、算预算够不够、填实验策略单、写测试与对照广告和落地页首屏 | 实验策略单 + 登记草稿 + 广告素材 |
| `ad-hypothesis-testing` 假设验证 | 事先登记并锁定检验方法，投放 1–3 天，按样本规模固定用 Barnard 精确检验或两比例 z 检验，Holm / BH 校正，校正同一用户重复点击 | 六种结论之一 + 学习记录 |

三个技能可以单独用。大多数想法只需要第一个：它告诉你需求有多大、钱从哪来、哪个假设最不确定；只有当那个假设值得花钱验证时，才走后两个。

## 安装

**Claude Code**

```
/plugin marketplace add SttFang/idea-validation-skills
/plugin install idea-validation-skills@idea-validation-skills
```

**Codex**

```bash
codex plugin marketplace add SttFang/idea-validation-skills
codex plugin add idea-validation-skills@idea-validation-skills
```

也可以直接把 `skills/` 下的目录拷进 `~/.claude/skills/` 或 `~/.agents/skills/`。

## 数据

调研默认走 [APIsRouter](https://apisrouter.com) 信息接口：一个密钥覆盖 DataForSEO（关键词月量、自动补全、搜索结果页、搜索趋势）、Reddit、X、YouTube、抖音（含巨量算数）、小红书、知乎、微博、微信、B站、Similarweb，按次计费。Hacker News、GitHub、网页存档用免费公开接口。一轮完整验证通常 150–250 次调用、$8–15，耗时 1.5–2.5 小时。

```bash
mkdir -p ~/.config/apisrouter
printf '%s' '<你的密钥>' > ~/.config/apisrouter/api-key && chmod 600 ~/.config/apisrouter/api-key
```

`skills/idea-validation/scripts/ar.py` 负责报价、按上限购买、24 小时内同一输入不重复付费、记账；下单后立刻记账，断网或重跑会接上原请求，不会重复扣费；被限流时按服务端要求等待后重试。接口清单和单价见 [`skills/idea-validation/data-sources.md`](skills/idea-validation/data-sources.md)。也可以换成你自己接的 DataForSEO、Reddit、X、Similarweb，流程和规则不变。

## 用法

对 Claude 或 Codex 说：

- 「帮我验证这个想法能赚多少钱：给独立开发者的 X 定时发帖工具，每月 9 美元，先看美国和英国」
- 「上次调研说点击份额最不确定，帮我设计一轮 50 美元的搜索广告对照实验」
- 「投放结束了，这是结果，按登记的方法下结论」

## 脚本

全部只依赖 Python 3 标准库。

| 脚本 | 用途 | 测试 |
|---|---|---|
| `skills/idea-validation/scripts/revenue_model.py` | 三档收入、获客成本、敏感度排序、多市场合计 | `python3 -m unittest scripts/test_revenue_model.py`（在该技能目录下） |
| `skills/idea-validation/scripts/ar.py` | APIsRouter 信息接口客户端 | `python3 -m unittest scripts/test_ar.py` |
| `skills/idea-validation/scripts/keywords.py` | 结果页摘要、近似变体合并、聚类、关键词准确性与丰富性指标 | `python3 -m unittest scripts/test_keywords.py` |
| `skills/idea-validation/scripts/evidence.py` | 原话逐字核对、按独立作者统计、饱和度判断 | `python3 -m unittest scripts/test_evidence.py` |
| `skills/ad-campaign-build/scripts/count_chars.py` | 按谷歌广告规则数标题和描述字符（中日韩按 2 计） | — |
| `skills/ad-hypothesis-testing/scripts/ab_stats.py` | 可行性、登记锁定、检验、学习记录 | `python3 -m unittest scripts/test_ab_stats.py`（在该技能目录下） |

## 原则

- 先问清楚提出者、想透问题和场景，再找数据；不拿产品功能词直接核量。
- 数人不数帖，原话逐字可查，每个结论写出分母。
- 结论是区间加最不确定的假设，不是一个数字；没有出处的参数标为假设。
- 证据分层：行为数据 > 社区原话 > 措辞线索 > 建模估计，不混用。
- 花钱前先报价；Similarweb 这类贵接口先确认。
- 广告实验的门槛、检验方法、停止时机在开投前锁定，看过数据之后改的一律无效。

## 许可

MIT
