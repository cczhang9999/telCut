# telCut

Telegram 新消息包含“是ZF-DA组”时，自动在同一聊天发送一张真实 macOS 菜单栏日期时间图片。只发图片，不附文字、不引用原消息。支持自己发出的消息和别人发来的消息；匹配忽略大小写、空格和换行。

默认截取主屏幕右上角 868×62 像素的横条，与参考图的形状一致。图标、主题和日期时间来自当前电脑的真实屏幕，不绘制或改写时间。程序需要在你的 Mac 上持续运行。

## 本机已安装的版本

如果旧版仍在运行，先在原终端按 **Ctrl+C**，再双击 **启动telCut.command**。已有 `.env` 和 Telegram 登录会话会继续使用，无需重复填写。

出现“telCut 已启动”后，在 Telegram 中发送包含以下文字的新消息：

```text
是ZF-DA组
```

图片会作为普通照片发到这条消息所在的聊天，不引用原消息，也没有文字说明。发出的图片没有文字，所以不会再次触发截图。每条消息最多尝试一次。“您好！这里是ZF-DA组，我们将不定时抽查……”也会匹配；单独发送 telCut 已不再触发。

请在 macOS 设置中将菜单栏设为始终显示，并让目标屏幕右上角的日期、时间可见。启动后会直接监听 Telegram 指令，不会在截图预检处等待回车。可以主动执行 `telcut preview --output preview.png` 检查内容。收到指令后才检查系统屏幕录制权限并截图；权限未生效或菜单栏为空白时，终端会显示原因，不发送空白横条，也不打断监听。菜单栏内容检测只检查可见文字或图标，仍需人工确认预览里日期时间完整。

## 从源码安装

需要 macOS 和 Python 3.9 或以上版本：

```bash
git clone https://github.com/cczhang9999/telCut.git
cd telCut
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
telcut init
```

然后双击 `启动telCut.command`，按本机终端提示完成设置。也可以运行 `python setup_local.py`。

首次设置需要在 [my.telegram.org](https://my.telegram.org) 的 API development tools 中取得自己的 `api_id` 和 `api_hash`。程序会在本机终端询问这些值及 Telegram 登录信息，`api_hash` 不回显；凭据无需发送到聊天中。已有登录信息时跳过这一步。

需要给运行程序的终端授予 macOS **系统设置 → 隐私与安全性 → 屏幕录制** 权限；首次截图会通过系统 API 检查或请求权限。授权后如仍提示权限未生效，请退出整个终端应用并重新打开启动脚本，再发送一条包含触发词的新消息。截图来自系统原生 `screencapture`；只把裁剪后的图片上传，完整屏幕截图在私有临时目录中处理后删除。

## 本地配置

`config.json` 的默认指令配置：

```json
{
  "all_chats": true,
  "ignore_bots": true,
  "chat_ids": [],
  "sender_ids": [],
  "keywords": ["是ZF-DA组"],
  "trigger_mode": "contains",
  "include_outgoing": true,
  "quote_reply": false,
  "send_as_document": false,
  "cooldown_seconds": 0,
  "max_message_age_seconds": 120,
  "caption": "",
  "capture": {
    "display": 1,
    "right_width_ratio": 0.5,
    "top_height_ratio": 0.04,
    "width_pixels": 868,
    "height_pixels": 62,
    "require_menu_content": true
  }
}
```

`width_pixels`、`height_pixels` 按实际截屏像素裁剪，优先于比例设置。若时间或图标不完整，可适当增大尺寸并重新预览；不同屏幕、缩放比例、菜单栏布局可能需要调整。`display: 1` 为主屏幕，`2` 为第二块屏幕。设为 `null` 时使用宽高比例。

`all_chats: true` 监听所有可发送消息的聊天，并要求非空关键词；如仅允许部分聊天，改为 `false` 并在 `chat_ids` 填写 `telcut chats` 输出的数字 ID。默认忽略机器人消息、服务消息和广播频道。不补发离线历史消息。发送失败或限流时在终端记录，不发送错误文字到聊天，也不会自动重试同一指令。

修改配置后需要停止并重新启动程序。终端窗口需保持运行，按 Ctrl+C 停止。电脑休眠、锁屏、菜单栏隐藏或断网时不能保证生成图片。

其他命令：

```bash
telcut preview --output preview.png
telcut login
telcut chats
telcut run
```

预览只保存在本机，不上传。配置也可通过 `telcut --config /绝对路径/config.json run` 指定。`.env`、`config.json` 和 `.telcut/` 已被 Git 忽略；会话文件可以用于登录账号，请勿上传。

## 验证

```bash
python -m unittest discover -s tests -v
```

测试使用模拟消息、模拟发送接口和合成图像，不向真实 Telegram 聊天发送消息。真实屏幕权限和消息发送需在你的 Mac 上验证。
