```skill
---
name: garden-world
description: 自动抓取"我的花园世界"每日兑换码（通用码+周码+限时码），渐进式检测限时码更新并在适当时机推送。
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - garden-world
    emoji: "🌸"
---

# garden-world

当用户提到"我的花园世界兑换码""通码""周码""限时码"时，优先使用此技能。

## 前提

安装项目及浏览器：
```bash
pip install /path/to/garden-world   # 或 pip install -e /path/to/garden-world
python3 -m playwright install chromium
```

**首次使用**需登录小红书（搜索功能需要登录）：
```bash
garden-world login
```
弹出浏览器窗口，用小红书 App 扫码登录。凭证保存在 `.garden_world/browser_profile/`（Chromium 持久化会话目录）。

若输出包含 `QR_IMAGE:` 行，其后的文件路径即为登录二维码截图（整个登录墙），可推送给用户辅助远程扫码。

## 核心工作流

本技能采用**渐进式检测**架构：

1. **首次运行** — 搜索小红书，找到当天兑换码帖子，推送通用码（和周码，若有），缓存帖子URL。
2. **后续运行** — 直接重新访问缓存URL（快速路径，无需再搜索），检测是否有新的限时码被博主更新。
3. **渐进推送** — 每个限时码在其时间窗开始后5分钟且代码值非空时推送。已推送的码不会重复推送。

> 博主通常在19:00左右发帖，帖子里只有时间窗但限时码为空。之后在每个时间窗开始时编辑帖子填入限时码。
> 本技能在每次cron执行时重新抓取帖子，一旦发现新增的限时码就立即推送。

## 执行步骤

1. 使用 `exec` 工具运行：`garden-world --now`
2. 解读输出：
   - `STATUS: ok` — 成功。查看 `NOTIFY:` 行。
   - `STATUS: no_today_post_found` — 今日帖子尚未发布，稍后重试。
   - `STATUS: no_new_code_due` — 无新增需推送的码（已推送或限时码尚未公布）。
   - `STATUS: auth_required`（stderr）— 登录过期。提醒用户运行 `garden-world login`。若有 `QR_IMAGE:` 行，可将截图推送给用户。
3. 如有 `NOTIFY:` 行，逐条发送到当前会话渠道。
4. `INFO: windows=` 行显示限时码状态：`✓` 表示码已获取，`?` 表示尚未公布。
5. 如需强制重新搜索（跳过缓存URL），运行 `garden-world --now --force-refresh`。

## 环境变量（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 推送状态文件路径 |
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 小红书搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 搜索候选帖最大数量 |

## 建议的定时任务

每5分钟执行一次（19:00-23:30 CST），覆盖所有限时码窗口：
```bash
openclaw cron add \
  --name "garden-world-codes" \
  --cron "*/5 19-23 * * *" \
  --tz "Asia/Shanghai" \
  --session isolated \
  --message "执行 garden-world skill，运行兑换码抓取并把所有 NOTIFY 结果发到微信" \
  --announce \
  --channel wechat
```

## 注意事项

- 周码是可选的，不是每天都有。
- 限时码通常有3个，每个有15分钟窗口，分布在约19:58、21:26、22:14左右（每天不同）。
- 首次发现帖子时限时码可能为空（显示为 `?`），这是正常的 — 后续cron运行会自动检测更新。
- **信任博主机制**：成功解析的高质量博文会记录博主ID，下次搜索时优先选择该博主的帖子（+5评分加成），避免每天从零搜索。
- 支持7种博文格式变体（点分时间如`20.12`、`限时兑换码+兑换码N:`、`限时(HH:MM-HH:MM)兑换码:`、`限时码一/二/三` 中文序号等）。
- `INFO: trusted_bloggers=` 行显示当前信任博主及其成功次数。
```
