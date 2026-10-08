#!/usr/bin/env python3
"""按谷歌广告规则数广告文案字符：中文、日文、韩文等双宽字符每个算 2 个。

用法：python3 count_chars.py --headline "Turn Story Ideas Into Scripts" --description "..."
超限时退出码为 1。
"""
import argparse
import sys
import unicodedata

LIMITS = {'headline': 30, 'description': 90, 'path': 15}


def width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for kind in LIMITS:
        ap.add_argument(f'--{kind}', action='append', default=[])
    a = ap.parse_args(argv)
    over = False
    for kind, limit in LIMITS.items():
        for text in getattr(a, kind):
            n = width(text)
            ok = n <= limit
            over = over or not ok
            print(f"{'通过' if ok else '超限'}  {kind}  {n}/{limit}  {text}")
    return 1 if over else 0


if __name__ == '__main__':
    sys.exit(main())
