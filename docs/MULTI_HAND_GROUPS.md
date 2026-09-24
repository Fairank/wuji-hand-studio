# Hand Workbench 0.2.9 · 多手群组 / Multi-Hand Groups

## 中文

非官方个人演示工具，单一应用；一个工作区可含多个独立手部会话。

### 设置

- 发现功能仅列出广播而不连接或使能；用户可选择加入所选工作区或新工作区，也可先建空工作区再发现。
- 已连接设备按序列号、代次和左右侧校验；已有的手空闲时可移至其他工作区。
- 预览手是明确的离线模型，从不显示为在线，也不计为实体设备。

### 编排

- 同动作模式将一支所选舞蹈发给 2–8 只手；配对模式须一只左手与一只右手，摆放为掌心朝前、拇指朝内。
- 八个自编动作：跨手波浪、反向波浪、往返波浪、中心外扩涟漪、双手交替、同步绽放、十指钢琴、双波追逐。
- 手部底座/手腕不动；关节曲线为自行编写而非官方录制，实机动作未经验证。
- 预览支持 Hand 1 与 Hand 2；真实编排目前仅支持 Hand 2。
- 软件不声称具备双底座/手间碰撞检查或机械同步精度。

### 运行

- 选择参与手、动作、速度、幅度及 1 或 3 个循环，再明确启动预览或真实手。
- 真实手仅在用户请求并明确确认工作区后才使能。
- 先准备全部参与手，再逐一在各自实测姿态进入待发状态；全部就绪后才共同开始。
- 真实群组需共享 Linux 时钟：同机内置 Linux 符合要求，未同步的独立 Linux 电脑会被拒绝；预览使用主机公共单调时钟。
- 进入/主体/返回/保持各段按全组所需最慢时长重新定时，沿用各手现有电流、Kp、Kd 及已配置速度策略，不引入新力值。
- 任一故障或编排界面连接超时即请求全部停止；停止确认失败会如实报告，不宣称已停止。

### 状态

- 软件检查：Windows Python 463 项通过、2 项跳过；Linux fake SDK/数学及工作区 33 项通过；左右两个独立预览进程的 HTTP 测试在完整播放、手动停止、控制连接超时与工作区移动上均通过。
- 界面、打包版与原位安装已验证，见 [验证记录](VALIDATION_0.2.9.md)；实机测试仍待进行；源码、打包与实机测试相互独立。
- Claude Opus 5.5 max 负责基础代码、测试与文档；主工程师负责控制设计并审查产出。
- 不含私有训练模型或凭据。

## English

Unofficial personal demonstration tool, single application. One workspace may hold multiple independent hand sessions.

### Setup

- Discovery lists advertisements without connecting or enabling; add hands to the selected or a new workspace, or create an empty one first.
- Connected devices are verified by serial, generation and side; idle hands can move between workspaces.
- Preview hands are explicit offline models, never shown online or counted as physical devices.

### Dances

- Same-action mode sends one selected dance to 2–8 hands. Pair mode requires one left and one right hand (palms forward, thumbs inward).
- Eight authored routines: cross-hand wave, reverse wave, out-and-back wave, center-outward ripple, alternating hands, synchronous bloom, ten-finger piano, two chasing waves.
- Bases/wrists do not move. Joint curves are authored, not official recordings; physical motions are unvalidated.
- Preview: Hand 1 and 2. Real group dance: currently Hand 2 only.
- No two-base/inter-hand collision checking or mechanical synchronization accuracy is claimed.

### Running

- Choose participants, action, speed, amplitude and 1 or 3 cycles; explicitly start preview or real hands.
- Real hands are enabled only on request, with clear workspace confirmation.
- All participants are prepared, then each armed at its measured pose; common start only after all are ready.
- Real groups need a shared Linux clock: same-machine built-in Linux qualifies; separate unsynchronized Linux computers are rejected. Preview uses the common host monotonic clock.
- Entry/body/return/hold segments are retimed to the group's slowest needed duration, using each hand's existing current, Kp, Kd and configured speed policy—no new force values.
- A failure or expired group UI lease requests stop-all; failed stop confirmation is reported, not declared stopped.

### Status

- Software checks: Windows Python 463 passed, 2 skipped; Linux fake-SDK/math/workspace 33 passed; two independent left/right preview processes passed an HTTP test of complete play, manual stop, expired lease and workspace move.
- UI, packaged execution and in-place installation were verified; see [validation](VALIDATION_0.2.9.md). Physical testing is still pending; source, packaged and physical tests are separate.
- Claude Opus 5.5 max handles basic code, tests and docs; the main engineer designs controls and reviews outputs.
- No private trained models or credentials are included.
