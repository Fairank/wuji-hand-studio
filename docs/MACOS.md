# Hand Workbench on Mac / macOS 0.2.19

非官方个人展示工具。0.2.17 基于 Windows `v0.2.16-windows`（`d8a9f49`），使用原生 Cocoa / WKWebView，保留节目单、顺序与随机播放、多手工作区、连接、官方校准和映射页面。

## 安装与启动

Apple Silicon、macOS 13.5+。解压 `HandWorkbench-0.2.19-macos-arm64.zip`，核对配套 SHA-256，将 `HandWorkbench.app` 放入应用程序。无需另装 Python、Homebrew、VMware 或填写 SSH。安装包含 Lima 和 Ubuntu ARM64 磁盘，不代表 SDK 首次准备无需联网。

本地包为 ad-hoc 签名，尚无 Apple Developer ID 签名或公证。来源和摘要核对后，如系统阻止启动，使用“隐私与安全性”明确允许；不要关闭 Gatekeeper 或批量删除隔离属性。GitHub 是否已有安装包，以实际 Release 附件为准。

应用数据继续使用 `~/Library/Application Support/WujiStudio`，不清空既有参数、SDK 配置、标定备份。启动与页面切换默认不连接硬件、不播放实机动作。只读状态轮询不会为刷新校准列表启动 Linux；明确点击刷新、安装、诊断或连接时才允许访问控制环境。

## 原生磨砂玻璃

macOS 26+ 使用 `NSGlassEffectView` 的 Clear 表层，底层是 `NSVisualEffectView` 的 `underWindowBackground + behindWindow` 磨砂材质，避免 Regular 全窗叠层再次遮住背景。较旧系统使用同一 behind-window vibrancy。顶部及侧栏保留透明背景，主要内容使用局部半透明阅读面。明暗主题与原生材质同步，减少透明时恢复实色，遵循系统辅助功能设置。材质用途见 [Apple 官方说明](https://developer.apple.com/documentation/appkit/nsvisualeffectview/material-swift.enum/underwindowbackground)。

保留系统关闭、最小化、缩放和拖动行为。没有屏幕录制、其他窗口像素采集或自制外部折射。公开系统材质会处理背景，实际效果随系统、背景及窗口活跃状态变化，并不保证和 Windows Acrylic 逐像素一致。

Mac 的透明 WebView 在重新附着窗口材质时可能停住装饰性 CSS 时间线，因此原生窗口关闭这类过渡，保证文字和选中状态立即可见；不影响 3D 帧、动作编排或控制频率。

右上角设备标题统一为 `wuji hand`；型号、手性与序列号仍由设备连接信息确认。

0.2.19 修复动作预览状态、动作名称和画面来源标识的英文切换，暂停时也立即同步。语言胶囊、三点菜单与打开的连接面板采用统一的浅深主题。Mac 顶部空白栏、WUJI 标志和工作台标题可以拖动窗口；按钮、下拉框、模型视角与浮窗拖动不受影响。使用 [pywebview 的公开拖动区域设置](https://pywebview.flowrl.com/api/)，不改系统全局鼠标设置或安全权限。

0.2.18 取消深色外围的重叠黑底，并统一语言胶囊、设置与弹窗控件。设置 → 外观可选择 90/100/110% 文字大小，仅改变界面排版。展示动作中新增三个“大幅度预览 · 仅预览”，用于观看模型关节范围，不能用于真实手播放或实机轨迹导出。数字手势是按官方几何精修的项目创作，不是官方真人录制、触碰认证或实物验收。

## Linux 与设备边界

首次点击“安装内置控制环境”会校验随包镜像，并联网准备固定版本 SDK。默认 2 CPU、2 GiB 内存、最大 12 GiB 虚拟磁盘；之后明确连接时由应用管理启动，不需打开额外虚拟机界面。

Linux 使用应用自己的 `~/.hand-workbench-runtime`，不改个人 `~/.lima`，不导入个人 SSH 密钥或转发 agent，不共享个人文件夹。控制代码按内容摘要部署为不可变版本，避免新版界面调用旧版控制程序。

只有全部工作区和设备会话安全结束后，正常退出才尝试关闭这个带所有权标记的 Linux；不会强杀其他虚拟机，也不会在安装进行中终止环境。

二代以太网连接需要可达的专用网卡；网络配置请求 macOS 管理员授权，排除默认路由和其他已使用网段。多设备必须明确选择。物理网口亮灯不等于 SDK 已发现或可收到有效反馈。

一代 USB 透传尚未实现；一代模型存在不代表内置 Linux 能访问一代 USB 手，需要外部 Linux 控制端。此次软件移植没有执行真实手套标定、实体手连接或电机命令。

当前锁定的开源求解器只在 Linux x86_64 / Python 3.12 通过验证，Mac ARM64 内置模式返回明确不支持，并禁用其安装入口。SDK 映射、参数草稿与 YAML 导出仍可用；不伪称 ARM 环境已完成真实求解验收。

## 构建与验证

在 Apple Silicon 构建机准备 Python 3.12、requirements.txt、PyInstaller；准备镜像时需 qemu-img。

```sh
python scripts/prepare_macos_payload.py
python build.py
python scripts/verify_macos_bundle.py
open -n dist/HandWorkbench.app
```

可设置 `WUJI_DIST_DIR` 将生成物放到指定缓存目录。`--binary-only` 不生成 ZIP；`--package-only` 复用已生成的可执行程序，仅更新随包资源、签名和归档。不要把更新了源代码却没重建的可执行程序当成新版。

`scripts/workbenchctl.py` 只检查或操作应用自己的窗口和固定功能，不提供任意 shell/JavaScript 执行。Mac 桥使用闭包和 WebKit 公共接口，保持页面 `script-src 'self'`，不启用 unsafe-eval。

架构、签名、镜像摘要、模型离线渲染、原生界面交互和实际硬件必须分别验收。验证记录见 [0.2.17 移植报告](MACOS_PORT_0.2.17.md) 和 [0.2.18 精修报告](MACOS_REFINEMENT_0.2.18.md)。

## English

Native Apple Silicon Cocoa/WKWebView port of Windows v0.2.16. macOS 26+ uses system frosted Liquid Glass, with native vibrancy on older supported systems. Linux ARM64 is bundled; first SDK preparation still needs networking. No external screen capture, no automatic motor playback. Ad-hoc signed, not notarized. Hand 1 USB passthrough and the verified x86_64 open solver are not supported by the built-in ARM runtime. SDK mapping and draft/export remain available. Software acceptance does not establish hardware acceptance.
