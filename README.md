# 灵巧手工作台 / Hand Workbench

[English](#english) · [Linux 安装 / Installation](docs/LINUX.md) · [Ubuntu CLI 操作指南](docs/UBUNTU_CLI_QUICKSTART_ZH.md) · [更新记录 / Changelog](docs/CHANGELOG.md)

**非官方个人展示工具，不是舞肌科技发布、维护或背书的产品。** 官方名称、示例和标识仅说明兼容对象和来源。

一个软件、一个图标、一个安装包。公开展示与自用模型共用同一套程序；私有模型通过菜单导入为本机数据，不再提供独立“基础版 / 研究版”软件。

## Windows 下载和启动

在 [Releases](../../releases) 选择实际已上传的安装包，各版本沿用同一 Windows 软件、安装身份和用户数据目录。当前验证范围见 [0.2.73记录](docs/VALIDATION_0.2.73.md)；构建、安装和公开发布结果另记于外部最终交付清单，源码版本不代表安装包已经上传。Windows 当前用户安装无需另装 Python；安装器检查微软 WebView2 运行时，缺少时联网安装。包暂未进行发布者代码签名，下载需核对实际发布的 SHA-256。

## 0.2.73 会话复用、更新状态与报告归属

读取目标后再次核对当前设备归属及数据新鲜度，避免等待期间过期的会话被复用。官方固件读回确认后保留准确版本事实，旧目录不再复活已完成的提醒。诊断导出绑定当前显示的报告，旧预览回复不拉走用户页面；明确恢复使用后可恢复已停止的版本检查。界面设计和 SDK 直显路径保持不变。范围见[本轮记录](docs/VALIDATION_0.2.73.md)。

## 0.2.72 直接显示官方 SDK 目标

遥操作画面直接采用官方 SDK 会话提供的目标，不再创建或运行工作台自加的碰撞投影，不另设角度追赶或滤波。当前官方公开接口未提供可调用的防穿模合同，不额外承诺防穿模。身份、左右手、行程与旧帧期限核对继续保留；页面设计及入口不改。显示链只做模拟验证，不连接或驱动实物。范围见[本轮记录](docs/VALIDATION_0.2.72.md)。

## 0.2.71 遥操作画面的等待与期限

主画面及独立小窗按每轮开始时间安排下一次取图，取消解码后的额外固定等待；图像解码可超时退出，晚到旧帧不再显示为实时。映射画面的源数据期限贯穿最终校验、缓存和浏览器解码。碰撞预览复用同一模型、同一准确姿态已完成的完整几何检查，计算预算留给新姿态；新候选仍完整校验，不改变官方 SDK 目标或真实手指令。范围见[本轮记录](docs/VALIDATION_0.2.71.md)。

## 0.2.70 保留已确认身份与明确恢复预览

设备卡保留准确当前会话已经确认的左右手信息，数据帧暂时过期不再丢失身份；未接入且官方未提供侧别时说明连接后识别。短暂空发现保留原时限内的待复核记录，不假在线。失去新数据时可由用户明确重新接入原设备，已有资料保留；正常数据时复用连接。纯界面刷新可保留准确的只读手套预览会话，跟随、配对或不明状态继续保护。范围见[本轮记录](docs/VALIDATION_0.2.70.md)。

## 0.2.69 标定会话、已确认侧别与预览目标

标定中或退出待确认的准确会话继续显示在设备页；标定入口复用同一设备当前 SDK 已确认的左右手，不再被缺字段的旧扫描记录遮住。预览取消独立的每帧角度追赶限制，保留原碰撞路径、关节限位和时间预算保护。官方动作超时显示实际原因、最后检查与原阈值；SDK 更新预检补齐模型加载所依赖的接口。范围见[本轮记录](docs/VALIDATION_0.2.69.md)。

## 0.2.68 已存标定的 SDK 应用与遥操作入口

明确使用已存标定时，在核对人员、设备和左右手后，通过官方接口应用并读回已验证的人手模型；自定义路径未设置不再误报成需要重新标定。遥操作页提供明确的选择、开始和重试入口，保留当前虚拟或实机模式；实机跟随继续由用户单独启动。范围见[本轮记录](docs/VALIDATION_0.2.68.md)。

## 0.2.67 标定资料接入等待与退出状态恢复

已存标定的 SDK 启动、资料读回和首帧接入使用原用途切换的 45 秒总预算，避免仍在正常加载时被局部 12 秒等待提前结束。新 SDK 连接失败后，准确同会话的完整退出证明可以解除历史清理阻塞，原失败原因保留；不自动重连或忽略未知退出。范围见[本轮记录](docs/VALIDATION_0.2.67.md)。

## 0.2.66 发现未报告左右手时的用途切换

修复官方发现没有提供左右手时，普通遥操作入口将空侧别拼成无效型号的问题。沿用所选映射型号作为目标意图，连接后仍由 SDK 核对真实设备侧别；已报告的冲突不放行。同时修复取消与最后连接派发的时序，以及双窗口旧提示覆盖当前用途。范围见[本轮记录](docs/VALIDATION_0.2.66.md)。

## 0.2.65 切换人员后的设备用途与模拟验收

明确选择另一位已有标定的佩戴者后，普通 CLI → 遥操作现在在释放 CLI 前核对当前官方人员，使用对应资料，避免旧标定接续或旧恢复记录抢占选择。已运行的准确 SDK 会话和用户明确选择的 SDK 档案仍保留其身份。新一轮连续转接、人员覆盖、信号中断及页面交错范围见[本轮记录](docs/VALIDATION_0.2.65.md)。

## 0.2.64 同一设备切换用途

设备页统一选择设备，进入 CLI 或遥操作时沿用当前设备，由后台确认退出后转接。页面显示当前设备与用途，普通操作区不再重复查找和接入。已有官方命名佩戴者的标定通过原始导出、SDK 导入与实际模型读回转交遥操作；同一 SDK 会话再次进入时直接复用。取消、错误清理和明确重试的范围见[本轮记录](docs/VALIDATION_0.2.64.md)。

## 0.2.63 人员并发与恢复复查

修复标定人员操作与状态读取互相等待、部分写入或迟到读取继续显示旧人员、CLI 回退后自动检查仍暂停，以及服务恢复后报告按钮卡住的问题。支持包增加内部文件 CRC 校验，损坏附件不会再显示完整导出成功。重新执行人员保存与套用、更新、导出、模拟断流和明确重试，范围见[本轮记录](docs/VALIDATION_0.2.63.md)。

## 0.2.62 连续操作与准确取消

继续主动检查连续使用和异常恢复，修复 CLI 更新占用释放失败后卡住后续操作、损坏备份记录进入恢复，以及旧页面取消影响新标定的问题。标定开始与取消绑定本次请求或准确运行编号，服务变化后退休旧页面等待状态；配置、官方原始档案及现有 SDK 控制链保持。范围见[本轮记录](docs/VALIDATION_0.2.62.md)。

## 0.2.61 日常组合流程复查

继续检查连接、更新、人员标定、虚拟预览和实机跟随之间的完整过程。覆盖标定前保留官方完整档案，组件更新启动失败时保留准确占用并允许显式核对恢复。中等窗口保留设备编号空间，窄屏跟随时暂停入口与键盘顺序一致。真实手套与灵巧手端点由模拟信号替代，执行现有控制链检查断流、停止和重试；具体结果与实物边界见[本轮记录](docs/VALIDATION_0.2.61.md)。

## 0.2.60 流程与失败恢复

继续复查标定、连接、展示、更新和导出的完整过程，修复在途接续无法取消、旧服务回复恢复过期状态、CLI 校验失败仍显示最新版，以及异常诊断元数据阻止导出的问题。普通预览控制同样核对当前型号与播放归属。修复前后及非作者复查范围见[本轮记录](docs/VALIDATION_0.2.60.md)，软件验证与实物验收分别记录。

## 0.2.59 遥操作单入口

侧栏合并为一个“遥操作”，页面内切换虚拟预览与实机跟随，共用已有连接和标定资料。模式切换不重新连接或启动动作，旧链接和标定后的下一步继续可用。沿用下列标定、连接稳定性与展示选手修复，验证边界见[本轮记录](docs/VALIDATION_0.2.59.md)。

## 0.2.58 标定套用与独立展示选手

标定完成后可直接进入虚拟或实机遥操作，复用官方用户档案与本次模型。修正 Windows 发现与控制端广播目录不一致导致的接续失败，补齐多用户、左右手保存和失败重试的核对；收到有效 SDK 数据后才显示标定已应用。展示页可独立选择型号与左右手，模型、动作预览与导出使用同一选择。局部停止与断开绑定原会话和控制代次，避免迟到请求误停新连接或新一轮跟随；连接与实机启动仍需用户明确操作。范围与验证边界见[本轮记录](docs/VALIDATION_0.2.58.md)。

## 0.2.57 标定与遥操作接续

选择手套与标定后直接显示原有引导表单，最终点击“开始标定”才接入，已有准确会话先确认释放。官方结果按 SDK 用户对象的准确身份核对，保存模型、官方导出、导入与遥操作使用同一身份；自定义旧模型不能凭所在目录跳过校验。旧刷新不能覆盖正在标定的新状态。固件终端输入的显示与交互一并复查。流程证据与实物验收边界见[本轮记录](docs/VALIDATION_0.2.57.md)。

## 0.2.55 官方服务与文件安装

手套更新窗口直接读取官方目录和准备官方 ZIP，查询、下载不占用设备；安装复用原版 CLI 文件模式，保留人工确认与维护保护。设备版本核对独立于目录，清楚区分软件验证与尚待接回设备的实物验收。范围见[本轮记录](docs/VALIDATION_0.2.55.md)。

## 0.2.54 固件兼容目录与版本显示

修复兼容目录失败后丢失已读版本、误显示旧目标和错误开放升级的问题；原版人工确认入口及未知退出保护保留。真实页面与非作者复查覆盖失败、重试和设备切换。官方兼容目录404本身仍未消除，不代表设备损坏，也不冒称固件已升级。详见[本轮记录](docs/VALIDATION_0.2.54.md)。

## 0.2.53 完整过程复查

继续主动检查任务启动、诊断、录制、支持包、节目单和设备更新的失败恢复与晚到回复。修复后重走原流程并独立交叉复查，软件检查与实物验收分别记录。每项维护按[验收规则](docs/QA_WORKFLOW.md)执行，具体发现见[本轮记录](docs/VALIDATION_0.2.53.md)。

## 0.2.52 中断、退出与异步恢复

修复损坏窗口配置阻止启动、设备列表旧响应覆盖新选择或离线状态、发现清理失败丢失退出记录，以及子工作区退出未确认仍被移除的问题。发现失败后只允许显式重试结束原查找，不自动接入设备。模型包拒绝非有限数值与错误形状；内置运行时和SDK在可捕获中断后恢复原环境，恢复不明时保留原备份。2189项隔离软件回归通过，浏览器实测与边界见 [验证记录](docs/VALIDATION_0.2.52.md)。

## 0.2.51 同类环境与路径故障排查

排查31个运行模块中的64处显式进程启动，以及更新、诊断和导出路径，修复8类相关问题。原生CLI统一使用同一份用户目录配置，非法目录在启动前拒绝；相对控制端路径与实际工作目录一致，Mac控制环境完整传递。文件权限和空间错误不再误报成设备通信问题，更新错误可展开详情；诊断或官方ZIP保存失败时保留已取得的原始输出，明确显示未完成部分。完整清单与范围见 [同类故障审计](docs/VALIDATION_0.2.51.md)。

## 0.2.50 Windows 固件升级目录

修复官方 Windows 固件升级提示 `HOME not set; cannot locate firmware cache` 的问题。只给升级子进程补齐同一用户的 HOME，并通过现成 ConPTY 接口传入；保留显式用户目录，不改父进程环境和 SDK 标定库。升级失败时保留原终端输出，并在更新页显示准确设备对应的官方失败原因。

原版升级确认、设备选择及写后版本核对保持由同一官方流程完成，不自动确认或重试刷写。本轮软件检查与真实终端环境验证通过，不等于实物固件已经刷写成功。

## 0.2.49 设备发现与维护

未接入时每5秒仅刷新发现列表，由用户点击连接。断开后，后台版本提醒只使用已经取得的设备资料与检查结果，不再启动设备固件查询。手套发现也不再通过工厂地址临时连接探测。

打开设备更新时暂停目录发现；若已在扫描，显式维护请求会等待本次只读扫描确认退出后继续。维护过程和未知退出状态保留占用保护，关闭空闲更新页后恢复发现，不自动连接。此次按用户要求直接在本机更新并同步源码，不发布新的下载包。

## 0.2.48 更新与工具恢复

修复慢速下载被总时限截断、旧完成记录与新失败混显，以及过期预览或节目单回复影响新任务的问题。多手工作区保存与移除失败时保留准确归属，未知连接不会反复创建新会话。导出、录制、节目单、映射参数和模型导入分别检查成功、失败与恢复流程，具体证据范围见验证记录。

Windows 发布可包含小型联网更新入口和完整离线安装包：`windows-x64-setup.exe` 会显示下载进度并核对本版完整包的长度与 SHA-256，再交给原安装器；`windows-x64-offline-setup.exe` 包含完整文件。二者沿用同一软件与用户资料。用户仍自行在软件内点击更新，检查和下载不等于已经完成安装。

## 0.2.47 任务与恢复

单手动作暂停后保留进度与连接，继续由用户明确触发；停止仍可独立执行。任务标签可拖出为独立窗口，窗口交接保留原任务归属。CLI 画面调整同步终端尺寸，固件恢复后的版本与提醒来自新读回的同一设备。映射预览按官方模型体积约束显示，SDK原始目标与实机反馈保持原样，显示检查不等于实物避碰验收。

## 0.2.46 CLI 独立维护

CLI拥有独立的检查、更新和自动更新选项；工作台空闲时切换到已核验的官方版本，失败保留原版。设备发现、官方画面和标定共用选中组件，用户标定库继续保存在原处。遥操作SDK与设备固件使用各自的维护流程。

## 0.2.45 更新进度与原位操作

软件更新入口在当前页打开，检查、下载、校验及安装均有对应反馈；下载进度使用真实字节，安装阶段显示安装器自身进度。SDK和固件沿用原官方维护流程，未知总量仅显示活动状态。验证与公开发布范围见 [0.2.45记录](docs/VALIDATION_0.2.45.md)。

本轮一并检查此前报告的设备与界面问题：明确断连后清理任务画面、目标映射变化时自动交接、独立任务窗口和多手暂停/继续。完整模式的设备工具位于侧栏底部，切换模式保持设置位置与分类名称。源级检查、实际安装和设备验收范围均在验证记录中分别列明。

## 0.2.44 CLI 与遥操作直接切换

明确选择「切换到遥操作」「切换到官方画面」或新的接入目标后，工作台核对目标与档案、结束原会话并连接新目标。可核实的原用户与标定资料沿用；缺少选择时先在同一处补选，取消保留原连接。已开始的原动作先按原停止流程确认停用，切换不会自动开始新动作。

旧会话释放未确认时保留错误，不把界面断开当作设备已退出。页面导航和发现列表中的候选选择仍不自动连接。0.2.43 修复空标定用户阻挡档案列表，0.2.44 修复只读扫描被误显示为已有设备会话。软件隔离验证、正常桌面安装和实物验收分别记录，见 [0.2.44 验证范围](docs/VALIDATION_0.2.44.md)。

## 0.2.41 Windows 正常启动与发现

全新 Windows 配置直接使用官方本机发现；已有远程与自定义配置保持原样，扫描不自动连接。首页同一错误只显示一次。修正验收环境：从资源管理器正常启动，核对实际使用的配置与设备发现，不能仅使用开发工具启动的结果。Codex MSIX 数据重定向导致的本机配置分离、恢复边界与验收见 [0.2.41 记录](docs/VALIDATION_0.2.41.md)。

## 0.2.40 版本检查、实体手与完整任务复验

修复安装版遗漏所选 Python 的 TLS 策略文件导致版本检查在联网前失败的问题；构建携带原文件并必须通过离线 TLS 初始化。版本检查保留本地文件、证书、网络、HTTP 与资料格式等具体原因，不再统一说成未联网。最终正式包及安装后的联网读取按同一冻结摘要独立核对。

遥操作直接提供实体灵巧手的查找、选择和连接，手套接收中可保留原档案与会话查找目标；连接反馈与开始跟随仍分开。当前发现、已有任务和最近连接分别显示，发现过期不冒称物理断联；准确停止确认后恢复查找，重新发现仍由你选择连接。存储异常、配对、编组、采集、映射和导出各自补录失败恢复与再次使用。范围与未测实物项目见[本版记录](docs/VALIDATION_0.2.40.md)。

## 历史：0.2.39 连接、使用与更新

在设备页明确连接手套或灵巧手，查看、标定和遥操作沿用原任务。可用更新显示提醒，可选择稍后；设备更新收在“更多”中，不在连接时打开升级窗口。固件检查过期时自动重新查找原设备；升级仍通过官方提示确认，结束后核对实际版本。SDK 版本读取与预览可以并行，更新时保留原空闲门禁。界面与完整软件流程、安装包和实物效果分别验收，见[本版记录](docs/VALIDATION_0.2.39.md)。

## 0.2.36 任务归属与取消修复

设备任务页同时核对当前设备与会话，旧标签不能控制同工作区后来接入的另一台设备；同一设备重连后重新核对会话并清除旧启动确认。原视图节点保留，不通过重载或重新连接来修复归属。

修正旧任务误取消另一只手套标定、活动编组误占闲置成员、旧状态请求回滚任务列表，以及取消后迟到回调仍显示配对成功等问题。标定接续取消对应准确的一次尝试，已进入遥操作后的旧取消不会代替原暂停/断开；临时档案复验文件会在成功和失败路径清理。软件检查、安装和实物验收各自记录，见[0.2.36验证范围](docs/VALIDATION_0.2.36.md)。

## 0.2.35 一块画面，直接操作

按用户反馈删除额外的「映射预览 / 碰撞预演 / 实机反馈」切换栏，不将它改藏到高级选项。手套单独接入时显示预览；目标灵巧手反馈接入后显示实测，实测过期保留明确失效状态，不回退生成目标冒充在线。连接、开始实机跟随、暂停、断开仍由原操作控制；自动画面选择不发送设备命令。

手套与官方CLI画面继续支持软件内尺寸调节。0.2.34的标定接续、官方档案沿用、设备任务/明确配对、双分区与软件内浮动功能保留。独立碰撞显示试验不再作为客户画面模式提供，不能据此宣称实机碰撞避免已经解决。验证边界见[0.2.35软件记录](docs/VALIDATION_0.2.35.md)。

## 历史候选：0.2.34 设备任务、标定接续与全软件交互

标定以官方参考图、当前指令和实际进度为中心；准备、采集、求解、成功、异常在同一任务区展示，不叠加就绪说明或嵌套滚动。设备列表、单手/多手、遥操作和设置沿用统一的字号、控件、边界与间距；历史折叠，多手控制先于画面，预览主要操作先于可选设置。

手套画面与官方CLI画面可在底部拖动调整高度，或在「画面大小」中调整、恢复自适应；各区域分别记住尺寸，不重新载入或连接设备。

分段切换复用同一选中面连续移动，状态刷新和单手/多手跨页搬移不截断动效。减少动态效果、高对比度、键盘和独立浮窗保持原功能。官方图示、终端及SDK继续复用，生成的设计参考不会替代官方资源。完整隔离软件回归1325项，1323项通过、2项按原条件跳过；实际档案互通与5090预演另有独立证据。软件检查不等于实际设备效果已验收，详见[界面规范](docs/UI_DESIGN_SYSTEM.md)和[验证范围](docs/VALIDATION_0.2.34.md)。

标定完成后，选择「继续使用这只手套」，工作台通过官方用户资料导出/校验/导入沿用本次手型，核对准确设备、用户和模型摘要，再进入同一手套的遥操作预览。无需再次手动扫描、选择和接入；这一步不会开启实机跟随。资料冲突或清理结果未知时保留具体错误，不覆盖另一份档案。

已连接设备生成任务标签，可明确选择目标配对。组合后的手套与灵巧手固定在一个任务中；标签支持双分区、拖放、软件内浮动与停靠，关闭视图保留会话。独立灵巧手和已有多手编组复用原动作与同步控制。选择、浏览和移动标签不发送设备命令；配对只读取目标反馈，实机跟随仍单独开始、暂停和断开。

遥操作画面区分「映射目标」「碰撞预演」「实机反馈」。碰撞预演复用原模型和 MuJoCo 接触约束，只改变独立显示姿态，不更改发送目标；软接触残余、第一代模型的部分碰撞覆盖和无效状态均明确标示。它不代表实物已具备避碰能力。

## 历史：0.2.33 独立遥操作工作区

侧栏「遥操作」集中选择手套、SDK档案、映射型号与目标灵巧手。手套可单独预览；接入目标只读取反馈，实际跟随仍需明确开始。连接、暂停和断开留在当前工作区，技术指标、超时与连接设置收进详情，主要操作不再被长说明推到页面底部。

官方Viewer与遥操作不是同一个设备会话。Viewer正在运行时，明确「切换到遥操作」只释放本工作区精确拥有的手套Viewer，确认清理后准备原遥操作控制会话；不会直接连接手套、开始跟随或迁移用户。映射目标和灵巧手反馈可分别查看，失效实测不会退回目标画面冒充反馈。操作与平台边界见[遥操作说明](docs/TELEOPERATION.md)。采集、映射和设备控制继续复用官方SDK/求解器；完整CLI没有另一个现成的teleop驱动命令。

## 0.2.32 手动接入与官方图形标定引导

扫描只列出设备，选择不会连接。点击「接入并查看」才打开所选原版Viewer；再选「标定」，确认佩戴者并点击「切换到标定」，软件核对并释放自己拥有的这一Viewer后，才用同一手套运行官方标定。不会自动结束其他工作区或换连设备。

本地佩戴者资料读取可以与设备发现同时进行，不把“已连接设备”作为只读档案查询的前提。连接、用户切换/创建与实际采集仍保留各自门禁。

默认图形引导显示官方JSONL当前动作、稳定性、约束和进度。六动作及参考图来自官方说明，采集、求解和发布仍调用固定CLI 2026.10.12/SDK。日志进入详情；高级选项保留原版终端，同一时刻只运行一种标定。参考图只按固定链接及摘要缓存到用户目录，不随源码或安装器分发。

后台刷新和发现有效期不再遮住当前步骤。图形模式成功必须有匹配用户、侧别和URDF的官方result、实际退出0及清理确认。软件和设备边界见[0.2.32验证记录](docs/VALIDATION_0.2.32.md)，操作见[标定说明](docs/CALIBRATION.md)。本次没有真实六动作采集或模型质量验收。

## 历史：0.2.30 原版终端流程

0.2.29 虽然合并了入口，仍要求先在工作台填写用户、左右手和覆盖确认，再进入官方终端，保留了两套准备流程。用户指出这一点后，本版移除默认 Windows 路径的重复准备表单。

打开「连接与校准 → 官方 CLI」，在上方只选择一次实际设备编号。「查看画面」打开原版 Viewer；选择手套后点「开始标定」，同一显示区直接进入原版 Human TTY。创建或切换用户、已有模型的覆盖确认、六步彩色动作指导和结果页都由固定官方 CLI **2026.10.12** 原流程提供。覆盖默认选择 No；工作台不替你创建用户、确认覆盖或按键。无需在工作台再次选择用户、左右手或设备。Viewer 用于查看骨架和触觉数据；标定使用同一区域中的官方终端。这里是原版终端菜单和动作图示，不是 Studio 图形界面；你直接按提示操作，不需要编写源码。

设备发现记录必须新近有效、编号和手套类型匹配；启动前核对实际左右手及会话归属。其他工作区、Viewer、手套预览或遥操作仍占用设备时，先明确结束所属会话，再开始下一项。原版终端通过现成 pywinpty 3.1.0、xterm.js 5.5.0 与 fit 0.10.0 显示，保留官方动作与交互。显式旧 Linux/custom JSONL 路径继续保留命名用户、侧别和覆盖同意门禁。

工作台不从终端文字推测当前用户或模型保存成功。退出码0只记为 `session_ended`；实际用户、左右手和 URDF 以官方终端的保存结果及档案为准。没有进行本轮真实手套采集或标定质量验收。步骤见 [标定说明](docs/CALIBRATION.md)。

0.2.29 的软件更新框架、原生 CLI 离线包、同一安装身份与资料保留方式继续使用；0.2.30 会重新构建和核对。完整 Python 回归1085项，1083项通过、2项按条件跳过，用时49.954秒；90项标定专项与全量重叠，不重复累计。独立界面验收23个唯一场景全部通过，使用隔离HTTP接口、原版终端渲染组件与合成ANSI输出；不代表实际Viewer或手套通过。最终包及覆盖安装结果按 [0.2.30 软件验证记录](docs/VALIDATION_0.2.30.md) 与外部最终交付清单分别记录。本版二进制 Release 尚未发布，真实网络更新闭环不能据此称为已通过。

## 历史：0.2.29 一个官方 CLI 工作区与软件更新

「连接与校准 → 官方 CLI」现在把原版 Viewer 与手套标定放在同一个工作区，共用设备选择。「查看画面」明确打开所选设备的画面，「准备标定」展开用户与左右手准备项；实际标定仍需另点「开始标定」。不再有独立的“官方可视化 / 手套校准”顶层分页，切换或展开也不会自行启动、停止或换连设备。

默认 Windows 路径的发现、Viewer、SDK 用户和手型标定使用同一固定版本 **2026.10.12** 的原生 CLI 及 Windows SDK 用户库。标定使用现成 **pywinpty 3.1.0、xterm.js 5.5.0 与 fit 0.10.0** 承载完整官方彩色终端动作指导，不重画姿势，不把 JSONL 文字反馈当作原版视觉界面。命名用户、覆盖同意、左右手和设备身份预检仍保留；终端运行时的档案以官方终端内实际选择为准。终端退出码0只表示会话结束，不能据此宣称模型保存成功；需结合官方结果并刷新 SDK 模型状态。操作见 [标定说明](docs/CALIBRATION.md)。

原生 CLI 发布 ZIP 随安装包离线提供。默认 Windows CLI 以后随工作台软件一起更新，不再下载另一份 Linux binary 后把它当作当前实际使用的 CLI。已有 Linux 用户不静默迁移；现有 WSL 手套预览、映射和遥操作仍使用其自身 SDK 用户库，相同用户名不代表模型已同步。显式旧控制端兼容路径与默认 Windows 路径分别说明。

「设置 → 关于 → 软件更新」提供检查、下载安装包、重启并安装。安装版启动会检查已发布版本；只有你点击下载才准备安装包，只有所有相关会话结束、条件满足后才可重启安装。更新面向工作台及配套 CLI、界面组件，保留用户资料。来源固定为本仓库，下载按发布资产的 SHA-256 或同名校验文件验证；没有公开安装包时明确显示“尚无已发布安装包”，不称为最新。维护者后续需要实际发布新版安装包，这个入口才有可下载的更新。

最终源码回归执行1078项，1076项通过、2项按条件跳过，用时51.584秒。独立主流程与TTY33项、更新卡19项、生产CSP变更面8项及相关P2修复复验均通过，各组有交叉，不累加为总测试数。界面与终端测试使用合成数据，原生 PTY 生命周期测试使用合成子进程；用户手套仍断开，没有真实 SDK/手套采集或标定质量验收。跳过原因、证据和安装交付边界见 [0.2.29 软件验证记录](docs/VALIDATION_0.2.29.md)。后续多设备任务标签及拖出窗口尚未实现。下方版本小节为历史记录，不覆盖本节的运行环境与更新方式。

## 历史：0.2.28 滚动区域对齐

主面板的原生滚动条轨道上下避开圆角，滑块与面板边缘保留间距；长页面与设置弹窗共用原生滚动样式，保留滚轮、键盘和触屏行为。设备连接与标定逻辑沿用0.2.27。

当时标定页只适配JSONL步骤与采集反馈，尚未嵌入完整原版终端视觉引导。此缺项由上方0.2.29实现说明取代；0.2.28的历史验证本身不能作为新终端功能的证据。

## 历史：0.2.27 手套标定准备与官方画面入口

手套校准按设备、用户、左右手组织准备项，就地扫描并显示具体未就绪原因。Default 用户需要先创建或切换命名用户；设备过期、身份不符、状态未核对和会话占用分别说明。官方当前步骤、稳定性、约束与进度来自实际事件，不根据计时估算。完整操作与模型保存范围见 [标定使用说明](docs/CALIBRATION.md)。

官方可视化默认进入实时设备；原“空窗口”改为「无设备查看器」，明确只是打开原版查看器，没有实时数据、不能标定，仍保留不接设备打开的能力。软件验证与后续真实采集分开，详见 [0.2.27 验证记录](docs/VALIDATION_0.2.27.md)。

设备中心撤掉自动扫描开关：未接入时每5秒查找，手动选择具体设备并连接，成功后暂停；「添加设备」恢复查找。删除旧自动连接设置，旧偏好不会触发自动接入。原会话保留断开入口；扫描不自动连接、不启动动作，也不为发现新设备而结束已有会话。分类与其他单选背景连续平移，确认控件及禁用状态保持一致。

## 0.2.26 设备中心、统一展示与简单模式

首页改为“设备”中心，左侧为最近连接，右侧为当前发现，不再使用欢迎大卡片、黑色大按钮和手绘手形。最近连接只记录实际接入事实，在本机按设备编号去重，最多保留12条；记录存在不等于当前在线。当前设备在页面可见、会话允许时每5秒自动扫描；扫描开关已在0.2.27按最新要求移除，以本页上方的新流程为准。

侧栏统一为“灵巧手展示 / Hand demonstrations”，页面内选择“单手 / 多手”；简单模式也能使用多手。复用原有展示、节目单和多手控制，切换保留活动会话、画面与输入，不开始或停止动作。无设备也可先选择动作和预览。

顶部 Hand 与 Glove 分开显示，未接入时进入对应的统一设备目录，按实际编号选择；不会直接连接第一台设备。身份核对后才补充可确认的型号与左右手，SDK 新鲜数据和官方 Viewer 运行状态分别显示。

语言切换只保留在设置；轻柔回弹默认用于日常控件，删除设置中的试用演示。模式选择只绘制一个移动底层，英文侧栏为完整单词留出空间。

本版中文设备名称统一使用“灵巧手”，覆盖页面、按钮、连接状态和服务端可见提示；保留官方 Hand / Glove 产品名。

首次默认简单模式，侧栏及设置中可切换到完整模式，展开参数、设备工具及高级配置。切换保留已有会话、控件与草稿，不发送连接、使能、标定或动作命令；偏好在本机保存。移除侧栏及工作区常驻声明，“关于”、许可证和本页的非官方身份说明继续保留。

Windows 原生磨砂增加事件恢复：回到窗口、还原窗口或系统外观变化后，按已有透明偏好重新应用，无需再次拨动开关。事件合并处理，普通拖动不逐帧重设；不轮询、不采集桌面，也不改变焦点或系统偏好。Windows 背景 Acrylic 在窗口失去焦点时可以退为实色，这仍由系统决定，不承诺失焦后持续透明。[微软 Acrylic 说明](https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic)

使用方法见 [设备首页与工作台模式](docs/SIMPLE_MODE.md)，既有要求及待验收项目见 [用户要求与验收清单](docs/REQUESTS_AND_ACCEPTANCE.md)。软件验证、构建及本机安装范围见 [0.2.26 验证记录](docs/VALIDATION_0.2.26.md)；早期安装候选保留为历史，不作为最终交付证据。公开安装包以上方实际 Release 为准。

## 历史：0.2.25 统一设备发现与选择

“连接与校准 → 设备连接”先显示统一设备目录，区分左、右机械手与手套，按实际序列号选择后再进入需要的功能。可见且空闲时自动发现，扫描与预览、标定及动作分开；选择卡片不启动机械手。

标定页使用共享发现缓存，用户档案刷新不再重复扫描。扫描失败或结果过期后不能沿用旧结果启动 Viewer；指定机械手编号时继续等待目标，不改连先出现的另一只设备。目录、手套预览及标定选择保持同步，旧元数据明确标为未核对。

沿用同一软件身份并保留用户数据，官方 CLI / SDK 来源与既有版本管理不变，不安装或升级 Studio。**回归与界面测试使用隔离假接口和合成数据，真实手套画面与标定尚未验收。** 操作说明见 [设备发现与选择](docs/Device-discovery.md)，最终测试数量、安装和发布结果见 [0.2.25 验证记录](docs/VALIDATION_0.2.25.md)。

## 历史：0.2.24 原版 Viewer 端口兼容

官方 CLI 在 Windows 实测会选到 5061 作为数据端口，Chromium 会以 `ERR_UNSAFE_PORT` 拒绝该端口，导致原版 Viewer 停在欢迎页。工作台仅对这类端口建立固定目标的本机转接，保留原版数据与界面；不关闭浏览器端口安全检查，不开放局域网入口。设备发现、数据接收和完成标定分别报告。

## 0.2.23 Windows 手套发现与官方画面

Windows 内置 WSL 的默认配置改由主机原生官方 CLI 发现手套并打开原版实时 Viewer，避免依赖 WSL 广播。第一次明确查找时，按需准备并校验固定的 Windows CLI 2026.10.12，使用独立缓存；不安装 Studio、不更改全局 WSL，也不覆盖 Linux CLI 的更新选择。

“空窗口”和“实时设备”分开说明，顶部明确标为“连接机械手”。SDK 手套预览与官方 Viewer 分开呈现，交接前需明确关闭纯预览并确认退出。官方标定继续使用原 Linux 用户，通过发现地址连接，并核对实际设备类型、序列号和左右手。进程运行不等于反馈新鲜，画面出现不等于已经标定。使用与更新边界见 [Windows 手套连接说明](docs/Windows-glove-network.md)；安装、发布和实机结果另行记录。

## 历史：0.2.22 统一设备与演示工作流

连接、官方校准、手套映射和多手演示使用一致的工作面与按钮层级。连接状态、当前标定用户、配置草稿和控制端实际确认分别显示；常用操作前置，环境配置和长说明按需展开，不重复实现官方校准。

手套映射保留五指参数格与原生模型关节参考，分别呈现保存与应用；服务离线时不再沿用旧帧声称映射已生效。多手页集中安排设备、同步/双手动作、播放方式与整组停止。

内层映射和多手分段选择也使用连续滑动底板；高级选项平滑展开收起。减少动态效果即时生效，停止按钮不缩放、不延迟发送。原生磨砂及既有设备、映射和动作参数保留。升级记录与未覆盖范围见 [0.2.22 验证记录](docs/VALIDATION_0.2.22.md)。

## 历史：0.2.21 空载打开与连续导航

「连接与校准 → 官方可视化 → 直接打开」无需接设备或启动 Linux 控制端，可显示本地原版 Rerun Viewer；它不生成手部或触觉反馈。切到「设备画面」后才查找设备，并按原有明确操作启动官方 CLI。

侧栏选中底块连续滑动并轻微回弹，快速切换从当前位置继续；分段选择器采用同一反馈。系统或软件的“减少动态效果”会立即停止动画。保留原生磨砂，不恢复外部背景捕获或折射。

设备工具收拢为实时反馈、参数调节和官方诊断；记录及触碰实验放在「设置 → 数据与工具」，保留数据与原控制逻辑。导入模型的流程预览不代表触碰识别或自动轻扣已通过实机验收。

源码运行或构建前先执行 `python prepare_official_viewer.py`，按清单获取并核对官方原版静态资源；安装包已包含这些资源。原版 JS / WASM 不改写，随包保留许可。详见 [0.2.21 验证记录](docs/VALIDATION_0.2.21.md)。

## 历史：0.2.20 官方原版可视化与独立 CLI 更新

打开「连接与校准 → 官方可视化」，查找设备、选择序列号后打开完整的官方 `wuji viz` Viewer。Hand 2 的关节与触觉、Glove 的骨架与原始矩阵使用原版界面；外围工具栏和明暗主题与工作台衔接。不再重复绘制手套骨架，工作台独有的映射目标与 MuJoCo 反馈预览仍保留。官方 Viewer 当前支持 Hand 2 和 Glove，Hand 1 仍使用工作台原有预览。

主窗口每次启动在后台检查官方 CLI 发布，自动克隆公开仓库记录版本来源，下载实际官方发布包并核对 SHA-256。版本和所需帮助接口通过检查后，等待所有设备工作区闲置再切换；保留上一版并支持回退。SDK、固件和标定档案不会随 CLI 自动升级。查找、打开、标定、Doctor 和日志导出直接使用官方 CLI；启动不会自行扫描或连接设备。

原版画面通过单一 iframe 显示，专注查看不新开第二个 Viewer。隐藏页面时卸载画面，CLI 会话单独停止。`--no-zero` 跳过触觉清零，不启用电机；Glove Viewer 仍设置数据流频率。CLI 没有持续反馈新鲜度信息，画面存在不代表设备当前在线。详见 [官方 CLI / Viewer 使用与更新说明](docs/OFFICIAL_CLI_VIEWER.md)。自动源码克隆使用 Git；缺少 Git、网络或官方摘要时明确报错并保留当前可用 CLI，不用未校验版本替换。

## 历史：0.2.19 内置官方 CLI / SDK 与状态恢复

当前采用工作台内置的官方 **CLI / SDK 2026.10.12**，配套控制环境为 **1.1.0**。打开「设置 → 连接与设备 → 官方 CLI / SDK」，安装或更新内置控制组件，再点击「检查 CLI / SDK」。卡片分别显示配套目标版本、环境安装记录，以及本次实际检查得到的版本和时间；实际检查会导入 SDK 并运行 CLI 的版本命令，不扫描、连接设备或使能电机。安装记录不能代替实际检查，检查通过也不代表设备连接、标定或实机动作已经通过。

用户已取消 Studio 嵌入方向。0.2.19 移除 Studio 页面及安装、打开入口，不再提供完整原版显示或其 S1–S3 改造作为本版新功能。官方 Studio 最终版 2026.9.22 已停止维护。后端只保留旧组件状态查询、核对和关闭的迁移兼容；升级不主动删除原 Studio HOME、用户档案或标定数据，不自动将其转换为 CLI 档案。详见 [Studio 历史与迁移边界](docs/OFFICIAL_STUDIO.md)。

多手独立设置增加始终可见的返回总览入口和当前设备名称；返回保留子会话，不代替操作员停止动作。校准操作错误和状态刷新错误分别显示，刷新恢复只清除对应刷新错误。隐藏主、子画面停止图像请求与解码，重新可见恢复显示；后台渲染按画面需求工作，设备反馈接收与控制心跳保持独立。桌面菜单和退出确认补充可访问名称。

标定统一使用官方 CLI，移除额外的六姿势图卡和姿势选择器。工作台只展示当前官方步骤、真实进度、结果与错误，原始事件默认折叠；不重复维护 CLI 已提供的采集与求解功能。

手套所选 SDK 用户保持到该连接结束，再在断开后恢复先前用户。Hand 1 实测显示使用其 0–19 编号；Hand 2 保留各指四关节的设备编号，代际未知或反馈不完整时不把目标姿态当作实测。断线后的停用未知按设备和会话保留，新的只读连接或另一只设备的确认不能把旧记录清为已停用。

本机升级缓存支持在中断或校验失败后重新暂存完整安装器；构建会核对缓存可执行文件的版本与产品身份，避免把旧程序包装为新版本。提供 [Windows 隔离软件检查 CI 模板](ci/windows-checks.yml.example)，覆盖语法、模拟升级失败及选定回归。当前上传凭据没有修改 GitHub 工作流的权限，模板尚未启用为 Actions；本机实测结果与远端 CI 分开记录。源码、模拟界面、安装版与实机结果分别记录，不将其中一项代替其余验收。

## 历史：0.2.18 完整 Ubuntu Wuji Studio

以下 0.2.18 及更早段落保留当时实现记录，当前功能以上方0.2.32说明为准。0.2.18 的 Studio 嵌入入口已取消，以下内容不是当前版本的安装或使用指引。

Windows 侧栏新增「Wuji Studio」，在工作台内部显示完整、未修改的官方 Ubuntu 应用，保留官方连接、Visualization、标定和隐私提示。首次使用从官方 GitHub 下载经 SHA-256 核对的程序，在已有内置 Linux 中安装专属图形组件，不另建大型虚拟机，不采集 Windows 桌面。

「组件设置」独立检查、安装和切换 Studio 版本，保留旧程序；用户档案与程序分开保存。查询失败明确显示缓存或随包已核验版本，不声称已确认最新。回退只切换程序，不自动降级档案。官方最终版为 **2026.9.22，已经停止维护**，版本管理不会凭空提供新的官方版本。

当时 Studio 与工作台单手、多手设备会话互斥，「离线查看」隔离原程序的设备网络。工作台不能读取原版内部电机状态，原程序退出不代表已确认电机停用。已有 CLI 标定和映射不自动改写成 Studio 档案。历史边界见 [Studio 说明](docs/OFFICIAL_STUDIO.md)；离线显示测试不等于真实标定或动作验收。

## 0.2.17 官方 SDK/CLI 更新与故障导出

顶部、左侧导航与主内容区采用层次不同的半透明底色，移除覆盖系统磨砂的旧白色渐变，连接卡片也减轻白底叠加。Windows 原生磨砂下可透出外部背景颜色，文字和控件保持实色。深色模式同样处理；开启“减少透明度”后恢复不透明。不使用桌面截图、额外 CSS 模糊或折射算法。

Windows 内置控制环境更新到官方 SDK 和 CLI **2026.10.12**，公共出厂镜像为 1.1.0。旧 1.0.0/1.0.1 环境可在连接页用“安装内置控制环境”原位升级：先校验和暂存新版，验证后切换，保留旧 SDK/CLI 备份；个人标定、参数与记录不从出厂镜像覆盖。不会升级设备固件。

修复 Windows USB 网卡类型字段导致的漏识别。默认设备网段不匹配时可识别并显示配置入口，只调整用户选择的空闲有线网口，不改 Wi‑Fi、默认网关或 DNS。

内置 Linux 的广播扫描不可用时，手套扫描会只读探测官方默认 `192.168.1.100:50001`、`192.168.1.101:50001`，读取真实类型和序列号，立即关闭探测句柄；再次打开预览时重新核对身份。它不启动机械手跟随。改过 IP/端口的设备仍需按实际配置设置通路，不能声称覆盖所有自定义网络。Doctor 增加独立的 IPv4:端口选择器，与 SN 二选一，适合按官方发现记录复核。

官方诊断使用控制器同一 Python 环境，避免专用 SDK 已安装却被系统 Python 误报缺失。显示官方 `fix` 建议和报告时间，保留警告、失败、跳过与原始输出；退出码 0 不当作全项通过。可下载可读文本、官方原始 JSON 和完整运行记录，重开软件仍可查看上次诊断。

“故障简报与官方支持包”调用 `wuji logs export`，选择最近 1–30 天，包含 SDK/Studio/CLI 日志以及官方默认采集的诊断、标定录制。官方 ZIP 原样保留，另附简报、导出命令结果和最近诊断。原始动作录制和设备序列号可能在包内；文件仅保存在本机，不自动发送。退出码 1 和部分采集失败如实显示，未发现设备时不生成虚构的错误码、温度或运动复测。此入口不清错、不使能、不运行主动诊断配方。

上游依据：[SDK v2026.10.12](https://github.com/wuji-technology/wuji-sdk/releases/tag/v2026.10.12)、[CLI v2026.10.12](https://github.com/wuji-technology/wuji-cli/releases/tag/v2026.10.12)。Studio 已在 v2026.9.22 宣布停止维护，当前接入以 SDK/CLI 为准。macOS 的准备脚本同步版本与校验值，本轮没有制作或验证 Mac 安装包。

0.2.17 当时尚未内嵌完整原版 Studio；此项由后续 0.2.18 实施，Ubuntu 版无需再嵌套。

本轮检查本地 Claude CLI 入口时未找到可用安装，没有新模型调用或 Claude 产出；实现与验收由主助手完成。通用辅助的历史贡献保持原记录。

## 0.2.16 节目单编辑与播放分离

节目单按「动作列表、播放设置、播放到」组织，底部固定显示状态和当前模式的启动按钮。速度与重复次数分别标注；持续循环改为开关，随机种子收进高级设置。「补齐全部动作」只追加缺少项，保留已有参数与顺序。

启动成功后自动回到手部画面，左侧切换为正在播放的节目单，提供暂停、继续和停止，结束后恢复单动作设置。多手页同样先选屏幕预览或真实手，再显示对应启动按钮。手套标定、求解、设备参数及后台动作执行方式未改。

通用列表组件由本地 `claude-opus-5-5 --effort max` 编写，返回模型一致且成功退出；主助手审核后集成，补充受控输入刷新并验证改序、删除焦点与长列表。未外发私有代码、模型、设备数据或凭据。

## 0.2.15 柔和回弹

按钮按下与松手加入轻微回弹，弹窗和分区采用短过渡，折叠箭头及开关自然停稳。在「设置 → 外观」点击“试一下”预览，开启“减少动态效果”可关闭。没有新增常驻动画循环、外部折射或滚动接管；电机参数、动作时序与标定逻辑不变。见 [动效说明](docs/INTERFACE_MOTION.md)。

离线曲线基础模块由本地 `claude-opus-5-5 --effort max` 完成，实际返回同名模型且退出成功。8 项测试和主助手独立数值积分核对通过后，生成静态 CSS 采用；未外发私有源码、设备数据或凭据。

## 0.2.14 操作入口与标定说明

连接浮层改为两张设备状态卡，以及「连接设备、手套标定、手套映射」三个图标入口。展示、校准、映射和设置统一主次按钮、折叠行、文档入口、键盘焦点与禁用状态，保留原生磨砂窗口。

手套六姿势标定继续由官方 CLI 采集、判定和求解。界面补充标定档案的持久化与跨控制环境迁移说明，明确输出调整、开源映射参数作用于机械手目标换算，不改写手套原始测量。原版 Studio 手势插图、人体模型与触觉 Visualization 尚未嵌入，不能将本轮界面整理算作完成这些功能。详情见 [标定与映射](docs/CALIBRATION_AND_TUNING.md)。

通用图标导航行由本地 `claude-opus-5-5 --effort max` 编写并经主助手审核集成。首次调用连接拒绝，重试实际返回模型一致且成功；没有外发私有代码、模型、设备数据或凭据。本轮未操作真实设备。

## 0.2.13 连接流程与关节图解

连接区按「设备连接、手套校准、映射调校、遥操作预览」组织。机械手与手套在同一页分别连接；六姿势校准采用左侧宫格、右侧准备与操作；高级配置和预设按需展开。保留原生磨砂窗口，改进深浅色选中状态、间距与窄窗口排版。

五行四列输出表现在附带模型关节名称。点选格子或右侧手指、关节选择器，查看原生模型高亮、动作含义、模型索引和模型角度范围。一代、二代左右手共 80 张参考图由随包 MJCF 生成，不播放物理动作，也不是实时反馈画面。

**模型索引不等于设备节点号。** 只有当前设备已有人工核对的显示映射且反馈新鲜，才显示确认节点和角度；未核对、断开或数据过期均明确标出。官方当前文档仍未提供 SDK S1–S4 与模型 J0–J3 的完整轴序对应，不能把按模型顺序显示的参考图当成设备对应验收。一代缺少明确轴名时保留原生轴编号，不套用二代语义。见 [官方命名说明](https://docs.wuji.tech/docs/zh/wuji-hand/latest/control-guide/)。

本轮关节图解的基础组件由本地 `claude-opus-5-5 --effort max` 完成，实际模型一致、退出成功；主助手审核语义、集成和验收。仅提供通用组件规格，没有外发私有代码、模型、设备反馈或凭据。本次没有驱动真实手或运行真实标定。

## 0.2.12 六宫格校准与内嵌开源映射

校准改为三列两行的六宫格，每格显示姿势示意和要求，当前步骤、已采集状态来自官方反馈。用户与手套选择、原始诊断按需展开；不是按本地计时模拟完成。

「手套 → 手映射 → 官方参数表」保留可填写和粘贴的数值矩阵，并接入固定版本的官方开源求解器。首次点击「准备求解环境」，联网下载独立依赖；在手套预览中点击「应用到开源映射」，收到匹配参数的新帧才显示已生效，也可切回 SDK。实机跟随仍单独开启。本版新增求解环境支持 Windows 内置 Linux / Linux x86_64 Python 3.12，Mac 尚未验证。

官方 SDK 内置映射并未开放这套参数；本版嵌入的是独立开源算法。不是完整原版 Studio，也不声称复制了研发内部面板。四种手型经过真实求解器的合成骨架测试，真实手套贴合与实机跟随尚待设备验收。见 [使用步骤与边界](docs/CALIBRATION_AND_TUNING.md)。

六宫格基础组件由本地 `claude-opus-5-5 --effort max` 辅助，实际返回同名模型且完成；主助手审核、集成并验证。没有发送私有代码、模型、设备数据或凭据。

## 0.2.11 官方标定反馈与数值参数表

「连接与校准」补全官方标定姿势、稳定性、约束和诊断的显示；未知字段不算通过。20 关节输出调整改成五行四列数值表，不再逐个展开手指；支持表格粘贴和中英文切换保留编辑。

另设「官方参数表」，编辑公开 retargeting 的五指比例、对指距离与平滑配置，按设备组合保存草稿，并生成对应左右手/代际的完整 YAML。**它目前用于官方开源工具，不会应用到当前 SDK 内置映射。** 标定仍使用官方 CLI，尚不包含 Studio 的完整传感器录制与人体 URDF 查看器。见 [具体流程、字段与接入边界](docs/CALIBRATION_AND_TUNING.md)。

本轮通用表格由本地 `claude-opus-5-5 --effort max` 辅助实现，实际返回同名模型且正常完成；主助手审核、修正并完成集成。未发送私有模型、设备数据或凭据。

## 0.2.10 界面整理

导航集中为「单手展示」「多手演示」「连接与校准」，反馈、参数和诊断放进「调试工具」。单手展示先选真实手或预览，再选动作与速度；循环与幅度按需展开，节目单和运行详情分别打开。节目单收起后仍显示播放状态和停止入口。

多手演示把参与手、同步动作／双手衔接、动作和速度放在画面旁边。点击「管理设备」，分别处理「已添加」「添加设备」「工作区」，不再把设备管理和播放表单堆在一起。手套可视化、官方标定及映射仍在「连接与校准」中。

保留原生磨砂和明暗主题，清理隐藏面板的残留绘制与重复控件。这是界面整理，不更改电机力度、轨迹或发送频率；真实设备验收不由软件预览代替。

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

Ubuntu 24.04 x86_64 从 Git 克隆后，可在项目 `.venv` 安装官方 CLI / SDK 2026.10.12，并选择「本机 Ubuntu」，无需 SSH、虚拟机或 Studio。完整的固定版本下载、校验、配置路径，以及官方 `viz --sn SN --no-zero` 和 `calib hand-model --sn SN` 示例见 [Linux 本机安装](docs/LINUX.md)。当前工作台已嵌入完整原版 CLI Viewer；新的 Linux 安装与实机画面仍需在目标系统验收。

只想先在另一台 Ubuntu 装好官方工具、找手套、做手型标定和备份，可逐段照着 [Ubuntu CLI / SDK 操作指南](docs/UBUNTU_CLI_QUICKSTART_ZH.md) 执行；工作台的本机接入继续使用这些配套工具。

离线检查：`python src/desktop.py --self-check --render-check`。测试：`python -m unittest discover -s src`。

`--self-check` 输出包含产品身份与版本；构建器读取独立自检文件，二者与当前版本配置不符就拒绝打包。`ci/windows-checks.yml.example` 是尚未启用的 Windows 软件检查模板，使用独立临时数据、模拟安装器和模拟进程；模板本身不代表远端 CI、全套测试、安装版或实机验收通过。

Windows 构建需要先按 `src/runtime_manifest.json` 准备对应压缩镜像到 `runtime_payload/`（发布包附带镜像），不把运行环境提交到 Git。然后安装 `requirements-build.txt` 后运行 `python build.py`，再使用 `python build_installer.py --iscc PATH_TO_ISCC --webview-bootstrap PATH_TO_SIGNED_MICROSOFT_BOOTSTRAP --zh-language PATH_TO_CHINESE_ISL`。安装器使用 Inno Setup 6.4.3 和对应版本中文翻译。

Ubuntu 已有历史 0.1.3 包。macOS 的原生构建在 GitHub Mac runner 上进行，真实 Mac 与机械手验收仍待完成；不把 Windows 文件改名当作 Mac 安装包。

0.2.0 的本地 Claude 按用户当时偏好使用 `claude-opus-5-5 --effort max`，仅辅助边界明确的播放列表基础设计；实际返回模型为 `claude-opus-5-5`。输出经主维护者审核，控制逻辑、设备边界和真实结果由主维护者核验。代码接口用于检视软件，不操作桌面。来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## English

Version 0.2.40 fixes a packaged Python TLS policy file omission that caused release checks to fail before any network request. Builds preserve the selected interpreter's exact policy and require offline TLS initialization; certificate and hostname validation remain enabled. Teleoperation exposes explicit physical-hand discovery, selection and connection while retaining the receiving glove session. Current discovery, retained tasks and local connection history remain separate. Task-specific failure/recovery evidence, package/install evidence and physical acceptance are recorded separately in [0.2.40 validation](docs/VALIDATION_0.2.40.md) and the [task evidence index](docs/TASK_ACCEPTANCE_0.2.40.md).

Version 0.2.32 requires an explicit connection before calibration. Discovery and selection never connect. Connect and view starts the selected official Viewer; Switch to calibration explicitly releases only that owned session and starts the same glove's official calibration. The default graphical guide reads official schema-v2 JSONL, with official six-pose references cached privately from pinned URLs. Capture, solver and publishing remain in the unmodified CLI/SDK. Original Human TTY remains an advanced alternative. Background refresh and discovery age do not obscure an active run. Guided completion requires a matching official result, actual exit zero and confirmed cleanup. See [0.2.32 validation](docs/VALIDATION_0.2.32.md). No physical calibration-quality acceptance is claimed.

The following descriptions are retained as history.

Version 0.2.30 removes the duplicate Workbench calibration preparation retained in 0.2.29. In Connection → Official CLI, select a device once. View device opens the original Viewer; Start calibration opens the original Human TTY in the same display stage. The pinned official CLI 2026.10.12 owns profile creation/switching, overwrite confirmation (No by default), six colored pose guides and its result journey. No second Workbench user, hand-side or device selection is required. Device identity, recent discovery and cross-workspace session checks remain enforced; the explicit legacy JSONL path retains its existing named-user and overwrite protections.

The terminal uses pywinpty 3.1.0, xterm.js 5.5.0 and fit 0.10.0. Workbench does not infer model ownership or successful saving from terminal output: exit code 0 means only `session_ended`. Check the actual user, hand side and URDF in the official result/profile. No physical hand-model calibration or quality acceptance was performed in this release.

The 0.2.29 application-update framework and offline native CLI bundle are retained and rebuilt for 0.2.30, using the same installation identity and user-data directory. The full Python source suite ran 1085 tests in 49.954 seconds: 1083 passed and 2 were conditionally skipped. The 90 calibration-specific checks overlap with that total. See [0.2.30 software validation](docs/VALIDATION_0.2.30.md) for the UI and delivery boundary; package hashes, installation and source publication belong to the external delivery record. No new binary Release is published yet. Existing Linux profiles are not silently migrated; WSL preview and teleoperation still use their own SDK profile store.

Version 0.2.20 embedded the original official `wuji viz` Viewer under Connection → Visualization and introduced independent CLI release preparation and idle switching. That historical update policy does not describe the default Windows path in 0.2.29. See the [CLI / Viewer guide](docs/OFFICIAL_CLI_VIEWER.md) and [0.2.20 validation](docs/VALIDATION_0.2.20.md). Physical-device acceptance remains separate from software tests.

For a source installation on Ubuntu 24.04 x86_64 with Python 3.12 and the pinned official CLI / SDK, see [Local Linux installation](docs/LINUX.md). Linux can run locally without SSH or a VM. The guide also covers official visualization and calibration commands; the workbench embeds the complete original CLI Viewer. A fresh Linux installation and physical-device view still require acceptance on the target system.

The 0.2.19 update uses the bundled official CLI and SDK 2026.10.12 with controller image 1.1.0. Open Settings → Connections → Official CLI / SDK to install or select the components and run the software check. Actual CLI version and SDK import results are separate from the installation record and bundled target. These checks do not connect devices or enable motors. Build, installation and publication status must be confirmed in the [0.2.19 validation record](docs/VALIDATION_0.2.19.md).

Studio embedding has been cancelled. Its final vendor release, 2026.9.22, is no longer maintained. The 0.2.19 interface provides no Studio installation or open action; only legacy backend query/probe/close compatibility remains. Existing Studio HOME, calibration and profile files are retained without automatic conversion. The 0.2.18 section above is historical; its embedded UI and the cancelled S1–S3 work are not new 0.2.19 features. See the [migration notes](docs/OFFICIAL_STUDIO.md).

This update adds a persistent return path from a child hand workspace, separates calibration operation and refresh errors, and suspends hidden-view image requests/decoding while retaining feedback and control heartbeats. It also corrects generation-specific feedback numbering, holds the selected SDK user for the glove connection lifetime, preserves unconfirmed stop records per device/session, repairs interrupted installer caches, and verifies cached build identity. The new Windows CI definition runs isolated software checks; no installed-build or hardware success is implied.

Version 0.2.9 adds logical multi-hand workspaces, explicit discovery-to-workspace assignment, shared same-action playback, eight authored left/right pair routines and actual per-session MuJoCo views. Real group playback currently requires native Hand 2 devices using the same Linux clock domain. Hardware acceptance and mechanical synchronization accuracy remain unverified. See [multi-hand guide](docs/MULTI_HAND_GROUPS.md) and [validation record](docs/VALIDATION_0.2.9.md).

**Hand Workbench is one unofficial personal hand demonstration application. It is not published, maintained or endorsed by Wuji Technology.** Windows uses an independent native window with embedded WebView2. One installer serves both demonstrations and optional local model packs; there is no separate Research application. Private weights are excluded from the public build.

Features include Chinese/English, minimalist glass chrome, four native hand profiles, text sequences, numbers, clock poses, authored finger dances, configurable parameters, feedback, recording, diagnostics and glove integration. Windows now bundles an optional minimal WSL 2 controller (~128 MB compressed), removing VMware and SSH setup from everyday use. Windows component setup may require administrator approval and a reboot. An external Linux controller remains available; native Windows SDK control and automatic Hand 1 USB passthrough are not claimed. New UI tests and synthetic mapping checks are not real hardware acceptance. The optional left-hand model is for offline simulation feedback only and cannot enable real-device grasping.

Use the [code-only maintenance interface](docs/DESKTOP_API.md) for inspecting the app, capturing its own WebView and applying a verified local installer. It never controls the computer's mouse/keyboard or captures other applications or the desktop. User data is preserved across upgrades. Packages are unsigned; check the trusted release and published SHA-256. Windows is the current release target; macOS validation remains pending.

Version 0.1.8 adds an always-visible connection control, discovered-device selection, refreshed translucent navigation and compact action layout. It pairs with controller image 1.0.1; upgrading the owned 1.0.0 environment preserves user parameters and records. No motor gains or trajectory settings are changed by this UI update.

Version 0.1.9 adds an Apple Silicon Cocoa/WKWebView preview and a bundled Ubuntu/Lima control runtime. Initial SDK setup requires internet; subsequent VM starts are managed by the app. Version 0.2.7 uses native vibrancy on macOS; experimental desktop refraction has been removed on Windows. Packages are ad-hoc signed, not notarized. Physical Mac VM startup, device operation and visual glass acceptance remain unverified. Hand 1 USB passthrough is not implemented; an external Linux controller remains available. The Mac workflow publishes a clearly marked prerelease only after a successful native build and bundle verification.
