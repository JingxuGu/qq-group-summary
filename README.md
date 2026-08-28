# QQ Group Summary

这是一个只读 QQ 消息归档与日报服务。NapCat 接收真实 QQ 群消息，Koishi 插件把指定群的 OneBot 事件转发给 Python；Python 负责 SQLite 入库、三类阶段摘要、通知去重、日报和 Mailjet API 邮件投递。

## 运行结构

```text
NapCat / QQ → OneBot → Koishi → http://127.0.0.1:8765 → Python → SQLite → LLM → Mailjet API
```

第一版不会自动回复、群发、加好友、标记已读、抓取链接内容或解析附件正文。

## Windows 首次启动

要求 Python 3.11+、Node.js 20+、Windows 版 QQ、NapCat 和 Koishi。

1. 在 PowerShell 中运行：

   ```powershell
   .\scripts\windows\start.ps1
   ```

2. 打开 `http://127.0.0.1:8765/ui`。首次运行会自动生成 `config.yaml` 和带随机入口 Token 的 `.env`。
3. 开发者在 `config.yaml` 中填写模型和已验证的 Mailjet 发件地址，在 `.env` 中填写 LLM API key、Mailjet API/Secret Key 和 `NAPCAT_WEBUI_TOKEN`。这些凭据不会发送给浏览器。
4. 用户在 WebUI 中连接自己的 QQ；系统会同步全部群组，并按连接后观测到的最近消息时间排序。用户选择需要总结的群并指定 Course、Academic 或 Casual 类型。
5. 用户在 WebUI 中查看摘要与原始消息，并修改日报接收邮箱和发送时间。
6. 另开 PowerShell 检查服务：

   ```powershell
   .\scripts\windows\health.ps1
   ```

数据库会自动创建，YAML 中的群配置会在每次启动时同步到 SQLite。

> 安全提示：仓库中的 `config.example.yaml` 和 `.env.example` 只是无密钥模板，不要把真实配置写进这两个文件。实际使用的 `config.yaml`、`.env` 及其本地变体均已加入 `.gitignore`，不会被 Git 提交。

## WebUI

WebUI 全部使用英文，默认只允许本机访问：

- **Dashboard**：查看消息接收、待摘要数量、群状态和邮件投递记录；
- **Summaries**：按 course、academic、casual 浏览长期保存的阶段摘要；
- **Messages**：搜索和分页查看 SQLite 中的原始消息；
- **Email**：修改用户自己的收件地址和每日发送时间；
- **Connect QQ**：直接在主站展示 QQ 登录二维码、自动检查连接状态，并在登录后选择群组；用户无需打开 NapCat 或输入管理 Token。

模型、API key、邮件提供商凭据和摘要阈值仍由开发者通过 `config.yaml` 与 `.env` 管理。群组订阅和群类型由 QQ 用户在 WebUI 中管理。WebUI 不会读取或返回开发者密钥。

## Koishi 与真实 QQ

`koishi/` 是只读转发插件。先在该目录执行 `npm install` 和 `npm run build`，再把这个本地插件加入你的 Koishi 项目。插件配置中：

- `endpoint` 使用 `http://127.0.0.1:8765/api/v1/messages`；
- `token` 必须与 Python `.env` 的 `MESSAGE_INGEST_TOKEN` 完全一致；
- `retries` 默认 3。

插件连接成功后会自动同步 QQ 群列表。它只为未订阅群上报群名、群号和最新活动时间；只有用户在 WebUI 中选中的群才会转发并保存消息内容。

在 NapCat WebUI 中启用 OneBot 11 WebSocket 服务，并让 Koishi 的 OneBot 适配器连接它。NapCat、QQ 和 Koishi 的版本需要彼此兼容；升级 QQ 或 NapCat 前先备份登录态和配置，并在测试群验证。

建议按以下顺序联调：

1. Python `/health/ready` 返回 `ready`；
2. Koishi 能连接 NapCat OneBot；
3. 测试群发送“周三交”，SQLite `messages` 表出现记录且 `is_noise=0`；
4. 重发同一 OneBot 事件，数据库不增加重复记录；
5. 分别用 course、academic、casual 测试群触发摘要；
6. 手动发送一次日报，再等待定时发送。

## 管理命令

使用项目虚拟环境的 Python 执行：

```powershell
python -m app.cli --config config.yaml init-db
python -m app.cli --config config.yaml summarize --force
python -m app.cli --config config.yaml send-digest
python -m app.cli --config config.yaml cleanup
```

`send-digest` 会先强制处理所有 pending 消息。模型失败或邮件 API 失败时命令返回错误，相关消息和发送游标保持不变。

## 测试

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

Koishi 插件检查：

```powershell
cd koishi
npm install
npm run check
```

## Debian ARM64 chroot 迁移

Python 业务代码没有 Windows 专用依赖。设备完成 root 和 Debian ARM64 chroot 后：

1. 安装 Python 3.11+、Node.js LTS、SQLite、Git、supervisor 和 NapCat 官方 Linux ARM64 运行包。
2. 把项目复制到 `/opt/qq-group-summary`，运行 `sh deploy/linux/install-app.sh`。
3. 把 Koishi 主项目放到 `/opt/koishi`，安装并启用本仓库的转发插件。
4. 先在终端分别验证 NapCat、Koishi、`python -m app.main`，确认 QQ 登录态可复用。
5. 复制 `deploy/linux/supervisor/*.conf` 到 `/etc/supervisor/conf.d/`；NapCat 的启动命令以当时官方 ARM64 安装包实际生成的命令为准，验证后再加入 supervisor，不能猜测安装路径。
6. 执行 `supervisorctl reread`、`supervisorctl update`，再重启手机验证 chroot 与三个服务自动恢复。
7. 测试屏幕关闭 12 小时的 Wi-Fi/QQ 保活，最后连续运行 72 小时，记录掉线、温度、内存、磁盘和邮件结果。

`deploy/linux/logrotate/qq-group-summary` 可安装到 `/etc/logrotate.d/qq-group-summary`。SQLite、`config.yaml`、Prompt 和 `.env` 需要备份；`.env` 权限应设为 600，SSH 不允许 root 密码直接登录。

## 数据可靠性

- 消息先入库，再标记明显噪声；短消息不会按长度删除。
- 只有模型输出通过结构校验并写入摘要后，消息才离开 pending。
- 日报成功交给邮件提供商后才推进游标；极端情况下可能重复发一封，但不会主动丢弃 pending 数据。
- 14 天清理只删除已进入摘要的原始消息，未总结消息不会过期删除。
- API key、Mailjet Secret Key、QQ Cookie 和登录凭据不会写入配置示例或日志。
