# PySide6 现代 GUI 迁移说明（v3.6.demo）

## 为什么采用渐进迁移

当前工作台的模型路由、媒体 Worker、应用内更新和异常恢复已经稳定。一次性把数千行 Tk 控件重写成 Qt，容易重新引入字体裁切、主题残留、窗口恢复闪烁和本地模型流程回归。因此迁移版只替换 UI 层，继续复用 `media_caption_core.py` 与 `media_caption_worker.py`。

## 当前第一阶段已经接入

- 三列固定比例工作区：平台设置/采样参数、素材区、结果与运行日志；
- 外部 API、Hugging Face、LM Studio、llama.cpp 原生 GGUF 的设置入口；
- API Key 仍通过 `SettingsStore` 使用 Windows DPAPI 保存，不写入仓库或普通界面日志；
- 图片/视频目录扫描、拖放素材、单次反推和批量反推；
- BatchRunner 后台线程、取消任务、进度、耗时、字数和字数/秒反馈；
- 日光/夜光主题使用一次性 QSS 重绘，不使用过渡动画，避免旧主题颜色残留；
- 参数预设、Seed 随机按钮、并发数、MTP 和思考标签开关；
- 迁移期间可从顶部打开经典 Tk 工作台，继续使用更新下载、媒体编辑器、项目中心和所有尚未搬完的细节功能。
- Qt 专项回归覆盖三列布局、主题即时切换、后端/运行时控件灰置逻辑和可选依赖缺失回退。

## 下一阶段接入顺序

1. 将平台设置中的 LM Studio 刷新/加载/卸载按钮接入 Qt 原生对话框；
2. 将系统说明、重大更新和应用内覆盖安装迁移到 Qt；
3. 接入视频片段时间轴、音频封装、项目恢复和导出 JSONL/CSV；
4. 以 Qt 版本执行真机 API、LM Studio 和 llama.cpp 回归后，再考虑替换默认 EXE 入口。

## 运行

```powershell
.\.venv312\Scripts\python.exe -m pip install -r requirements-qt.txt
.\.venv312\Scripts\python.exe media_caption_qt.py
.\.venv312\Scripts\python.exe media_caption_qt.py --smoke-test
```

`--smoke-test` 只验证三列界面、后端选项和本地运行方式，不访问网络、不读取模型权重，也不会产生 API 费用。

演示构建使用 `MediaCaptionTool-3.6.demo-Qt.spec`。固定文件版本保持数字形式 `3.6.0.0` 以兼容 Windows，界面与产品版本显示为 `3.6.demo`。`MediaCaptionTool-3.6.demo.spec` 仍用于构建经典 Tk 回退版。
