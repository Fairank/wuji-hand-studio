# 完整 Ubuntu Wuji Studio / Official Studio

Windows 0.2.18 侧栏「Wuji Studio」运行完整官方原程序，没有重写或精简界面。[官方发布页](https://github.com/wuji-technology/wuji-studio/releases) 最终版为 2026.9.22，已停止维护；后续工具参见 [官方迁移说明](https://docs.wuji.tech/docs/en/wuji-studio/latest/migration-to-cli/)。

## 打开与关闭

先结束所有工作区的机械手连接、手套会话、标定与诊断。首次「安装组件」需要内置 Linux 就绪，以及官方 GitHub、Ubuntu 软件源网络。不会另建大型虚拟机、更新设备固件或发送动作。安装进度显示在此页，详细依赖日志为组件目录中的 `setup.log`。

「离线查看」将原程序放在独立网络命名空间，不能扫描或操作设备。正常「打开 Studio」沿用已有设备网络。未新增网卡、修改默认路由或向局域网开放图形端口。WSL 广播、USB 直通、自定义地址仍受网络条件限制，界面出现不能推断已连接设备。

「专注显示」放大原界面，小窗口缩放完整布局。原版首次隐私选项由用户自行选择；工作台语言与主题不强制覆盖原版设置。

Studio 管理设备期间，所有工作台单手、多手工作区暂停建立新控制会话，停止入口保留。关闭前请在 Studio 内停止动作并断开。工作台不读取原程序内部电机状态，组件关闭只确认所属软件退出，不代表电机停用已确认。

## 更新与数据

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

## 来源与验收边界

安装包包含适配器与固定下载清单，不重新分发官方 Studio 二进制；官方程序遵循其自身条款。图形包许可证在 Linux `/usr/share/doc`，noVNC 客户端版权可从本机 `/studio-assets/COPYRIGHT.txt` 查看。

此模块只集成 Windows x64 的内置 Linux；Ubuntu 版直接使用官方程序，无需嵌套。本轮未构建 Mac 包。离线原版显示、关闭恢复和工作区互斥验证不等于手套标定、触觉或实机动作验收。
