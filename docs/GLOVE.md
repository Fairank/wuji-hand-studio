# 手套遥操作 / Glove teleoperation

本工作台为非官方工具。映射使用官方 `wuji_sdk.RetargetSession`，不是自己训练的模型，也不是录制回放。新接口已做软件测试，尚未完成真实手套带动机械手的验收。

## 电脑怎么连接

| 使用方式 | 是否需要开虚拟机 | 当前支持范围 |
|---|---|---|
| Ubuntu 本机运行工作台 + 官方 SDK | 不需要 | 手套、映射和设备控制均在本机 |
| Windows/Mac 工作台 + 独立 Linux 控制电脑/小主机 | 不需要 | 通过 SSH 连接，控制端须能访问手套和机械手 |
| Windows/Mac 工作台 + Linux 虚拟机 | 需要 | 可复用现有方式；设备发现需要合适的桥接和网段 |
| Windows 官方 CLI | 不需要 | 可扫描、读取、标定手套；不等于已实现原生完整遥操作 |
| 纯 Windows/Mac 实机跟随 | — | 本版未实现。官方 RetargetSession 当前支持 Linux x86_64/aarch64 |

不限定必须 Ubuntu；官方 Studio 图形标定程序当前列出的系统要求是 Ubuntu 22+。Linux 控制端不需要 ROS 2、GPU 或图形桌面来运行本页的 SDK 数据与控制链路。其他 Linux 发行版须自行核对官方二进制兼容性，不能据此承诺所有发行版都能用。WSL2 是可另行验证的 Linux 运行方式，设备网络发现/USB转接未经本项目验证，不列为开箱即用方案。

## 使用步骤

1. 更新控制端源码，按随包 `controller/README.md` 准备 Python 环境并安装 `wuji-sdk numpy`，保留自己的配置和标定用户。新版握手要求 `teleop_version=1`；旧控制端会明确提示升级。不要覆盖正在运行的控制程序目录，建议在独立目录部署。
2. 在“连接与校准”选本机 Linux 或远程 Linux；设置控制程序目录和 SDK Python 路径。手套与机械手应由该控制端访问。SSH 只传操作请求和显示状态，实时关节目标不绕经浏览器。
3. 在“手套遥操作”扫描，选择手套序列号及标定时的 SDK 用户。只扫描 Wuji Glove，不自动选机械手；左右手与工作台型号必须一致。Default 使用内置手型，具名用户可能尚未标定，本界面不会把它一概标成“标定成功”。
4. 点击“连接手套并预览”。页面显示官方映射目标；不会连接或启用机械手。适配一代/二代、左/右四种原生目标模型。不会把手套的21自由度人体关节角直接当作机械手20关节指令。
5. 二代手可点击“连接机械手反馈”。这一步只读反馈，画面改为实际机械手姿态；目标与实测数据可在下方展开比较。
6. 查看控制端实际加载的 Kp、Kd、电流(A)、发送频率和速度。修改参数仍通过原“参数调节”页保存、同步，断开遥操作会话后生效。未套用官方示例中的另一套增益。本次没有修改原参数默认值。
7. 确认活动空间后，手动点击“开始跟随”。从当时实测姿态进入，按原有用户速度设置接近实时手套目标。停止按钮请求停用电机；再次跟随需要再次明确开始。

本版一代手套映射可预览，一代实机跟随尚未接入。二代实机路径尚未做本版本硬件验收，不把软件测试计为实机成功。工作台中的普通动作、诊断和手套会话互斥，避免同时占用同一设备。

## 频率、显示与断连

- 官方手套骨架默认120Hz；页面的“映射更新率”是实际得到新姿态并完成映射的频率。它不是机械手1000Hz反馈率，也不是浏览器刷新率。
- 发指令沿用 `COMMAND_RATE_HZ`；速度沿用 `min(PATH_SPEED_RAD_S, COMMAND_SPEED_RAD_S)`，Kp/Kd/电流沿用已加载的 `motion_parameters.py`。实际发送率单列，不能把调用频率当成设备逐帧执行率。
- “数据龄期”基于控制端最新骨架接收时间，加工作台距最后状态的时间；不是校准过的端到端网络延迟测量。
- 图像标为“手套映射预览”或“实际机械手反馈”；已经连接机械手后，丢失实际反馈不会偷偷切到目标动画。
- “手套失联后停用”可在连接前设置100–2000ms，初值250ms。它是本项目明确展示的通信超时设置，不是官方接触力阈值。浏览器会话租约与官方诊断处理沿用既有控制链路；Warning记录，其他故障按既有官方等级处理。
- 重复、乱序、设备序号/时间戳回退的帧不刷新有效目标；需要断开重连才能建立新流。过量队列积压和无效映射会使目标无效。失联后不自动恢复电机运动。
- 本页只控制20个手指关节，不控制机械臂或腕座。没有力反馈手套驱动。

## 标定的替代方式

除了 Ubuntu 上的官方 Wuji Studio，官方 CLI 提供 `wuji calib hand-model` 与用户管理/导入导出入口。本机已核对 Windows CLI 的这些命令存在，但本轮没有运行标定、覆盖用户档案或升级设备固件。不同电脑上使用标定需要官方用户数据导出/导入，不能只复制序列号。公开包不包含使用者的标定数据、SN、SSH配置或训练权重。

## 官方依据（2026-09-20核对）

- [SDK实时遥操作示例：手套关键点到20关节，含左右手及两代设备](https://github.com/wuji-technology/wuji-sdk/blob/main/examples/python/retargeting/1.teleop_real.py)
- [SDK说明：Retargeting支持Linux x86_64 / aarch64](https://github.com/wuji-technology/wuji-sdk/blob/main/examples/python/README.md)
- [手套数据流与标称频率](https://docs.wuji.tech/docs/en/wuji-glove/latest/sdk-data-reference/)
- [官方Studio安装要求](https://docs.wuji.tech/docs/en/wuji-studio/latest/installation/)
- [CLI手型标定与用户数据](https://docs.wuji.tech/docs/en/wuji-cli/latest/hand-model-calibration/)

## English

The Glove page supports explicit scan, SDK user selection, side-checked glove connection, live official retarget preview, read-only Hand 2 preparation and separately started Hand 2 follow. SDK retargeting and hand publishing run on the same Linux controller, not through browser polling. Native Windows/Mac complete hardware teleoperation is not implemented; a separate Linux PC removes the need for a VM. First-generation mapping is preview-only in this integration. This release has not completed real glove/hand acceptance.

No automatic motor start or resume. Targets, sent commands and measured feedback remain separate. Existing user gains/current/speed settings and official fault classification are retained. Glove timeout is explicitly adjustable before connection. The application does not perform calibration itself or claim a named SDK user is calibrated. See the official links above.

Basic arrival-statistics utility and regression tests were assisted by local CLI `claude-fable-5-1 --effort max`, reviewed before use. Device identity, control, stream validity and acceptance were implemented/reviewed by the main maintainer. No private code, device records or credentials were shared with that assistant.
