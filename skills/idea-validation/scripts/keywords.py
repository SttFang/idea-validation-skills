#!/usr/bin/env python3
"""关键词设计的检查工具：结果页摘要、近似变体合并、按共享网址聚类、准确性与丰富性指标。

关键词表 keywords.csv 的列（缺的列留空即可，见 references/scenario-keywords.md）：
  keyword, scenario, stage, seed_class, source, upstream, volume, trend, serp_file,
  intent_fit, collision, keep

用法：
  python3 keywords.py serp data/serp-*.json --out serp-summary.json   # 每个词的前 10 条自然结果网址、域名、标题、页面模块
  python3 keywords.py variants keywords.csv --out keywords.csv        # 月量和 12 个月明细完全相同的词标为同一变体组
  python3 keywords.py cluster keywords.csv --serp serp-summary.json --min-shared 3 --out keywords.csv
  python3 keywords.py metrics keywords.csv [--reference reference.csv]

metrics 输出：意图精确率、撞词数、冗余率、场景 × 认知阶段格子覆盖率、种子类型分布、原话种子占比，
以及有参照集时的召回率、Chapman 估计的说法总数与找到比例、Chao2 下界、有效簇数（Vendi，需要 numpy）。
"""
import argparse
import collections
import csv
import glob
import json
import math
import re
import sys

STAGES = ['问题', '方案', '产品', '决定']
SEED_CLASSES = ['问题', '变通', '替代对比', '品类', '身份任务']
FIT = {'匹配': 1.0, '混合': 0.5, '不匹配': 0.0}


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def tokens(s):
    words = re.findall(r'[a-z0-9]+|[一-鿿]', s.lower())
    return {w[:-1] if len(w) > 3 and w.endswith('s') else w for w in words}


STOP = {'how', 'to', 'the', 'a', 'an', 'for', 'with', 'my', 'i', 'is', 'can', 'do', 'of', 'in', 'on', 'and',
        'or', 'across', 'between', 'from', 'your', 'you', 'what', 'best', '的', '怎', '么', '如', '何'}


def overlap(a, b):
    """去掉停用词后，较短一方被覆盖的比例；用户原话常比关键词多几个词，用它判断参照说法是否被找到。"""
    a, b = tokens(a) - STOP, tokens(b) - STOP
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def jaccard(a, b):
    a, b = tokens(a), tokens(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def kept(rows):
    return [r for r in rows if (r.get('keep') or '是') != '否' and (r.get('collision') or '否') != '是']


# ---------- serp ----------

def serp_items(path):
    doc = json.load(open(path, encoding='utf-8'))
    for rec in doc.get('records', []):
        for block in (rec.get('data') or [rec]):
            if isinstance(block, dict) and isinstance(block.get('items'), list):
                yield block.get('keyword') or doc.get('input', {}).get('keyword'), block


def cmd_serp(a):
    out = {}
    files = [f for pat in a.files for f in glob.glob(pat)]
    for f in files:
        for kw, block in serp_items(f):
            organic = [it for it in block['items'] if isinstance(it, dict) and it.get('type') == 'organic']
            organic.sort(key=lambda it: it.get('rank_absolute') or 999)
            top = organic[:a.top]
            out[kw] = {'file': f, 'item_types': block.get('item_types') or sorted({it.get('type') for it in block['items'] if isinstance(it, dict)}),
                       'urls': [it.get('url') for it in top], 'domains': [it.get('domain') for it in top],
                       'titles': [it.get('title') for it in top]}
    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'已汇总 {len(out)} 个词的结果页 → {a.out}。逐条给前 {a.top} 条结果标「是否属于目标场景」和页面类型，见 references/scenario-keywords.md 第 2 步 2.2 节。')
    return 0


# ---------- variants ----------

def cmd_variants(a):
    rows = read_csv(a.file)
    groups = collections.defaultdict(list)
    skipped = 0
    for r in rows:
        if r.get('volume') and r.get('trend'):
            months = [x for x in re.split(r'[,\s]+', r['trend']) if x]
            # 小词落在最低分档时月量和明细都一样（例如每月都是 10），这不是近似变体，不合并
            if float(r['volume']) <= a.min_volume or len(set(months)) <= 1:
                skipped += 1
                continue
            groups[(r['volume'], r['trend'])].append(r)
    n = 0
    for (vol, _), members in groups.items():
        if len(members) > 1:
            n += 1
            lead = max(members, key=lambda r: (-len(r['keyword']),))['keyword']
            for r in members:
                r['variant_group'] = lead
                r['count_volume'] = '是' if r['keyword'] == lead else '否'
    for r in rows:
        r.setdefault('variant_group', r['keyword'])
        r.setdefault('count_volume', '是')
    write_csv(a.out or a.file, rows)
    print(f'找到 {n} 个近似变体组：组内只有一个词计入月量（count_volume=是），其余不重复相加。'
          f'另有 {skipped} 个低量或走势平坦的词不参与合并（月量 ≤ {a.min_volume} 或 12 个月都一样），请人工确认是否同义。')
    return 0


# ---------- cluster ----------

def cmd_cluster(a):
    rows = read_csv(a.file)
    serp = json.load(open(a.serp, encoding='utf-8'))
    order = sorted(rows, key=lambda r: -float(r.get('volume') or 0))
    clusters = []  # (代表词, 网址或域名集合, 意图标签)
    pending = []
    for r in order:
        s = serp.get(r['keyword'])
        if not s:
            pending.append(r)
            continue
        urls = set(u for u in (s['domains'] if a.level == 'domain' else s['urls']) if u)
        label = r.get('intent_fit') or ''
        for rep, rep_urls, rep_label in clusters:
            if len(urls & rep_urls) >= a.min_shared and (not label or not rep_label or label == rep_label):
                r['cluster'] = rep
                break
        else:
            clusters.append((r['keyword'], urls, label))
            r['cluster'] = r['keyword']
    # 没买结果页的词：按字面归到最相近的簇代表词，够不上就自成一簇并标出来
    by_literal = 0
    for r in pending:
        best = max(clusters, key=lambda c: overlap(r['keyword'], c[0]), default=None)
        if best and overlap(r['keyword'], best[0]) >= a.literal:
            r['cluster'] = best[0]
            r['cluster_basis'] = '字面'
            by_literal += 1
        else:
            r['cluster'] = r['keyword']
            r['cluster_basis'] = '无结果页'
    for r in rows:
        r.setdefault('cluster_basis', '结果页')
    write_csv(a.out or a.file, rows)
    unit = '个域名' if a.level == 'domain' else '个完整网址'
    merged = sum(1 for r in rows if r['cluster_basis'] == '结果页') - len(clusters)
    print(f'{len(rows)} 个词归成 {len({r["cluster"] for r in rows})} 个簇：有结果页的词按共享 ≥{a.min_shared} {unit}聚类'
          f'（合并了 {merged} 个）；{by_literal} 个没有结果页的词按字面归入已有簇，'
          f'{len(pending) - by_literal} 个没有结果页也够不上字面相似，各自成簇。')
    if clusters and merged == 0:
        print('提示：一个词都没合并。小众领域的结果页常常互不重合，可改用 --level domain，或降低 --min-shared；'
              '此时冗余率不能说明词表没有重复，要人工看一遍。')
    return 0


# ---------- metrics ----------

def vendi(phrases):
    try:
        import numpy as np
    except ImportError:
        return None
    n = len(phrases)
    if n == 0:
        return None
    K = np.array([[jaccard(x, y) if i != j else 1.0 for j, y in enumerate(phrases)] for i, x in enumerate(phrases)])
    w = np.linalg.eigvalsh(K / n)
    w = w[w > 1e-12]
    return float(math.exp(-(w * np.log(w)).sum()))


def match_reference(ref_phrases, gen_rows, threshold):
    found = set()
    gen = [(r['keyword'], r.get('cluster') or r['keyword']) for r in gen_rows]
    for p in ref_phrases:
        if any(overlap(p, g) >= threshold for g, _ in gen):
            found.add(p)
    return found


def chao2(incidence):
    """incidence: {说法: 出现在几个独立来源}"""
    q1 = sum(1 for v in incidence.values() if v == 1)
    q2 = sum(1 for v in incidence.values() if v == 2)
    s = len(incidence)
    return s + (q1 * q1 / (2 * q2) if q2 else q1 * (q1 - 1) / 2)


def cmd_metrics(a):
    rows = read_csv(a.file)
    keep = kept(rows)
    judged = [r for r in rows if r.get('intent_fit') in FIT]
    out = {'keywords': len(rows), 'kept': len(keep),
           'collisions': sum(1 for r in rows if r.get('collision') == '是')}
    out['intent_precision'] = round(sum(FIT[r['intent_fit']] for r in judged) / len(judged), 3) if judged else None
    clusters = {r.get('cluster') or r['keyword'] for r in keep}
    out['clusters'] = len(clusters)
    out['redundancy'] = round(1 - len(clusters) / len(keep), 3) if keep else None
    scenarios = sorted({r.get('scenario') for r in keep if r.get('scenario')})
    cells = {(r.get('scenario'), r.get('stage')) for r in keep if r.get('scenario') and r.get('stage') in STAGES}
    out['scenarios'] = len(scenarios)
    out['cell_coverage'] = round(len(cells) / (len(scenarios) * len(STAGES)), 3) if scenarios else None
    out['seed_classes'] = dict(collections.Counter(r.get('seed_class') or '未标' for r in keep))
    out['quote_seed_share'] = round(sum(1 for r in keep if '原话' in (r.get('source') or '')) / len(keep), 3) if keep else None
    v = vendi([r['keyword'] for r in keep])
    out['vendi_effective'] = round(v, 2) if v else None
    if a.reference:
        ref = read_csv(a.reference)
        ref_phrases = sorted({r['phrase'] for r in ref if r.get('phrase')})
        found = match_reference(ref_phrases, keep, a.match)
        n1, n2, m = len(keep), len(ref_phrases), len(found)
        out['reference_phrases'] = n2
        out['recall'] = round(m / n2, 3) if n2 else None
        N = (n1 + 1) * (n2 + 1) / (m + 1) - 1
        out['chapman_total'] = round(N, 1)
        out['found_share'] = round(min(1.0, n1 / N), 3) if N > 0 else None
        upstream_gen = {(r.get('upstream') or r.get('source') or '技能') for r in keep}
        shared = upstream_gen & {(r.get('upstream') or r.get('source') or '参照') for r in ref}
        out['shared_upstreams'] = sorted(shared)
        inc = collections.Counter()
        for p in ref_phrases:
            srcs = {r.get('upstream') or r.get('source') for r in ref if r.get('phrase') == p}
            inc[p] = len(srcs) + (1 if p in found else 0)
        out['chao2_lower_bound'] = round(chao2(inc), 1) if inc else None
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    lines = [
        ('关键词', f"{out['keywords']}（保留 {out['kept']}，撞词 {out['collisions']}）"),
        ('意图精确率', out['intent_precision']),
        ('意图簇', out['clusters']),
        ('冗余率', out['redundancy']),
        ('场景数', out['scenarios']),
        ('场景 × 认知阶段格子覆盖率', out['cell_coverage']),
        ('种子类型分布', out['seed_classes']),
        ('原话种子占比', out['quote_seed_share']),
        ('有效簇数（Vendi）', out['vendi_effective']),
    ]
    if a.reference:
        lines += [('参照说法数', out['reference_phrases']), ('召回率', out['recall']),
                  ('估计说法总数（Chapman）', out['chapman_total']), ('找到比例', out['found_share']),
                  ('说法总数下界（Chao2）', out['chao2_lower_bound'])]
        if out['shared_upstreams']:
            lines.append(('注意', f"技能与参照集共用上游 {out['shared_upstreams']}：两者不独立，估计会偏低，需合并为一个来源"))
    print('| 指标 | 值 |\n|---|---|')
    for k, v in lines:
        print(f'| {k} | {v if v is not None else "—"} |')
    missing = [c for c in SEED_CLASSES if c not in out['seed_classes']]
    if missing:
        print(f"\n缺少种子类型：{'、'.join(missing)}。回到第 1 步补词。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serp'); s.add_argument('files', nargs='+'); s.add_argument('--out', default='serp-summary.json')
    s.add_argument('--top', type=int, default=10); s.set_defaults(fn=cmd_serp)
    v = sub.add_parser('variants'); v.add_argument('file'); v.add_argument('--out')
    v.add_argument('--min-volume', type=float, default=20, help='月量不超过它的词不参与变体合并')
    v.set_defaults(fn=cmd_variants)
    c = sub.add_parser('cluster'); c.add_argument('file'); c.add_argument('--serp', required=True)
    c.add_argument('--min-shared', type=int, default=3)
    c.add_argument('--level', choices=['url', 'domain'], default='url', help='按完整网址还是域名比较')
    c.add_argument('--literal', type=float, default=0.6, help='没有结果页的词，字面重合度达到多少归入已有簇')
    c.add_argument('--out'); c.set_defaults(fn=cmd_cluster)
    m = sub.add_parser('metrics'); m.add_argument('file'); m.add_argument('--reference')
    m.add_argument('--match', type=float, default=0.6, help='参照说法与关键词去掉停用词后，较短一方被覆盖多少算找到')
    m.add_argument('--json', action='store_true'); m.set_defaults(fn=cmd_metrics)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
