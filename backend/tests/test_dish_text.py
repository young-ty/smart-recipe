"""菜名写法归一测试（`app.services.dish_text`）。

这张同义词表同时被运行时召回与覆盖率报表使用，所以这里守两件事：
一是常见写法确实折到标准写法、折叠幂等；二是折叠不能把两道不同的菜折成同一个键。
"""

from __future__ import annotations

import pytest

from app.services.dish_text import SYNONYMS, fold_synonyms, similarity


@pytest.mark.parametrize(
    "written,standard",
    [
        ("西红柿炒蛋", "番茄炒蛋"),
        ("蕃茄牛腩", "番茄牛腩"),
        ("薯仔丝饼", "土豆丝饼"),
        ("马铃薯炖牛腩", "土豆炖牛腩"),
        ("洋芋丝", "土豆丝"),
        ("凉拌青瓜", "凉拌黄瓜"),
        ("干锅花菜", "干锅菜花"),
        ("冬菇滑鸡", "香菇滑鸡"),
        ("猪脚炖黄豆", "猪蹄炖黄豆"),
        ("鸡蛋炒饭", "蛋炒饭"),
        ("番茄牛腩面条", "番茄牛腩面"),
        ("蒜茸菠菜", "蒜蓉菠菜"),
    ],
)
def test_writing_variants_fold_to_the_standard_name(written, standard):
    assert fold_synonyms(written) == standard


def test_folding_is_idempotent_and_survives_empty_input():
    for text in ("西红柿炒蛋", "蒜茸菠菜", "红烧肉"):
        assert fold_synonyms(fold_synonyms(text)) == fold_synonyms(text)
    assert fold_synonyms("") == ""
    assert fold_synonyms(None) == ""


@pytest.mark.parametrize(
    "left,right",
    [
        ("青椒炒蛋", "苦瓜炒蛋"),
        ("麻婆豆腐", "家常豆腐"),
        ("土豆丝饼", "醋溜土豆丝"),
        ("蒜蓉菠菜", "白灼西兰花"),
        ("香菇滑鸡", "黄焖鸡"),
    ],
)
def test_different_dishes_keep_different_keys(left, right):
    """折叠不能把两道不同的菜折成同一个键：错召回一道别的菜比说不出更糟。"""

    assert fold_synonyms(left) != fold_synonyms(right)


def test_table_has_no_chained_replacements():
    """目标写法里不能再出现源写法，否则折叠不是幂等的（这条守住表本身）。"""

    for source, target in SYNONYMS:
        assert source not in target, "同义词表出现链式替换：%s → %s" % (source, target)


def test_similarity_ranks_near_names_above_unrelated_ones():
    assert similarity("土豆炖牛腩", "土豆炖牛肉") > 0.7
    assert similarity("土豆炖牛腩", "绿豆糕") < 0.2
    assert similarity("", "") == 0.0
