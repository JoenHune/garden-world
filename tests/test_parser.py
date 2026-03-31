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

print("\n=== ALL TESTS PASSED ===")
