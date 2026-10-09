"""菜名的写法归一与相似度：库内召回与覆盖率报表共用同一套规则。

只收「同一道菜的不同写法」（西红柿/番茄、薯仔/土豆、茸/蓉、冬菇/香菇 这类），
拿不准的一律不收：错召回一道别的菜，比说不出更糟。

两张表放在一个模块里是有原因的：报表用同一张表挑「免费能补的缺口」，
运行时用同一张表召回，两边口径必须一致，否则报表说能补、线上却补不上。
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

# 同义写法 → 标准写法。长词在前，避免短词先把长词切碎。
SYNONYMS: Sequence[Tuple[str, str]] = (
    ("西红柿", "番茄"),
    ("蕃茄", "番茄"),
    ("马铃薯", "土豆"),
    ("薯仔", "土豆"),
    ("洋芋", "土豆"),
    ("青瓜", "黄瓜"),
    ("花菜", "菜花"),
    ("冬菇", "香菇"),
    ("猪脚", "猪蹄"),
    ("鸡蛋", "蛋"),
    ("面条", "面"),
    ("茸", "蓉"),
)


def fold_synonyms(text: str) -> str:
    """把同义写法折成标准写法；只做替换，不动空白与其他字符。

    对已经折过的字符串再折一次结果不变（表里的目标词都不含源词），
    所以调用方不必在意自己拿到的是原始名还是已经折过的名字。
    """

    folded = text or ""
    for source, target in SYNONYMS:
        folded = folded.replace(source, target)
    return folded


def bigrams(text: str) -> List[str]:
    """二元字组；单字名退化成它自己。"""

    return [text[i:i + 2] for i in range(len(text) - 1)] or [text]


def similarity(left: str, right: str) -> float:
    """Dice 系数（按二元字组），够用来抓「差一个字」的同类菜名。"""

    if not left or not right:
        return 0.0
    a, b = bigrams(left), bigrams(right)
    pool = list(b)
    hit = 0
    for gram in a:
        if gram in pool:
            hit += 1
            pool.remove(gram)
    return 0.0 if not (len(a) + len(b)) else 2 * hit / (len(a) + len(b))
