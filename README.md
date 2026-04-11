# 🌸 garden-world

自动抓取手游**「我的花园世界」**每日小红书兑换码，解析通用码、周码和限时码，渐进式检测限时码更新并推送到微信。

## 特性

- **Daemon 守护模式** — `garden-world daemon` 一键启动，内置调度器 19:00–23:30 自动轮询，3 码全齐自动停止
- **微信直推** — 基于 iLink API 推送到微信，QR 码绑定 (`garden-world bind`)，多账号支持
- **多源交叉验证** — 搜索 Top N 候选帖，≥2 来源一致为高置信度，单源附带警告
- **时间窗口补全** — 主博主缺时间窗时，自动从其他博主帖子中搜索精确窗口
- **四维博主评分** — timeliness / reliability / format / time_window，不写时间的博主自动降权
- **渐进式限时码检测** — 首次记录时间窗，后续 cron 直接重新访问缓存帖子
- **8 种格式兼容** — 支持 Format A–H 博文格式变体
- **小红书 SSR 提取** — 从 `__INITIAL_STATE__` 解析内容，不依赖 DOM
- **一次扫码长期使用** — Chromium 持久化会话，凭证保留数周
- **自动回复** — daemon 模式下关键词匹配回复微信消息
- **幂等推送 + 原子状态写入** — 不会重复推送，断电不损坏

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

```bash
garden-world login            # 本地有显示器
garden-world login --headless  # 远程/无 GUI 环境
```

### 3. 绑定微信推送

```bash
garden-world bind
```

用微信扫描终端 QR 码 → 确认 → 向 Bot 发送一条消息以激活推送。

### 4. 启动 Daemon（推荐）

```bash
garden-world daemon
```

Daemon 会自动完成以下流程：

```
19:00        → 等待开始
19:05        → 搜索帖子，推送通用码 + 周码
19:05–23:30  → 每 5 分钟检测限时码时间窗
窗口+5min    → 自动抓取限时码并推送
3码全齐      → 当日停止轮询
```

### 5. 手动运行（一次性）

```bash
garden-world --now                # 检查当前有无新码
garden-world --now --force-refresh # 强制重新搜索
garden-world push --force          # 推送缓存码到微信
garden-world enrich                # 多源搜索补全时间窗口
```

## 工作原理

```
garden-world daemon
       │
       ├── 19:05 — 搜索小红书 Top N 帖子
       │          ├── 逐一解析（Format A–H）
       │          ├── 多源交叉验证 → 投票取最佳码
       │          ├── 时间窗口补全（enrich）
       │          └── ✅ 推送通用码 + 周码
       │
       ├── 每5分钟 — 重新访问缓存帖子 URL（快速路径）
       │          ├── 检测限时码是否已填入
       │          └── 窗口 +5min 且有值 → ✅ 推送
       │
       └── 3码全齐 or 23:30 → 停止当日轮询
```

### 多源交叉验证

```
搜索结果(8个帖子) → 解析+评分 → 博主A(12分) + 博主B(9分) + …
                                   │
                  ┌────────────────┘
                  ▼
         交叉验证: 通用码(3源一致) → ✅ 高置信
                   限时码1(2源一致) → ✅ 高置信
                   限时码2(仅1源) → ⚠ 低置信
                  │
                  ▼
         时间窗补全: 博主A有码无时间 + 博主C有时间无码 → 合并
```

## 项目结构

```
garden-world/
├── CHANGELOG.md               # 版本变更记录
├── SKILL.md                   # Anthropic Skill 定义（核心指令）
├── reference.md               # 详细参考文档（输出格式、环境变量等）
├── LICENSE                    # MIT-0 许可证
├── README.md                  # 项目说明（本文件）
├── pyproject.toml             # Python 项目元数据 + pytest 配置
├── scripts/
│   ├── install.sh             # 一键安装脚本
│   ├── bind.sh                # 微信绑定快捷脚本
│   ├── push.sh                # 推送快捷脚本
│   └── run.sh                 # daemon 启动脚本
├── src/
│   └── garden_world/
│       ├── __init__.py
│       ├── __main__.py        # python -m garden_world 入口
│       ├── autoreply.py       # 微信自动回复（关键词 → 模板）
│       ├── browser.py         # Playwright 浏览器自动化 + SSR 提取
│       ├── config.py          # 配置（支持环境变量）
│       ├── main.py            # 核心逻辑（解析、交叉验证、推送、CLI）
│       ├── models.py          # 数据模型（CodeBundle, BloggerScore, ...）
│       ├── scheduler.py       # 内置每日调度器
│       └── wechat.py          # 微信 iLink API 客户端（QR绑定/推送/轮询）
└── tests/
    ├── conftest.py
    ├── unit/                  # 纯逻辑单元测试（116 个，无需浏览器）
    │   ├── test_autoreply.py
    │   ├── test_blogger_scoring.py
    │   ├── test_cross_validate.py
    │   ├── test_parser.py
    │   ├── test_progressive.py
    │   ├── test_report.py
    │   ├── test_scheduler.py
    │   ├── test_trusted_bloggers.py
    │   └── test_wechat.py
    └── integration/           # 集成测试（需浏览器 + 网络）
        ├── test_batch_dates.py
        ├── test_headless_qr.py
        └── test_search.py
```

## Skill 文件说明

| 文件 | 用途 |
|------|------|
| [SKILL.md](SKILL.md) | Anthropic Skill 定义文件，包含执行步骤、登录流程、核心工作流 |
| [reference.md](reference.md) | 参考文档：结构化输出格式、命令行参数、环境变量、渐进式检测原理 |
| [scripts/install.sh](scripts/install.sh) | 安装脚本：`pip install -e .` + Playwright Chromium |

## 命令行参数

| 命令 | 说明 |
|------|------|
| `daemon` | 启动守护进程（内置调度 + 微信推送 + 自动回复） |
| `bind` | 绑定微信 ClawBot 账号（QR 码扫描） |
| `login` | 扫码登录小红书，`--headless` 无窗口模式 |
| `push [--force]` | 从缓存推送今日码到微信 |
| `enrich` | 多源搜索补全时间窗口并推送 |
| `import-wechat` | 从 OpenClaw 导入微信账号 |
| `--now` | 立即执行一次获取 |
| `--force-refresh` | 强制重新搜索（跳过缓存） |
| `--auto-login` | 登录过期时自动启动 headless 登录 |
| `-v` | 开启 DEBUG 日志 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 状态文件路径 |
| `GARDEN_WORLD_PROFILE_DIR` | `.garden_world/browser_profile` | Chromium 持久化会话目录 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 最大候选帖子数 |
| `GARDEN_WORLD_WECHAT_CONFIG` | `.garden_world/wechat.json` | 微信账号配置 |

## 测试

```bash
# 安装测试依赖
pip install -e ".[test]"

# 运行单元测试（116 个，无需浏览器）
pytest tests/unit/ -v

# 运行全部测试（包括集成测试，需已登录的浏览器会话）
pytest -v

# 仅运行集成测试
pytest -m integration -v
```

## 可靠性设计

- **Daemon + 内置调度** — 无需外部 cron，单进程守护 19:00–23:30
- **多源交叉验证** — Top N 帖子独立解析 → 多数投票 → 高置信度
- **时间窗口补全** — 博主 A 有码无时间 + 博主 B 有时间无码 → 自动合并
- **四维博主评分** — timeliness/reliability/format/time_window 滑动平均，持续优胜劣汰
- **context_token 自保** — 每次 getUpdates 刷新 token，发送失败自动 retry
- **快速路径** — 首次搜索后缓存 URL，后续直接访问单个页面
- **SSR 提取** — 从 `__INITIAL_STATE__` 提取内容，反爬也能工作
- **持久化会话** — `launch_persistent_context` 保留完整浏览器状态
- **8 种格式解析** — Format A–H 全覆盖（含中文序号、无序号时间提示等变体）
- **原子写入** — 状态文件先写 `.tmp` 再 `rename`
- **Early exit** — 3 码全齐当日自动停止，不浪费资源

## 许可

[MIT-0](LICENSE)
