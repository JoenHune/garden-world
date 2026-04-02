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

1. 运行 `garden-world --now --auto-login`
2. 解读输出：
   - `STATUS: ok` — 成功，查看 `NOTIFY:` 行
   - `STATUS: no_today_post_found` — 今日帖子尚未发布，稍后重试
   - `STATUS: no_new_code_due` — 无新增需推送的码
   - `QR_IMAGE: <路径>` — 登录过期，二维码已自动生成，**立即用 `message` 工具的 `media` 参数转发图片给用户**
   - `LOGIN_OK:` — 用户扫码成功，程序自动重试获取兑换码
   - `LOGIN_FAIL:` / `STATUS: login_failed` — 超时，告诉用户稍后重试
3. 如有 `NOTIFY:` 行，逐条发送到当前会话
4. `INFO: windows=` 行显示限时码状态：`✓` 已获取，`?` 尚未公布
5. 强制重新搜索：`garden-world --now --force-refresh --auto-login`

> **`--auto-login`** 会在登录过期时自动启动 headless 登录流程并输出 QR 二维码，
> 登录成功后自动重试 `--now`，**省去一次 LLM 往返，大幅缩短二维码送达时间**。

## 登录流程（自动模式）

使用 `--auto-login` 时，登录流程完全内联在 `--now` 中：

1. 检测到 `auth_required` → 自动启动 headless 浏览器
2. 输出 `QR_IMAGE: <文件路径>` → **立即**用 `message(action='send', media='<文件路径>')` 发送给用户
3. 输出 `LOGIN_WAIT:` 行 → 告知用户当前进度
4. 输出 `LOGIN_OK:` → 自动重新获取兑换码

> 必须用 `media` 参数传文件路径，不要用 `buffer` 或 base64。
> 每 90 秒自动刷新二维码并重新输出 `QR_IMAGE:`，注意转发最新的图片。
> 小红书二维码约 90 秒过期，**必须立即转发，不能延迟**。

## 手动登录流程（备用）

如需手动登录（不用 `--auto-login`）：

1. `garden-world login --headless`（阻塞最长 4 分钟）
2. 看到 `QR_IMAGE:` 后转发图片给用户
3. `LOGIN_OK:` 后重新运行 `garden-world --now`

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
