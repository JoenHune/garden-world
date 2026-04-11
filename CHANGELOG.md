# Changelog

## v0.3.0 — 2026-04-11

### 新增

#### Daemon 模式 (`garden-world daemon`)
- 内置调度器 — 每日 19:00–23:30 自动轮询，无需外部 cron
- 首 19:05 获取通用码 + 周码，之后每 5 分钟检测限时码时间窗
- 时间窗开始后 5 分钟自动抓取并推送限时码
- 3 个限时码全部推送后自动停止当日轮询（early exit）

#### WeChat 直推 (`wechat.py`)
- 基于 iLink API 的微信消息推送（绕过 OpenClaw 调度层）
- QR 码绑定流程 (`garden-world bind`)
- 多账号管理（最多 5 个）
- 图片推送 — CDN 上传 + AES-128-ECB 加密
- 自动从 OpenClaw 导入已有账号 (`garden-world import-wechat`)
- `context_token` 自动刷新 — 每次 `getUpdates` 从所有消息类型提取 token
- 发送失败时自动 retry（`ret=-2` → refresh → 重发）

#### 自动回复 (`autoreply.py`)
- 关键词匹配 → 模板回复
- 规则配置文件 `.garden_world/autoreply.json`
- 作为 daemon 子线程运行，持续刷新 `context_token`

#### 多源交叉验证
- 搜索 Top N 候选帖并逐一解析
- ≥2 个来源一致 → 高置信度；单源 → 低置信度警告
- 跨源补充时间窗口（一个源有码、另一个源有精确时间 → 合并）

#### 博主评分系统 v2
- 四维评分: `timeliness` / `reliability` / `format` / `time_window`
- 信任加成公式: `timeliness×2 + reliability×3 + format×1 + time_window×2`（满分 +8）
- 不写时间窗的博主被持续降权（滑动平均 → 趋向 0）

#### 时间窗口增强 (enrich)
- `garden-world enrich` — 强制多源搜索补全时间窗口
- daemon 自动 fallback — 快速路径无时间窗时触发多源补全
- Format F 时间提示提取 — `限时码一:20点左右` → `start="20:00"`
- Format H 解析器 — `限时码:HH:MM评论区见` 格式
- 缓存窗口保留 — 新解析结果为空时保留旧的精确窗口

#### 推送改进
- `garden-world push [--force]` — 从缓存推送，无需浏览器
- 双消息: 完整报告 + 最新裸码（方便复制）
- 时间窗缺失显示 `(未更新)` 而非隐藏

#### 占位符过滤增强
- 新增过滤词: `兑换`, `仅可`, `每个`
- 防止 `每个码仅可兑换一次` 被误识别为兑换码

### 修复
- `context_token` 过期问题 — `get_updates()` 现在从所有消息类型提取 token（含系统事件、关注事件），与 OpenClaw 行为一致
- `_get_bridge()` 初始化时预刷新 token，一次性命令不再因 stale token 失败
- `broadcast_text()` 收到 `ret=-2` 后自动 refresh + retry
- `(未更新)` 显示恢复 — 时间窗或码缺失时始终显示，不再静默隐藏

### 测试
- 新增测试文件: `test_wechat.py`, `test_scheduler.py`, `test_autoreply.py`, `test_blogger_scoring.py`, `test_cross_validate.py`
- 总计 116 个单元测试全通过

---

## v0.2.0 — 2026-04-05

- 多源交叉验证基础
- 信任博主系统 v1（单维评分）
- 格式化报告输出
- 代码净化（`_sanitize_code`）
- 博主降权（超时未更新码）
- 非官服帖过滤

## v0.1.0 — 初始版本

- 小红书帖子搜索 + SSR 提取
- 7 种博文格式解析（Format A–G）
- 渐进式限时码检测
- 幂等推送 + 原子状态写入
- Playwright 持久化会话 + headless 登录
- OpenClaw Skill 兼容
