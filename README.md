# Emby Notifier Gateway

一个轻量级、生产级的 Emby Webhook 通知与远程控制网关。

## ✨ 功能特性

- **全面事件监控**: 支持播放(开始/暂停/停止)、媒体库(新增/删除/更新)及用户登录/登出等所有 Emby Webhook 事件。
- **智能集数解析**: 自动将 `S01E01, E02, E05` 转换为 `第 1-2, 5 集` 等易读格式。
- **可视化进度条**: 提供 `当前时间 / 总时长 (百分比)` 及字符进度条 $\text{[███░░░]}$。
- **媒体海报推送**: 可选开启资源封面图片发送 (仅支持 Telegram)。
- **多渠道通知**: 支持 Telegram Bot 和 Email，可通过配置独立开关。
- **高可用性**: 内置指数退避重试机制，防止网络波动导致通知丢失。
- **远程控制**: 提供简单的 HTTP API 用于暂停、播放、停止当前活跃会话或刷新媒体库。

## 🚀 快速部署

### 1. 部署
将本项目代码克隆到本地，修改 `docker-compose.yml` 中的环境变量后运行：
```bash
docker-compose up -d --build
```

### 2. Emby Webhook 配置
在 **Emby 服务器 $\rightarrow$ 设置 $\rightarrow$ Webhook** 中添加：
- **Webhook URL**: `http://<运行机器IP>:8000/webhook`
- **请求内容类型**: `JSON`
- **勾选事件**: 建议勾选所有事件（特别是 Playback 相关和 Item 相关）。

## 🛠️ 配置参数

| 变量 | 描述 | 默认值 |
| :--- | :--- | :--- |
| `EMBY_URL` | Emby 服务器地址 (含端口) | `http://your-emby-ip:8096` |
| `EMBY_API_KEY` | Emby API 密钥 | - |
| `ENABLE_COVER_IMAGE` | 是否发送资源封面海报 | `true` |
| `NOTIFY_TELEGRAM_ENABLED` | 是否开启 Telegram 通知 | `true` |
| `NOTIFY_EMAIL_ENABLED` | 是否开启 Email 通知 | `false` |

## 🎮 控制接口

| 动作 | 接口 | 描述 |
| :--- | :--- | :--- |
| 暂停 | `POST /control/pause` | 暂停当前活跃的播放会话 |
| 播放 | `POST /control/play` | 恢复当前活跃的播放会话 |
| 停止 | `POST /control/stop` | 停止当前活跃的播放会话 |
| 刷新 | `POST /control/refresh` | 触发 Emby 媒体库扫描刷新 |
-e 

