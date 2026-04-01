# 🌸 garden-world

自动抓取手游**「我的花园世界」**每日小红书兑换码，解析通用码、周码和限时码，渐进式检测限时码更新并推送到微信。

专为 [QClaw](https://qclaw.qq.com/) / [OpenClaw](https://github.com/nicepkg/openclaw) 设计的 Skill，也可独立运行。

## 特性

- **渐进式限时码检测** — 首次发现帖子时记录时间窗，后续 cron 直接重新访问同一帖子，一旦博主填入限时码立即推送
- **信任博主系统** — 自动记录高质量博主，下次搜索时给予评分加成（+5），避免每天从零搜索
- **多格式兼容** — 支持7种博文格式变体（标准/点分时间/中文序号/限时兑换码+编号/限时(时间)兑换码/本周通码 等）
- **小红书站内搜索** — 通过 [Playwright](https://playwright.dev/) 自动化浏览器直接在小红书站内搜索
- **SSR 数据提取** — 从 `__INITIAL_STATE__` 提取内容和作者信息，不依赖 DOM 渲染
- **一次扫码长期使用** — 首次 `garden-world login` 扫码登录，凭证保存在 Chromium 持久化会话目录；后续 cron 全程 headless
- **闭环登录验证** — 登录检测基于搜索结果是否可见，不依赖 cookie 或 CSS 选择器
- **幂等推送** — 本地状态文件记录已发送的码，重复执行不会重复通知
- **原子状态写入** — 状态文件先写 `.tmp` 再 `rename`，防断电损坏

## 工作原理

```
首次使用：garden-world login → 扫码登录 → 凭证保存到本地

每天 19:00 起，每 5 分钟执行一次
         │
         ▼
  今日帖子URL已缓存？  ─是→  快速路径：直接访问缓存URL
         │否                       │
         ▼                         ▼
  Playwright 搜索小红书        Playwright 获取帖子正文
         │                         │
         ▼                         │
  评分选出最佳候选帖      ◄────────┘
         │
         ▼
  正则解析 → 通用码 / 周码(可选) / 限时码×3（含时间窗）
         │
         ▼
  检查本地状态：哪些码还没发？哪些限时码已到时间且有实际值？
         │
         ▼
  输出 NOTIFY: 行 → QClaw 转发到微信
```

### 渐进式限时码检测

博主通常在 19:00 左右发帖，此时帖子中只有限时码的时间窗（如 `19:58~20:13`），但限时码内容为空。随后在每个时间窗开始时编辑帖子填入实际限时码。

本工具通过缓存帖子 URL 实现快速检测：

```
19:00  ─── 搜索发现帖子，推送通用码；限时码1/2/3 时间窗已知但码为空
19:58  ─── 限时码1 时间窗开始，博主更新帖子
20:03  ─── cron: 重新访问帖子 → 发现限时码1已填入 → ✅ 推送
21:26  ─── 限时码2 时间窗开始
21:31  ─── cron: 重新访问 → 发现限时码2 → ✅ 推送
22:14  ─── 限时码3 时间窗开始
22:19  ─── cron: 重新访问 → 发现限时码3 → ✅ 推送
```

### 博文格式示例

支持多种博主发布风格（7种格式变体）：

**标准格式 (Format A)**：
```
3.31我的花园世界兑换码+账号福利攻略
周码（4/1日前有效）:指尖花开治愈常在
今日通用 :四季轮转花事不断
限时码1（19:58～20:13）:露珠轻颤花信已至
限时码2（21:26～21:41）:直播带路种花不迷
限时码3（22:14～22:29）:同耕一方共享花开
```

**其他已兼容格式**：`(HH:MM-HH:MM):CODE`（无"限时码N"前缀）、`兑换码1:CODE`（编号式）、`第一个:CODE`（中文序号）、`限时码一:CODE`（中文数字）、`限时(12:00-14:00)兑换码:`（header+裸行）、`20.12-20.27`（点分时间）、`周兑换码`/`本周通码`（周码变体）

### 信任博主机制

工具自动记录高质量博文的作者信息（user_id + nickname）。评分阈值 ≥ 7 的博主会被记录到状态文件中。

后续搜索时，已知博主的帖子获得 +5 评分加成，确保在多个候选帖中优先选择。博主记录自动清理：超过 14 天未出现的博主会被移除，最多保留 10 个。

## 快速开始

### 前提

- Python 3.10+（macOS / Linux / Windows 均可）
- [Playwright](https://playwright.dev/python/) + Chromium 浏览器

### 1. 克隆并安装

```bash
git clone https://github.com/JoenHune/garden-world.git
cd garden-world
pip install -e .
python3 -m playwright install chromium
```

### 2. 首次登录小红书

**方式一：本地有显示器**
```bash
garden-world login
```
弹出浏览器窗口，用小红书 App 扫码登录。

**方式二：远程/无 GUI 环境（推荐用于 QClaw）**
```bash
garden-world login --headless
```
无窗口模式。命令会在标准输出打印：
- `QR_IMAGE: <path>` — 二维码截图文件路径
- `QR_BASE64: <base64>` — 二维码 PNG 的 base64 编码（备用，优先用 `QR_IMAGE` 文件路径发送图片）
- `LOGIN_WAIT:` — 等待扫码中
- `LOGIN_OK:` — 登录成功
- `LOGIN_FAIL:` — 超时（2分钟内未扫码）

所有输出均即时 flush，适合 QClaw 的 `exec` + `poll` 模式：后台启动命令，立即 poll 获取 QR 图片发送给用户。

> 凭证保存到 `.garden_world/browser_profile/`（Chromium 持久化会话目录），一般可持续数天到数周。
> 凭证过期后 `garden-world --now` 会输出 `STATUS: auth_required`，需重新登录。

### 3. 手动运行一次

```bash
garden-world --now
```

输出示例：
```
STATUS: ok date=2026-03-31 source=https://www.xiaohongshu.com/explore/...
NOTIFY: 【通用码】四季轮转花事不断
来源：3.31我的花园世界兑换码+账号福利攻略
NOTIFY: 【周码】指尖花开治愈常在
INFO: windows=1:19:58-20:13✓, 2:21:26-21:41?, 3:22:14-22:29?
SCHEDULE_HINT: QClaw cron 每5分钟运行一次；首次19:00开始
```

`INFO: windows=` 行中 `✓` 表示限时码已获取，`?` 表示尚未公布。

### 4. 配置定时任务

**方式一：安装为 QClaw Skill（推荐）**

```bash
cp -r skills/garden-world ~/.openclaw/skills/garden-world
```

然后在 QClaw 中对话：「帮我设置花园世界兑换码定时抓取」

**方式二：手动添加 cron**

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

## 项目结构

```
garden-world/
├── pyproject.toml                  # 项目元数据
├── skills/garden-world/SKILL.md    # QClaw/OpenClaw Skill 定义
├── src/garden_world/
│   ├── __init__.py
│   ├── __main__.py                 # python -m 入口
│   ├── browser.py                  # Playwright 浏览器自动化 + SSR 提取
│   ├── config.py                   # 配置（支持环境变量）
│   ├── models.py                   # 数据模型
│   └── main.py                     # 核心逻辑（渐进式检测 + 信任博主）
└── tests/
    ├── test_parser.py              # 解析器测试（11个格式用例）
    ├── test_progressive.py         # 渐进式推送模拟测试
    └── test_trusted_bloggers.py    # 信任博主管理测试
```

## 命令行参数

| 命令/参数 | 说明 |
|------|------|
| `login` | 扫码登录小红书，保存凭证 |
| `login --headless` | 无窗口模式登录，QR 码通过 stdout 输出 |
| `--now` | 立即执行一次，检查当前时间是否有新码可发 |
| `--force-refresh` | 强制重新搜索（跳过缓存URL快速路径） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 状态文件路径 |
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_CHANNEL` | `wechat` | 通知渠道 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 最大候选帖子数 |

## 可靠性设计

- **快速路径** — 首次搜索后缓存帖子URL，后续 cron 直接访问单个页面，无需重复搜索
- **信任博主** — 高质量博主自动记录，评分加成 +5，14天未见自动清理，最多记录10人
- **SSR 提取** — 从 `__INITIAL_STATE__` 提取笔记内容和作者信息，反爬也能工作
- **真实浏览器** — Playwright 驱动 Headless Chromium，与真人浏览行为一致
- **持久化会话** — Chromium `launch_persistent_context` 保留完整浏览器状态（cookies + localStorage + IndexedDB）
- **闭环登录验证** — 登录检测不依赖 cookie 或 CSS 选择器，直接验证搜索结果是否可见
- **评分选帖** — 多候选帖评分机制：通用码(+3)、周码(+1)、时间窗(+1)、实际限时码(+2)、信任博主(+5)
- **多格式解析** — 7种博文格式变体全覆盖，自动适应不同博主的发布风格
- **资源优化** — headless 模式下自动屏蔽图片/字体/视频，加速加载
- **原子写入** — 状态文件先写 `.tmp` 再 `rename`，防断电损坏
- **状态自清理** — 自动清除 7 天前的推送记录

## 许可

MIT
