"""Quick smoke test for parser logic."""
from garden_world.main import _parse_codes

sample = """
3.31我的花园世界兑换码+账号福利攻略
周码（4/1日前有效）:指尖花开治愈常在

宝子们！每天19点左右更新！！！记得蹲！

今日通用 :四季轮转花事不断
限时码（1️⃣5️⃣分钟有效）请及时兑换哦～
限时码1（19:58～20:13）:露珠轻颤花信已至
限时码2（21:26～21:41）:直播带路种花不迷
限时码3（22:14～22:29）:同耕一方共享花开
"""

bundle = _parse_codes(sample, "https://example.com/test")
assert bundle is not None, "parse returned None"
print(f"date_label: {bundle.date_label}")
print(f"title:      {bundle.post_title}")
print(f"weekly:     {bundle.weekly_code}")
print(f"universal:  {bundle.universal_code}")
for t in bundle.timed:
    print(f"timed {t.number}: {t.start}~{t.end} code={t.code}")

assert bundle.date_label == "3.31", f"date_label mismatch: {bundle.date_label}"
assert bundle.universal_code == "四季轮转花事不断", f"universal mismatch: {bundle.universal_code}"
assert bundle.weekly_code == "指尖花开治愈常在", f"weekly mismatch: {bundle.weekly_code}"
assert len(bundle.timed) == 3, f"timed count mismatch: {len(bundle.timed)}"
assert bundle.timed[0].code == "露珠轻颤花信已至"
assert bundle.timed[1].start == "21:26"
assert bundle.timed[2].end == "22:29"

print("\n=== ALL TESTS PASSED (Format A) ===")


# ── Format B: alternate blogger style (3.30 post by 玫玫) ──

sample_b = """3.30-我的花园世界兑换码
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

bundle_b = _parse_codes(sample_b, "https://example.com/test_b")
assert bundle_b is not None, "Format B: parse returned None"
print(f"\n--- Format B ---")
print(f"date_label: {bundle_b.date_label}")
print(f"title:      {bundle_b.post_title}")
print(f"weekly:     {bundle_b.weekly_code}")
print(f"universal:  {bundle_b.universal_code}")
for t in bundle_b.timed:
    print(f"timed {t.number}: {t.start}~{t.end} code={t.code}")

assert bundle_b.date_label == "3.30", f"Format B date_label: {bundle_b.date_label}"
assert bundle_b.universal_code == "荒漠生花梭影悠长", f"Format B universal: {bundle_b.universal_code}"
assert bundle_b.weekly_code == "指尖花开治愈常在", f"Format B weekly: {bundle_b.weekly_code}"
assert len(bundle_b.timed) == 3, f"Format B timed count: {len(bundle_b.timed)}"
assert bundle_b.timed[0].code == "一庭春色缓缓展开", f"Format B timed1: {bundle_b.timed[0].code}"
assert bundle_b.timed[0].start == "20:06", f"Format B timed1 start: {bundle_b.timed[0].start}"
assert bundle_b.timed[1].code == "柳色新新花事渐盛"
assert bundle_b.timed[2].end == "22:31"

print("\n=== ALL TESTS PASSED (Format B) ===")


# ── Format B with empty codes (early post) ──

sample_b_empty = """3.30-我的花园世界兑换码
我的花园世界兑换码3.30
晚上[七R]点开始更新：
（3.30当日）                荒漠生花梭影悠长
限时码：（[一R][五R]分钟有效）
（20：06-20：21）：
（21：24-21：39）：
（22：16-22：31）：
（3.25-3.31周码）指尖花开治愈常在
"""

bundle_b2 = _parse_codes(sample_b_empty, "https://example.com/test_b2")
assert bundle_b2 is not None, "Format B empty: parse returned None"
assert bundle_b2.universal_code == "荒漠生花梭影悠长"
assert len(bundle_b2.timed) == 3, f"Format B empty timed: {len(bundle_b2.timed)}"
assert all(t.code == "" for t in bundle_b2.timed), "Format B empty: all timed codes should be empty"
assert bundle_b2.timed[0].start == "20:06"

print("=== ALL TESTS PASSED (Format B empty) ===")


# ── Format C: "今日通码" + ordinal timed codes ──

sample_c = """我的花园世界3.27兑换 19:00更新
周码：指尖花开治愈常在
今日通码：以花会友因缘相聚

限时码（15分钟有效）
第一个：心有繁花一路芳华
第二个：花间一播治愈一刻
第三个：春风拂槛百花低语
"""

bundle_c = _parse_codes(sample_c, "https://example.com/test_c")
assert bundle_c is not None, "Format C: parse returned None"
print(f"\n--- Format C ---")
print(f"date_label: {bundle_c.date_label}")
print(f"universal:  {bundle_c.universal_code}")
print(f"weekly:     {bundle_c.weekly_code}")
for t in bundle_c.timed:
    print(f"timed {t.number}: code={t.code}")

assert bundle_c.date_label == "3.27"
assert bundle_c.universal_code == "以花会友因缘相聚", f"Format C universal: {bundle_c.universal_code}"
assert bundle_c.weekly_code == "指尖花开治愈常在"
assert len(bundle_c.timed) == 3, f"Format C timed count: {len(bundle_c.timed)}"
assert bundle_c.timed[0].code == "心有繁花一路芳华"
assert bundle_c.timed[1].code == "花间一播治愈一刻"
assert bundle_c.timed[2].code == "春风拂槛百花低语"
# No time windows in this format
assert bundle_c.timed[0].start == ""

print("=== ALL TESTS PASSED (Format C) ===")


# ── Format D: "N 点兑换码" + "N 点限时码" with validity ──

sample_d = """我的花园时间3.27兑换码
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

bundle_d = _parse_codes(sample_d, "https://example.com/test_d")
assert bundle_d is not None, "Format D: parse returned None"
print(f"\n--- Format D ---")
print(f"date_label: {bundle_d.date_label}")
print(f"universal:  {bundle_d.universal_code}")
print(f"weekly:     {bundle_d.weekly_code}")
for t in bundle_d.timed:
    print(f"timed {t.number}: {t.start}~{t.end} code={t.code}")

assert bundle_d.date_label == "3.27"
assert bundle_d.weekly_code == "指尖花开治愈常在"
assert bundle_d.universal_code == "以花会友因缘相聚", f"Format D universal: {bundle_d.universal_code}"

print("=== ALL TESTS PASSED (Format D) ===")

# ── Format A with DOT time separators (3.29 note style) ──

sample_a_dot = """3.29我的花园世界
3.29通码：闻花品茶且共春色
限时码（15分钟有效）
限时码1（20.12-20.27）：花开草绿花园锦绣
限时码2（21.28-21.43）：播间欢乐一半予你
限时码3（22.17-22.32）：一汀烟雨满园繁花
周码：指尖花开治愈常在
有效期3.25-3.31
"""

bundle_adot = _parse_codes(sample_a_dot, "https://example.com/test_adot")
assert bundle_adot is not None, "Format A dot: parse returned None"
print(f"\n--- Format A (dot time) ---")
print(f"date_label: {bundle_adot.date_label}")
print(f"universal:  {bundle_adot.universal_code}")
print(f"weekly:     {bundle_adot.weekly_code}")
for t in bundle_adot.timed:
    print(f"timed {t.number}: {t.start}~{t.end} code={t.code}")

assert bundle_adot.date_label == "3.29"
assert bundle_adot.universal_code == "闻花品茶且共春色", f"dot uni: {bundle_adot.universal_code}"
assert bundle_adot.weekly_code == "指尖花开治愈常在"
assert len(bundle_adot.timed) == 3, f"dot timed count: {len(bundle_adot.timed)}"
assert bundle_adot.timed[0].start == "20:12", f"dot start: {bundle_adot.timed[0].start}"
assert bundle_adot.timed[0].end == "20:27", f"dot end: {bundle_adot.timed[0].end}"
assert bundle_adot.timed[0].code == "花开草绿花园锦绣"
assert bundle_adot.timed[1].start == "21:28"
assert bundle_adot.timed[2].code == "一汀烟雨满园繁花"

print("=== ALL TESTS PASSED (Format A dot time) ===")


# ── Universal with parenthesized time — should NOT leak "00):" prefix ──

sample_uni_paren = """我的花园世界 3.29日兑换码
本周周码（3.25-3.31）：指尖花开治愈常在
今日通用兑换码（19:00）：闻花品茶且共春色
限时码一：20点左右，待更新
限时码二：21点左右，待更新
限时码三：22点左右，待更新
"""

bundle_up = _parse_codes(sample_uni_paren, "https://example.com/test_up")
assert bundle_up is not None, "Uni paren: parse returned None"
print(f"\n--- Universal with paren time ---")
print(f"universal:  {repr(bundle_up.universal_code)}")
print(f"weekly:     {repr(bundle_up.weekly_code)}")
for t in bundle_up.timed:
    print(f"timed {t.number}: code={repr(t.code)}")

assert bundle_up.universal_code == "闻花品茶且共春色", f"uni paren leaked: {bundle_up.universal_code}"
assert bundle_up.weekly_code == "指尖花开治愈常在"
# 限时码一/二/三 with "待更新" → 3 placeholders with empty codes
assert len(bundle_up.timed) == 3, f"timed count: {len(bundle_up.timed)}"
assert all(t.code == "" for t in bundle_up.timed), "all timed should be empty (待更新)"

print("=== ALL TESTS PASSED (Universal paren time) ===")


# ── 周兑换码 + 限时兑换码(...) + 兑换码N: format (3.28 Note [3]) ──

sample_e = """我的花园世界3.28兑换码
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

bundle_e = _parse_codes(sample_e, "https://example.com/test_e")
assert bundle_e is not None, "Format E: parse returned None"
print(f"\n--- Format E (限时兑换码 + 兑换码N) ---")
print(f"date_label: {bundle_e.date_label}")
print(f"weekly:     {bundle_e.weekly_code}")
for t in bundle_e.timed:
    print(f"timed {t.number}: {t.start}~{t.end} code={t.code}")

assert bundle_e.date_label == "3.28"
assert bundle_e.weekly_code == "指尖花开治愈常在", f"E weekly: {bundle_e.weekly_code}"
assert len(bundle_e.timed) == 4, f"E timed count: {len(bundle_e.timed)}"
assert bundle_e.timed[0].code == "桃李争春满院香", f"E timed1: {bundle_e.timed[0].code}"
assert bundle_e.timed[0].start == "12:00", f"E start: {bundle_e.timed[0].start}"
assert bundle_e.timed[0].end == "14:00", f"E end: {bundle_e.timed[0].end}"
assert bundle_e.timed[3].code == "嫣然一笑醉春光"
# Make sure VIP codes after "代言人" section are NOT included
assert all("杨紫" not in t.code for t in bundle_e.timed), "should not include VIP codes"

print("=== ALL TESTS PASSED (Format E) ===")


# ── 限时(HH:MM-HH:MM)兑换码: format (3.28 Note [6]) ──

sample_g = """我的花园世界2026.3.28兑换码ZFB
限时（12:00-14:00）兑换码：
红妆亦绽春风里
自信芳华自有时
温柔亦有千钧力
笑靥如花映日暖
#我的花园世界[话题]# #一起种植吧[话题]#
"""

bundle_g = _parse_codes(sample_g, "https://example.com/test_g")
assert bundle_g is not None, "Format G: parse returned None"
print(f"\n--- Format G (限时(time)兑换码:) ---")
print(f"date_label: {bundle_g.date_label}")
for t in bundle_g.timed:
    print(f"timed {t.number}: {t.start}~{t.end} code={t.code}")

assert bundle_g.date_label == "3.28"
assert len(bundle_g.timed) == 4, f"G timed count: {len(bundle_g.timed)}"
assert bundle_g.timed[0].start == "12:00"
assert bundle_g.timed[0].end == "14:00"
assert bundle_g.timed[0].code == "红妆亦绽春风里"
assert bundle_g.timed[3].code == "笑靥如花映日暖"

print("=== ALL TESTS PASSED (Format G) ===")


# ── 本周通码 with brackets → weekly, NOT universal ──

sample_bracket = """我的花园世界3.29兑换码
本周通码：【指尖花开治愈常在】
今日通码：闻花品茶且共春色
"""

bundle_br = _parse_codes(sample_bracket, "https://example.com/test_br")
assert bundle_br is not None, "Bracket: parse returned None"
print(f"\n--- 本周通码 with brackets ---")
print(f"weekly:     {repr(bundle_br.weekly_code)}")
print(f"universal:  {repr(bundle_br.universal_code)}")

assert bundle_br.weekly_code == "指尖花开治愈常在", f"bracket weekly: {bundle_br.weekly_code}"
assert bundle_br.universal_code == "闻花品茶且共春色", f"bracket uni: {bundle_br.universal_code}"

print("=== ALL TESTS PASSED (本周通码 brackets) ===")


print("\n=== ALL PARSER TESTS PASSED ===")