# 想法验证：你的想法能赚多少钱

给 Claude Code 和 Codex 用的三个技能，把「这个想法有没有人要、能赚多少钱」从拍脑袋变成一套可复查的流程。

| 技能 | 做什么 | 产出 |
|---|---|---|
| `idea-validation` 想法验证 | 多轮调研：关键词月量、单次点击价、走势 → Reddit 和 X 评论区里的痛点原话 → Similarweb 竞品流量与付费渠道占比 → 按低、中、高三档估算收入、获客成本 | 收入区间、证据清单、**最该先验证的假设** |
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

调研默认走 [APIsRouter](https://apisrouter.com) 信息接口：一个密钥覆盖 DataForSEO（关键词月量、自动补全、搜索结果页、搜索趋势）、Reddit、X、Similarweb，按次计费，一轮调研不用 Similarweb 时通常在 $2 以内，加上 3 个竞品的 Similarweb 约 $10。

```bash
mkdir -p ~/.config/apisrouter
printf '%s' '<你的密钥>' > ~/.config/apisrouter/api-key && chmod 600 ~/.config/apisrouter/api-key
```

`skills/idea-validation/scripts/ar.py` 负责报价、按上限购买、24 小时内同一输入不重复付费、记账。接口清单和单价见 [`skills/idea-validation/data-sources.md`](skills/idea-validation/data-sources.md)。也可以换成你自己接的 DataForSEO、Reddit、X、Similarweb，流程和规则不变。

## 用法

对 Claude 或 Codex 说：

- 「帮我验证这个想法能赚多少钱：给独立开发者的 X 定时发帖工具，每月 9 美元，先看美国和英国」
- 「上次调研说点击份额最不确定，帮我设计一轮 50 美元的搜索广告对照实验」
- 「投放结束了，这是结果，按登记的方法下结论」

## 脚本

全部只依赖 Python 3 标准库。

| 脚本 | 用途 | 测试 |
|---|---|---|
| `skills/idea-validation/scripts/revenue_model.py` | 三档收入、获客成本、敏感度排序 | `python3 -m unittest scripts/test_revenue_model.py`（在该技能目录下） |
| `skills/idea-validation/scripts/ar.py` | APIsRouter 信息接口客户端 | — |
| `skills/ad-campaign-build/scripts/count_chars.py` | 按谷歌广告规则数标题和描述字符（中日韩按 2 计） | — |
| `skills/ad-hypothesis-testing/scripts/ab_stats.py` | 可行性、登记锁定、检验、学习记录 | `python3 -m unittest scripts/test_ab_stats.py`（在该技能目录下） |

## 原则

- 结论是区间加最不确定的假设，不是一个数字；没有出处的参数标为假设。
- 证据分层：行为数据 > 社区原话 > 措辞线索 > 建模估计，不混用。
- 花钱前先报价；Similarweb 这类贵接口先确认。
- 广告实验的门槛、检验方法、停止时机在开投前锁定，看过数据之后改的一律无效。

## 许可

MIT
