---
name: garden-world
description: >
  自动抓取手游「我的花园世界」每日小红书兑换码（通用码+周码+限时码），
  渐进式检测限时码更新并在适当时机推送。当用户提到「花园世界兑换码」
  「通码」「周码」「限时码」时，使用此技能。
version: 0.1.0
compatibility: >
  Python >=3.9, Playwright >=1.40 + Chromium。
  macOS / Linux / Windows 均可。需要网络访问 xiaohongshu.com。
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - garden-world
    emoji: "🌸"
---

# garden-world

## 安装

```bash
pip install /path/to/garden-world   # 或 pip install -e /path/to/garden-world
python3 -m playwright install chromium
```

## 登录流程

小红书搜索需要登录。当 `garden-world --now` 输出 `STATUS: auth_required` 时，
必须先完成登录。

### 步骤 1：后台启动登录命令

```bash
garden-world login --headless
```

> 该命令会阻塞等待扫码（最长 2 分钟），必须用 `exec` 以后台方式运行。
> `--headless` 不弹出浏览器窗口，二维码通过标准输出传递。

### 步骤 2：poll 获取二维码

立即 `poll` 该命令的输出，会看到：

```
LOGIN_STARTING: 正在启动浏览器，请稍候…
QR_IMAGE: /path/to/.garden_world/browser_profile/qr.png
QR_BASE64: iVBORw0KGgoAAAANSUh...（base64 字符串）
LOGIN_WAIT: 请用小红书 App 或微信扫描上方二维码登录。
```

### 步骤 3：将二维码发送给用户

**必须将二维码图片发送到当前会话**，让用户能扫码。

使用 `QR_IMAGE:` 后面的文件路径，将该图片文件直接发送给用户：

```
QR_IMAGE: /path/to/.garden_world/browser_profile/qr.png
         ↑ 将此路径的图片文件发送到对话中
```

> `QR_BASE64:` 行是同一张图片的 base64 编码，作为备用方案。
> 优先使用文件路径发送图片，避免 base64 文本被直接显示给用户。

同时告知用户：「请用小红书 App 或微信扫描二维码登录，登录后我会自动继续。」

### 步骤 4：等待登录完成

继续 `poll` 登录命令的输出：
- `LOGIN_OK:` — 登录成功，重新执行 `garden-world --now`
- `LOGIN_FAIL:` — 登录超时，从步骤 1 重新开始

### 登录流程图

```
garden-world --now
       │
       ├── STATUS: ok → 处理 NOTIFY 行
       │
       └── STATUS: auth_required
              │
              ▼
    exec(后台): garden-world login --headless
              │
              ▼
    poll → QR_IMAGE + QR_BASE64 → 发送给用户
              │
              ▼
    poll → LOGIN_OK → garden-world --now（重新执行）
```

## 核心工作流

本技能采用**渐进式检测**架构：

1. **首次运行** — 搜索小红书，找到当天兑换码帖子，推送通用码（和周码），缓存帖子 URL
2. **后续运行** — 直接访问缓存 URL（快速路径，无需搜索），检测新的限时码
3. **渐进推送** — 限时码在其时间窗开始后 5 分钟且代码值非空时推送，已推送不重复

> 博主通常 19:00 左右发帖，帖子先有时间窗但限时码为空，
> 之后在每个时间窗开始时编辑帖子填入限时码。
> 本技能每次 cron 执行时重新抓取帖子，发现新增码立即推送。

## 执行步骤

1. 运行 `garden-world --now`
2. 解读输出：
   - `STATUS: ok` — 成功，查看 `NOTIFY:` 行
   - `STATUS: no_today_post_found` — 今日帖子尚未发布，稍后重试
   - `STATUS: no_new_code_due` — 无新增需推送的码
   - `STATUS: auth_required` — 登录过期，**执行上方「登录流程」**
3. 如有 `NOTIFY:` 行，逐条发送到当前会话
4. `INFO: windows=` 行显示限时码状态：`✓` 已获取，`?` 尚未公布
5. 强制重新搜索：`garden-world --now --force-refresh`

## 结构化输出格式

| 前缀 | 含义 |
|------|------|
| `STATUS:` | 执行结果状态码 |
| `NOTIFY:` | 需发送给用户的兑换码消息 |
| `INFO:` | 诊断信息（时间窗状态、信任博主等） |
| `SCHEDULE_HINT:` | cron 调度建议 |
| `ACTION:` | 需要执行的操作指引 |
| `LOGIN_STARTING:` | 登录浏览器启动中 |
| `LOGIN_WAIT:` | 等待扫码中 |
| `LOGIN_OK:` | 登录成功 |
| `LOGIN_FAIL:` | 登录超时或失败 |
| `QR_IMAGE:` | 二维码截图文件路径 |
| `QR_BASE64:` | 二维码 PNG 的 base64 编码 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 推送状态文件路径 |
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 小红书搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 搜索候选帖最大数量 |

## 建议的定时任务

每 5 分钟执行一次（19:00–23:30 CST），覆盖所有限时码窗口：

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

- 周码是可选的，不是每天都有
- 限时码通常有 3 个，每个有 15 分钟窗口，分布在约 19:58、21:26、22:14 左右（每天不同）
- 首次发现帖子时限时码可能为空（显示为 `?`），后续 cron 会自动检测更新
- **信任博主机制**：高质量博文的作者 ID 被记录，下次搜索时优先选择（+5 评分加成）
- 支持 7 种博文格式变体
- `INFO: trusted_bloggers=` 行显示当前信任博主及其成功次数
