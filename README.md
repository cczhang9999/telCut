# telCut

在 **macOS** 上收到指定 Telegram 聊天的新消息时，自动截取菜单栏右上角的真实系统日期和时间，并引用原消息回发 PNG。适用于个人 Telegram 账号；不需要对方添加机器人。

截图来自系统原生 `screencapture`，不绘制、不修改时间。程序需要在你的 Mac 上持续运行；电脑休眠、锁屏或断网时不能保证回复，也不能用 GitHub Actions 代替本机运行。

## 安装与首次使用

需要 macOS 和 Python 3.9 或以上版本。打开终端执行：

```bash
git clone https://github.com/cczhang9999/telCut.git
cd telCut
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
telcut init
cp .env.example .env
```

在 [my.telegram.org](https://my.telegram.org) 的 **API development tools** 中创建应用，取得自己的 `api_id` 和 `api_hash`，填写到 `.env`：

```dotenv
TELEGRAM_API_ID=你的数字ID
TELEGRAM_API_HASH=你的32位API哈希
```

这是 Telegram 用户账号客户端，登录和消息发送使用 [Telethon 官方接口](https://docs.telethon.dev/en/stable/basic/quick-start.html)。首次执行以下命令时，在本机终端输入手机号、Telegram 验证码，以及可选的两步验证密码：

```bash
telcut login
telcut chats
```

从输出中复制目标聊天的 **数字 ID** 到 `config.json` 的 `chat_ids`。群 ID 通常为负数，请完整复制，包括 `-100` 前缀；不支持用显示名称代替 ID。例子：

```json
{
  "chat_ids": [123456789, -1001234567890],
  "sender_ids": [],
  "keywords": [],
  "cooldown_seconds": 30,
  "max_message_age_seconds": 120,
  "caption": "",
  "capture": {
    "display": 1,
    "right_width_ratio": 0.5,
    "top_height_ratio": 0.04
  }
}
```

上面的 ID 只是格式示例，请替换成 `telcut chats` 的实际输出。

## 先确认截图，再开启回复

1. 在 macOS **系统设置 → 隐私与安全性 → 屏幕录制**（部分系统显示“屏幕与系统音频录制”）中允许运行程序的终端，如 Terminal 或 iTerm，然后退出并重新打开终端。
2. 在 **系统设置 → 控制中心 → 时钟选项** 中设置显示日期；在 **桌面与程序坞 → 菜单栏** 中关闭自动隐藏。不同 macOS 版本的设置位置可能略有不同。
3. 执行以下命令并打开预览，确认实际日期、时间完整可见：

```bash
source .venv/bin/activate
telcut preview --output preview.png
open preview.png
```

预览命令只在本地生成图片，不会发送消息。`display: 1` 表示主屏幕，`2` 表示第二块屏幕；目标屏幕必须有可见的时钟。`right_width_ratio` 是从屏幕右边保留的宽度比例，`top_height_ratio` 是从顶部保留的高度比例。默认保留右侧 50%、顶部 4%；如果时间被裁切，调整比例后重新预览。比例以真实截屏像素计算，适配 Retina；截图中可能包含同一区域的其他菜单栏图标。

确认后启动：

```bash
telcut run
```

从另一个账号向配置的聊天发送消息，即可看到回复。图片以 PNG 文件发送，保留菜单栏文字清晰度。按 **Ctrl+C** 停止。修改配置后需要重启 `telcut run`。

## 触发规则

| 配置 | 含义 |
| --- | --- |
| `chat_ids` | 必填白名单；空数组会拒绝启动自动回复 |
| `sender_ids` | 可选的发件人数字 ID 白名单；空数组表示目标聊天中的所有真人发件人 |
| `keywords` | 任一关键词包含匹配，不区分大小写；`[]` 表示所有新消息，包括图片等媒体消息。媒体的文字说明也参与匹配 |
| `cooldown_seconds` | 同一聊天两次尝试的最小间隔，默认 30 秒；冷却中的消息跳过，不延后补发 |
| `max_message_age_seconds` | 只处理最近的新消息，默认 120 秒；离线历史消息不会主动补发 |
| `caption` | 可选固定图片说明，默认不附加文字 |

例如仅响应包含“查岗”或“在线”的消息：`"keywords": ["查岗", "在线"]`。如果对方发送 `1` 就要回复，设置 `"keywords": ["1"]`，或保持空数组以响应任何消息。

仅监听新收到的消息；忽略自己发出的消息、机器人消息、服务消息和广播频道。群聊可配置 `sender_ids` 限制触发人员；需要发件人 ID 时，可在与该人的私聊中使用 `telcut chats` 获取其用户 ID。

SQLite 持久化消息去重，进程锁防止同一配置重复启动。每条消息在截图前登记；截图或发送失败也不会自动重发同一条消息，避免网络超时下重复上传。日志会记录失败；可修复权限或网络后让对方重新发一条消息。Telegram 限流时暂停回复，期间消息不排队补发。

配置、登录会话和去重数据库默认保存在当前项目目录（`config.json`、`.env`、`.telcut/`），均已加入 `.gitignore`。完整截屏只暂存在私有临时目录，裁剪后删除；发送内容仅为裁剪区域。不要上传 `.env` 或 `.telcut/`：会话文件可以用于登录你的账号。

也可通过 `telcut --config /绝对路径/config.json run` 使用其他配置；对应 `.env` 和 `.telcut/` 会位于该配置文件的目录。

## 排查与测试

- `chat_ids 为空`：执行 `telcut chats`，填入真实目标 ID。
- 截图失败：检查终端屏幕录制权限、重启终端并确认屏幕编号。菜单栏隐藏或锁屏时即使截屏成功也可能没有可见时钟，请先检查预览。
- 没有回复：确认本机程序仍在运行、发件人不是当前账号或机器人、关键词及发件人规则匹配，且不在冷却期；检查终端日志。
- 需要通过代理连接 Telegram：当前版本使用系统可直连的网络，不提供应用内代理配置。

```bash
python -m unittest discover -s tests -v
```

自动测试使用模拟消息、模拟发送接口和合成图像，不连接真实 Telegram，不向任何人发消息。真实账号登录、macOS 屏幕录制授权及另一账号发消息的端到端验证需要在你的 Mac 上完成。
