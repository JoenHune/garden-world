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

## 执行步骤

1. 运行 `garden-world --now`
2. 解读输出：
   - `STATUS: ok` — 成功，查看 `NOTIFY:` 行
   - `STATUS: no_today_post_found` — 今日帖子尚未发布，稍后重试
   - `STATUS: no_new_code_due` — 无新增需推送的码
   - `STATUS: auth_required` — 登录过期，**必须执行「登录流程」**
3. 如有 `NOTIFY:` 行，逐条发送到当前会话
4. `INFO: windows=` 行显示限时码状态：`✓` 已获取，`?` 尚未公布
5. 强制重新搜索：`garden-world --now --force-refresh`

## 登录流程

当输出 `STATUS: auth_required` 时：

1. **后台启动**：`exec garden-world login --headless`（阻塞最长 2 分钟）
2. **poll 获取二维码**：看到 `QR_IMAGE: <文件路径>` 后，将该图片文件直接发送给用户
3. **提示用户**：「请用小红书 App 或微信扫描二维码登录」
4. **等待结果**：继续 poll
   - `LOGIN_OK:` → 登录成功，重新运行 `garden-world --now`
   - `LOGIN_FAIL:` → 超时，从步骤 1 重新开始

> 优先用 `QR_IMAGE:` 文件路径发送图片，`QR_BASE64:` 是备用 base64 编码。

## 核心工作流

本技能采用**渐进式检测**：

1. **首次运行** — 搜索小红书，找到当天帖子，推送通用码（+周码），缓存帖子 URL
2. **后续运行** — 直接访问缓存 URL（快速路径），检测新的限时码
3. **渐进推送** — 限时码在时间窗开始后 5 分钟且值非空时推送，已推送不重复

## 建议的定时任务

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

详细的输出格式、环境变量、注意事项见 [reference.md](reference.md)。
