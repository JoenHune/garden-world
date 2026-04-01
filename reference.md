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
| `garden-world --now` | 立即执行一次，检查当前时间是否有新码可发 |
| `garden-world --now --force-refresh` | 强制重新搜索（跳过缓存 URL 快速路径） |
| `garden-world login` | 扫码登录小红书（弹出浏览器窗口） |
| `garden-world login --headless` | 无窗口模式登录，QR 码通过 stdout 输出 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 推送状态文件路径 |
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 小红书搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 搜索候选帖最大数量 |

## 渐进式检测原理

博主通常在 19:00 左右发帖，此时帖子中只有限时码的时间窗（如 `19:58~20:13`），
但限时码内容为空。随后在每个时间窗开始时编辑帖子填入实际限时码。

```
19:00  ─── 搜索发现帖子，推送通用码；限时码 1/2/3 时间窗已知但码为空
19:58  ─── 限时码 1 时间窗开始，博主更新帖子
20:03  ─── cron: 重新访问帖子 → 发现限时码 1 已填入 → ✅ 推送
21:26  ─── 限时码 2 时间窗开始
21:31  ─── cron: 重新访问 → 发现限时码 2 → ✅ 推送
22:14  ─── 限时码 3 时间窗开始
22:19  ─── cron: 重新访问 → 发现限时码 3 → ✅ 推送
```

## 注意事项

- 周码是可选的，不是每天都有
- 限时码通常有 3 个，每个有 15 分钟窗口，分布在约 19:58、21:26、22:14 左右（每天不同）
- 首次发现帖子时限时码可能为空（显示为 `?`），后续 cron 会自动检测更新
- **信任博主机制**：高质量博文的作者 ID 被记录，下次搜索时优先选择（+5 评分加成），超 14 天未出现自动清理，最多保留 10 个
- 支持 7 种博文格式变体（点分时间如 `20.12`、`限时兑换码+兑换码N:`、`限时(HH:MM-HH:MM)兑换码:`、`限时码一/二/三` 中文序号等）
- `INFO: trusted_bloggers=` 行显示当前信任博主及其成功次数
