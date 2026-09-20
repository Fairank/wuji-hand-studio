# 内置控制环境 / Built-in controller

Windows 0.1.6 附带约 128 MB（十进制）的精简控制环境。它使用 WSL 2，不附带 Linux 桌面或 SSH 服务，不需要另开 VMware 或填写账户密码。它是本软件的控制组件，不是另一个工作台。

在“连接与校准”选择“安装内置控制环境”。软件校验镜像、导入唯一的 `HandWorkbenchControl` 发行版、验证官方 SDK，再选择该控制端。之后连接设备会自动启动所需进程。查看安装状态不会启动 Linux，也不会自动连接手；动作仍由用户启动。安装与切换控制端沿用本机保存的参数。

首次使用的电脑若未安装 WSL 2，可展开“Windows 还没有 WSL 2？”并请求安装系统组件。Windows 可能弹出管理员确认，并要求用户重启。软件不自动重启，也不修改其他发行版、默认发行版或全局 WSL 配置。离线使用需要提前装好微软 WSL 2 系统组件；本包只附带本应用的 Linux 用户空间。

本轮验证：Windows 11 的 WSL 2.7.14、Ubuntu Base 24.04.5、Python 3.12、官方 Wuji SDK/CLI 2026.8.31；无特权 `workbench` 用户成功导入 SDK 和控制程序。实际 1000 Hz 收发和机械手动作还需接线后测试，不能由“环境就绪”推断已验收。

二代手通过电脑的设备网卡联网，Windows 必须能到达设备网段；WSL 默认网络下，自动发现不保证可用，必要时填写设备完整地址（包括端口，例如官方默认 `192.168.1.110:7447`）。一代 USB 设备还需要转接进 WSL，本版不承诺一代 USB 自动直通；一代可继续选择实体 Linux 控制端。

发行版存储位于 `%LOCALAPPDATA%/WujiStudio/runtime/HandWorkbenchControl`，大小会随记录增加。不会预先占用显示的虚拟磁盘最大容量。卸载工作台不会自动删除个人控制环境、参数或记录。已有同名但目录不属于工作台的发行版会被拒绝接管。安装失败保留现场，只有匹配本次镜像的安装才能接续验证。

组件来源：[Microsoft WSL](https://learn.microsoft.com/windows/wsl/install)、[Ubuntu Base](https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/)、[官方 SDK](https://github.com/wuji-technology/wuji-sdk/releases/tag/v2026.8.31)、[官方 CLI](https://github.com/wuji-technology/wuji-cli/releases/tag/v2026.8.31)。包保留 Linux 软件包各自的版权与许可证；镜像 SHA-256 位于 `src/runtime_manifest.json`。构建基础步骤见 `controller/runtime-bootstrap.sh`。公开镜像不含个人参数、SSH 密码、设备日志、私有模型或预设设备身份。

## English

The Windows installer includes a minimal WSL 2 control environment (~128 MB compressed), without a desktop or SSH server. Use **Connection → Set up built-in controller** once. Subsequent connections launch the official Linux SDK inside this managed environment without a VMware window or SSH credentials.

Missing Windows virtualization components may require administrator approval and a reboot. Neither is performed silently. The workbench does not change other distributions or global WSL settings. The installed environment and controller imports were verified; physical feedback timing and motions remain subject to hardware validation. Hand 2 requires a reachable device network; direct addressing may be needed under WSL NAT. Hand 1 USB passthrough is not automatically configured.
