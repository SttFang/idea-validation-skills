#!/usr/bin/env python3
"""社区证据库检查：原话逐字核对、按独立作者统计、饱和度判断。

证据库是 evidence.jsonl，每行一条编码后的原话片段，字段见 references/coding.md：
  id, market, platform, community, url, author, date, engagement, quote, source_file,
  codes（编码本里的代码列表）, force, stage, severity, wtp_level, segment, round,
  job, search_phrase, tools_used（列表）, solved, why_not（三层链：讨论什么→搜索什么→用了什么）

用法：
  python3 evidence.py verify evidence.jsonl            # 每条原话必须在 source_file 原文里逐字出现
  python3 evidence.py stats evidence.jsonl [--market 中文]             # 每个代码的独立作者数、社区数、平台数，高产作者占比
  python3 evidence.py saturation evidence.jsonl [--market 英文] [--batch 10] [--threshold 0.05]
  python3 evidence.py chain evidence.jsonl [--market 中文]             # 每个代码的三层链：讨论→搜索→使用→是否解决

退出码：verify 有不通过的行时为 1；saturation 未饱和时为 1。
"""
import argparse
import collections
import html
import json
import re
import sys

WTP_LEVELS = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6']
STAGES = ['初念', '被动寻找', '主动寻找', '决定', '使用']
FORCES = ['推力', '拉力', '焦虑', '惯性']
SOLVED = ['yes', 'partial', 'no', 'unknown']
REQUIRED = ['id', 'platform', 'community', 'url', 'author', 'quote', 'source_file', 'codes']


def load(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    sys.exit(f'第 {n} 行不是合法 JSON：{e}')
    return rows


def norm(s):
    """统一空白、引号、HTML 转义和标签，避免因为排版差异误判（知乎、Hacker News 的正文带 HTML）。"""
    s = s.replace('\\n', ' ').replace('\\"', '"')
    for _ in range(2):
        s = html.unescape(s)
    s = re.sub(r'<[^>]{0,200}>', ' ', s)
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    return re.sub(r'\s+', ' ', s).strip().lower()


def flatten(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            flatten(v, out)
    elif isinstance(obj, list):
        for v in obj:
            flatten(v, out)


_cache = {}


def source_text(path):
    if path not in _cache:
        raw = open(path, encoding='utf-8').read()
        parts = [raw]
        try:
            flatten(json.loads(raw), parts)
        except (json.JSONDecodeError, ValueError):
            pass
        _cache[path] = norm(' \n '.join(parts))
    return _cache[path]


def cmd_verify(a):
    rows = load(a.file)
    bad = 0
    ids = collections.Counter(r.get('id') for r in rows)
    for r in rows:
        problems = [f'缺字段 {k}' for k in REQUIRED if not r.get(k)]
        if ids[r.get('id')] > 1:
            problems.append('id 重复')
        if r.get('wtp_level') and r['wtp_level'] not in WTP_LEVELS:
            problems.append(f"wtp_level 只能是 {'/'.join(WTP_LEVELS)}")
        if r.get('stage') and r['stage'] not in STAGES:
            problems.append(f"stage 只能是 {'/'.join(STAGES)}")
        if r.get('force') and r['force'] not in FORCES:
            problems.append(f"force 只能是 {'/'.join(FORCES)}")
        if r.get('solved') and r['solved'] not in SOLVED:
            problems.append(f"solved 只能是 {'/'.join(SOLVED)}")
        if r.get('tools_used') is not None and not isinstance(r['tools_used'], list):
            problems.append('tools_used 必须是列表')
        if r.get('severity') is not None and r['severity'] not in range(0, 6):
            problems.append('severity 只能是 0–5')
        if r.get('quote') and r.get('source_file'):
            try:
                if norm(r['quote']) not in source_text(r['source_file']):
                    problems.append('原话在原始数据里找不到（可能被改写或编造）')
            except FileNotFoundError:
                problems.append(f"原始数据文件不存在：{r['source_file']}")
        if problems:
            bad += 1
            print(f"不通过 {r.get('id')}: {'；'.join(problems)}")
    print(f'核对 {len(rows)} 条，通过 {len(rows) - bad} 条，不通过 {bad} 条。不通过的原话不能进报告。')
    return 1 if bad else 0


def select(rows, a):
    """按市场、平台筛选；中英文要分开计数、分开下结论。"""
    if getattr(a, 'market', None):
        rows = [r for r in rows if r.get('market') == a.market]
    if getattr(a, 'platform', None):
        rows = [r for r in rows if r.get('platform') == a.platform]
    if not rows:
        sys.exit('筛选后没有片段：检查 market / platform 字段的取值。')
    return rows


def cmd_stats(a):
    rows = select(load(a.file), a)
    authors = collections.Counter(r['author'] for r in rows)
    n_auth = len(authors)
    top_k = max(1, round(n_auth * 0.05))
    top_share = sum(c for _, c in authors.most_common(top_k)) / len(rows) if rows else 0
    print(f'片段 {len(rows)} 条，独立作者 {n_auth} 人，平台 {len({r["platform"] for r in rows})} 个，'
          f'社区 {len({(r["platform"], r["community"]) for r in rows})} 个')
    print(f'前 5% 作者（{top_k} 人）贡献 {top_share:.0%} 的片段' +
          ('，超过 30%：统计一律按每人一票' if top_share > 0.30 else ''))
    by_code = collections.defaultdict(lambda: {'authors': set(), 'communities': set(), 'platforms': set(),
                                               'wtp': collections.Counter(), 'stage': collections.Counter()})
    for r in rows:
        for c in r['codes']:
            d = by_code[c]
            d['authors'].add(r['author'])
            d['communities'].add((r['platform'], r['community']))
            d['platforms'].add(r['platform'])
            if r.get('wtp_level'):
                d['wtp'][r['wtp_level']] += 1
            if r.get('stage'):
                d['stage'][r['stage']] += 1
    print('\n| 代码 | 独立作者 | 社区 | 平台 | 主动寻找及之后占比 | 最高付费证据 | 达到成立门槛 |')
    print('|---|---|---|---|---|---|---|')
    for c, d in sorted(by_code.items(), key=lambda kv: -len(kv[1]['authors'])):
        st = d['stage']
        later = sum(st[s] for s in STAGES[2:])
        share = f'{later / sum(st.values()):.0%}' if st else '—'
        top_wtp = max(d['wtp'], key=WTP_LEVELS.index) if d['wtp'] else '—'
        ok = len(d['authors']) >= a.min_authors and len(d['communities']) >= 2
        print(f"| {c} | {len(d['authors'])} | {len(d['communities'])} | {len(d['platforms'])} | {share} | {top_wtp} | {'是' if ok else '否'} |")
    print(f'\n成立门槛：至少 {a.min_authors} 名独立作者、2 个社区（可调默认值，见 references/coding.md）。')
    return 0


def cmd_saturation(a):
    rows = select(load(a.file), a)
    order, seen = [], set()
    codes_by_author = collections.defaultdict(set)
    for r in rows:
        if r['author'] not in seen:
            seen.add(r['author'])
            order.append(r['author'])
        codes_by_author[r['author']].update(r['codes'])
    batches = [order[i:i + a.batch] for i in range(0, len(order), a.batch)]
    known = set()
    print(f'| 批次 | 作者数 | 新代码 | 累计代码 | 新代码占比 |')
    print('|---|---|---|---|---|')
    shares = []
    for i, b in enumerate(batches, 1):
        new = set().union(*(codes_by_author[x] for x in b)) - known
        share = len(new) / len(known) if known else 1.0
        known |= new
        shares.append(share)
        print(f'| {i} | {len(b)} | {len(new)} | {len(known)} | {share:.0%} |')
    base = 3
    full = [s for b, s in zip(batches, shares) if len(b) == a.batch]  # 没凑满的最后一批不参与判断，避免偏乐观
    if len(full) < len(shares):
        print(f'\n最后一批只有 {len(batches[-1])} 人，不足 {a.batch} 人，不参与饱和判断。')
    saturated = len(full) >= base + 2 and all(s <= a.threshold for s in full[-2:])
    print('\n' + ('已饱和：最近两批新代码占比都不超过 {:.0%}，可以停止取数。'.format(a.threshold) if saturated else
                  '未饱和：需要至少 {} 批（每批 {} 名独立作者），且最近两批新代码占比都不超过 {:.0%}。继续取数，或在报告里降低结论强度。'
                  .format(base + 2, a.batch, a.threshold)))
    return 0 if saturated else 1


def cmd_chain(a):
    """每个代码一条链：讨论什么（job）→ 搜索什么（search_phrase）→ 用了什么（tools_used）→ 是否解决（solved）。
    全部按独立作者计数。一个工具「没解决 + 部分解决」的作者多于「解决了」的，标为错位：工具卖的和人要的对不上。"""
    rows = select(load(a.file), a)
    by_code = collections.defaultdict(list)
    for r in rows:
        for c in r['codes']:
            by_code[c].append(r)
    for c, rs in sorted(by_code.items(), key=lambda kv: -len({r['author'] for r in kv[1]})):
        authors = {r['author'] for r in rs}
        print(f'\n## {c}（{len(authors)} 名独立作者）')
        jobs = collections.defaultdict(set)
        phrases = collections.defaultdict(set)
        tools = collections.defaultdict(lambda: {s: set() for s in SOLVED})
        why = collections.defaultdict(list)
        with_tools = set()
        for r in rs:
            if r.get('job'):
                jobs[r['job']].add(r['author'])
            if r.get('search_phrase'):
                phrases[r['search_phrase']].add(r['author'])
            for t in r.get('tools_used') or []:
                with_tools.add(r['author'])
                tools[t][r.get('solved') or 'unknown'].add(r['author'])
                if r.get('why_not') and r.get('solved') in ('partial', 'no'):
                    why[t].append(f"{r['why_not']}（{r['id']}）")
        top = lambda d: '；'.join(f'{k}（{len(v)} 人）' for k, v in sorted(d.items(), key=lambda kv: -len(kv[1]))[:a.top]) or '—'  # noqa: E731
        print(f'- 讨论什么：{top(jobs)}')
        print(f'- 搜索什么：{top(phrases)}')
        print(f'- 用了什么：没写 tools_used 的作者 {len(authors - with_tools)} 人')
        if tools:
            print('\n| 工具 | 用过的作者 | 解决 | 部分 | 没解决 | 错位 |')
            print('|---|---|---|---|---|---|')
            for t, d in sorted(tools.items(), key=lambda kv: -len(set().union(*kv[1].values())))[:a.top]:
                users = set().union(*d.values())
                miss = len(d['partial'] | d['no']) > len(d['yes'])
                print(f"| {t} | {len(users)} | {len(d['yes'])} | {len(d['partial'])} | {len(d['no'])} | {'是' if miss else '否'} |")
            for t, ws in why.items():
                print(f'- {t} 没解决的原因：' + '；'.join(ws[:a.top]))
    print('\n「错位」只说明在这些作者里工具没命中问题；样本小于 5 人时只当线索。反身检查写进第 8 步。')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('verify'); v.add_argument('file'); v.set_defaults(fn=cmd_verify)
    s = sub.add_parser('stats'); s.add_argument('file'); s.add_argument('--min-authors', type=int, default=5)
    t = sub.add_parser('saturation'); t.add_argument('file'); t.add_argument('--batch', type=int, default=10)
    t.add_argument('--threshold', type=float, default=0.05)
    c = sub.add_parser('chain'); c.add_argument('file'); c.add_argument('--top', type=int, default=8)
    c.set_defaults(fn=cmd_chain)
    for x in (s, t, c):
        x.add_argument('--market', help='只统计某个市场，例如 中文 / 英文（对应 market 字段）')
        x.add_argument('--platform', help='只统计某个平台')
    s.set_defaults(fn=cmd_stats); t.set_defaults(fn=cmd_saturation)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
