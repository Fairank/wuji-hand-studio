# Studio 历史与 CLI / SDK 迁移 / Retired Studio integration

**0.2.19 已按用户最终选择取消 Studio 嵌入。** 当前工作台不提供 Studio 页面、安装、更新、选择版本或打开入口。官方 Studio 最终版为 2026.9.22，已经停止维护；本版使用官方 CLI / SDK 2026.10.12、配套控制环境 1.1.0。此文件保留原链接，说明历史和旧数据边界，不再作为安装 Studio 的指引。

## 当前入口

打开「设置 → 连接与设备 → 官方 CLI / SDK」，安装或更新内置控制组件，再运行「检查 CLI / SDK」。卡片分别显示环境安装记录、配套目标版本、实际检查版本和时间。实际检查导入 SDK、读取其包版本并运行 CLI 版本命令，不扫描、连接设备，不开始校准或使能电机。

工作台现有的连接、六姿势标定、手套映射与遥操作继续使用各自明确的操作入口。CLI / SDK 软件检查不等于这些设备流程已经通过。当前构建、安装和发布结果以 [0.2.19 验证记录](VALIDATION_0.2.19.md) 为准；Mac/Ubuntu 的历史安装版验收不因本次文档更新而改变。

## 旧组件兼容与数据保留

后端只保留旧组件状态查询、核对和关闭的迁移兼容：`GET /api/official-studio`、`official_studio_probe`、`official_studio_close`。旧客户端的安装、更新、版本选择、打开及申请新 Viewer 会话的动作不再放行。它们不是本版公开的新使用流程。

对已有会话的核对或关闭只用于确认所属软件是否退出；工作台不能读取原版内部电机状态。关闭原版窗口、图形服务或 Linux 进程，不能当作已确认设备停用。若旧会话占用尚未解除，仍按真实状态处理，不把残留记录直接当成空闲。

升级不主动删除 Studio HOME、个人档案、标定或原有程序数据，也不自动将 Studio 档案转换、合并或降级到 CLI 用户档案。需要迁移时，按官方文档使用用户档案导入/导出；不要以一份环境的标定文件存在，推定它已经在另一环境中生效。下方旧路径仅用于识别保留数据，不是删除清单。

CLI 用户标定位于运行 SDK 的控制环境中，与 Studio HOME 分开。手型、触觉与工作台配对参数也各有作用范围，详见 [标定与调参说明](CALIBRATION_AND_TUNING.md)。保留一个工作台、原安装身份和用户数据目录，不创建另一款 CLI / SDK 应用。

## 历史：0.2.18 的实现范围

以下记录旧版行为，不是 0.2.19 安装或使用指引。随后提出的 S1–S3 Studio 改造已取消，不作为本版新功能或通过项；不继续实施黑边优化或完整原版显示验收。旧版离线显示、关闭恢复或工作区互斥结果也不能替代真实手套标定、触觉或实机动作验收。

Windows 0.2.18 侧栏「Wuji Studio」运行完整官方原程序，没有重写或精简界面。[官方发布页](https://github.com/wuji-technology/wuji-studio/releases) 最终版为 2026.9.22，已停止维护；后续工具参见 [官方迁移说明](https://docs.wuji.tech/docs/en/wuji-studio/latest/migration-to-cli/)。

## 历史：打开与关闭

先结束所有工作区的机械手连接、手套会话、标定与诊断。首次「安装组件」需要内置 Linux 就绪，以及官方 GitHub、Ubuntu 软件源网络。不会另建大型虚拟机、更新设备固件或发送动作。安装进度显示在此页，详细依赖日志为组件目录中的 `setup.log`。

「离线查看」将原程序放在独立网络命名空间，不能扫描或操作设备。正常「打开 Studio」沿用已有设备网络。未新增网卡、修改默认路由或向局域网开放图形端口。WSL 广播、USB 直通、自定义地址仍受网络条件限制，界面出现不能推断已连接设备。

「专注显示」放大原界面，小窗口缩放完整布局。原版首次隐私选项由用户自行选择；工作台语言与主题不强制覆盖原版设置。

Studio 管理设备期间，所有工作台单手、多手工作区暂停建立新控制会话，停止入口保留。关闭前请在 Studio 内停止动作并断开。工作台不读取原程序内部电机状态，组件关闭只确认所属软件退出，不代表电机停用已确认。

## 历史：更新与数据

「组件设置」检查官方版本、安装所选版本、选择已安装版本或回到上一个程序。需先关闭 Studio 并结束设备会话。下载固定来自官方 GitHub；核对发布大小、SHA-256、包名、版本和架构后暂存切换。失败保留原选择，不执行原包安装脚本。GitHub 限流或断网时明确提示，显示上次核验记录或随包固定版本，不将其称为在线最新结果。

回退只切换程序，不自动降级或迁移档案；请先使用官方导出功能备份跨版本数据。旧程序不会随更新删除。官方已停止维护，此功能不承诺未来一定有新版本。

| 内容 | 位置 |
|---|---|
| Windows 缓存、版本选择、日志 | `%LOCALAPPDATA%/WujiStudio/official-studio` |
| 原版程序与校验收据 | `/opt/hand-workbench-studio/versions/<版本>` |
| 原版专用 HOME 与持久档案 | `/home/workbench/.local/share/hand-workbench-studio/profile` |
| 原版图形会话日志 | `/home/workbench/.local/share/hand-workbench-studio/runtime/<会话>/session.log` |

原程序专用 HOME 不覆盖控制用户现有 `~/.wuji`、映射和记录。CLI 与 Studio 档案不会自动合并；共享时使用官方档案导入/导出，不能将原 CLI 标定直接标为已在新 Studio 档案内生效。重启保留档案，卸载工作台不自动删除用户文件。

图形服务仅监听本机，使用独立会话认证。公开状态、检查接口和收据不输出图形密码。画面来自专属 Linux X 会话，不读取其他 Windows 窗口或桌面。当前使用软件渲染，未承诺固定刷新率。

## 历史：来源与验收边界

安装包包含适配器与固定下载清单，不重新分发官方 Studio 二进制；官方程序遵循其自身条款。图形包许可证在 Linux `/usr/share/doc`，noVNC 客户端版权可从本机 `/studio-assets/COPYRIGHT.txt` 查看。

此模块只集成 Windows x64 的内置 Linux；Ubuntu 版直接使用官方程序，无需嵌套。本轮未构建 Mac 包。离线原版显示、关闭恢复和工作区互斥验证不等于手套标定、触觉或实机动作验收。

## 当前官方来源

- [Studio 仓库及维护状态](https://github.com/wuji-technology/wuji-studio)
- [Studio 迁移至 CLI](https://docs.wuji.tech/docs/en/wuji-studio/latest/migration-to-cli/)
- [CLI v2026.10.12](https://github.com/wuji-technology/wuji-cli/releases/tag/v2026.10.12)
- [SDK v2026.10.12](https://github.com/wuji-technology/wuji-sdk/releases/tag/v2026.10.12)

## English

Studio embedding is retired in 0.2.19 at the user's request. The current Workbench uses bundled CLI / SDK 2026.10.12 and controller image 1.1.0, managed under Settings → Connections → Official CLI / SDK. Actual version/import checks remain separate from installation records and do not connect devices or enable motors.

Only legacy backend query/probe/close compatibility remains; installation and opening are rejected. Existing Studio HOME, calibration, profiles and program data are retained without automatic migration or deletion. Software exit does not confirm motor disable. The 0.2.18 description is historical, and cancelled S1–S3 work is not a 0.2.19 feature or acceptance result. Hand Workbench remains one unofficial application.
