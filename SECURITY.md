# 安全与隐私说明

## 公开仓库与安装包不包含什么

每次公开发布前会运行 `tools/check_public_release.py`。仓库和 Windows Release 包不得包含：

- API Key、Bearer Token、GitHub Token 或私钥；
- `credentials.bin`、`credentials-*.bin`、`settings.json`、`config.json`；
- 用户创建或导入的提示词模板；
- 最近项目、数据集路径、本地模型目录、项目日志或训练素材；
- Windows/macOS 用户目录等个人绝对路径。

公开版首次启动时：

- API Key 为空；
- 自定义接口地址为空；
- 用户要求为空；
- 提示词预设库为空；
- 系统提示词编辑器为空。

代码中的服务商域名、公开 API Base URL、模型 ID 和官方控制台入口属于程序联网所必需的公开路由，不是用户凭据。

## 本地数据保存位置

应用数据默认位于：

```text
%LOCALAPPDATA%\MediaCaptionTool\
```

其中：

- `settings.json`：界面偏好、模型 ID、采样参数等非敏感设置；
- `credentials.bin`：火山引擎密钥的 DPAPI 密文；
- `credentials-<平台>.bin`：其他平台独立的 DPAPI 密文；
- `projects/`：任务状态、失败记录和恢复信息；
- `backups/`：默认不含凭据的轮换备份；
- `diagnostics/`：脱敏诊断包。

DPAPI 密文只能由保存它的 Windows 账户在本机解密。清空平台密钥会删除相应密文文件。

## 网络边界

- 只有用户启动生成任务或连接测试时，应用才会访问所选模型平台。
- 启用自动更新检查时，应用只访问本仓库的 GitHub Releases 公共 API，不发送项目路径、提示词、密钥或素材。
- 媒体 Worker 只监听 `127.0.0.1` 随机端口，使用一次性令牌，并限制在本次任务授权目录内。
- 生成请求默认不自动重试，避免超时后重复计费。

## 发布前检查

```powershell
.\.venv312\Scripts\python.exe tools\check_public_release.py
.\.venv312\Scripts\python.exe -m unittest discover -s tests -v
.\.venv312\Scripts\python.exe media_caption_tool_v3.py --smoke-test
```

发现安全问题时，请不要在公开 Issue 中粘贴真实密钥、完整请求头、项目路径或训练素材。
