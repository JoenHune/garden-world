# 🌸 garden-world

自动抓取手游**「我的花园世界」**每日小红书兑换码，解析通用码、周码和限时码，渐进式检测限时码更新并推送。

本项目是一个 [Anthropic Skill](https://docs.anthropic.com/en/docs/agents-and-tools/computer-use/skill-library)，也可独立运行为命令行工具。

## 特性

- **渐进式限时码检测** — 首次发现帖子时记录时间窗，后续 cron 直接重新访问同一帖子，一旦博主填入限时码立即推送
- **信任博主系统** — 自动记录高质量博主，下次搜索时给予评分加成（+5），避免每天从零搜索
- **多格式兼容** — 支持 7 种博文格式变体（标准/点分时间/中文序号/限时兑换码+编号/限时(时间)兑换码/本周通码 等）
- **小红书站内搜索** — 通过 [Playwright](https://playwright.dev/) 自动化浏览器直接在小红书站内搜索
- **SSR 数据提取** — 从 `__INITIAL_STATE__` 提取内容和作者信息，不依赖 DOM 渲染
- **一次扫码长期使用** — 首次 `garden-world login` 扫码登录，凭证保存在 Chromium 持久化会话目录
- **闭环登录验证** — 登录检测基于搜索结果是否可见，不依赖 cookie 或 CSS 选择器
- **幂等推送** — 本地状态文件记录已发送的码，重复执行不会重复通知
- **原子状态写入** — 状态文件先写 `.tmp` 再 `rename`，防断电损坏

## 快速开始

### 前提

- Python 3.9+（macOS / Linux / Windows 均可）
- [Playwright](https://playwright.dev/python/) + Chromium 浏览器

### 1. 安装

**方式一：一键安装**

```bash
git clone https://github.com/JoenHune/garden-world.git
cd garden-world
bash scripts/install.sh
```

**方式二：手动安装**

```bash
git clone https://github.com/JoenHune/garden-world.git
cd garden-world
pip install -e .
python3 -m playwright install chromium
```

### 2. 首次登录小红书

**本地有显示器：**

```bash
garden-world login
```

**远程/无 GUI 环境（headless）：**

```bash
garden-world login --headless
```

输出说明：
- `QR_IMAGE: <path>` — 二维码截图文件路径
- `QR_BASE64: <base64>` — 二维码 PNG 的 base64（备用）
- `LOGIN_OK:` — 登录成功
- `LOGIN_FAIL:` — 超时

> 凭证保存到 `.garden_world/browser_profile/`，一般可持续数天到数周。
> 过期后 `garden-world --now` 会输出 `STATUS: auth_required`，需重新登录。

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

### 4. 配置定时任务（可选）

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
  输出 NOTIFY: 行
```

### 渐进式限时码检测

博主通常在 19:00 左右发帖，此时帖子中只有限时码的时间窗（如 `19:58~20:13`），但限时码内容为空。随后在每个时间窗开始时编辑帖子填入实际限时码。

```
19:00  ─── 搜索发现帖子，推送通用码；限时码1/2/3 时间窗已知但码为空
19:58  ─── 限时码1 时间窗开始，博主更新帖子
20:03  ─── cron: 重新访问帖子 → 发现限时码1已填入 → ✅ 推送
21:26  ─── 限时码2 时间窗开始
21:31  ─── cron: 重新访问 → 发现限时码2 → ✅ 推送
22:14  ─── 限时码3 时间窗开始
22:19  ─── cron: 重新访问 → 发现限时码3 → ✅ 推送
```

## 项目结构

```
garden-world/
├── SKILL.md                   # Anthropic Skill 定义（核心指令）
├── reference.md               # 详细参考文档（输出格式、环境变量等）
├── LICENSE                    # MIT-0 许可证
├── README.md                  # 项目说明（本文件）
├── pyproject.toml             # Python 项目元数据 + pytest 配置
├── scripts/
│   └── install.sh             # 一键安装脚本
├── src/
│   └── garden_world/          # Python 包源码
│       ├── __init__.py
│       ├── __main__.py        # python -m garden_world 入口
│       ├── browser.py         # Playwright 浏览器自动化 + SSR 提取
│       ├── config.py          # 配置（支持环境变量）
│       ├── main.py            # 核心逻辑（解析、渐进检测、信任博主）
│       └── models.py          # 数据模型
└── tests/
    ├── conftest.py            # pytest 共享配置 & markers
    ├── unit/                  # 纯逻辑单元测试（无需浏览器）
    │   ├── test_parser.py     # 解析器测试（11 个格式用例）
    │   ├── test_progressive.py # 渐进式推送模拟测试
    │   └── test_trusted_bloggers.py  # 信任博主管理测试
    ├── integration/           # 集成测试（需浏览器 + 网络）
    │   ├── test_batch_dates.py     # 多日期批量搜索
    │   ├── test_headless_qr.py     # 无头模式 QR 截图
    │   └── test_search.py          # E2E 搜索流程
    └── debug/                 # 手动调试脚本（非自动化测试）
        ├── debug_cookies.py
        └── debug_raw_notes.py
```

## Skill 文件说明

| 文件 | 用途 |
|------|------|
| [SKILL.md](SKILL.md) | Anthropic Skill 定义文件，包含执行步骤、登录流程、核心工作流 |
| [reference.md](reference.md) | 参考文档：结构化输出格式、命令行参数、环境变量、渐进式检测原理 |
| [scripts/install.sh](scripts/install.sh) | 安装脚本：`pip install -e .` + Playwright Chromium |

## 命令行参数

| 命令/参数 | 说明 |
|------|------|
| `login` | 扫码登录小红书，保存凭证 |
| `login --headless` | 无窗口模式登录，QR 码通过 stdout 输出 |
| `--now` | 立即执行一次，检查当前时间是否有新码可发 |
| `--force-refresh` | 强制重新搜索（跳过缓存 URL 快速路径） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 状态文件路径 |
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_CHANNEL` | `wechat` | 通知渠道 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 最大候选帖子数 |

## 测试

```bash
# 安装测试依赖
pip install -e ".[test]"

# 运行单元测试（无需浏览器）
pytest tests/unit/ -v

# 运行全部测试（包括集成测试，需已登录的浏览器会话）
pytest -v

# 仅运行集成测试
pytest -m integration -v
```

## 可靠性设计

- **快速路径** — 首次搜索后缓存帖子 URL，后续 cron 直接访问单个页面
- **信任博主** — 高质量博主自动记录，评分加成 +5，14 天未见自动清理
- **SSR 提取** — 从 `__INITIAL_STATE__` 提取笔记内容，反爬也能工作
- **真实浏览器** — Playwright 驱动 Headless Chromium，与真人浏览行为一致
- **持久化会话** — `launch_persistent_context` 保留完整浏览器状态
- **闭环登录验证** — 直接验证搜索结果是否可见
- **评分选帖** — 多候选帖评分：通用码(+3)、周码(+1)、时间窗(+1)、实际限时码(+2)、信任博主(+5)
- **多格式解析** — 7 种博文格式全覆盖
- **资源优化** — headless 模式下自动屏蔽图片/字体/视频
- **原子写入** — 状态文件先写 `.tmp` 再 `rename`
- **状态自清理** — 自动清除 7 天前的推送记录

## 许可

[MIT-0](LICENSE)
