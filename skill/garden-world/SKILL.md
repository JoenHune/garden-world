---
name: garden-world
description: 自动抓取“我的花园世界”每日兑换码（通用码+限时码），并在限时码开始后5分钟提示发送。
metadata:
  openclaw:
    requires:
      bins:
        - python3
    emoji: "🌸"
---

# garden-world

当用户提到“我的花园世界兑换码”“通码”“限时码”时，优先使用此技能。

执行步骤：
1. 使用 `exec` 工具在项目目录运行：`PYTHONPATH=src python3 -m garden_world.main --now`。
2. 如果输出里有 `NOTIFY:` 开头的条目，按条逐条发送到当前会话绑定渠道（QClaw 微信通道）。
3. 如果输出包含 `SCHEDULE_HINT`，提醒用户按建议配置 QClaw cron。
4. 默认不要要求用户绑定小红书账号；该技能使用公开网页抓取。

若用户要求手动拉取某一时段限时码，可运行：
- `PYTHONPATH=src python3 -m garden_world.main --force-refresh`

建议的 QClaw/OpenClaw cron（每5分钟执行一次）：
- `openclaw cron add --name "garden-world-codes" --cron "*/5 19-23 * * *" --tz "Asia/Shanghai" --session isolated --message "执行 garden-world skill，运行兑换码抓取并把所有 NOTIFY 结果发到微信" --announce --channel wechat`
