# J.A.R.V.I.S

> 一个基于 Python 的个人 AI 助手，灵感来自钢铁侠的贾维斯。支持语音输入、多智能体协作、工具扩展、长期记忆。

## 功能特性

- **语音输入**：按住 Alt 键说话，自动转录为文本
- **三层智能体架构**：主脑 → 任务智能体 → 子智能体，分层处理复杂任务
- **工具系统**：内置 10+ 工具（文件读写、搜索、记忆、待办、时钟等），支持第三方扩展
- **长期记忆**：带权重的记忆存储，支持时间条件触发（定时提醒/拒绝）
- **权限模型**：路径黑名单 + 智能体身份硬编码 + Accessible 访问控制
- **工具分发**：通过 `.l.json` 自描述文件 + 压缩包一键安装第三方工具

## 快速开始

### 环境要求

- Python 3.10+
- Windows（当前主要支持平台，语音输入依赖 `pynput`）
- DashScope API Key（通义千问）— [获取地址](https://dashscope.console.aliyun.com/)
- Tavily API Key（联网搜索，可选）— [获取地址](https://tavily.com/)

### 安装

```bash
git clone https://github.com/Quouop/J.A.R.V.I.S..git
cd J.A.R.V.I.S
pip install -r requirements.txt
```

### 配置

编辑 `HandleTool/config.toml`：

```toml
dashscopeApiKey = "get"    # "get" 表示从环境变量 DASHSCOPE_API_KEY 读取
tavilyApiKey = "tvly-xxx"  # 填入 Tavily API Key，留空则禁用联网搜索
```

或通过环境变量：

```bash
# Windows PowerShell
$env:DASHSCOPE_API_KEY = "sk-xxx"
$env:TAVILY_API_KEY = "tvly-xxx"

# Linux/macOS
export DASHSCOPE_API_KEY="sk-xxx"
export TAVILY_API_KEY="tvly-xxx"
```

### 运行

```bash
python main.py
```

启动后：
- **按住 Alt 键**：开始语音输入，松开结束
- **按住 Ctrl 键**：安装工具（输入压缩包路径或 Git URL）
- **按 ESC**：退出程序

## 架构

### 三层智能体

```
用户 ←→ 主脑 (MainAgent, 8 轮)
              │
              ├─ taskagent 工具（异步线程）
              │    ↓
              │  任务智能体 (TaskAgent, 40 轮)
              │    │
              │    ├─ subagent 工具（同步阻塞）
              │    │    ↓
              │    │  子智能体 (subAgent, 40 轮)
              │    │    ↓
              │    │  subagent_complete 返回进度
              │    │
              │    └─ task_complete 提交任务结果
              │
              └─ query 拉取任务完成报告
```

| 智能体 | 轮数 | 执行方式 | 权限 |
|---|---|---|---|
| MainAgent | 8 | 主线程 | 只读 + 调度，无破坏性工具 |
| TaskAgent | 40 | daemon 线程 | 完整文件操作 + 可派生 subAgent |
| subAgent | 40 | 同步阻塞 | 文件操作，无记忆/无调度权，用完即毁 |

### 权限模型

三层权限校验，每个工具调用都必须通过：

1. **身份硬编码**：`source` 参数由系统注入，LLM 无法篡改（直接赋值，不用 `setdefault`）
2. **Accessible 列表**：每个工具声明可用者，`tool_list.json` 中的 `Accessible` 字段
3. **路径校验**：`.bpath` 黑名单 + TaskAgent 的 `allowed_path` 范围限制

### 工具系统

内置工具：

| 工具 | 功能 | 可用者 |
|---|---|---|
| `read` | 读取文件 | 全部 |
| `write` | 覆盖写入 | TaskAgent, subAgent |
| `str_replace` | 匹配修改 | TaskAgent, subAgent |
| `grep` | 内容搜索 | TaskAgent, subAgent |
| `glob` | 文件名匹配 | TaskAgent, subAgent |
| `bash` | 执行命令 | TaskAgent, subAgent |
| `web_search` | 联网搜索 | 全部 |
| `web_crawl` | 网页抓取 | 全部 |
| `memory` | 长期记忆 | MainAgent, TaskAgent |
| `todo` | 待办管理 | MainAgent, TaskAgent |
| `clock` | 时钟提醒 | MainAgent, TaskAgent |
| `tool_search` | 工具查询 | 全部 |
| `taskagent` | 派生 TaskAgent | MainAgent |
| `task_complete` | 任务完成报告 | TaskAgent |
| `subagent` | 派生 subAgent | TaskAgent |
| `subagent_complete` | 子任务完成报告 | subAgent |

## 工具开发

J.A.R.V.I.S 支持第三方工具扩展。开发规范详见 [工具开发手册](./teaching_you_how_to_make_a_tool_for_Jarvis(yes_this_project_really_call_this).md)。

核心要点：
- 工具是 Python 文件，暴露 `run(params)` 函数，返回 `str`
- 工具根目录放一个 `.l.json` 自描述文件（字段：name, ToolPath, description, Accessible 等）
- 打包为压缩包（zip/7z/rar），用户通过 Ctrl 键安装
- `ToolPath` 写相对路径（相对于 `.l.json`），安装时自动修正为绝对路径

## 项目结构

```
J.A.R.V.I.S/
├── main.py                    # 入口，键盘监听 + 工具安装
├── Call_Llm.py                # 主脑对话循环
├── Prompt.jarvis.md           # 主脑系统提示词
├── LoadSystemPrompy.py        # 提示词占位符替换
├── TouchFile.py               # 语音录制模块
├── asr.py                     # 语音识别
├── cleanup.py                 # 退出清理
├── tool_list.json             # 工具注册表
├── .bpath                     # 路径黑名单
├── HandleTool/
│   ├── ParsingTool.py          # 工具解析与路由
│   ├── bpath_check.py         # 黑名单校验模块
│   ├── config.toml            # API Key 配置
│   ├── memory.py / todo.py / clock.py
│   ├── read.py / write.py / str_replace.py
│   ├── grep.py / glob.py / bash.py
│   ├── web_search.py / web_crawl.py
│   ├── tool_search.py
│   ├── TAgentTool/            # 任务智能体
│   │   ├── main.py
│   │   ├── task_complete.py
│   │   └── Prompt.taskagent.md
│   └── SAgentTool/            # 子智能体
│       ├── main.py
│       ├── subagent_complete.py
│       └── Prompt.subagent.md
├── clearhtml/                 # HTML 清洗模块
├── README.md
├── LICENSE
└── .gitignore
```

## 配置文件

| 文件 | 用途 |
|---|---|
| `HandleTool/config.toml` | API Key 配置 |
| `.bpath` | 路径黑名单（每行一条规则） |
| `tool_list.json` | 工具注册表 |
| `Prompt.jarvis.md` | 主脑系统提示词 |

## 开发计划

详见 [Challenge 清单](./Challenge_of_everyone(even_if_you_are_an_ai).md)：

- **主动唤醒**：J.A.R.V.I.S 能定时自主执行任务（如凌晨自动点咖啡）
- **CLI 闪屏修复**：切换功能时的闪烁问题仍需处理

## 贡献

欢迎提交 Issue 和 Pull Request。

开发新工具请阅读 [工具开发手册](./teaching_you_how_to_make_a_tool_for_Jarvis(yes_this_project_really_call_this).md)。

## License

[MIT](./LICENSE) © 2026 Gnorma, Fermi, and Fermi's father
