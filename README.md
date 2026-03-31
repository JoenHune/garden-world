# 🌸 garden-world

自动抓取手游**「我的花园世界」**每日小红书兑换码，解析通用码、周码和限时码，并在限时码生效后自动推送到微信。

专为 [QClaw](https://qclaw.qq.com/) / [OpenClaw](https://github.com/nicepkg/openclaw) 设计的 Skill，也可独立运行。

## 特性

- **无需登录小红书** — 通过搜索引擎 + [Jina Reader](https://jina.ai/reader/) 公开抓取
- **零第三方依赖** — 仅使用 Python 标准库，开箱即用
- **限时码智能触发** — 解析每个限时码的时间窗，在开始后 5 分钟自动抓取并推送
- **幂等推送** — 本地状态文件记录已发送的码，重复执行不会重复通知
- **多引擎容错** — DuckDuckGo + Bing 双搜索源，HTTP 自动重试，原子状态写入

## 工作原理

```
每天 19:00 起，每 5 分钟执行一次
         │
         ▼
  搜索引擎找到今日博文 URL
         │
         ▼
  Jina Reader 提取博文正文
         │
         ▼
  正则解析 → 通用码 / 周码 / 限时码×3（含时间窗）
         │
         ▼
  检查本地状态：哪些码还没发？哪些限时码已到时间？
         │
         ▼
  输出 NOTIFY: 行 → QClaw 转发到微信
```

### 博文格式示例

```
3.31我的花园世界兑换码+账号福利攻略
周码（4/1日前有效）:指尖花开治愈常在
今日通用 :四季轮转花事不断
限时码1（19:58～20:13）:露珠轻颤花信已至
限时码2（21:26～21:41）:直播带路种花不迷
限时码3（22:14～22:29）:同耕一方共享花开
```

## 快速开始

### 前提

- Python 3.10+（macOS / Linux / Windows 均可）
- [QClaw](https://qclaw.qq.com/) 或 [OpenClaw](https://github.com/nicepkg/openclaw)（用于定时调度和微信推送）

### 1. 克隆仓库

```bash
git clone https://github.com/JoenHune/garden-world.git
cd garden-world
```

### 2. 手动运行一次

```bash
PYTHONPATH=src python3 -m garden_world.main --now
```

输出示例：
```
STATUS: ok date=2026-03-31 source=https://www.xiaohongshu.com/explore/...
NOTIFY: 【通用码】四季轮转花事不断
来源：3.31我的花园世界兑换码+账号福利攻略
NOTIFY: 【周码】指尖花开治愈常在
INFO: windows=1:19:58-20:13, 2:21:26-21:41, 3:22:14-22:29
SCHEDULE_HINT: QClaw cron 每5分钟运行一次；首次19:00开始
```

### 3. 配置 QClaw 定时任务

**方式一：安装为 Skill（推荐）**

将 `skill/garden-world/` 文件夹复制到 QClaw 技能目录：

```bash
cp -r skill/garden-world ~/.openclaw/skills/garden-world
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

这会在每天 19:00~23:55 之间每 5 分钟执行一次，自动推送新发现的兑换码到微信。

## 项目结构

```
garden-world/
├── pyproject.toml                  # 项目元数据
├── skill/garden-world/SKILL.md     # QClaw/OpenClaw Skill 定义
├── src/garden_world/
│   ├── __init__.py
│   ├── __main__.py                 # python -m 入口
│   ├── config.py                   # 配置（支持环境变量）
│   ├── models.py                   # 数据模型
│   └── main.py                     # 核心逻辑
└── tests/
    └── test_parser.py              # 解析器测试
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--now` | 立即执行一次，检查当前时间是否有新码可发 |
| `--force-refresh` | 强制重新搜索（忽略缓存的帖子 URL） |

## 环境变量

所有配置均可通过环境变量覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GARDEN_WORLD_KEYWORD` | `我的花园世界 兑换码` | 搜索关键词 |
| `GARDEN_WORLD_TZ` | `Asia/Shanghai` | 时区 |
| `GARDEN_WORLD_STATE_PATH` | `.garden_world/state.json` | 状态文件路径 |
| `GARDEN_WORLD_CHANNEL` | `wechat` | 通知渠道 |
| `GARDEN_WORLD_MAX_CANDIDATES` | `8` | 最大候选帖子数 |

## 限时码触发机制

博主每天发帖后会陆续更新 3 个限时码，每个有 15 分钟有效期。本工具的处理策略：

1. **19:00** — 首次运行，搜索今日博文，立即推送通用码和周码
2. **每 5 分钟轮询** — 检查是否有新的限时码时间窗已到
3. **限时码开始后 5 分钟** — 重新抓取博文获取限时码内容并推送
4. **幂等保护** — 已推送的码记录在本地状态文件，不会重复发送

```
19:00  ─── 推送通用码 + 周码
19:58  ─── 限时码1 时间窗开始
20:03  ─── ✅ 抓取并推送限时码1（开始后5分钟）
21:26  ─── 限时码2 时间窗开始
21:31  ─── ✅ 抓取并推送限时码2
22:14  ─── 限时码3 时间窗开始
22:19  ─── ✅ 抓取并推送限时码3
```

## 可靠性设计

- **双搜索引擎**：DuckDuckGo + Bing，任一可用即可
- **HTTP 重试**：每次请求最多 3 次，指数退避
- **宽松 SSL**：兼容证书过期或不完整的环境
- **Jina Reader 校验**：检测空内容和错误页面
- **原子写入**：状态文件先写 `.tmp` 再 `rename`，防断电损坏
- **状态自清理**：自动清除 7 天前的推送记录

## 许可

MIT
