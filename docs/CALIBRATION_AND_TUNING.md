# 官方标定与参数表 / Calibration and tuning

工作台是非官方个人工具。「连接与校准」页继续调用官方 CLI 采集和标定。0.2.19 按用户最终选择取消 Studio 嵌入，采用内置官方 CLI / SDK 2026.10.12、配套控制环境 1.1.0；保留一个工作台和原用户数据。Studio 最终版 2026.9.22 已停止维护，历史与旧档案边界见 [Studio 迁移说明](OFFICIAL_STUDIO.md)。

## 先检查官方组件

进入「设置 → 连接与设备 → 官方 CLI / SDK」。需要时安装或更新内置控制组件，再点击「检查 CLI / SDK」。环境记录只说明组件安装状态；实际版本来自本次 CLI 版本命令和 SDK 导入检查，分别显示检查时间与配套目标。未检查、检查失败或服务失联保持待核对，不能将目标版本当作实际运行结果。

软件检查不扫描或连接手套、机械手，不开始标定、不使能电机，也不升级设备固件。Windows 系统支持不足时，系统组件安装可能要求管理员确认或重启；完成后返回同一卡片继续。校准仍是之后单独选择的设备操作。0.2.19 完整验证状态见 [验证记录](VALIDATION_0.2.19.md)。

## 在软件内标定

打开「连接与校准 → 手套校准」，在「选择用户与手套」中选择命名 SDK 用户、捕获手套和左右侧。Default 不能保存人体模型。已有模型需要明确选择覆盖。开始调用 `wuji --jsonl calib hand-model`，并检查当前用户、设备侧与跨工作区占用；不发送机械手运动指令。

工作台不再提供额外的六姿势浏览器、示意图或可选择的姿势卡片。页面只显示本次官方 CLI 正在报告的步骤、进度、结果与错误；姿势名称是官方步骤的中文或英文标签，不是工作台另外编排的采集流程。未报告当前步骤时保持等待，不默认选择第一步，也不按经过时间推算进度。原始事件和官方诊断收在运行详情中。

采集、稳定性判定、约束判定、求解和模型发布均由官方工具完成。当前姿势采集完成不代表整次标定成功；必须收到正式 result 且进程退出码为 0。页面刷新官方采集状态的间隔为 500 ms，不改变 SDK 采样频率。

0.2.19 分开保留校准操作错误与状态读取错误。刷新失败时显示官方状态尚未确认，并禁用依赖旧状态的启动操作；刷新成功只清除本次读取错误，不抹掉之前的操作失败。服务恢复不能被记为标定完成。

成功模型仍按 SDK 用户及左/右侧保存在官方目录。工作台保存 CLI 事件报告，但尚未实现 Studio 的全传感器 Calibration Debug 录制、完整人体 URDF 查看器或官方动作插图。官方插图通过页面链接查阅。新 UI 的反馈解析经过软件样例测试；真实手套的端到端标定仍需设备验收。

## 两种参数表各做什么

### 重启、换设备与换电脑

手套重启不会清除已经成功保存的 SDK 标定。手型标定按 SDK 用户与左右侧保存到控制环境的 `~/.wuji/sdk/users/<profile-id>/models/{left,right}_hand.urdf`；同一用户的同侧手套共用该手型。触觉标定则另按用户与手套编号保存到 `~/.wuji/sdk/users/<profile-id>/tactile/<glove-sn>/`。Default 档案不保存这些个人标定。

这些文件在运行 SDK 的电脑/系统用户环境里，而不是靠手套上电记忆。Windows 内置 Linux、实体 Linux 与原来运行 Studio 的电脑可能是不同环境；换环境时需使用官方用户档案导入/导出。工作台的映射参数另外按设备配对保存，只改变骨架到机械手目标的换算，不改写原始传感器、官方人体手型或手套固件；只有明确开启实机跟随才发送相应动作。

手套连接使用所选 SDK 用户，并保持到该会话结束；断开设备和会话对象后才恢复先前用户。此修复避免初始化后立即切回另一个用户而重载错误的参数，不能替代真实手套的标定准确性验收。

Hand 1 的实测显示按设备编号 0–19 读取，Hand 2 按各指四关节编号读取。代际未知、反馈陈旧或缺关节时，不用映射目标填补实测姿态。隐藏页面停止图像请求、解码与无需求渲染；反馈接收、租约心跳和设备控制不随显示暂停。

断线、进程退出或收到停止请求，都不自动证明电机已经停用。停用未知按设备和会话保存；新连接、切换用户或另一只设备的停用确认不会清除旧记录。需要对应设备/会话的明确确认，相关退出或升级门槛才可解除。

### 已取消的 Studio 嵌入与可视化边界

工作台统一调用官方 CLI，不再维护重复的自制姿势示意功能。0.2.18 曾有完整 Studio 页面，0.2.19 已取消其入口、安装和打开功能；旧组件只保留状态核对及关闭兼容，不主动删除 Studio HOME 和标定档案。S1–S3 Studio 改造不作为本版功能或验收成果。原版人体模型、触觉和录制不能由当前工作台画面或历史离线显示结果推定为已经验收。

0.2.20 在「连接与校准 → 官方可视化」嵌入原版 `wuji viz`，移除重复的手套骨架显示，保留工作台独有的映射与机械手预览。软件版本检查不自动启动 Viewer；设备需明确选择后打开。画面存在不能证明数据仍然新鲜，使用和独立 CLI 更新见 [原版 Viewer 说明](OFFICIAL_CLI_VIEWER.md)。

依据：[SDK 用户档案](https://docs.wuji.tech/docs/en/wuji-cli/latest/user/)、[触觉标定保存](https://docs.wuji.tech/docs/en/wuji-studio/latest/tactile-calibration/)、[官方实时可视化](https://docs.wuji.tech/docs/en/wuji-cli/latest/visualization/)。

| 页面 | 实际作用 | 如何生效 |
|---|---|---|
| 实时输出调整 | 官方 SDK 输出后的 20 关节幅度系数、角度偏移和时间平滑 | 保留原有保存、手套预览应用与重连流程 |
| 官方参数表 | 开源 `wuji-retargeting` 的目标比例、对指距离、低通与变化权重 | 保存草稿、应用到嵌入的开源映射预览，或生成完整 YAML |

参数表使用可填写数值的行列格子，支持按单元格编辑及从表格粘贴矩形数据。空值、非有限数、越出表格的粘贴均不覆盖旧值。每个工作区的草稿按代际、左右侧、手套、机械手、SDK 用户隔离；保存冲突要求重新载入。

官方表格的五行是五根手指，三列为 PIP、DIP、TIP。`segment_scaling` 缩放的是手腕到关键点的向量，不是机械手电流、关节增益或逐节骨长。`pinch_thresholds.d1/d2` 单位为 cm；d1 小于 d2。`lp_alpha` 为输出低通系数，变小会增加平滑和滞后；`norm_delta` 是逐帧关节变化正则权重。它们不是接触力或安全阈值。

四套默认模板从官方提交 `531f6ed4250b475d2e9231f54e988fc9b1c5b4ea` 原样保留，分别覆盖 Hand 1/2、左/右手。模板具体默认值可能与旧文档示例不同，页面以固定模板为准；校验和与许可随包保存。导出仅改变这四组可编辑字段，保留优化器、旋转、模型路径和其他权重。

导出的完整 YAML 放到该官方仓库的 `example/config` 目录，选择对应模型和左右手，用官方 `tuning_tool.py` 加载。相对 URDF/MJCF 路径依赖原仓库及其模型子模块，不能把 YAML 孤立放在任意目录就运行。

### 在软件里使用开源求解器（0.2.12）

1. 使用 Windows 内置 Linux 控制端；在官方参数表点击「准备求解环境」。首次联网下载约 130 MB 依赖，安装到独立 Python 3.12 环境，不替换 SDK 的依赖。
2. 选择手型、SDK 用户与手套，在手套预览连接完成后重新载入对应配对的参数表。数值表支持逐格编辑和矩形粘贴。
3. 点击「应用到开源映射」。当前有机械手反馈会话时，需先断开；切换不会启用电机。选择和草稿按设备组合保存。
4. 控制端收到新骨架后，页面按实际引擎和参数摘要确认「本表已生效」。保存、发出切换请求、陈旧帧都不算生效。若参数不合适，点击「使用 SDK 映射」。
5. 确认手套预览后，再使用原有机械手连接和跟随入口；实际反馈与求解目标继续分开。

当前 SDK 的内置 RetargetSession 仍不接受这套 YAML。本版嵌入 MIT 许可的公开 `wuji-retargeting` 核心（提交 531f6ed4）作为可选引擎，不改写 SDK 求解器，不是完整原版 Studio。首版安装器只验证 Linux x86_64 Python 3.12；Mac/ARM 和远程 SSH 新求解环境未集成，原有 SDK 路径保留。

开源求解器用 21 个官方手套骨架点求解 20 个关节。Hand 2 的运行 URDF 改为与软件画面一致的 Beta 2，Hand 1/2 左右模型固定到 c2cd7f8d；输出逐项按关节名重排。Hand 1 的 URDF 与 MJCF 有最多 0.0005 rad 的小数舍入差，采用两者限位交集，未扩大原界限。原模板导出的 YAML 仍保留官方 Beta 1 相对路径，与软件内 Beta 2 运行入口不同。

依赖按 wheel 哈希固定，源码和模型在运行前校验；求解器独立进程不持有设备连接。首次 SciPy 加载放到初始化，后续超时或无效结果使预览失效，不更新帧的新鲜时间。一次合成骨架测试的稳态耗时约 5–10 ms，不承诺 1000 Hz 求解，也不代表真实手套准确率。



研发人员使用的多行多列数值面板没有截图或字段名称，因此不能认定此表与内部工具完全相同。这里每个字段都有公开来源。

## 官方依据

- [Studio 维护说明](https://github.com/wuji-technology/wuji-studio)
- [Studio 六姿势、稳定性与约束流程](https://docs.wuji.tech/docs/en/wuji-studio/latest/calibration/)
- [CLI 标定和结构化事件](https://docs.wuji.tech/docs/en/wuji-cli/latest/hand-model-calibration/)
- [SDK 标定回调示例](https://github.com/wuji-technology/wuji-sdk/blob/main/examples/python/wuji_glove/5.calibration.py)
- [开源映射调参说明](https://docs.wuji.tech/docs/en/wuji-retargeting/latest/tuning/)
- [固定配置源](https://github.com/wuji-technology/wuji-retargeting/tree/531f6ed4250b475d2e9231f54e988fc9b1c5b4ea/example/config)

## English

Version 0.2.19 uses bundled CLI / SDK 2026.10.12 and controller image 1.1.0. Settings → Connections → Official CLI / SDK separates the actual version/import check from installation records and bundled targets. That check does not connect devices or start calibration. Studio embedding is cancelled; its 0.2.18 implementation is historical, and existing Studio profiles are retained without automatic conversion.

Calibration executes the official CLI. The duplicate six-pose browser and custom illustrations are removed; one compact view displays the CLI's current step, actual progress, result and error. Raw official events remain in run details. Missing fields stay unknown, and elapsed time never supplies progress. Refresh recovery clears refresh errors without erasing an operation failure. The chosen SDK user remains selected for the glove connection lifetime. Generation-specific measured feedback stays separate from mapped targets; hidden rendering does not pause device reception or heartbeats. Unconfirmed stops remain attached to their device/session. Physical calibration acceptance remains separate from software tests.

Live output gain/offset adjustments and official solver parameters remain separate tabs. Version 0.2.12 embeds the pinned open retargeter as an optional engine in an isolated Linux x86_64/Python 3.12 environment. Prepare dependencies, apply in glove-only preview, then verify fresh output with the matching parameter digest. SDK fallback is explicit; hand follow remains a separate operator action. The SDK's built-in mapper does not accept these YAML values. This is not the complete original Studio, and physical glove/hand acceptance is still pending. Hand 2 uses the viewer-matched Beta 2 model; exported upstream YAML retains its original Beta 1 paths. Mac/ARM setup is not yet integrated.
