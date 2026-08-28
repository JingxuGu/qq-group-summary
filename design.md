# QQ 多群聊日报系统：软件工程设计文档

## 1. 项目目标

这个项目要完成的事情是：让一个常驻在线的 QQ 账号监听若干指定群聊，把新消息保存下来，按群类型进行阶段性总结，并在每天固定时间把“上一次成功发送日报之后产生的新内容”整理成一封邮件发送给自己。

第一版只支持 QQ，不支持微信、飞书、钉钉。未来如果需要扩展其他聊天平台，再抽象消息接入层。第一版不做 RAG、不做网页爬取、不做图片 OCR、不解析 PDF/Word 文件正文，也不做复杂话题聚类。

核心原则：QQ 消息先可靠落库再做总结，不因为模型调用失败而丢消息；不把全天原始消息一次性塞给模型，而是不断产生“阶段摘要”；不同类型群采用不同摘要策略；每天只发一封邮件；邮件发送成功后才推进发送游标；原始消息保留 14 天，摘要长期保留；LLM Provider 可切换。

## 2. 目标邮件结构

```text
QQ 消息日报 2026-xx-xx

第一部分：重要通知

[某课程群]
最早的原始通知……
后续更新：DDL 从周三改为周五。

第二部分：学术群内容

## VLA 学术讨论群
今天主要讨论……
xxx 认为……
xxx 补充……
分歧在于……

知识 tags：
VLA / action chunking / diffusion policy

第三部分：闲聊群总结

## 同学群
主要聊了……
值得关注的信息是……
```

当天没有有效内容的群完全不显示。

## 3. 技术栈

推荐技术栈：

```text
QQ
↓
NapCat
↓
OneBot
↓
Koishi
↓
Python 业务服务
↓
SQLite
↓
Qwen / GLM API
↓
Mailjet Send API（SMTP 备用）
↓
你的邮箱
```

Python 负责数据清洗、数据库读写、LLM API、阶段摘要、日报、邮件、定时任务和日志。Koishi 属于 Node.js 生态，因此需要学习少量 JavaScript/TypeScript，但第一版应尽量把 Koishi 当作“QQ 消息入口”，复杂业务逻辑放到 Python。

建议 Python 依赖以 `httpx`、`SQLAlchemy` 或标准库 `sqlite3`、`APScheduler`、`python-dotenv`、`PyYAML` 为主。初学阶段可先用标准库 `sqlite3`，等理解数据库后再决定是否引入 ORM。

第一版数据库使用 SQLite，不使用 PostgreSQL、MongoDB、Redis。第一版定时任务使用 APScheduler。邮件默认使用 Mailjet Send API v3.1，并保留 SMTP 备用通道。服务端使用 Git 管理代码，部署在 Galaxy A9 Star 的 Debian ARM64 chroot 中。

## 4. 各组件职责

### NapCat

NapCat 负责登录真实 QQ 账号并接收群消息。第一版保持只读：不自动回复、不群发、不批量加好友、不主动标记消息已读。

### OneBot

OneBot 是 NapCat 与上层 Bot 程序之间的标准接口，不需要深入研究协议内部。

### Koishi

Koishi 负责连接 NapCat、接收 QQ 消息事件、识别来源群，并把消息转换成系统内部统一格式后交给 Python 服务。

### Python 服务

Python 服务负责消息落库、轻量过滤、阶段总结触发、选择不同群 Prompt、调用大模型、保存摘要、通知去重、组装日报、发邮件、清理原始消息。

推荐最终使用一个很小的 FastAPI 服务作为 Koishi 到 Python 的入口，例如 `/messages`，但开发最初阶段可以先不接 FastAPI，直接用假数据测试。

### SQLite

SQLite 保存群配置、原始消息、阶段摘要、重要通知和邮件发送记录。数据库就是一个文件，适合单用户、小型长期服务和手机服务器。

## 5. 大模型 API 选型

### 主模型：Qwen3.5-Flash

第一版默认使用 `Qwen3.5-Flash`。原因是你的任务属于大量中文输入、较短输出、不需要复杂推理的总结工作，更重视低输入价格、中文能力和稳定性，而不是旗舰推理能力。

截至本方案制定时核对到的官方价格，中国内地、单次输入不超过 128K token 时约为：

```text
输入：¥0.24 / 百万 token
输出：¥2.4 / 百万 token
```

价格可能变化，因此程序只保存 provider、model、base_url 等配置，不把价格写入业务逻辑。

### 备用模型：GLM-4.7-FlashX

当 Qwen API 暂时不可用、想做效果对比，或者某些群的总结效果明显不好时，切换到 `GLM-4.7-FlashX`。

### 开发测试：GLM-4.7-Flash

开发和调 Prompt 阶段可以使用当前低价/免费的 `GLM-4.7-Flash`，但不要把“永久免费”当成长期架构前提。

### 为什么不默认 DeepSeek

早期方案考虑过 DeepSeek，但 2026 年 8 月中旬其 API 定价策略发生调整。你的任务也不需要强推理能力，因此不再默认 DeepSeek。可以保留 DeepSeek Provider，以后价格或效果变化时再切换。

### Provider 抽象

不要写死成：

```python
def summarize_with_qwen(...):
    ...
```

推荐：

```text
Summarizer
    ↓
LLMProvider
    ├── QwenProvider
    ├── GLMProvider
    └── DeepSeekProvider
```

概念接口：

```python
class LLMProvider:
    def summarize(self, messages, prompt):
        ...
```

配置：

```yaml
llm:
  primary:
    provider: qwen
    model: qwen3.5-flash

  fallback:
    provider: glm
    model: glm-4.7-flashx
```

## 6. 群类型

第一版每个被用户选中的群必须指定为：

```text
course    课程群
academic  学术群
casual    闲聊群
```

连接 QQ 后，Koishi 自动同步该账号的全部群列表。WebUI 按服务连接后观测到的最近消息时间倒序展示群组，用户勾选需要总结的群，并在界面中指定 `course`、`academic` 或 `casual`。群订阅和类型保存在 SQLite，不写入 `config.yaml`。未订阅群只同步群名、群号和活动时间，不保存消息内容。

## 7. 三类群的处理方式

### 课程群

课程群主要是通知和答疑。明确通知尽量保留原文、发送者、时间和来源群，不做大幅改写。相同通知自动语义去重，以最早出现的通知作为主记录；如果后续明确修改时间、地点、DDL 等，则追加为“通知更新”。普通答疑允许轻度压缩。

### 学术群

学术群采用高保真总结。尽量保留讨论主题、不同成员观点、争论过程、分歧、共识、未解决问题，并提取知识 tags。知识 tags 只列名称，不解释，例如 `VLA`、`Diffusion Policy`、`Action Chunking`。

### 闲聊群

闲聊群采用高压缩策略。重点保留有用资源、明确计划、值得关注的事件和有一定信息量的讨论。普通闲聊高度概括或省略。

## 8. 消息过滤

在 LLM 前只做非常保守的规则过滤，可以过滤纯表情、完全重复消息、明显系统噪声和固定无信息短语。

不要简单使用“消息长度小于 N 就删除”，因为“周三交”“302教室”“我参加”虽然短，但有重要信息。

原则是：宁可把少量垃圾交给 LLM，也不要在规则层误删重要信息。

## 9. 文件、链接与非文本消息

文件只记录文件名、发送者、时间以及附近解释文字，不解析文件正文。

链接与文件一致，只保留 URL、可获得的标题、发送者和附近说明，不主动抓取网页。

图片、视频、语音只记录存在，不做 OCR、ASR 或内容理解。

## 10. 阶段总结

每个群维护自己的未总结消息缓冲。满足三个条件中的任意一个就触发阶段总结：

1. 消息条数达到阈值；
2. 连续静默达到阈值；
3. 本轮累计时长达到最大值。

默认配置：

```yaml
summary_policy:
  academic:
    max_messages: 400
    idle_minutes: 60
    max_window_hours: 4

  course:
    max_messages: 150
    idle_minutes: 30
    max_window_hours: 2

  casual:
    max_messages: 500
    idle_minutes: 90
    max_window_hours: 6
```

每个群允许覆盖默认值。

## 11. 完整数据流

```text
QQ群新消息
↓
NapCat
↓
OneBot
↓
Koishi
↓
统一 Message 格式
↓
轻量过滤
↓
SQLite 原始消息
↓
判断阶段总结条件
↓
按 group_type 选择 Prompt
↓
调用 Qwen3.5-Flash
↓
保存阶段摘要 / 通知候选
↓
每天固定时间
↓
读取上次成功发送后的 pending 数据
↓
通知去重与变更合并
↓
组装一封日报
↓
Mailjet Send API（SMTP 备用）
↓
发送成功后推进 delivery cursor
```

## 12. 数据库建议

### groups

```text
id
qq_group_id
name
type
enabled
available
last_message_at
max_messages
idle_minutes
max_window_hours
created_at
updated_at
```

### messages

```text
id
qq_message_id
group_id
sender_id
sender_name
message_type
text
attachment_title
url
sent_at
received_at
summary_batch_id
created_at
```

`summary_batch_id = NULL` 表示还没进入阶段总结。

### summary_batches

```text
id
group_id
started_at
ended_at
message_count
summary_text
knowledge_tags_json
status
created_at
```

状态可为 `pending` 或 `sent`。

### notifications

```text
id
source_group_id
source_message_id
first_seen_at
title
original_text
latest_update_text
dedup_key
status
created_at
updated_at
```

### deliveries

```text
id
window_start
window_end
email_subject
status
sent_at
error_message
created_at
```

### settings

至少保存：

```text
last_successful_delivery_at
raw_message_retention_days
```

## 13. 原始消息保留

原始消息默认保留 14 天，做成配置项：

```yaml
storage:
  raw_message_retention_days: 14
```

即使已经总结，也保留 14 天方便回溯。超过保留期自动清理。阶段摘要长期保留。

## 14. 日报发送游标

日报窗口不固定为自然日，而是：

```text
上一次成功发送日报时间
→
本次准备发送日报时间
```

邮件发送失败时不推进 `last_successful_delivery_at`，相关数据继续保持 pending，下次补发。

## 15. 日报固定三部分

第一部分是重要通知，跨群汇总、语义去重、保留最早通知，并融合后续明确更新。

第二部分是学术群内容，按群分别展示讨论概述、主要成员观点、分歧/共识和知识 tags。

第三部分是闲聊群总结，按群展示，高度压缩。没有有效信息的群不显示。

## 16. Prompt 设计

至少三套 Prompt：

```text
course_prompt
academic_prompt
casual_prompt
```

学术 Prompt 必须要求不补充群聊中没有出现的知识、不解释 tag、准确保留“谁提出了什么观点”、不确定的归属不要猜。

课程 Prompt 必须要求时间、地点、DDL、数字不得自行修改，区分最早通知和后续更新。

闲聊 Prompt 必须要求极度压缩低价值闲聊，优先保留可能需要行动或关注的信息。

## 17. 错误处理

LLM 失败时，Qwen 先重试 1~2 次，仍失败则切 GLM fallback；如果仍失败，保留原始消息并等待下一轮，不设置已总结状态。

邮件发送失败时，delivery 标记 failed，发送游标不推进，数据保持 pending。

QQ/NapCat 掉线时写日志并尝试重连，由进程守护器负责崩溃后的自动拉起。

## 18. 配置示例

```yaml
app:
  timezone: Asia/Shanghai
  daily_email_time: "22:30"

storage:
  database: "./data/qq_summary.db"
  raw_message_retention_days: 14

llm:
  primary:
    provider: qwen
    model: qwen3.5-flash
  fallback:
    provider: glm
    model: glm-4.7-flashx

summary_policy:
  academic:
    max_messages: 400
    idle_minutes: 60
    max_window_hours: 4
  course:
    max_messages: 150
    idle_minutes: 30
    max_window_hours: 2
  casual:
    max_messages: 500
    idle_minutes: 90
    max_window_hours: 6
```

API key 和邮箱密码不要写入 YAML。使用 `.env`：

```text
QWEN_API_KEY=...
GLM_API_KEY=...
MAILJET_API_KEY=...
MAILJET_SECRET_KEY=...
MAILJET_WEBHOOK_USERNAME=...
MAILJET_WEBHOOK_PASSWORD=...
```

并把 `.env` 加入 `.gitignore`。

## 19. 推荐目录结构

```text
qq-daily/
├── README.md
├── config.yaml
├── .env
├── .gitignore
├── koishi/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── ingestion/
│   ├── storage/
│   ├── llm/
│   ├── summarizer/
│   ├── digest/
│   ├── mail/
│   └── jobs/
├── prompts/
│   ├── academic.txt
│   ├── course.txt
│   └── casual.txt
├── data/
└── tests/
```

## 20. MVP 开发顺序

第一阶段只做 Python + SQLite：手动构造一条假 QQ 消息，成功写入数据库并读取。

第二阶段只做 LLM API：用手工复制的群聊文本调用 Qwen3.5-Flash，确保 API key 使用环境变量，失败不会让程序崩溃。

第三阶段实现三种 Prompt，并准备课程、学术、闲聊三种测试数据。

第四阶段实现阶段总结三条件触发，先使用数据库假消息，不接真实 QQ。

第五阶段实现 Mailjet Send API，先给自己发送订阅确认邮件，再发送模拟日报；SMTP 仅作为备用通道。

第六阶段实现日报发送游标，验证“成功推进、失败不推进”。

第七阶段再接 Koishi/NapCat。

第八阶段部署到 Galaxy A9 Star Debian chroot，配置 SSH、自启动、日志和进程守护。

## 21. 初学者学习顺序

```text
Python 基础
↓
pip / venv
↓
HTTP / JSON
↓
httpx / requests
↓
SQLite / SQL
↓
Git
↓
Linux 命令行
↓
Node.js / npm 基础
↓
Koishi / OneBot
↓
进程管理
```

第一版不需要先学 Kubernetes、Docker 编排、Redis、PostgreSQL 管理、前端框架、深度学习训练或向量数据库。

## 22. 测试建议

通知测试要覆盖“原通知→重复转发→后续修改”，预期只有一个事项，保留最早通知并标记更新。

学术测试要至少有三个人表达不同观点，预期不把 A 的观点写到 B 名下，不凭空加结论，并能提取 tags。

闲聊测试要在大量“哈哈、+1、表情、收到”中夹一条真正重要信息，预期邮件突出重要内容。

还要人为填写错误 LLM API key 和错误 Mailjet Secret Key，验证模型失败不丢原始消息、邮件失败不推进发送游标。

## 23. 日志与安全

至少记录程序启动、QQ 连接、收到消息数量、阶段总结触发、模型调用、日报生成、邮件发送和数据清理。

日志中不要打印 API key、Mailjet Secret Key、Webhook 密码、Cookie 或 QQ 登录凭证。

QQ 侧第一版保持只读，避免自动回复、群发、加好友、加群、撤回和批量已读。

## 24. 第一版明确不做

第一版不做微信、飞书、钉钉、多平台统一接入、RAG、向量数据库、OCR、图片理解、语音识别、PDF 正文解析、网页抓取、日历自动创建、Web 管理后台、手机 App、多用户系统、Docker 和 Kubernetes。

## 25. 推荐的第一个开发子任务

新开开发对话后，第一项任务建议写成：

> 建立 Python 项目骨架，使用 SQLite 建立 `groups` 和 `messages` 两张表，编写一个最小程序，能够把一条手工构造的群消息写入数据库并读取出来，并为这个功能写最基本的测试。

完成后再进入 LLM API。

## 26. 第一版完成标准

完成时必须满足：指定多个 QQ 群可监听，群可配置为 course/academic/casual，消息可靠入库，阶段总结按三条件触发，学术群保留成员观点并生成 tags，课程通知自动去重并处理更新，闲聊群高压缩，文件和链接只记录标题与上下文，每天只发一封邮件，邮件覆盖上一次成功发送后的数据，失败不会漏日报，原始消息 14 天清理，摘要长期保存，默认 Qwen3.5-Flash、支持 GLM fallback，服务器重启后能够继续运行。
