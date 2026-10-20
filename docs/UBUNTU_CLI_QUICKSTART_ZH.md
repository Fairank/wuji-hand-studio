# 在另一台 Ubuntu 上装 CLI / SDK，再做手套标定

这份说明可以一段一段复制到那台 Ubuntu 的终端。先装工具，再找手套、选用户和标定；安装本身不会开始标定。**CLI 能做手套手型标定，也能打开官方可视化。** 这里的 `hand-model` 是人体手型标定，与机械手电机零位标定是两项不同的操作。

下面使用 **Ubuntu 24.04 x86_64**、官方 **CLI / SDK 2026.10.12**。配套版本和 CLI 校验值来自本仓库固定清单；不是查询“网上最新版”。本轮已只读核对相应 CLI 命令帮助，**没有在另一台 Ubuntu 执行新安装、连接设备或开始标定**。

## 1. 先确认电脑，再装基础工具

在 Ubuntu 终端运行：

```sh
uname -m
```

看到 `x86_64` 再继续。如果显示 `aarch64` 或其他结果，不要下载下面的 x86_64 包。Ubuntu 22.04 用户还需先自行准备 Python 3.12 和对应的 `venv` 支持；下面的安装步骤按 Ubuntu 24.04 编写。

```sh
sudo apt-get update
sudo apt-get install -y curl ca-certificates python3 python3-venv libstdc++6
python3 --version
```

Ubuntu 24.04 应显示 Python 3.12。之后的 Python 包装在自己的目录里，不用 `sudo pip`：

```sh
mkdir -p "$HOME/wuji-tools"
python3 -m venv "$HOME/wuji-tools/.venv"
"$HOME/wuji-tools/.venv/bin/python" -m pip install --upgrade pip
"$HOME/wuji-tools/.venv/bin/python" -m pip install \
    'wuji-sdk==2026.10.12' 'numpy>=2.2,<3' 'eclipse-zenoh==1.9.0'
```

## 2. 下载 CLI，核对后放进自己的命令目录

整块复制执行。下载文件保存在 `~/wuji-tools/` 下的新临时目录，校验失败就停止，不安装这份文件。

```sh
(
    set -eu
    download_dir="$(mktemp -d "$HOME/wuji-tools/download.XXXXXX")"
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
    mkdir -p "$HOME/.local/bin"
    install -m 0755 "$cli_source" "$HOME/.local/bin/wuji"
)
```

再让 `wuji` 在现在和以后新开的 Bash 终端都能直接使用。下面只在 `.bashrc` 没有相同设置时补一行，不会每次重复追加：

```sh
path_line='export PATH="$HOME/.local/bin:$PATH"'
touch "$HOME/.bashrc"
grep -Fqx "$path_line" "$HOME/.bashrc" || printf '\n%s\n' "$path_line" >> "$HOME/.bashrc"
export PATH="$HOME/.local/bin:$PATH"
hash -r

wuji --version
"$HOME/wuji-tools/.venv/bin/python" -c 'from importlib.metadata import version; print("wuji-sdk", version("wuji-sdk"))'
```

两项应对应 `2026.10.12`。CLI 版本和 Python 包版本正确，只说明软件装好了，尚未说明手套连接或标定成功。

## 3. 找到手套的序列号

先让这个终端使用刚安装的 SDK 环境。以后新开终端需要标定或检查时，也先执行这一行：

```sh
. "$HOME/wuji-tools/.venv/bin/activate"
```

先断开或退出正在占用同一手套的 Studio、工作台或其他 SDK 会话，再打开手套并接好数据连接。在 Ubuntu 终端执行：

```sh
wuji devices
```

从结果里找自己这只**手套**的序列号。下文的 `手套SN` 要替换为这个实际值，保留两边的英文双引号：

```sh
wuji ping --sn "手套SN"
```

先确认能找到并访问这只手套，再做后面的标定。这里不填写猜测的 IP，也不需要为了教程关闭防火墙。

## 4. 选一个会保存标定的用户

第一次使用、还没有 `hand_demo` 用户时运行：

```sh
wuji user create hand_demo --switch
```

如果之前已经创建过它，只切换过去，不要重复创建：

```sh
wuji user switch hand_demo
```

确认当前用户：

```sh
wuji user show
```

使用这样的命名用户；`Default` 不能运行这项手型标定。以后继续选择自己的已有用户，不必每次新建。

## 5. 明确开始手型标定

手套戴好后，将实际序列号填进命令，再执行：

```sh
wuji calib hand-model --sn "手套SN"
```

接下来跟着终端当时的姿势名称、稳定性检查和结果操作。六个姿势可这样理解：

1. 拇指尖轻轻碰食指尖。
2. 拇指尖轻轻碰中指尖。
3. 拇指尖轻轻碰无名指尖。
4. 拇指尖轻轻碰小指尖。
5. 食、中、无名、小指弯曲约 90°。
6. 手指伸平并拢。

以 CLI 提示的实际步骤为准，保持自然姿势并等它采集。某一步失败时先放松、重新戴好并查看错误提示；不要靠反复加力、硬掰手指或一直重试同一姿势来“刷过”。已有模型需要覆盖时，先看清提示再决定。采完几个姿势不等于整次成功，要等 CLI 给出最终成功结果。

成功后再看一次当前用户信息：

```sh
wuji user show
```

成功保存的标定在这台电脑、这个系统用户的官方 SDK 用户档案里。正常重启手套或电脑不会自动删除它，不必每次重启重做；之后要选对同一个 SDK 用户和左右侧。换电脑、换系统用户或换控制环境时，需要迁移用户档案，不能只把序列号抄过去。

## 6. 导出一份备份

在确认当前用户就是刚标定的用户后运行：

```sh
wuji user export "$HOME/wuji-calibration-backup.zip"
```

把导出的文件放到自己的备份位置。如果已有同名备份，先改用一个新的文件名。备份中可能含个人标定和设备信息，不要随手上传到公开仓库。

## 7. 看官方可视化

同样先让其他程序释放这只设备，将 `SN` 换成自己要查看的设备序列号：

```sh
wuji viz --sn "SN" --no-zero
```

`--no-zero` 表示启动时不自动进行触觉清零。它与手型标定是不同的操作；看到画面也不能代替标定成功结果。

如果终端给出本机浏览器地址，在**运行该命令的那台 Ubuntu** 上打开它。`127.0.0.1` 或 `localhost` 指当前这台电脑，不能直接拿到另一台 Windows 的浏览器里当作 Ubuntu 的地址。

结束时回到这一个终端，按一次 **Ctrl+C**，等它退出并回到命令提示符。CLI 进程退出不作为机械手电机已停用的证明。

## 遇到问题先看这三种

| 现象 | 先怎么做 |
|---|---|
| 下载失败、超时，或校验失败 | 先确认 Ubuntu 能访问 Ubuntu 软件源、GitHub 和 PyPI；网络恢复后重试失败的那一块。校验未通过不要解压安装，也不要改掉教程的 SHA 来跳过核对。 |
| `wuji: command not found` | 先执行 `"$HOME/.local/bin/wuji" --version`。绝对路径可用时，重新执行上面的 PATH 设置，或新开一个 Bash 终端再试；不要因此重复安装 SDK。 |
| 找不到设备、`ping` 不通 | 确认设备上电、数据线或网络连接正确，其他 Studio/工作台/SDK 会话已断开；重新运行 `wuji devices`，核对设备类型和完整 SN。网络设备需按官方要求处于可达网络；一代 USB 还需按官方要求配置 Linux USB 访问。不猜网段、不写死 IP，也不关闭防火墙。 |

**不要在普通 Ubuntu 上运行本项目的 `controller/runtime-bootstrap.sh`。** 它是 Windows 内置 WSL 环境的引导脚本，包含专用系统用户和 `/etc/wsl.conf` 设置；本教程不需要它。

## 和工作台一起用

CLI 可以先独立完成官方标定和可视化，工作台继续使用同套官方 CLI / SDK。工作台源码的克隆与启动见 [Linux 本机安装](LINUX.md)；如果已按本页安装，选择「本机 Ubuntu」后，SDK Python 与 CLI 路径可使用下面输出的值，控制程序目录仍填工作台仓库的 `src` 绝对路径：

```sh
printf 'SDK Python: %s/wuji-tools/.venv/bin/python\nCLI: %s/.local/bin/wuji\n' "$HOME" "$HOME"
```

先核对已有连接配置，再明确保存，不要用示例覆盖自己的 `connection.json`。工作台有手套校准入口、原始骨架和映射预览；完整官方 CLI Viewer 当前尚未嵌入。标定保存、预览画面和真实设备结果仍分别确认，详见 [工作台标定说明](CALIBRATION_AND_TUNING.md)。
