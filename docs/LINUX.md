# Linux 本机安装 / Local Linux installation

以下步骤面向 **Ubuntu 24.04 x86_64、Python 3.12**，从源码安装工作台及配套的官方 **CLI / SDK 2026.10.12**。Linux 可以直接运行本机控制端，无需 SSH 或虚拟机，也无需安装 Studio。Ubuntu 22.04 需先自行准备 Python 3.12 与对应的 `venv` 支持；不要用系统默认的 Python 3.10 代替。ARM64 不能使用下面的 x86_64 CLI 压缩包。

本轮只核对了仓库中的依赖、配置字段、下载地址及校验值；**尚未在新的 Linux 环境实际安装或验收**。安装成功、软件版本检查、设备连接及真实标定是不同的结果。

如果只想先在另一台 Ubuntu 装工具、找手套并按步骤完成标定和备份，可以看 [Ubuntu CLI / SDK 操作指南](UBUNTU_CLI_QUICKSTART_ZH.md)。本页侧重工作台源码安装与本机接入。

## 1. 克隆与 Python 环境

在 Ubuntu 终端执行。建议使用英文目录；当前控制端路径校验不接受中文、`~` 或未展开的 shell 变量。

```sh
sudo apt-get update
sudo apt-get install -y git python3.12 python3.12-venv ca-certificates curl libstdc++6

mkdir -p "$HOME/projects"
cd "$HOME/projects"
git clone --branch feature/hand-workbench-0.2.1 https://github.com/Fairank/wuji-hand-studio.git
cd wuji-hand-studio

python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install 'wuji-sdk==2026.10.12' 'eclipse-zenoh==1.9.0'
```

`requirements.txt` 安装工作台界面和 MuJoCo 预览依赖；官方 SDK 和 Zenoh 单独安装到同一环境。上述版本与本项目配套控制环境一致，并不表示联网查询到的最新版本。

## 2. 安装固定版本的官方 CLI

继续在仓库根目录执行。下载只写入项目下新建的临时目录，校验成功后才解压，并将 CLI 安装到项目的 `.venv/bin/wuji`。地址和 SHA-256 与 [配套环境清单脚本](../controller/runtime-bootstrap.sh) 一致。

```sh
(
    set -eu
    download_dir="$(mktemp -d "$PWD/.cli-download.XXXXXX")"
    cli_archive="$download_dir/wuji.tar.gz"
    curl --fail --location --retry 2 \
        --output "$cli_archive" \
        https://github.com/wuji-technology/wuji-cli/releases/download/v2026.10.12/wuji_2026.10.12_x86_64-unknown-linux-gnu.tar.gz
    printf '%s  %s\n' \
        '4e84764ffbdfff2e1b420bf8742b8d8d4c1f6db337dc0e7149945d9893e5775a' \
        "$cli_archive" | sha256sum --check -
    mkdir "$download_dir/unpacked"
    tar -xzf "$cli_archive" -C "$download_dir/unpacked"
    cli_source="$(find "$download_dir/unpacked" -type f -name wuji -print -quit)"
    test -n "$cli_source"
    install -m 0755 "$cli_source" "$PWD/.venv/bin/wuji"
)
```

可单独核对软件版本；这些命令不连接设备：

```sh
.venv/bin/wuji --version
.venv/bin/python -c "from importlib.metadata import version; print('wuji-sdk', version('wuji-sdk')); print('eclipse-zenoh', version('eclipse-zenoh'))"
```

**普通 Linux 不要运行 `controller/runtime-bootstrap.sh`。** 它专用于 Windows 内置 WSL 环境，会创建专用系统用户并写入 `/etc/wsl.conf`；本页的本机安装不需要这些操作。

## 3. 启动并选择本机控制端

```sh
.venv/bin/python prepare_official_viewer.py
.venv/bin/python src/desktop.py
```

Linux 桌面入口会优先尝试 Chromium、Chrome 或 Edge 的应用窗口，未找到时使用默认浏览器。它不会自动连接设备。没有桌面或只需前台服务时可用：

```sh
.venv/bin/python src/desktop.py --serve
```

`--serve` 不打开窗口，服务默认仅监听 `127.0.0.1:8781`；这不等于在无图形环境中已经验证了 MuJoCo 画面渲染。

首次打开「连接与校准 → 控制端与诊断设置」，将控制方式选为「本机 Ubuntu」。在仓库根目录运行下面的命令，将输出的完整路径分别填入界面：

```sh
printf 'agent_directory=%s/src\npython=%s/.venv/bin/python\ncli=%s/.venv/bin/wuji\n' "$PWD" "$PWD" "$PWD"
```

| 界面字段 | 配置字段 | 对应路径 |
|---|---|---|
| 控制程序目录 | `agent_directory` | `$(pwd)/src` |
| SDK Python 路径 | `python` | `$(pwd)/.venv/bin/python` |
| 官方 CLI 路径 | `cli` | `$(pwd)/.venv/bin/wuji` |

表中的 `$(pwd)` 只是路径说明；界面中必须填终端实际输出的绝对路径。保存后再由操作员选择代际、左右侧和设备并连接，不需填写 SSH 主机或密钥。已有 `connection.json` 时先核对原设置，再明确保存；上述安装命令不会生成或覆盖该文件，也不要用示例文件覆盖自己的连接配置。

## 4. 官方 Visualization 与标定

官方 CLI 提供 `viz` 可视化和 `calib hand-model` 手型标定，SDK 负责对应的设备访问和数据接口。因此可以在 Linux 安装 CLI / SDK 后使用这些官方能力，无需 Studio。**当前工作台尚未嵌入完整的官方 CLI Viewer**；现有手套骨架、映射目标预览和机械手实测画面分别有自己的含义，不能作为完整 Viewer 或标定成功的替代证明。

下面的命令会访问设备。先断开工作台对同一设备的会话，将 `SN` 替换为自己的设备序列号，再由操作员明确执行：

```sh
.venv/bin/wuji viz --sn SN --no-zero
```

`--no-zero` 表示不在启动时自动进行触觉清零。可视化有画面不代表完成了标定，也不保证反馈一直新鲜。

手型标定是另一项需要用户配合完成姿势的操作，不会随安装或软件版本检查自动开始：

```sh
.venv/bin/wuji calib hand-model --sn SN
```

需要保存个人标定时，先通过官方用户管理选择自己的命名 SDK 用户；Default 不保存个人手型模型。已有模型的覆盖应由用户明确选择。工作台内也提供「手套校准」入口，使用官方 CLI 事件展示进度与结果；只有官方最终成功结果才算完成。详见 [标定、参数与可视化边界](CALIBRATION_AND_TUNING.md) 和 [控制端说明](../controller/README.md)。

## English

Use Ubuntu 24.04 x86_64 with Python 3.12. The commands above clone `feature/hand-workbench-0.2.1`, install the project requirements plus SDK 2026.10.12 and Zenoh 1.9.0 into `.venv`, and verify the pinned CLI archive before installing it as `.venv/bin/wuji`. Ubuntu 22.04 needs a separately prepared Python 3.12 environment. Do not run the WSL-only `controller/runtime-bootstrap.sh` on a normal Linux host.

Select **Local Ubuntu** and enter the expanded absolute paths for `agent_directory`, `python` and `cli` shown above. No SSH or VM is required. Review existing connection settings before saving; the installation commands do not overwrite `connection.json`. The desktop entry prefers a Chromium/Chrome/Edge app window, then the default browser; `--serve` runs without opening a window.

Official `wuji viz --sn SN --no-zero` and `wuji calib hand-model --sn SN` are explicit device operations. The former skips automatic tactile zeroing; the latter needs the operator's calibration poses and an appropriate named SDK user for persistence. The complete CLI Viewer is not embedded in the current workbench. This guide was checked against local source only; a fresh Linux installation and hardware calibration were not tested in this round.
