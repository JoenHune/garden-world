"""Unit tests for the code parser — 11 format variations.

All tests are pure-logic (no browser, no network).
Run with: ``pytest tests/unit/test_parser.py -v``
"""
from __future__ import annotations

import pytest

from garden_world.main import _parse_codes


# ── Format A: standard style ────────────────────────────────────

SAMPLE_A = """\
3.31我的花园世界兑换码+账号福利攻略
周码（4/1日前有效）:指尖花开治愈常在

宝子们！每天19点左右更新！！！记得蹲！

今日通用 :四季轮转花事不断
限时码（1️⃣5️⃣分钟有效）请及时兑换哦～
限时码1（19:58～20:13）:露珠轻颤花信已至
限时码2（21:26～21:41）:直播带路种花不迷
限时码3（22:14～22:29）:同耕一方共享花开
"""


def test_format_a():
    bundle = _parse_codes(SAMPLE_A, "https://example.com/a")
    assert bundle is not None
    assert bundle.date_label == "3.31"
    assert bundle.universal_code == "四季轮转花事不断"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert len(bundle.timed) == 3
    assert bundle.timed[0].code == "露珠轻颤花信已至"
    assert bundle.timed[1].start == "21:26"
    assert bundle.timed[2].end == "22:29"


# ── Format B: alternate blogger style (time in parens, no prefix) ──

SAMPLE_B = """\
3.30-我的花园世界兑换码
我的花园世界兑换码3.30
[红色心形R]以下兑换码只针对官服，ID: ys开头
晚上[七R]点开始更新：
（3.30当日）                荒漠生花梭影悠长
限时码：（[一R][五R]分钟有效）
（20：06-20：21）：一庭春色缓缓展开
（21：24-21：39）：柳色新新花事渐盛
（22：16-22：31）：清风入园万物舒展
（3.25-3.31周码）指尖花开治愈常在
"""


def test_format_b():
    bundle = _parse_codes(SAMPLE_B, "https://example.com/b")
    assert bundle is not None
    assert bundle.date_label == "3.30"
    assert bundle.universal_code == "荒漠生花梭影悠长"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert len(bundle.timed) == 3
    assert bundle.timed[0].code == "一庭春色缓缓展开"
    assert bundle.timed[0].start == "20:06"
    assert bundle.timed[1].code == "柳色新新花事渐盛"
    assert bundle.timed[2].end == "22:31"


# ── Format B with empty codes (early post, not yet revealed) ───

SAMPLE_B_EMPTY = """\
3.30-我的花园世界兑换码
我的花园世界兑换码3.30
晚上[七R]点开始更新：
（3.30当日）                荒漠生花梭影悠长
限时码：（[一R][五R]分钟有效）
（20：06-20：21）：
（21：24-21：39）：
（22：16-22：31）：
（3.25-3.31周码）指尖花开治愈常在
"""


def test_format_b_empty_timed():
    bundle = _parse_codes(SAMPLE_B_EMPTY, "https://example.com/b2")
    assert bundle is not None
    assert bundle.universal_code == "荒漠生花梭影悠长"
    assert len(bundle.timed) == 3
    assert all(t.code == "" for t in bundle.timed)
    assert bundle.timed[0].start == "20:06"


# ── Format C: ordinal timed codes (第一个 / 第二个 / 第三个) ──

SAMPLE_C = """\
我的花园世界3.27兑换 19:00更新
周码：指尖花开治愈常在
今日通码：以花会友因缘相聚

限时码（15分钟有效）
第一个：心有繁花一路芳华
第二个：花间一播治愈一刻
第三个：春风拂槛百花低语
"""


def test_format_c():
    bundle = _parse_codes(SAMPLE_C, "https://example.com/c")
    assert bundle is not None
    assert bundle.date_label == "3.27"
    assert bundle.universal_code == "以花会友因缘相聚"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert len(bundle.timed) == 3
    assert bundle.timed[0].code == "心有繁花一路芳华"
    assert bundle.timed[1].code == "花间一播治愈一刻"
    assert bundle.timed[2].code == "春风拂槛百花低语"
    assert bundle.timed[0].start == ""  # no time windows in this format


# ── Format D: "N 点兑换码" / "N 点限时码" with 有效期 ──

SAMPLE_D = """\
我的花园时间3.27兑换码
本周周码：指尖花开治愈常在
有效期：3 月 31 日 23:59

①       7 点兑换码：以花会友因缘相聚
有效期：23:59

②      20 点限时码：心有繁花一路芳华
有效期：20:09-20:24

③       21 点限时码：花间一播治愈一刻
有效期：21:21-21:36

④       22 点限时码：春风拂槛百花低语
有效期：22:04-22:19
"""


def test_format_d():
    bundle = _parse_codes(SAMPLE_D, "https://example.com/d")
    assert bundle is not None
    assert bundle.date_label == "3.27"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert bundle.universal_code == "以花会友因缘相聚"


# ── Format A with dot time separators (20.12 instead of 20:12) ──

SAMPLE_A_DOT = """\
3.29我的花园世界
3.29通码：闻花品茶且共春色
限时码（15分钟有效）
限时码1（20.12-20.27）：花开草绿花园锦绣
限时码2（21.28-21.43）：播间欢乐一半予你
限时码3（22.17-22.32）：一汀烟雨满园繁花
周码：指尖花开治愈常在
有效期3.25-3.31
"""


def test_format_a_dot_time():
    bundle = _parse_codes(SAMPLE_A_DOT, "https://example.com/adot")
    assert bundle is not None
    assert bundle.date_label == "3.29"
    assert bundle.universal_code == "闻花品茶且共春色"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert len(bundle.timed) == 3
    assert bundle.timed[0].start == "20:12"
    assert bundle.timed[0].end == "20:27"
    assert bundle.timed[0].code == "花开草绿花园锦绣"
    assert bundle.timed[1].start == "21:28"
    assert bundle.timed[2].code == "一汀烟雨满园繁花"


# ── Universal with parenthesized time — must not leak "00):" ──

SAMPLE_UNI_PAREN = """\
我的花园世界 3.29日兑换码
本周周码（3.25-3.31）：指尖花开治愈常在
今日通用兑换码（19:00）：闻花品茶且共春色
限时码一：20点左右，待更新
限时码二：21点左右，待更新
限时码三：22点左右，待更新
"""


def test_universal_with_paren_time():
    bundle = _parse_codes(SAMPLE_UNI_PAREN, "https://example.com/up")
    assert bundle is not None
    assert bundle.universal_code == "闻花品茶且共春色"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert len(bundle.timed) == 3
    assert all(t.code == "" for t in bundle.timed)
    # Time hints extracted from "N点左右" text
    assert bundle.timed[0].start == "20:00"
    assert bundle.timed[1].start == "21:00"
    assert bundle.timed[2].start == "22:00"
    # No end time derivable from approximate hints
    assert all(t.end == "" for t in bundle.timed)


# ── Format F: CJK ordinals with codes filled — no time in text ──

SAMPLE_F_FILLED = """\
我的花园世界 4.11日兑换码
本周周码（4.8-4.14）：花园入洛共见春光
今日通用兑换码（19:00）：花开成景春意成行
限时码一：风送花香满园成趣
限时码二：一园芳菲恰好盛放
限时码三：春色入园万物生辉
"""


def test_format_f_filled_codes_no_time():
    bundle = _parse_codes(SAMPLE_F_FILLED, "https://example.com/ff")
    assert bundle is not None
    assert bundle.universal_code == "花开成景春意成行"
    assert len(bundle.timed) == 3
    assert bundle.timed[0].code == "风送花香满园成趣"
    assert bundle.timed[1].code == "一园芳菲恰好盛放"
    assert bundle.timed[2].code == "春色入园万物生辉"
    # No time info in the text — start/end should be empty
    assert all(t.start == "" for t in bundle.timed)
    assert all(t.end == "" for t in bundle.timed)


# ── Format E: 限时兑换码(...) + 兑换码N: ──

SAMPLE_E = """\
我的花园世界3.28兑换码
我的花园世界3.28兑换码 [樱花R]

限时兑换码（当天中午 12点到14点兑换有效）:
兑换码1:桃李争春满院香
兑换码2:千花竞艳沐朝阳
兑换码3:花开富贵满园芳
兑换码4:嫣然一笑醉春光

周兑换码（有效期至3.31）: 指尖花开治愈常在
代言人专属福利码:
兑换码1:杨紫邀你共赏新花
兑换码2:与杨紫共赴花园
"""


def test_format_e():
    bundle = _parse_codes(SAMPLE_E, "https://example.com/e")
    assert bundle is not None
    assert bundle.date_label == "3.28"
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert len(bundle.timed) == 4
    assert bundle.timed[0].code == "桃李争春满院香"
    assert bundle.timed[0].start == "12:00"
    assert bundle.timed[0].end == "14:00"
    assert bundle.timed[3].code == "嫣然一笑醉春光"
    # VIP codes after "代言人" section must NOT be included
    assert all("杨紫" not in t.code for t in bundle.timed)


# ── Format G: 限时(HH:MM-HH:MM)兑换码: + bare code lines ──

SAMPLE_G = """\
我的花园世界2026.3.28兑换码ZFB
限时（12:00-14:00）兑换码：
红妆亦绽春风里
自信芳华自有时
温柔亦有千钧力
笑靥如花映日暖
#我的花园世界[话题]# #一起种植吧[话题]#
"""


def test_format_g():
    bundle = _parse_codes(SAMPLE_G, "https://example.com/g")
    assert bundle is not None
    assert bundle.date_label == "3.28"
    assert len(bundle.timed) == 4
    assert bundle.timed[0].start == "12:00"
    assert bundle.timed[0].end == "14:00"
    assert bundle.timed[0].code == "红妆亦绽春风里"
    assert bundle.timed[3].code == "笑靥如花映日暖"


# ── 本周通码 with brackets → weekly, NOT universal ──

SAMPLE_BRACKET = """\
我的花园世界3.29兑换码
本周通码：【指尖花开治愈常在】
今日通码：闻花品茶且共春色
"""


def test_weekly_bracket_vs_universal():
    bundle = _parse_codes(SAMPLE_BRACKET, "https://example.com/br")
    assert bundle is not None
    assert bundle.weekly_code == "指尖花开治愈常在"
    assert bundle.universal_code == "闻花品茶且共春色"


# ── Edge cases ──

def test_empty_text_returns_none():
    assert _parse_codes("", "https://example.com/empty") is None


def test_irrelevant_text_returns_none():
    assert _parse_codes("random text without codes", "https://example.com/random") is None
