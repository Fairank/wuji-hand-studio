# 0.2.20 验证记录

本版将官方 CLI 的原版 Viewer 嵌入现有工作台，并增加独立 CLI 版本管理。仍是一个非官方个人工具，沿用原产品、安装身份与数据目录。

## 已核实的官方来源

2026-10-21 实际获取官方 CLI 2026.10.12 的发布元数据、Linux x86_64 归档和公开 git tag。归档 SHA-256 为 `4e84764ffbdfff2e1b420bf8742b8d8d4c1f6db337dc0e7149945d9893e5775a`；解压后可执行文件 SHA-256 为 `a1776c0cc5e94cccdfcf5d381afb3d329158c9bb77830387a0aac74eb1ec51fb`。来源仓库 tag commit 为 `a54280f4a6d4cf43b37f551c35e9db04b6cf476d`，不与二进制编译 commit 混为一谈。

已在内置 Linux 独立目录实际运行该二进制的版本、主帮助、viz 帮助和 hand-model 标定帮助，退出均成功。未运行实际设备可视化、扫描、清零、标定或电机命令。

## 软件与界面

最终完整软件回归为 **723 项：721 通过、2 项环境跳过，零失败、零错误**。覆盖合成发布包校验、版本切换与回退、会话互斥、启动停止竞态、异常退出，以及慢网和取消下载。原始失败保留：安装版初测曾暴露网页回退缺摘要、60 秒传输预算不足；已分别修复并增加回归，不把初测失败改写为通过。

两项跳过来自 Windows 环境限制，其中 Linux `flock` 检查已在隔离 Linux 中另行运行通过；Windows 主记录仍按 721 通过、2 跳过保存。

Linux 另以普通 Python 假 CLI 实测管道 EOF、正常停止、PID 身份确认、runner 意外结束后的子进程退出及旧记录恢复；36 项检查和三个实际假进程流程通过。它们不等于真实设备或原版 Viewer 数据流测试。原版 Viewer 的普通 iframe 嵌入方式已核对 Rerun 0.36.0 官方实现；不额外加入会阻断子页的隔离头。

使用安装的 Edge / Playwright 对真实工作台页面进行全模拟验证：1320、960、700 宽度，中英文和暗色；单 iframe、隐藏恢复、原位专注、主题查询保留、读取失败、残留会话停止恢复和 CLI 更新失败重试通过。无 JavaScript 错误、横向溢出或按钮重叠。iframe 内容为明确的模拟画面，不是实际官方 Viewer 与设备连接验收。

## 设备边界

本轮不连接真实手、手套，不执行运动、固件升级或标定。实际原版 Viewer 的设备画面、触觉数据、反馈持续性与实机功能需在设备连接后独立确认。Windows 软件、Ubuntu 源码和 macOS 安装包的验收不互相替代。

## 构建与发布

Windows **0.2.20 已完成本地构建、安装和软件验收**。安装版使用 WebView2，159 个 Web 资产与最终源码一致；直接比较最终源码与 EXE 内冻结代码，88 个 frozen code objects 和 97 份打包控制端源码全部匹配，无差异。最终原生界面检查没有 JavaScript 错误或横向溢出，语言及外观偏好保持。文件时间戳不作为构建一致性的证明。

| 本地产物 | SHA-256 |
| --- | --- |
| 最终 EXE | `a6538d7178ed8434a21ddc6499e064a58ca4fd97496e29755eb4f5c5d5b1f76b` |
| Windows 安装包 | `22c9d00fc9c1f22e9e8304601dd631fb1b10ec69f5ae32aed50da2aa0eef6470` |

安装版实际完成官方 CLI 的下载、校验、准备、软件兼容检查和选用，再回退到内置 CLI。重启后仍保持 `current.source=bundled`，验证启动检查不会撤销回退选择；显式检查并更新后，独立 CLI 恢复为 `current.validated=true`、`mode=wsl`。本轮内置与独立 CLI 的版本号均为 2026.10.12，验证的是来源和路径切换、回退保持及显式恢复，并非跨版本号升级。

下载取消测试在暂存文件已有 **17,235,968 字节**时请求关闭，**1.406 秒**成功退出，最终 `.prepare-*` 暂存目录为 0。回退初次报告器因重复 `elapsed_s` 参数报错，原记录保留；后续只读确认实际切换、回退、服务关闭与暂存清理均已完成，没有重放回退动作。

数据保留核对确认 **4 份配置和 57 份 Linux 标定 profile 字节未变**。正常会话计数由 7 增至 21；停止记录和已确认设备记录保持为空。最初只比较文件整体哈希产生的报警保留，并另以原文件哈希和字段核对说明这些正常运行变化；未为通过检查恢复或改写会话日志。

证据位于 `outputs/workbench-cli-0220/`：`build-result.json`、`native-final-0220.json`、`frozen-source-verification.json`、`native-download-cancel.json`、`native-cli-roundtrip/rollback-confirmed.json`、`native-cli-roundtrip/restore.json` 和 `userdata-preservation-confirmed.json`。

此次验收范围按用户要求限定为软件。原版 Viewer iframe 内容仍为 mock QA，真实设备画面和 WASM 加载尚未验证；本轮未连接、扫描、清零、标定或驱动设备。本地安装包已完成，源码提交与 GitHub Release 上传、发布状态另行记录。
