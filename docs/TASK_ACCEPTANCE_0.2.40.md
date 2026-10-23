# 0.2.40 逐任务证据索引

用户要求每个不同任务从开始到终点检查，包含可用的取消/停止、失败恢复和再次使用。本表索引实际证据，不宣称所有物理条件或所有可能异常均通过。没有取消入口的短操作注明不适用；没有暴露的功能不算已通过任务。

正常路径基线保留在 `customer-flow-0239/integrated/task-catalog.json`、`task-catalog-other.json`、`update-task-acceptance-catalog-final06.json`。这些文件的原状态不改写。本版补证与摘要统一记入 `customer-flow-0240/reconciliation-final.json`；具体记录中的源码、外部替身及完成条件优先于文字概括。软件证据不等于同一安装包已验收。

## 原 14 行失败证据缺口的逐项对应

| 原任务 ID | 本次独立失败、恢复与再次使用 | 补证 |
| --- | --- | --- |
| `settings-language` | 拒读/拒写/坏值后仍可切换，解除故障保存并重载沿用 | `0239/resilience-ui/acceptance-final.json` 的 settings-storage/language |
| `settings-mode` | 简单/完整模式拒读/拒写/坏值回退，当次可用，恢复保存与重载 | 同上 settings-storage/mode |
| `settings-appearance` | 主题、透明与动态效果各自异常、恢复保存与重载；另以独立上下文证明主题拒读/拒写后任务脚本启动并可重用 | 同上 settings-storage/theme/transparency/motion；`0240/settings/theme-after-final01.json` |
| `settings-size-official` | 官方画面独立拒读/拒写/非数值，调整→恢复保存→重载→Reset/Escape→再用 | `0240/size-resilience/size-resilience-cross-audit.json` 的 official |
| `settings-size-glove` | 手套画面单独执行同一完整链，不借官方画面通过 | 同上 glove |
| `task-view-layout` | 目录离线、准确归属端不可用时拒绝写入；浮动/停靠/关视图/重开后恢复同一文档 | `0239/resilience-ui/acceptance-final.json` 的 task-view-layout-unavailable |
| `task-pair-unpair` | 原配对的解除失败可见、归属不丢；明确重试，再配对/解除 | `0239/peer-failures/task-catalog-peer-failures-final040.json` 的同名任务 |
| `task-pair-inflight-cancel` | 延迟配对中取消失败保持准确归属；明确恢复并重新使用，不自动重连 | 同一 final040 目录独立同名任务 |
| `single-hardware-controls` | 实机模式启动/停用失败与未确认状态，明确恢复、停止及新一轮使用 | 同一 final040 目录独立同名任务；设备回执合成 |
| `multi-workspace-create-preview-members` | 空名称不派发；创建成员失败不留假成员；明确添加左右模型并继续使用 | `0239/integrated/group-gaps/group-gaps-final040.json` 同名任务 |
| `multi-synchronized-preview` | 同步预览准备失败全体中止；同模式重试→停止→新运行自然完成 | 同上同名任务 |
| `multi-discover-connect-real-controls` | 准确设备连接失败不自重试；明确移除失败会话、重连、准备失败恢复、两轮开始/停止 | 同上同名任务；真实电机未测 |
| `multi-move-member` | 活动组移动被原服务拒绝且成员保留；停止后移动，再将同一准确设备移回 | 同上同名任务 |
| `multi-remove-members-workspace` | 活动移除拒绝，非空工作区不提供删除；停止/断开/移除后新建并再次删除 | 同上同名任务 |

上表的 `0239`/`0240` 是本机 `outputs/customer-flow-0239`/`customer-flow-0240` 的简称。所有旧 “Not exercised” 行和运行中源码变化的未接受记录保留，不能仅修改措辞就宣布关闭。

## 其他独立任务的证据与终点

| 不同任务 | 检查到的终点、恢复或限制 | 证据索引 |
| --- | --- | --- |
| 发现→选择→连接→断开→再用 | 扫描不连接；保持原任务；明确连接与断开；提醒不跳升级窗口 | 原 integrated 客户链；本版 presence/independent-acceptance-final.json |
| 当前发现与最近连接 | 发现过期只清当前列表；原会话保留；稀疏启动报告准确匹配才入历史；未知清理不扫描 | `0240/presence/presence-cross-audit-final.json` |
| 官方查看与标定切换 | 同一准确设备与任务，用户明确切换；原画面/指导复用 | 原 integrated 客户链 |
| 标定取消、错误、完成与重用 | 三次独立运行依次取消→失败→完成；清理、模型保存与接续分别确认 | `0239/calibration-resilience/acceptance-final.json`；worker/Job/官方事件输入合成 |
| 标定资料接续 | 按准确运行、用户与模型进入同一预览；不启动跟随 | 原 integrated 客户链及 calibration-resilience |
| 遥操作实体手发现与连接 | 保留正在接收的手套/档案；查找失败可重试；HTTP 200 后异步失败仍显示，明确重试才清除；准确选择/连接反馈后才可明确跟随 | `0239/peer-failures/physical-entry-async-01.json`、`0239/resilience-ui/hand-scan-independent-final.json`；旧 `0240/teleop/physical-entry-cross-audit-01.json` 保留为布局/流程独审 |
| 遥操作跟随、停止与断开 | 明确开始，停用未知不假清；恢复后可再用；实际设备传输为替身 | 原客户链、physical-entry 独立页面链 |
| 单手预览 | 选动作→预览→播放→暂停/继续→停止→再用 | 原 task-catalog 的 single-preview |
| 设备配对准备失败 | 独立准备拒绝与回滚，不借解除配对的通过 | 原 task-catalog 的 task-pair-failure |
| 多手衔接预览 | 单独编组准备、开始、停止、自然结束与重用 | 原 task-catalog 的 multi-paired-preview；现有组无暂停/继续 |
| 参数本机保存 | 草稿撤销、保存、非法值/版本冲突、重读、再次保存 | `0239/other/advanced-ui-05.json` 与 task-catalog-other |
| 参数同步控制端 | 合成传输失败→明确重试→准确版本确认→重用；无活动同步取消入口 | 同上；真实远端及硬件另验 |
| 文字展示 | 取消选择、无效输入拒绝、明确播放、暂停/继续、停止与新文字完成 | 同上独立 text 任务 |
| 数字展示 | 原数字完成/无效输入；另录数字选择器 Cancel/Close/Escape 不派发，再明确播放完成 | 同上；`0239/resilience-ui/acceptance-final.json` 的 digit-picker-cancel-reuse |
| 节目单预览 | 编排保存、播放/暂停/停止、自然完成、过期保活、旧请求迟到与再次使用 | 原 ProgramRunner/advanced-ui 证据；准确 run_id 保护 |
| 节目单实机协议 | 独立实机模式确认、失败、停用及再次使用；不以预览 Stop 替代 | `0239/peer-failures/task-catalog-peer-failures-final040.json` 的 playlist-hardware-protocol；电机合成 |
| 动作轨迹导出 | 可见 CSV 下载与重复内容核对；JSON 是接口格式；不启动设备 | 原 advanced-ui 与 task-chains；系统下载取消未测 |
| 诊断运行 | 无结果→运行→合成超时→重试→恢复保存报告；无取消入口 | 原 other 诊断任务 |
| 诊断文本导出 | 实际可见文本链接、无报告隐藏、拒绝无效格式、重复导出 | 原 other 独立 doctor-export-text |
| 诊断原始 JSON 导出 | 原始官方数据独立链接、重复导出和恢复 | 原 other 独立 doctor-export-raw |
| 诊断完整 JSON 导出 | 完整报告独立链接、重复导出和恢复 | 原 other 独立 doctor-export-full |
| 官方支持包导出 | 独立原始 ZIP 下载、部分失败仍保留可用原件、重试产生新记录 | 原 other 独立 support-original |
| 支持摘要导出 | 独立摘要链接，缺原 ZIP 仍如实报告；重试/重复下载 | 原 other 独立 support-brief |
| 支持全部文件导出 | 独立合并文件、ZIP 完整性与新记录 | 原 other 独立 support-combined；无自动上传或导出取消入口 |
| 可选模型导入 | 原选择器回调取消、拒坏包、合成数据包安装与幂等重用 | 原 other private-model-import；系统文件窗/私有模型未测；无导出功能 |
| 映射设置与预设 | 真页面编辑/保存/预设/删除、旧修订冲突、左右与设备隔离、重载 | `0239/integrated/extra-task-ui-cross-audit.json` 独立 mapping 任务 |
| 官方求解参数与 YAML | 另补真实编辑页面、草稿取消、保存/导出、坏值/修订冲突、重载 | 同一 final040 目录的 official-tuning-save-and-export |
| baseline 采集 | 实际 Recorder→合成反馈→时间到保存→原记录重读 | `0239/integrated/extra-task-ui-cross-audit.json`；原服务另测各标签 |
| 带标签采集 | 真页面开始→停止保存→导出→断连关闭→明确恢复/再用→重载 | 同上独立 recording 任务；不当作实测样本 |
| 自动更新提醒 | app/CLI/SDK 分别被动提醒、稍后、明确进入对应项；不自动安装 | 原 update-task-acceptance-catalog-final06 |
| 软件更新 | 下载中断/重试、摘要校验、准确助手计划、助手失败重试；模拟新进程不等于真实安装 | 同上 workbench-update；最终安装证据外置 |
| 原生 CLI 更新 | 指向同一工作台更新，避免更新另一份未使用组件 | 同上 native-cli-update |
| 托管 CLI 更新/回退 | 合成官方包暂存、校验、激活、失败回退及重查 | 同上 managed-cli-update；实际发布安装另验 |
| SDK 元数据与失败原因 | 同版预览并行；本地/缓存/证书/TLS/网络/HTTP/元数据等分类与重试 | `0240/metadata-independent-04.json`、`0239/peer-failures/sdk-diagnostic-closure.json` |
| SDK 更新 | 空闲检查、精确 wheel/导入/切换、失败回退；没有实际替换控制环境 | 原 update-task-acceptance-catalog-final06 |
| SDK 退出未知后的恢复 | 准确进程与 guest 回执核对、明确恢复及重查 | 同上 sdk-recovery；输入合成 |
| 固件准确检查与官方升级入口 | 原 SN→只读结果→稍后/明确查看→准确 Viewer 停止→原版人工 No/Yes→退出后重新核对 | 原 integrated/customer-journey-final-03-frozen.json；未真实刷写 |
| 固件未知写入恢复 | 同设备/进程/占用和真实字段解释有独立后端证明；没有未知写入完整 UI 或实物成功声明 | 原 firmware-recovery-contract / firmware-backend / sdk 独立恢复证据 |
| 退出、重开与桌面生命周期 | 原 49 项独立退出/清理/同应用重开/配置保护软件检查保留 | `0239/task-lifecycle.json`；实际电机停用与系统失焦观感不由其替代 |

## 版本与实物边界

0.2.40 完整回归 1718 项：1716 通过、2 项原条件跳过、0 失败/错误，Python 源稳定；准确结果见 `customer-flow-0240/full-final-02.json`。最终源/文档摘要、EXE/setup/安装一致性、原生捕获与真实官方元数据读取，以 `customer-flow-0240/` 最终交付清单为准；旧 0.2.39 测试数和试装不移作本版通过证据。每项专项可能与全量及其他专项重叠，不合计为总功能数。

真实固件刷写、SDK 环境替换、佩戴标定与模型质量、实机跟随、碰撞避免、时延和多设备物理并行未因本表而通过。独立原生任务窗、多手暂停/继续及模型包导出未提供，不能把它们算作已交付功能。软件内视图关闭也不等于断开设备。历史原文见[0.2.39 检查表](TASK_ACCEPTANCE_0.2.39.md)。
