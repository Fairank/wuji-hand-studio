# 灵巧手工作台 / Hand Workbench

[English](#english)

**非官方个人展示工具，不是舞肌科技发布、维护或背书的产品。** 官方名称、示例和标识仅说明兼容对象和来源。

一个软件、一个图标、一个安装包。公开展示与自用模型共用同一套程序；私有模型通过菜单导入为本机数据，不再提供独立“基础版 / 研究版”软件。

## Windows 下载和启动

在 [Releases](../../releases) 下载 `HandWorkbench-0.1.6-windows-x64-setup.exe`。当前用户安装，无需另装 Python。安装器检查微软 WebView2 运行时，缺少时联网安装。包暂未进行发布者代码签名，附有 SHA-256 校验文件。

Windows 使用独立原生窗口和内嵌 WebView2，不依赖浏览器应用窗口。只运行一个主实例；模型小窗由用户选择打开。退出时正常结束设备会话、保存采集记录并关闭所属本机服务。启动不自动连接设备或启用电机。

原有用户数据目录 `%LOCALAPPDATA%/WujiStudio` 保持不变，仅为升级兼容的内部路径，不代表另一款软件。`WUJI_STUDIO_DATA` 可指定独立数据目录。升级保留参数、连接配置和记录。端口在 8781–8800 中选空闲值，写入数据目录的 `native-window.json`。

## 功能

- 中文 / English；简洁白灰界面，导航、分段控件、工具条和弹窗使用玻璃材质；可减少透明及动态效果。Windows 的自有实现，不是调用苹果 Liquid Glass 系统 API。
- 一代 / 二代 × 左手 / 右手四种原生 MuJoCo 模型，实测关节同步、视角调整、浮动模型小窗。
- 动作选择和循环、整句文字（例如 `wuji tech`）、数字、启动时刻 HH:MM、九套编排手指舞。字母是参考 ASL 的固定手腕近似，不是 WUJI 官方动作或通用手语。见 [动作来源与倍速说明](docs/MOTIONS.md)。
- 0.1.6 使用中国常用单手数字比法：修正 4 的收拇指与 6–9；九套舞蹈均有明显侧摆。数字/舞蹈首次选择完整编排幅度，用户手动选择的幅度继续保留。控制端动作库版本 4 才支持本轮轨迹。
- [实验性外部背景折射](docs/EXTERNAL_REFRACTION.md)：菜单明确开启后在本机处理外部图案，默认关闭；不作为苹果系统效果复刻。内部工具条、控件与短过渡进一步统一。
- 参数调节、发送频率与真实反馈统计、采集和记录、官方诊断、手套映射预览及显式启动跟随。
- [代码检视与升级接口](docs/DESKTOP_API.md)：读取软件状态、界面布局、导出自身画面、检查和应用安装包。不发送桌面鼠标键盘输入，不截取其他软件。

## 设备与模型边界

Windows 新增[内置轻量控制环境](docs/BUILT_IN_CONTROLLER.md)，随安装包附带约 128 MB 的 WSL 2 用户空间，不需 VMware 或 SSH 配置；首次启用缺少的 Windows 组件可能需要管理员确认及重启。也可继续选择实体 Linux 控制端。Ubuntu 可本机运行 SDK。它仍在 Linux 中运行官方 SDK，不是原生 Windows SDK；一代 USB 自动转接与 macOS 内置控制端尚未实现。详见 [控制与诊断说明](controller/README.md)、[手套说明](docs/GLOVE.md)（如对应版本提供）。

二代左右手使用各自官方录制；新型号路径及新增编排动作未逐项完成实机验收。一代目前只开放官方张开 / 握拳实机适配，其他动作只预览。软件的 1000 Hz 是目标发送节拍，实际发送与反馈频率分别显示，不等于画面帧率或逐帧执行保证。没有因桌面改版调整电机增益或轨迹参数。

公开仓库及安装器不含自训权重、训练记录、设备记录或凭据。私有数据包从用户自己的私有仓库获取，再导入同一程序。当前二代左手识别器只支持离线仿真反馈；电流 A 不能直接当作仿真力矩或接触力 N。导入模型不会开放实机自主轻扣；仍需标定和独立验收。

## 源码和构建

Python 3.12：`python -m pip install -r requirements.txt`，然后 `python src/desktop.py`。

离线检查：`python src/desktop.py --self-check --render-check`。测试：`python -m unittest discover -s src`。

Windows 构建需要先按 `src/runtime_manifest.json` 准备对应压缩镜像到 `runtime_payload/`（发布包附带镜像），不把运行环境提交到 Git。然后安装 `requirements-build.txt` 后运行 `python build.py`，再使用 `python build_installer.py --iscc PATH_TO_ISCC --webview-bootstrap PATH_TO_SIGNED_MICROSOFT_BOOTSTRAP --zh-language PATH_TO_CHINESE_ISL`。安装器使用 Inno Setup 6.4.3 和对应版本中文翻译。

本次优先完成 Windows；Ubuntu 已有历史 0.1.3 包，macOS 源码入口见 [Mac 指南](docs/MACOS.md)，尚未在 Mac 验证新桌面版本。不把 Windows 文件改名当作其他系统安装包。

本地 Claude `claude-fable-5-1 --effort max` 协助基础 CSS、安装脚本、常规统计和文档；输出经主维护者审核修正。代码检视代替桌面操作。控制决策、模型边界和真实结果由主维护者核验。来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## English

**Hand Workbench is one unofficial personal hand demonstration application. It is not published, maintained or endorsed by Wuji Technology.** Windows uses an independent native window with embedded WebView2. One installer serves both demonstrations and optional local model packs; there is no separate Research application. Private weights are excluded from the public build.

Features include Chinese/English, minimalist glass chrome, four native hand profiles, text sequences, numbers, clock poses, authored finger dances, configurable parameters, feedback, recording, diagnostics and glove integration. Windows now bundles an optional minimal WSL 2 controller (~128 MB compressed), removing VMware and SSH setup from everyday use. Windows component setup may require administrator approval and a reboot. An external Linux controller remains available; native Windows SDK control and automatic Hand 1 USB passthrough are not claimed. New UI tests and synthetic mapping checks are not real hardware acceptance. The optional left-hand model is for offline simulation feedback only and cannot enable real-device grasping.

Use the [code-only maintenance interface](docs/DESKTOP_API.md) for inspecting the app, capturing its own WebView and applying a verified local installer. The maintenance interface never controls the computer's mouse/keyboard; its screenshots redact opt-in external refraction tiles. User data is preserved across upgrades. Packages are unsigned; check the trusted release and published SHA-256. Windows is the current release target; macOS validation remains pending.
