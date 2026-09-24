# 官方标定与参数表 / Calibration and tuning

工作台是非官方界面。Wuji Studio 的公开仓库目前提供说明与发行信息，没有可直接移植的完整应用源码。官方已将后续工具维护转向 CLI / SDK。因此本项目接入官方标定流程，而不是声称内嵌原版 Studio。

## 在软件内标定

打开「连接与校准 → 手套校准」，在「选择用户与手套」中选择命名 SDK 用户、捕获手套和左右侧。Default 不能保存人体模型。已有模型需要明确选择覆盖。开始调用 `wuji --jsonl calib hand-model`，并检查当前用户、设备侧与跨工作区占用；不发送机械手运动指令。

官方步骤为拇指分别与食、中、无名、小指指尖相触，再四指弯曲约 90°，最后伸平并拢。页面跟随官方姿势名称或已确认的六姿势编号，显示官方 `variance_ok` 稳定性和 `constraints_ok` 约束检查；诊断值、单位、误差与建议照原始反馈显示。缺字段显示等待，不按本地阈值补成通过。未知步骤保留原始诊断，不强行指向一个姿势。

采集、稳定性判定、约束判定、求解和模型发布均由官方工具完成。当前姿势采集完成不代表整次标定成功；必须收到正式 result 且进程退出码为 0。页面刷新官方采集状态的间隔为 500 ms，不改变 SDK 采样频率。

成功模型仍按 SDK 用户及左/右侧保存在官方目录。工作台保存 CLI 事件报告，但尚未实现 Studio 的全传感器 Calibration Debug 录制、完整人体 URDF 查看器或官方动作插图。官方插图通过页面链接查阅。新 UI 的反馈解析经过软件样例测试；真实手套的端到端标定仍需设备验收。

## 两种参数表各做什么

| 页面 | 实际作用 | 如何生效 |
|---|---|---|
| 实时输出调整 | 官方 SDK 输出后的 20 关节幅度系数、角度偏移和时间平滑 | 保留原有保存、手套预览应用与重连流程 |
| 官方参数表 | 开源 `wuji-retargeting` 的目标比例、对指距离、低通与变化权重 | 保存草稿或生成完整 YAML，供官方 `tuning_tool.py` 加载 |

参数表使用可填写数值的行列格子，支持按单元格编辑及从表格粘贴矩形数据。空值、非有限数、越出表格的粘贴均不覆盖旧值。每个工作区的草稿按代际、左右侧、手套、机械手、SDK 用户隔离；保存冲突要求重新载入。

官方表格的五行是五根手指，三列为 PIP、DIP、TIP。`segment_scaling` 缩放的是手腕到关键点的向量，不是机械手电流、关节增益或逐节骨长。`pinch_thresholds.d1/d2` 单位为 cm；d1 小于 d2。`lp_alpha` 为输出低通系数，变小会增加平滑和滞后；`norm_delta` 是逐帧关节变化正则权重。它们不是接触力或安全阈值。

四套默认模板从官方提交 `531f6ed4250b475d2e9231f54e988fc9b1c5b4ea` 原样保留，分别覆盖 Hand 1/2、左/右手。模板具体默认值可能与旧文档示例不同，页面以固定模板为准；校验和与许可随包保存。导出仅改变这四组可编辑字段，保留优化器、旋转、模型路径和其他权重。

导出的完整 YAML 放到该官方仓库的 `example/config` 目录，选择对应模型和左右手，用官方 `tuning_tool.py` 加载。相对 URDF/MJCF 路径依赖原仓库及其模型子模块，不能把 YAML 孤立放在任意目录就运行。

**当前 SDK 的内置 RetargetSession 没有开放这些 YAML 参数。本版不启动开源优化器；生成或保存 YAML 不会改变正在运行的遥操作。** 这条限制在软件内直接显示，不用“已应用”误导操作者。后续接入该求解器需要独立验证模型版本、关节顺序和输出边界。

研发人员使用的多行多列数值面板没有截图或字段名称，因此不能认定此表与内部工具完全相同。这里每个字段都有公开来源。

## 官方依据

- [Studio 维护说明](https://github.com/wuji-technology/wuji-studio)
- [Studio 六姿势、稳定性与约束流程](https://docs.wuji.tech/docs/en/wuji-studio/latest/calibration/)
- [CLI 标定和结构化事件](https://docs.wuji.tech/docs/en/wuji-cli/latest/hand-model-calibration/)
- [SDK 标定回调示例](https://github.com/wuji-technology/wuji-sdk/blob/main/examples/python/wuji_glove/5.calibration.py)
- [开源映射调参说明](https://docs.wuji.tech/docs/en/wuji-retargeting/latest/tuning/)
- [固定配置源](https://github.com/wuji-technology/wuji-retargeting/tree/531f6ed4250b475d2e9231f54e988fc9b1c5b4ea/example/config)

## English

Calibration executes the official CLI, with the Workbench displaying public SDK feedback fields. Stability and constraint checks are official booleans; missing values stay unknown. This is not the original Studio UI or its full-sensor debug recorder. Physical calibration acceptance remains separate from software tests.

Live output gain/offset adjustments and archived open-retargeting solver configuration are distinct tabs. The latter is a per-pairing draft/editor and complete YAML export only. The current built-in SDK solver does not accept these YAML settings, and this release does not start a second solver or silently change teleoperation. Keep exported files under the pinned upstream repository's example/config directory so relative model paths resolve.
