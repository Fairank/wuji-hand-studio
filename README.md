# 灵巧手工作台 / Hand Workbench

[English](#english) · [更新记录 / Changelog](docs/CHANGELOG.md)

**非官方个人展示工具，不是舞肌科技发布、维护或背书的产品。** 官方名称、示例和标识仅说明兼容对象和来源。

一个软件、一个图标、一个安装包。公开展示与自用模型共用同一套程序；私有模型通过菜单导入为本机数据，不再提供独立“基础版 / 研究版”软件。

## Windows 下载和启动

在 [Releases](../../releases) 选择实际已上传的安装包。Windows 0.2.9 是同一软件的原位更新，当前用户安装，无需另装 Python。Windows 安装器检查微软 WebView2 运行时，缺少时联网安装。包暂未进行发布者代码签名，附有 SHA-256 校验文件。源码分支与本地安装包的验证状态见 [0.2.9 验证记录](docs/VALIDATION_0.2.9.md)。

## 0.2.9 多手工作区与双手编排

发现设备后，可加入当前工作区或新建工作区；也可先创建空工作区，再添加设备。每只手仍保留独立的连接、参数、标定和手套映射，可在空闲时移动到其他工作区。右上角「多手」返回总览。

同一工作区可让 2–8 只手执行相同手指舞；左右配对新增跨手波浪、反向与往返波浪、中心涟漪、交替、同步绽放、十指钢琴和双波追逐共八组动作。双画面显示各自原生 MuJoCo 姿态，明确区分预览和实测反馈，可调整视角。详见 [多手编排说明](docs/MULTI_HAND_GROUPS.md)。

真实编排目前限二代手共享同一 Linux 控制端的时钟；同机内置 Linux 可作为控制端。独立 Linux 主机间的同步、一代真实群组编排、双手之间的避碰和实际机械同步精度尚未验收。控制故障或编排连接超时会请求全组停止，并分别报告停止确认。

本轮本地 Claude Opus 5.5 max 承担动作基础曲线、界面与说明，主助手设计并审核控制协调、设备隔离及验收；实际返回模型、完成状态和审核结果在本地记录。未向 Claude 发送私有训练模型或设备记录。

## 0.2.8 多手、手套与软件内标定

- 多工作区按设备序列号配对；各自保活、参数和节目单独立，可在总览查看配对与停止所有动作。
- 软件内执行官方六姿势手部模型标定，保留真实 CLI 诊断、成功结果与报告；取消会传到 Linux 中实际运行的官方进程。
- 20 关节输出幅度、偏移、平滑按设备组合保存，支持命名预设和手套预览应用；原始 SDK、映射后、已发送与实测角度分别显示。
- 一代手的手套跟随使用官方 LowPass 适配，二代继续使用关节命令接口。软件验证不等于具体硬件验收；内置 Linux 的一代 USB 访问仍需设备透传条件。
- 保留原生磨砂、黑白主题与同一个 Windows 软件。见 [连接与标定说明](docs/MULTI_HAND_GLOVE.md)。

基础代码可通过本地 Claude CLI 协作；当前用户指定 `claude-opus-5-5 --effort max`。通用映射数据保存/测试及六步标定界面交给 Claude，主助手审核设备控制、会话互斥与实际验证结果。认证、网络失败和实际返回模型分别记录，不把已派发当成完成。

## 0.2.7 原生磨砂与设置整理

按实际试用反馈，移除实验性桌面背景折射：不再采集桌面，不再经过 JPEG / WebView 传图，也没有折射开关或调节项。Windows 保留系统原生 Acrylic 磨砂；macOS 源码统一使用原生 vibrancy。旧实验记录仅供历史追溯，不是当前功能。

设置按外观、连接与设备、数据与工具分类。中英文、主题、减少透明和动态效果集中管理；手套可视化、官方标定、映射和多手工作区可以直接进入。右上角连接详情新增独立的手套状态和入口。修复反馈刷新反复重建节目单、打断正在编辑的速度和循环选项的问题。

全部需求与未验证事项见 [功能验收清单](docs/REQUIREMENTS.md)。本次本地 Claude CLI 不可用，没有新 Claude 调用；实现与审核由主维护者完成。物理设备验收仍按各功能分别记录。

## 0.2.2 连接与校准

动作页与工作台顶栏改为独立滚动层：向下查看动作时，页面标题不会滑进玻璃顶栏。移除四边模拟玻璃的浅色遮挡条，在桌面窗口留下更清楚的原生通透边缘。该历史版本曾包含实验背景折射，0.2.7 已移除。

“连接与校准”集中设备、实时可视化、手套校准和手套→手机械映射。可视化把当前三维机械手画面与手套原始 21 关键点骨架分开显示；无手套数据时明确显示空态。手套手型标定通过[官方 Wuji CLI 流程](https://docs.wuji.tech/docs/zh/wuji-cli/latest/hand-model-calibration/)完成，按命名 SDK 用户及左右手保存；覆盖已有标定必须勾选确认。标定不会驱动机械手。此版本已做程序逻辑与离线界面测试，**未完成手套实物六姿势标定验收**。工作台输出增益、偏移和平滑仍作用于官方 21→20 关节映射之后，保存后重连生效。

此版本的基础双语文案由本地 `claude-opus-5-5 --effort max` 辅助整理，实际返回模型同为 `claude-opus-5-5`，由主维护者检查后采用；关键控制和验收由主维护者负责。

## 0.2.1 界面修订

动作页把手指舞节目单折叠成一行，点击后再编排；播放期间自动展开，避免空节目单挤掉主要动作控件。白色与黑色主题都把通透材质集中在顶栏和导航，内容卡片使用清晰的实色与更明确的文字对比，键盘焦点、窄窗口布局和系统减少动态效果继续保留。设计参考苹果公开的人机界面规范和 iOS 27 的可读性方向；Windows WebView2 的实现不是苹果系统原生 Liquid Glass，也不宣称完成外部桌面背景的实时折射。

## 0.2.0 工作区与设置

设置页提供系统/白色/黑色外观、单只机械手只读自动发现、单只手套自动发现与仅预览连接。多手设备页可建立独立的左/右、一代/二代工作区，在同一窗口切换；每个工作区有自己的连接、参数、记录和手套映射。软件内的自动发现仍只覆盖官方二代出厂地址及已支持的接口；多只设备同时发现时需选择。创建工作区不会驱动电机。

手指舞节目单支持挑选动作、逐条倍速与次数、顺序/随机、整单循环、预览及显式实机启动。新增 0.1–4 倍的离散播放选项；这是轨迹时间倍率，不是电机控制频率或力度。高倍率受现有电机参数、动作时长和控制端拒绝条件约束，并未逐项完成实机验收。节目单任何一段中断都会停止后续动作。

手套设置在官方 SDK 完成 21 个关键点到 20 个手关节的映射后，提供 20 关节各自的输出幅度、角度偏移及平滑时间。默认设置不改变官方结果；这不是修改官方 SDK 的 IK 权重或标定。保存后需重连手套。遥操作仍需在各工作区明确选择并启动，自动发现本身不会启动机械手跟随。

Windows 用原生窗口材质和轻量模糊边缘；软件内的半透明面板与过渡可随系统减少透明设置回退。macOS 共用界面源码，但本轮没有在 Mac 上构建或实机验收。0.2.7 已移除外部背景折射。

## macOS 0.1.9 预览

Apple Silicon 版使用原生 Cocoa 窗口和 WKWebView，沿用同一软件、动作库和右上角连接入口。`.app` 内附 Ubuntu ARM64 磁盘与 Lima；首次点击安装内置控制环境需联网准备官方 SDK，以后连接时自动启动。没有 VMware 窗口，无需用户填写 SSH 配置。详见 [Mac 安装与已知限制](docs/MACOS.md)。

当前已上传源码，**Mac 安装包尚未生成**：本机 Mac 不在线，GitHub 登录缺少工作流写入权限。构建流程先保存在 `ci/macos.yml.example`，不将模板误报为已运行的 GitHub Actions。

0.2.7 起使用原生 vibrancy 磨砂，减少透明时使用实色。构建验证、视觉验收、真实设备验收分别记录，不能互相替代。此版为未公证的预览包，不宣称已完成 M4 Max 的 Linux 启动、实机或外部背景折射验收。内置 Linux 暂不透传一代 USB 手；二代以太网自动发现仍需真实 Mac 验证，自定义网段可手动填写，多设备需自行选择。

## 0.1.8 全局连接与界面更新

右上角常驻连接状态和连接 / 断开按钮，各页面都可使用。点击状态打开设备详情和多设备选择，复杂配置保留在连接页。动作执行中断开会先请求停止并等待确认。

点击“连接”时重新发现设备，读取设备自身的左右手身份，再同步型号、原生模型和反馈映射。留空地址时，内置 Windows 控制端尝试二代左右手的两个出厂地址；不会用上次序列号代替新发现结果。多只设备同时出现时提供选择框。自定义地址可手动填写。连接只接收反馈，不恢复上一轮动作。

这项修复需要同时更新桌面端和 Linux 控制程序，以及 `eclipse-zenoh==1.9.0` 依赖。旧桌面端继续使用明确选择的型号连接，避免新控制程序自动换手后旧界面仍使用错误模型。USB 自动转接、未知自定义网段自动搜索及 macOS 内置控制端不在本次已验证范围。本版使用配套控制镜像 1.0.1，已完成独立全新导入检查。从内置 1.0.0 升级会校验镜像哈希、备份旧控制文件并保留用户参数和记录；运行中的设备会话必须先结束。

Windows 使用独立原生窗口和内嵌 WebView2，不依赖浏览器应用窗口。只运行一个主实例；模型小窗由用户选择打开。退出时正常结束设备会话、保存采集记录并关闭所属本机服务。启动不自动连接设备或启用电机。

原有用户数据目录 `%LOCALAPPDATA%/WujiStudio` 保持不变，仅为升级兼容的内部路径，不代表另一款软件。`WUJI_STUDIO_DATA` 可指定独立数据目录。升级保留参数、连接配置和记录。端口在 8781–8800 中选空闲值，写入数据目录的 `native-window.json`。

## 功能

0.1.8 整理了动作页布局：型号选择移到连接页，常规幅度和倍速说明收进“动作来源与参数说明”，缩小数字幅度的提示仍直接显示。顶栏、侧栏和连接浮层采用统一的半透明材质、边缘高光和短过渡；内容区域保持清晰，支持减少透明和动画。

- 中文 / English；简洁白灰界面，导航、分段控件、工具条和弹窗使用玻璃材质；可减少透明及动态效果。Windows 的自有实现，不是调用苹果 Liquid Glass 系统 API。
- 一代 / 二代 × 左手 / 右手四种原生 MuJoCo 模型，实测关节同步、视角调整、浮动模型小窗。
- 动作选择和循环、整句文字（例如 `wuji tech`）、数字、启动时刻 HH:MM、九套编排手指舞。字母是参考 ASL 的固定手腕近似，不是 WUJI 官方动作或通用手语。见 [动作来源与倍速说明](docs/MOTIONS.md)。
- 0.1.6 使用中国常用单手数字比法：修正 4 的收拇指与 6–9；九套舞蹈均有明显侧摆。数字/舞蹈首次选择完整编排幅度，用户手动选择的幅度继续保留。控制端动作库版本 4 才支持本轮轨迹。
- 系统原生磨砂、清晰内容卡片及短过渡。实验背景折射已移除，不采集桌面。
- 参数调节、发送频率与真实反馈统计、采集和记录、官方诊断、手套映射预览及显式启动跟随。
- [代码检视与升级接口](docs/DESKTOP_API.md)：读取软件状态、界面布局、导出自身画面、检查和应用安装包。不发送桌面鼠标键盘输入，不截取其他软件。

## 设备与模型边界

Windows 的[内置轻量控制环境](docs/BUILT_IN_CONTROLLER.md)随安装包附带约 132 MiB 的 WSL 2 用户空间，不需 VMware 或 SSH 配置；首次启用缺少的 Windows 组件可能需要管理员确认及重启。也可继续选择实体 Linux 控制端。Ubuntu 可本机运行 SDK。Windows/Mac 内置环境仍在 Linux 中运行官方 SDK，一代 USB 自动转接尚未实现。详见 [控制与诊断说明](controller/README.md)、[手套说明](docs/GLOVE.md)（如对应版本提供）。

二代左右手使用各自官方录制；新型号路径及新增编排动作未逐项完成实机验收。一代目前只开放官方张开 / 握拳实机适配，其他动作只预览。软件的 1000 Hz 是目标发送节拍，实际发送与反馈频率分别显示，不等于画面帧率或逐帧执行保证。没有因桌面改版调整电机增益或轨迹参数。

公开仓库及安装器不含自训权重、训练记录、设备记录或凭据。私有数据包从用户自己的私有仓库获取，再导入同一程序。当前二代左手识别器只支持离线仿真反馈；电流 A 不能直接当作仿真力矩或接触力 N。导入模型不会开放实机自主轻扣；仍需标定和独立验收。

## 源码和构建

Python 3.12：`python -m pip install -r requirements.txt`，然后 `python src/desktop.py`。

离线检查：`python src/desktop.py --self-check --render-check`。测试：`python -m unittest discover -s src`。

Windows 构建需要先按 `src/runtime_manifest.json` 准备对应压缩镜像到 `runtime_payload/`（发布包附带镜像），不把运行环境提交到 Git。然后安装 `requirements-build.txt` 后运行 `python build.py`，再使用 `python build_installer.py --iscc PATH_TO_ISCC --webview-bootstrap PATH_TO_SIGNED_MICROSOFT_BOOTSTRAP --zh-language PATH_TO_CHINESE_ISL`。安装器使用 Inno Setup 6.4.3 和对应版本中文翻译。

Ubuntu 已有历史 0.1.3 包。macOS 的原生构建在 GitHub Mac runner 上进行，真实 Mac 与机械手验收仍待完成；不把 Windows 文件改名当作 Mac 安装包。

0.2.0 的本地 Claude 按用户当时偏好使用 `claude-opus-5-5 --effort max`，仅辅助边界明确的播放列表基础设计；实际返回模型为 `claude-opus-5-5`。输出经主维护者审核，控制逻辑、设备边界和真实结果由主维护者核验。代码接口用于检视软件，不操作桌面。来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## English

Version 0.2.9 adds logical multi-hand workspaces, explicit discovery-to-workspace assignment, shared same-action playback, eight authored left/right pair routines and actual per-session MuJoCo views. Real group playback currently requires native Hand 2 devices using the same Linux clock domain. Hardware acceptance and mechanical synchronization accuracy remain unverified. See [multi-hand guide](docs/MULTI_HAND_GROUPS.md) and [validation record](docs/VALIDATION_0.2.9.md).

**Hand Workbench is one unofficial personal hand demonstration application. It is not published, maintained or endorsed by Wuji Technology.** Windows uses an independent native window with embedded WebView2. One installer serves both demonstrations and optional local model packs; there is no separate Research application. Private weights are excluded from the public build.

Features include Chinese/English, minimalist glass chrome, four native hand profiles, text sequences, numbers, clock poses, authored finger dances, configurable parameters, feedback, recording, diagnostics and glove integration. Windows now bundles an optional minimal WSL 2 controller (~128 MB compressed), removing VMware and SSH setup from everyday use. Windows component setup may require administrator approval and a reboot. An external Linux controller remains available; native Windows SDK control and automatic Hand 1 USB passthrough are not claimed. New UI tests and synthetic mapping checks are not real hardware acceptance. The optional left-hand model is for offline simulation feedback only and cannot enable real-device grasping.

Use the [code-only maintenance interface](docs/DESKTOP_API.md) for inspecting the app, capturing its own WebView and applying a verified local installer. The maintenance interface never controls the computer's mouse/keyboard; its screenshots redact opt-in external refraction tiles. User data is preserved across upgrades. Packages are unsigned; check the trusted release and published SHA-256. Windows is the current release target; macOS validation remains pending.

Version 0.1.8 adds an always-visible connection control, discovered-device selection, refreshed translucent navigation and compact action layout. It pairs with controller image 1.0.1; upgrading the owned 1.0.0 environment preserves user parameters and records. No motor gains or trajectory settings are changed by this UI update.

Version 0.1.9 adds an Apple Silicon Cocoa/WKWebView preview and a bundled Ubuntu/Lima control runtime. Initial SDK setup requires internet; subsequent VM starts are managed by the app. Version 0.2.7 uses native vibrancy on macOS; experimental desktop refraction has been removed on Windows. Packages are ad-hoc signed, not notarized. Physical Mac VM startup, device operation and visual glass acceptance remain unverified. Hand 1 USB passthrough is not implemented; an external Linux controller remains available. The Mac workflow publishes a clearly marked prerelease only after a successful native build and bundle verification.
