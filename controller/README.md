# 控制端与官方诊断 / Controller and official diagnostics

界面和 MuJoCo 预览不用机械手，也不用虚拟机。控制真实手需要官方 SDK；当前官方支持
Ubuntu 22+。Windows、Mac 的界面可以连接一台常驻 Linux 电脑，不一定使用虚拟机。
本项目不附带账户、密钥、密码或已配置的设备地址。

## Ubuntu 本机使用

1. 在 Ubuntu 准备 Python 3.10+，创建项目环境：`python3 -m venv ~/wuji-studio-venv`。
2. 用该环境安装 SDK：`~/wuji-studio-venv/bin/pip install wuji-sdk numpy`。
3. 下载包完整解压后运行工作台。在“连接与校准 → 控制端与诊断设置”选“本机 Ubuntu”。
4. 控制程序目录选下载包的 `controller/source`，或 Git 克隆的 `src`；填写绝对路径。
   SDK Python 路径填 `~/wuji-studio-venv/bin/python` 对应的完整绝对路径，不能原样填 `~`。
5. 保存设置，选择代数和左右手，再点击连接。无需 SSH 服务或虚拟机。

控制目录需可写，用于用户明确执行的参数同步和采样。SDK 未安装或路径不正确时会显示
连接失败；软件不会替你安装系统组件或自动启动电机。

## Windows / Mac 使用远程 Linux

在 Linux 完成上述环境安装，将下载包 `controller/source` 内容或仓库 `src` 放入独立目录。
配置该机 SSH 服务和自己的密钥，先核对主机指纹。工作台选“远程 Linux（SSH）”，填写
主机、用户、控制目录、SDK Python 和本机密钥路径；留空密钥使用正常 SSH agent / 默认密钥。
未知主机指纹不会自动接受。连接配置可留待目标电脑上的维护者或 Codex 完成。

参数在网页本机保存后，断开设备再明确点击同步；重新连接加载。保存不会开始动作。
二代留空设备地址自动发现；需要指定时使用官方 SDK 所需的 IPv4 或 `IPv4:port`。
一代建议自动发现；实机当前适配官方张开/握拳，其余动作仅预览。

## WujiDoctor

在运行 SDK 的 Linux 控制端按[官方安装说明](https://github.com/wuji-technology/wuji-cli)
安装 `wuji` CLI，并在设置中填写可执行文件路径（例如自己的用户目录下 `.local/bin/wuji`）。
进入“官方诊断”可以检查 CLI 版本、执行 `wuji doctor --json`、查看分项和导出原始报告。
可选序列号由 `--sn` 传递。无需部署控制脚本即可单独使用远程官方诊断，但需要有效 SSH。

先断开本软件 SDK 会话再诊断，避免争用设备。此入口不执行动作、标定、升级或修复命令。
环境和网络检查不是电机压力测试；官方没有该设备的检查项时，`skip` 原样显示为“跳过”，
不计为通过。CLI 退出码和原始输出保留；解析失败时展示原文，不猜测设备正常。
接口和格式用合成数据测试；未连接真实手做本版本硬件验收。

来源：[SDK 支持平台](https://docs.wuji.tech/docs/en/wuji-sdk/latest/introduction/)、
[官方诊断范围](https://docs.wuji.tech/docs/en/wuji-cli/latest/doctor/)。

## English

The UI and preview need no VM. Ubuntu can run the SDK controller **locally without
SSH**; select Local Ubuntu and provide the absolute controller and venv Python paths.
Windows/macOS connect over SSH to a Linux SDK host, which can be a physical computer.
No credentials or machine-specific configuration are shipped. Hardware still requires
the official SDK, currently documented for Ubuntu 22+.

Install the official CLI on the controller and enter its executable path. Diagnostics
offers `wuji --version` and `wuji doctor --json` with an optional serial number.
Disconnect this app's SDK session first. Reports preserve official status values,
exit code and raw output. A skipped device check is not a passed motor test. No
calibration, upgrade, motor stress test or automatic repair is performed by this entry.
