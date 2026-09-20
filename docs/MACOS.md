# Hand Workbench on Mac / Mac 预览版

0.1.9 是同一个非官方个人展示软件的 Apple Silicon 原生 Cocoa / WKWebView 版本，沿用中英文、动作库、参数页面、右上角连接入口及独立 MuJoCo 小窗。

## 安装

仅下载 Releases 中实际存在的 `HandWorkbench-0.1.9-macos-arm64.zip`，核对 SHA-256，解压后把 `HandWorkbench.app` 放入“应用程序”。不需另装 Python、Homebrew、VMware 或填写 SSH。需要 macOS 13.5+ 和 Apple Silicon；Intel Mac 暂无此内置环境安装包。

包采用本地临时签名，尚未获得 Apple Developer ID 签名和公证。首次可能被 Gatekeeper 阻止；核对来源后使用系统“隐私与安全性”中的明确允许操作，不要关闭系统检查或批量删除隔离属性。

第一次在连接页点“安装内置控制环境”：校验附带 Ubuntu ARM64 磁盘、Lima 和官方诊断 CLI，在软件自己的目录创建 Linux，联网安装固定版本 SDK。需要能访问 Ubuntu 软件源和 PyPI，准备进度和错误直接显示。默认 2 CPU、2 GiB 内存、最大 12 GiB 虚拟磁盘；实际磁盘占用随使用增长。之后连接时自动启动，无需另开虚拟机。

Lima 管理自己的内部密钥和本地传输，不导入个人 SSH 密钥、不转发 SSH agent。只共享软件自己的控制目录，不共享个人文件夹。安装、启动和连接均不自动播放动作。

## 插手和换手

二代手使用专用以太网或 USB 网卡。右上角“连接”会检查当前网卡；唯一专用网卡缺少设备子网时，请求 macOS 管理员授权添加局部地址，不改 Wi-Fi、默认网关及正在使用的其他网段。多个候选网卡在软件内选择。系统重启后临时地址可能需要再次授权。

每次重新调用官方 SDK 发现设备，验证型号和真实左右手身份，然后同步模型和反馈；不把上一次序列号当成新发现。多只手由用户选择。内置模式尝试二代左右手出厂地址；自定义地址可填写，未知任意网段和其他厂商不在自动兼容承诺中。

**一代 USB 透传尚未实现。** 四类原生模型及 SDK 适配不等于 Mac 内置 Linux 已能获取全部 USB 设备。一代 USB 手请使用外部 Linux 控制端，不能把二代以太网测试当作一代通过。

## 玻璃与代码检视

macOS 26 动态检查公开 `NSGlassEffectView`；旧系统使用 `NSVisualEffectView` 原生通透材质，减少透明时回退实色。保留系统标题栏、窗口按钮与调整大小行为，工作区保持易读。没有抓取其他窗口或屏幕。系统玻璃及外部图案折射的实际观感需真机验收，源码接入不等于视觉通过。

代码检视接口可以查看软件状态和自身 WKWebView 截图，不控制电脑鼠标键盘。自身截图不能证明其他窗口背景的折射效果。

## 构建和验收

在 Apple Silicon Mac 安装 Python 3.12、requirements.txt、PyInstaller；构建机另需 qemu-img（例如 Homebrew qemu），仅转换构建磁盘，用户安装不用。

```sh
python scripts/prepare_macos_payload.py
python build.py
python scripts/verify_macos_bundle.py
open -n dist/HandWorkbench.app
```

构建脚本从官方固定版本下载并校验摘要，不能导出个人 VM 作为镜像。Linux 和助手在 `.app` 内，单独拖动应用不会丢失组件。首次准备 SDK 仍需网络，不能称完全离线安装。

构建流程已保存在 `ci/macos.yml.example`。当前 GitHub 登录缺少 `workflow` 权限，服务器拒绝创建正式工作流，因此尚未生成或上传 Mac 安装包。获得授权后可将模板放到 `.github/workflows/macos.yml`，在 Mac ARM64 runner 原生构建；通过后上传包和 `macos-validation.json`。架构、签名完整性和文件哈希不等于 VM 启动、实机连接或玻璃视觉验收。

Linux 独立目录为 ~/.hand-workbench-runtime，不改个人 ~/.lima。使用短路径避免 macOS 本地套接字路径长度限制。

数据路径沿用 `~/Library/Application Support/WujiStudio`，保留已有参数；日志 `desktop.log`。源码克隆可运行 `bash scripts/run-macos.command`，会创建项目内 `.venv-macos`。未准备 Linux 组件时仍可看界面、MuJoCo 或使用自行选择的外部 Linux。

历史源码启动脚本由本地 Claude Opus 5 max 协助，主维护者审核。此次原生材质基础代码由本地 `claude-fable-5-1 --effort max` 完成，已审核并修正窗口生命周期和错误回退后采用；24 项 Mac 逻辑测试通过，尚未真实 Mac 视觉验收。

## English

Same unofficial app, native Cocoa/WKWebView on Apple Silicon macOS 13.5+. Ubuntu disk and Lima are bundled; initial SDK preparation needs internet. Later VM starts are app-managed. No VMware UI or user-managed SSH credentials. Dedicated Ethernet setup requests macOS approval and excludes default-route or already-configured networks. Every connection discovers and verifies the current hand; multiple hands require selection. Hand 1 USB passthrough is not implemented. macOS 26 glass falls back to native vibrancy on older systems. Ad-hoc signed, not notarized. Real VM/device and visual acceptance are separate from package checks.
