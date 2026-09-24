# 更新记录 / Changelog

## 0.2.11 — 官方标定反馈与可编辑参数矩阵

- 修复官方 SDK 回调的六姿势编号、姿势名称和 progress 字段解析；稳定性、约束分别显示官方结果，缺失数据保持未知。
- 标定设置集中在「选择用户与手套」，运行中收起；官方采集状态每 500 ms 更新。继续由官方 CLI 采集、求解和发布模型，没有另造标定算法。
- 20 关节幅度/偏移改成五指 × 四关节数值表，支持矩形粘贴、完整数值检查、切换语言保留未完成输入。
- 新增独立的官方开源映射参数草稿表：五指目标比例、对指距离和输出平滑；按配对隔离保存，带版本冲突检查，导出完整 YAML。
- 四套官方 YAML 固定至提交 531f6ed4，保留路径、其他权重、原始配置、校验和及 MIT 许可。与当前 SDK 内置映射分开显示，不冒称保存后已实时应用。
- 通用表格由本地 Claude Opus 5.5 max 辅助，审核后采用。没有调整电机参数或启动实机；验证范围见 [记录](VALIDATION_0.2.11.md)。

Official calibration feedback is now visible, and output settings use editable numeric matrices. A separate per-pairing editor exports the pinned open-retargeting YAML; it does not modify the current SDK solver. See [scope and workflow](CALIBRATION_AND_TUNING.md).

## 0.2.10 — 简化单手展示、多手演示与设备管理

此前播放、参数说明和设备管理堆在同一页，操作需要反复向下翻。这次把常用步骤留在画面旁边，低频设置按需打开。

- 主导航：单手展示、多手演示、连接与校准；反馈、参数、诊断和记录收进调试工具。
- 单手展示：先选真实手／预览，再选动作和速度；循环、幅度可展开，节目单和运行详情使用独立弹窗。节目单收起后仍可看状态并停止。
- 多手演示：参与手、同步动作／双手衔接与双画面集中展示。「管理设备」内分已添加、添加设备和工作区三个页签，保留发现、分配、移动、移除和独立设置。
- 清理隐藏面板占位、非当前页面的装饰残留、重复画面来源按钮和过时的启动提示；改善黑色主题的选中对比与窄窗口布局。原截图白条的确切成因尚未完全复现，验证范围见下方报告。
- 保留真实反馈／预览来源、错误状态、停止入口，以及手套可视化、官方标定、映射。没有调整电机增益、轨迹、发送频率或控制边界。

Windows 源码检查为 463 项通过、2 项跳过。两个独立预览进程通过完整播放、手动停止、控制超时和设备移动检查；已验证弹窗、键盘切换、语言和明暗模式。安装包及原生窗口结果见 [0.2.10 验证记录](VALIDATION_0.2.10.md)。本轮没有驱动实机。

本地 Claude Opus 5.5 max 辅助通用弹窗基础代码；主助手负责布局决策、接入审核和实际验收。继续使用一个安装包、原有用户数据目录和系统磨砂，不恢复桌面截图折射。

### English

0.2.10 simplifies the single-hand and ensemble pages. Common playback controls sit beside the views; playlists, run details and device administration open on demand. Device management separates existing hands, discovery/addition and workspaces. Hidden-panel paint artifacts, duplicate controls, stale request messages and dark selected-state contrast were corrected. Hardware control behavior is unchanged. See the linked validation report for software/package evidence and physical-test limitations.

## 0.2.9 — 多手工作区与双手连续动作

这次把“每只手只能单独开工作区”扩展成“一个工作区可以管理多只手”。发现设备后，可以加入当前工作区，也可以新建工作区；原有单手连接、参数、手套配对及标定继续独立保存。

### 新增

- 右上角多手入口；新建空工作区、发现设备后分配、空闲设备移动及离线模型预览。
- 同一工作区内 2–8 只手执行同一个手指舞，或一左一右执行连贯编排。
- 八组双手动作：跨手波浪、反向波浪、往返波浪、中心向外涟漪、双手交替、同步绽放、十指钢琴、双波追逐。
- 各只手独立的原生 MuJoCo 画面、正面视角与拖动调视角，明确标出预览、实测反馈或参考姿态。
- 速度、幅度及循环设置；共同开始时间，并按最慢参与手调整各段时长。
- 全组停止与停止全部手，展示设备级错误和未确认的停止。

### 修复与整理

- 同一只设备连接尚未完成时，重复添加会被识别，避免创建重复会话。
- 一只手准备失败时清理整组；控制连接超时会结束整组；启动过程中点击停止也会继续处理。
- 多手页面刷新和中英文切换保留当前选择；修正代次显示、指令字段和成员状态。
- 修复新双手画面的裁切与重复标题，保留黑白主题和原生磨砂，没有恢复桌面折射。

### 已验证与尚未验证

Windows 463 项通过、2 项跳过；Linux 33 项通过。两个独立预览进程及打包程序通过完整播放、手动停止、控制超时与设备移动检查。已原位安装并检查原生窗口，保留用户数据。详细证据见 [0.2.9 验证记录](VALIDATION_0.2.9.md)。

本轮没有驱动真实机械手。真实多手编排目前需要二代手共享同一 Linux 时钟域；一代群组动作仅预览。多底座之间的碰撞、实物手套组合和机械同步精度尚未验收。这些动作是自编轨迹，不是官方录制。

本地 Claude Opus 5.5 max 完成五项基础代码、界面、测试和文档任务；主助手设计并审核控制协调与验收。公开软件不含私有训练模型和凭据。

## English

0.2.9 adds multi-hand membership within one workspace, discovery-to-workspace assignment, eight paired routines, same-action groups and per-session MuJoCo views. It fixes duplicate connection attempts, partial-start cleanup, stop-during-start handling, changing UI selections and clipped previews. Existing glove pairing, official calibration and mapping presets remain separate for each hand.

The Windows app was upgraded in place and verified without physical motion. Software tests passed as reported above; real grouped Hand 2 playback requires one Linux clock domain. Physical performance, inter-hand collision checking and mechanical synchronization accuracy remain unverified.
