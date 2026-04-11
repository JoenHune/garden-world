# garden-world 参考文档

## 结构化输出格式

所有输出前缀在 stdout 上即时 flush，适合 `exec` + `poll` 模式。

| 前缀 | 含义 |
|------|------|
| `STATUS:` | 执行结果状态码（`ok` / `auth_required` / `no_today_post_found` / `no_new_code_due` / `error`） |
| `NOTIFY:` | 需发送给用户的兑换码消息 |
| `INFO:` | 诊断信息（时间窗状态、信任博主等） |
| `SCHEDULE_HINT:` | cron 调度建议 |
| `ACTION:` | 需要执行的操作指引 |
| `LOGIN_STARTING:` | 登录浏览器启动中 |
| `LOGIN_WAIT:` | 等待扫码中 |
| `LOGIN_OK:` | 登录成功 |
| `LOGIN_FAIL:` | 登录超时或失败 |
| `QR_IMAGE:` | 二维码截图文件路径（优先用此路径发送图片） |
| `QR_BASE64:` | 二维码 PNG 的 base64 编码（备用） |

## 输出示例

```
STATUS: ok date=2026-03-31 source=https://www.xiaohongshu.com/explore/...
NOTIFY: 【通用码】四季轮转花事不断
来源：3.31我的花园世界兑换码+账号福利攻略
NOTIFY: 【周码】指尖花开治愈常在
INFO: windows=1:19:58-20:13✓, 2:21:26-21:41?, 3:22:14-22:29?
INFO: trusted_bloggers=花园小助手(3次)
SCHEDULE_HINT: QClaw cron 每5分钟运行一次；首次19:00开始
```

## 登录流程详细输出

```
LOGIN_STARTING: 正在启动浏览器，请稍候…
LOGIN_STARTING: 浏览器已启动，正在加载登录页面…
QR_IMAGE: /path/to/.garden_world/browser_profile/qr.png
QR_BASE64: iVBORw0KGgoAAAANSUh...
LOGIN_WAIT: 请用小红书 App 或微信扫描上方二维码登录（超时4分钟）。
LOGIN_WAIT: 等待扫码中… 剩余 225 秒
LOGIN_WAIT: 等待扫码中… 剩余 210 秒
...
LOGIN_WAIT: 二维码可能已刷新，正在重新截图…
QR_IMAGE: /path/to/.garden_world/browser_profile/qr.png
QR_BASE64: iVBORw0KGgoAAAANSUh...
LOGIN_WAIT: 新二维码已生成，请重新扫码（剩余 135 秒）
...
LOGIN_OK: 登录成功！浏览器配置已保存到 .garden_world/browser_profile
```

> 每 15 秒输出一次倒计时状态，每 90 秒自动刷新二维码并重新输出 `QR_IMAGE:`。

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
    poll → QR_IMAGE → 立即将图片文件发送给用户
              │
              ▼
    poll → LOGIN_WAIT (倒计时) → 告知用户进度
              │
              ├── 90秒后 → 新 QR_IMAGE → 重新发送图片
              │
              ▼
    poll → LOGIN_OK → garden-world --now（重新执行）
```

## 命令行参数

| 命令/参数 | 说明 |
|------|------|
| `garden-world daemon` | 启动守护进程（内置调度 + 微信推送 + 自动回复） |
| `garden-world bind` | 绑定微信 ClawBot 账号（QR 码扫描） |
| `garden-world login [--headless]` | 扫码登录小红书 |
| `garden-world push [--force]` | 从缓存推送今日码到微信 |
| `garden-world enrich` | 多源搜索补全时间窗口并推送 |
| `garden-world import-wechat` | 从 OpenClaw 导入微信账号 |
| `garden-world --now` | 立即执行一次，检查当前时间是否有新码可发 |
| `garden-world --now --force-refresh` | 强制重新搜索（跳过缓存 URL 快速路径） |
| `garden-world --now --auto-login` | 登录过期时自动 headless 登录 |
| `-v` / `--verbose` | 开启 DEBUG 日志 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 推送状态文件路径 |
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 小红书搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 搜索候选帖最大数量 |
| `GARDEN_WORLD_WECHAT_CONFIG` | `.garden_world/wechat.json` | 微信账号配置路径 |

## Daemon 模式

`garden-world daemon` 启动后会自动完成以下流程：

1. **19:05** — 搜索帖子，推送通用码 + 周码
2. **19:00–23:30** — 每 5 分钟检测限时码时间窗
3. **窗口开始 +5min** — 自动抓取限时码并推送
4. **3 码全齐** — 当日停止轮询（early exit）

Daemon 同时运行：
- **AutoReply 线程** — 持续 long-poll getUpdates，关键词匹配自动回复
- **context_token 刷新** — 每次 getUpdates 自动更新 token，确保推送不中断

## 微信推送

### 绑定流程

```bash
garden-world bind
```

1. 终端显示 QR 码 → 微信扫码 → 确认
2. 向 Bot 发送任意消息（如"你好"）以激活推送
3. 绑定完成，确认消息自动发送

### context_token 生命周期

iLink API 要求每次发送消息时带上 `context_token`。该 token 随每条用户消息下发：

- **Daemon 模式** — AutoReply 持续轮询，token 始终新鲜 ✅
- **一次性命令** — `push`/`enrich` 启动时主动 refresh ✅
- **发送失败 ret=-2** — 自动 refresh → 重试一次 ✅
- **长时间无消息** — token 可能过期，需向 Bot 发一条消息激活

### 多账号

最多 5 个账号，推送时广播到所有已绑定账号。

## 博主评分系统

### 四维评分

| 维度 | 权重 | 含义 |
|------|------|------|
| `timeliness` | ×2 | 帖子发布的时效性 |
| `reliability` | ×3 | 码与其他源交叉验证一致率 |
| `format` | ×1 | 博文格式规范度 |
| `time_window` | ×2 | 是否写出时间窗口 |

信任加成 = `timeliness×2 + reliability×3 + format×1 + time_window×2`（满分 +8）

### 降权机制

- 不写时间窗口的博主 `time_window_score` 持续下降（滑动平均 α=0.3）
- 超过时间窗仍未填码 → `success_count` 直接扣减
- 14 天未出现 → 自动清理
