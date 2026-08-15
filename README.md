# 芊熠智能打标工作台

面向 LoRA、图像生成和视频生成训练集的 Windows 桌面标注工具。批量扫描图片与视频，通过火山引擎或常用 OpenAI 兼容平台生成高质量 sidecar TXT，并在同一个工作台中完成复核、清理、重试和训练数据导出。

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D4?logo=windows)](https://github.com/wozhendemeiyou/qianyi-media-caption-tool/releases/latest)
[![Tests](https://img.shields.io/badge/Tests-78%20passed-35B46F)](./tests)
[![Version](https://img.shields.io/badge/Release-v3.6.3-285C96)](https://github.com/wozhendemeiyou/qianyi-media-caption-tool/releases/latest)

> 公开仓库与 Release 已清空 API Key、自定义接口值、用户提示词和提示词预设。用户在软件内填写的密钥使用 Windows DPAPI 分平台加密，只保存在当前 Windows 账户的本地应用目录。

## 文档导航

- [图文使用手册](docs/USER_GUIDE.md)：从首次配置到扫描、打标、复核、导出和应用内更新；
- [完整更新记录](CHANGELOG.md)：v3.3 至今的每次公开版本变化；
- [安全与隐私说明](SECURITY.md)：凭据、模板、本地数据、网络访问和发布前检查；
- [Windows Releases](https://github.com/wozhendemeiyou/qianyi-media-caption-tool/releases)：下载、版本说明与 SHA-256。

## 界面预览

![芊熠智能打标工作台](docs/images/workbench.png)

<p align="center">
  <img src="docs/images/launch.png" width="49%" alt="品牌启动页">
  <img src="docs/images/project-center.png" width="49%" alt="项目中心">
</p>

<p align="center">
  <img src="docs/images/platform-settings.png" width="49%" alt="平台设置">
  <img src="docs/images/prompt-editor.png" width="49%" alt="提示词编辑器">
</p>

<p align="center">
  <img src="docs/images/sampling.png" width="49%" alt="模型与采样面板">
  <img src="docs/images/orphan-txt.png" width="49%" alt="孤立 TXT 筛选">
</p>

<p align="center">
  <img src="docs/images/system-info.png" width="70%" alt="系统说明与应用内更新">
</p>

## 为什么使用它

| 亮点 | 带来的价值 |
| --- | --- |
| 一次扫描，完整配对 | 递归发现图片、视频和同名 TXT，立即区分缺少标签、无效标签、孤立 TXT、不可读媒体与输出冲突。 |
| 面向训练目标的提示策略 | 可选择训练主体、风格 LoRA 或风景/场景侧重点，支持中文自然语言、英文描述和逗号词组标签。 |
| 双层提示词编辑 | 用户要求与系统提示词模板分区编辑；公开版不内置模板，用户自行编写或导入并只保存在本机。 |
| 画布优先导航 | 左侧固定显示项目中心、图像打标、视频反推、平台设置，中央素材画布不再被低频参数挤占。 |
| 视频反推入口 | 视频任务拥有独立顶栏入口和任务状态，继续复用可靠的扫描、批处理、停止与恢复主线。 |
| 折叠采样面板 | 任务设置中可展开 Max Tokens、Temperature、Top P、Top K、频率/存在惩罚和 Seed，并提供稳定、平衡、创意三组预设。 |
| 安全滚轮交互 | 关闭状态的下拉框与数值框不响应滚轮改值；滚轮继续用于右侧设置区或已展开选项列表的浏览。 |
| 双模式检查器 | 右侧在当前素材和任务设置间切换；当前素材内集中复核标注结果、提示词和运行日志。 |
| 固定任务栏 | 开始、停止、重试、处理选中、批处理、导出、进度和统计始终固定在窗口底部。 |
| 系统说明与更新 | 内置功能说明、当前版本和更新日志；发现新版本后可在软件内下载、校验、覆盖并自动重启。 |
| 多 API 平台 | 内置火山引擎、OpenAI、Google、月之暗面、千问和 SiliconFlow，并支持用户填写 OpenAI 兼容 Base URL、模型 ID 与 API Key。 |
| 推理输出控制 | 平台设置提供 MTP 加速与思考标签清理滑块；兼容本地模型可启用原生 MTP，所有后端均可清理最终标注中的思考区块。 |
| 常驻硬件状态 | 底部持续显示 CPU、内存和 NVIDIA GPU 利用率/显存；采集在后台线程中完成，不阻塞界面。 |
| 可恢复的批处理 | 跳过已有有效 TXT，失败项独立记录并可重试；停止任务后再次运行不会浪费已经完成的结果。 |
| 按需媒体引擎 | 视频任务才启动独立 Worker；随机本地端口、一次性令牌和项目目录白名单共同限制访问范围。 |
| 备份与诊断 | 项目状态每日自动轮换备份；系统说明页可手动备份或导出不含密钥、媒体和完整标签的脱敏诊断包。 |
| 实际 10 并发 | 外部 API 后台可同时执行 10 个请求。标准压力测试记录 `peak_active_requests: 10`，界面数值与线程池一致。 |
| 安全的密钥与数据边界 | API Key 由 DPAPI 加密；项目删除只清理应用元数据，不删除原始媒体；生成结果只写入同名 TXT。 |
| 可视化复核 | 缩略图网格和紧凑列表自由切换，支持搜索、状态筛选、多选、结果编辑、触发词补写和批量替换。 |
| 孤立 TXT 治理 | 直接列出有 TXT、无媒体的历史残留，可查看内容、在资源管理器定位或确认删除，并且绝不会送入模型或训练数据导出。 |
| 离线质量工具 | 感知哈希相似图检测、JSONL/CSV 导出和完整离线压力测试均不产生 API 费用。 |

## 典型工作流

1. 在项目中心添加一个媒体目录。
2. 进入图像打标或视频反推，在右侧“任务设置”中选择输出格式、语言、训练侧重点、触发词和并发数。
3. 在“平台设置”中选择运行后端、本地模型目录、服务商和模型，并填写自己的 API Key；Base URL 由系统内置。
4. 在提示词页自行编写或导入系统模板，并按任务需要填写用户要求。公开版首次启动时模板库为空。
5. 点击“扫描”，先处理缺少 TXT、无效 TXT、孤立 TXT 和重名输出冲突。
6. 点击“开始任务”或只处理当前筛选/选中的素材。
7. 在右侧“当前素材”中检查生成结果、提示词和运行日志，必要时编辑、批量替换或添加触发词。
8. 导出训练数据 JSONL/CSV，或直接使用媒体旁的 sidecar TXT。

每个媒体文件对应同目录、同文件名的 TXT：

```text
dataset/
├── image-001.jpg
├── image-001.txt
├── image-002.png
├── image-002.txt
├── clip-001.mp4
└── clip-001.txt
```

## 核心能力

### 扫描与质量检查

- 支持 JPG、JPEG、PNG、WebP、GIF、HEIC、HEIF、MP4、MOV 和 AVI。
- 单次目录遍历同时收集媒体与 TXT，自动忽略 Windows 系统目录。
- 检测无法解码的图片、空 TXT、错误响应 TXT、缺失 TXT 和孤立 TXT。
- 检测同名不同扩展媒体写入同一个 TXT 的冲突，冲突项不会发送 API 请求。
- 图片自动应用 EXIF 方向；HEIF/HEIC 使用独立解码保护。

### 批量打标

- 外部 API 支持 1 至 10 并发，本地模型固定单并发控制显存。
- 生成请求不自动重试，避免网络超时后重复计费；失败项由用户明确触发重试。
- 支持中止，网络退避可取消，未开始的队列项会标记为已取消。
- 每个成功结果先原子写入 TXT，再更新项目状态，意外退出时已完成结果仍然保留。
- 所有等待项都会写入项目状态；程序异常退出后，项目中心显示“上次意外中断”，可点击“恢复未完成”继续失败、取消和未开始素材。
- 可为所有有效结果补写训练触发词，重复执行不会重复添加。

### 视频与采样

- 火山引擎支持 20 MB 内视频直传，以及最大 512 MB 的文件上传与预处理流程。
- 火山引擎与千问支持视频输入；其他平台的实际能力取决于所选视觉模型。
- 视频任务按需启动独立媒体 Worker。检测到 FFprobe 时会先验证视频流和容器完整性；没有媒体工具时安全降级为平台原生视频输入。
- Worker 已提供 FFmpeg 抽帧和单声道 16 kHz WAV 音频提取接口，为后续本地视频模型与语音理解能力预留统一引擎。
- 请求层根据平台能力过滤采样字段。例如 OpenAI 不发送 `top_k`，避免因不支持的参数导致整批失败。
- 本地 Hugging Face 后端使用 Max Tokens、Temperature、Top P、Top K 和 Seed，仍固定单并发控制显存。

### 复核与整理

- 缩略图网格、紧凑列表、分页、文件名搜索和状态筛选。
- 多选后强制重新打标、批量替换文本或导出当前范围。
- 孤立 TXT 使用独立列表显示，不进入缩略图解码队列，不会锁定或误处理文件。
- 离线感知哈希相似图检测只筛选候选项，不自动删除用户素材。

## 模型与计费路由

| API 平台 | 默认模型 | 接口 |
| --- | --- | --- |
| 火山引擎 | 豆包 Seed 系列 | 按模型自动选择 Coding Plan 或标准接口 |
| OpenAI | `gpt-5.6-terra` | `https://api.openai.com/v1/chat/completions` |
| Google | `gemini-3.7-flash` | Google OpenAI 兼容接口 |
| 月之暗面 | `kimi-k3` | Moonshot OpenAI 兼容接口 |
| 千问 | `qwen3.8-max` | 阿里云百炼兼容接口 |
| SiliconFlow | `Qwen/Qwen3.6-35B-A3B` | `https://api.siliconflow.cn/v1/chat/completions` |
| 自定义 | 用户填写模型 ID | 用户填写 OpenAI 兼容 Base URL |

| 界面模型 | 模型 ID | 请求地址 | 用量标签 |
| --- | --- | --- | --- |
| 豆包 Seed 2.1 Turbo | `doubao-seed-2-1-turbo-260628` | `/api/coding/v3/chat/completions` | Coding Plan |
| MiniMax M3 | `MiniMax-M3` | `/api/coding/v3/chat/completions` | Coding Plan |
| 豆包 Seed 1.6 Vision | `doubao-seed-1-6-251015` | `/api/coding/v3/chat/completions` | Coding Plan |
| 豆包 Seed 2.1 Pro | `doubao-seed-2-1-pro-260628` | `/api/v3/chat/completions` | 按量计费 |
| 豆包 Seed 2.0 Pro | `doubao-seed-2-0-pro-260215` | `/api/v3/chat/completions` | 按量计费 |

## 安全与隐私

- 仓库和 Release 安装包不包含 API Key、用户提示词模板、最近项目、数据集路径或运行记录。
- 首次启动时 API Key、自定义接口、用户要求、系统提示词和预设库均为空；程序只保留公开服务商地址与模型路由。
- 火山引擎 API Key 加密到 `%LOCALAPPDATA%\MediaCaptionTool\credentials.bin`，其他平台使用独立的 `credentials-<平台>.bin`。
- 平台设置页按服务商只显示脱敏占位符；清空密钥会删除对应 DPAPI 密文，火山引擎还会清理旧版配置中的明文残留。
- 普通设置 JSON 只保存模型 ID、采样参数等非敏感设置，不保存 API Key 或认证头。
- HTTP 错误日志会清理 Bearer Key，并保留请求 ID 便于排查。
- 项目运行状态、失败清单和日志位于 `%LOCALAPPDATA%\MediaCaptionTool`。
- 媒体 Worker 只绑定 `127.0.0.1` 随机端口，认证令牌通过子进程环境传递，不写入配置文件；输入和输出必须位于本次任务授权目录。
- 每日自动备份只包含设置和项目元数据，默认排除所有 DPAPI 凭据文件；诊断包进一步移除密钥字段、完整标签正文和媒体内容。
- “删除项目”只删除应用自身的项目元数据，绝不递归删除媒体目录。
- 默认在真实启动后异步访问 GitHub 公共 Releases API 检查版本，不发送 API Key、项目路径或任何用户数据；可在平台设置中关闭。
- 用户确认后，软件从官方 GitHub Release 下载 Windows EXE/ZIP，校验体积、ZIP 完整性、EXE 格式和 Release SHA-256（如提供），退出后由独立更新器覆盖并重启。
- 独立更新器会切断旧 PyInstaller `_MEI` 运行环境，并等待旧版单文件进程完成清理，避免立即升级后出现 `python312.dll` 加载失败。
- 自动更新不会删除 API Key、项目记录或数据集；源码运行模式不会覆盖 Python 环境。

## 下载与运行

无需 Python 的用户可从 [GitHub Releases](https://github.com/wozhendemeiyou/qianyi-media-caption-tool/releases/latest) 下载 Windows 品鉴包，解压后运行 EXE。

源码运行需要 Python 3.12：

```powershell
git clone https://github.com/wozhendemeiyou/qianyi-media-caption-tool.git
cd qianyi-media-caption-tool
python -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe media_caption_tool_v3.py
```

首次使用时进入“平台设置”，选择运行后端、服务商和模型并填写自己的 Key；随后在“提示词”页编写或导入自己的系统模板。仓库没有示例 Key、示例模板，也不需要创建包含明文 Key 的配置文件。

视频完整性检查、抽帧和音频提取需要 FFmpeg。源码运行时可将 `ffmpeg`、`ffprobe` 加入 `PATH`，或将 `ffmpeg.exe`、`ffprobe.exe` 放入 `assets/media/`。未安装时图片功能和平台原生视频输入不受影响。

## 本地模型

“外部 API / 本地模型”可在任务开始前切换。本地后端加载用户指定目录中的 Hugging Face 视觉语言模型，目录必须包含 `config.json`。本地后端目前支持图片，固定单并发；视频仍使用外部 API。

- “启用 MTP”仅对包含原生 MTP 预测层、且当前 Transformers 版本支持 `use_mtp` 的本地模型生效；不兼容时自动使用普通生成。云 API 的 MTP 由服务商推理端决定。
- “移除思考标签”默认开启：兼容平台会尽量关闭思考输出，客户端还会清除 `<think>`、`<thinking>`、`<analysis>` 与 `<reasoning>` 区块，避免训练 TXT 混入推理过程。

根据显卡环境安装 PyTorch 后，再安装本地模型依赖：

```powershell
.\.venv312\Scripts\python.exe -m pip install -r requirements-local.txt
```

模型需要兼容 `AutoProcessor`，以及 `AutoModelForImageTextToText`、`AutoModelForVision2Seq` 或自定义 `AutoModelForCausalLM` 加载接口。

## 测试与压力验证

所有自动化测试和压力测试均使用模拟接口，不访问付费 API。

```powershell
.\.venv312\Scripts\python.exe -m unittest discover -s tests -v
.\.venv312\Scripts\python.exe tools\check_public_release.py
.\.venv312\Scripts\python.exe tools\stress_test.py --profile quick
.\.venv312\Scripts\python.exe tools\stress_test.py --profile standard
```

2026-08-10 标准档结果：

| 场景 | 规模 | 结果 |
| --- | --- | --- |
| 大目录扫描 | 10,000 个有效媒体、50 个坏文件、100 个孤立 TXT | 通过 |
| 并发批处理 | 2,000 项、10 并发、模拟失败 | 通过，峰值活跃请求 10 |
| 负载中取消 | 2,000 项 | 通过，取消延迟 0.459 秒 |
| JSONL/CSV 导出 | 10,000 条 | 通过 |
| 最坏重复图聚类 | 1,500 张同组图片 | 通过 |

标准档总耗时 57.299 秒，峰值 RSS 79.62 MB；完整报告见 [`docs/stress-report.json`](docs/stress-report.json)。这验证的是本地处理路径，不代表各平台账户的服务端限流或配额容量。

## 构建 Windows EXE

```powershell
.\.venv312\Scripts\pyinstaller.exe --noconfirm MediaCaptionTool-3.6.3.spec
```

构建配置只收集运行所需代码与视觉资源，不打入 API Key、用户设置、项目记录或本地模型权重。如果存在 `assets/media/`，构建时会一并收集 FFmpeg 媒体组件。

## 项目结构

```text
.
├── assets/                       # 启动页与应用图标
├── docs/                         # 图文手册、匿名产品截图、Release 说明和压力报告
├── tests/                        # 核心与 GUI 离线测试
├── tools/                        # 截图、压力测试和公开发布安全检查工具
├── media_caption_core.py         # 扫描、模型路由、HTTP、日志与批任务
├── media_caption_worker.py       # 按需媒体 Worker、安全通信与 FFmpeg 接口
├── media_caption_tool_v3.py      # Tkinter 桌面工作台
├── MediaCaptionTool-3.6.3.spec   # PyInstaller 单文件构建
├── CHANGELOG.md                  # 每次公开版本的完整更新记录
├── SECURITY.md                   # 凭据、模板、本地数据与发布边界
├── requirements.txt              # 基础依赖
└── requirements-local.txt        # 可选本地模型依赖
```

## 更新记录与发布规则

每个公开版本必须同步更新：

1. `APP_VERSION` 与界面内置本版说明；
2. [`CHANGELOG.md`](CHANGELOG.md)；
3. `docs/releases/vX.Y.md` GitHub Release 说明；
4. 最新匿名截图和 [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)；
5. Windows ZIP/EXE 与 SHA-256；
6. `tools/check_public_release.py`、自动化测试和 GUI 冒烟结果。

## 使用提示

- 真实 API 调用可能产生费用，请根据账户额度选择模型和并发数。
- Coding Plan 或账户侧可能限制并发；客户端支持 10 并发不代表所有账户都拥有 10 并发配额。
- 在批量处理前先用少量素材验证提示词、输出语言和模型计费方式。
- 对重要数据集执行孤立 TXT 删除、批量替换等操作前，建议保留备份。
