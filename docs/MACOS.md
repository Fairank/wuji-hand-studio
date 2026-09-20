# macOS 源码版 / macOS source edition

Mac 适配及启动入口已准备，**尚未在真实 Mac 上验证启动、渲染或生成安装包**。
Windows 和 Ubuntu 的通过结果不能代表 macOS 通过。当前没有可下载的 macOS `.app`。

## 克隆后运行

准备 Python 3.10–3.13（建议原生架构 Python 3.12）、Git，然后：

```sh
git clone https://github.com/Fairank/wuji-hand-studio.git
cd wuji-hand-studio
bash scripts/run-macos.command
```

首次会在项目的 `.venv-macos` 安装依赖；后续检查依赖指纹，无变化直接启动。
脚本也可在 Finder 中双击。路径支持空格、中文；不会安装 Homebrew、修改系统 Python、
设置 SSH 或自动连接机械手。界面优先打开 Chrome/Edge 应用窗口，否则使用默认浏览器。
**界面和 MuJoCo 预览不用虚拟机。控制真实手仍需已配置的 Linux SDK 控制端。**

私有 Research 仓库使用同一个启动脚本；克隆私有仓库时需要自己的 GitHub 访问权限。
Basic 不附带我们训练的模型；Research 的模型目前仅用于二代左手仿真离线识别。

## 自检和打包

```sh
bash scripts/run-macos.command --self-check --render-check
.venv-macos/bin/python -m unittest discover -s src
.venv-macos/bin/python -m pip install -r requirements-build.txt
.venv-macos/bin/python build.py
```

打包必须在 Mac 执行；输出架构跟随所用 Python。Apple Silicon 应用原生 arm64 Python，
Intel 用 x64 Python。本项目使用 MuJoCo 离屏 CGL 渲染，没有调用交互式 `mujoco.viewer`，
因此启动脚本不替换成 `mjpython`。实际 CGL 兼容性仍待 Mac 检查。

本地构建没有 Developer ID 签名或 Apple 公证。没有实际 `.app` 时不能宣称通过
Gatekeeper 或可供所有 Mac 直接安装。普通源码自检不要求 Apple 开发者证书。

## 配置与排错

- 运行数据：`~/Library/Application Support/WujiStudio`；研究版为 `WujiStudioResearch`。
- 日志：上述目录中的 `console.log`。启动不会覆盖已调好的参数。
- 本机端口默认 8781，普通启动遇到占用会选择空闲端口；也可固定端口运行 `WUJI_STUDIO_PORT=8782 bash scripts/run-macos.command`。
- Python 不在常见位置：首次创建环境时指定 `WUJI_PYTHON=/absolute/path/python3.12`。
- 无效或外部链接的 `.venv-macos` 会被拒绝并保留。自行重命名后重试；脚本不会递归删除环境。
- 只准备环境而不启动：`WUJI_DRY_RUN=1 bash scripts/run-macos.command`（首次仍会下载依赖）。
- 已安装环境的离线启动：`WUJI_SKIP_INSTALL=1 bash scripts/run-macos.command`。
- 强制重新检查依赖：`WUJI_FORCE_INSTALL=1 bash scripts/run-macos.command`。
- `WUJI_ALLOW_NON_DARWIN=1` 仅用于在 Linux 验证脚本逻辑，不代表 Mac 验收。

脚本由本地 Claude **claude-opus-5 / effort max** 协助编写，维护者审核后调整；
移除了自动递归删除，修正了解释器指定、环境路径检查和不适用于本应用的 viewer 建议。

## English

The macOS source launcher is ready for testing. **No native Mac startup, rendering,
`.app`, signing or notarization has been validated yet.** Use Python 3.10–3.13
(prefer native-architecture 3.12), clone this repository and run
`bash scripts/run-macos.command`. The commands above also cover offline checks and
native packaging on a Mac.

The launcher creates only a project-local `.venv-macos`, caches the requirements
hash and interpreter identity, and forwards application arguments. It never
installs a system Python, changes SSH access, connects a hand or starts motors.
Invalid or symlinked environments are preserved and rejected. Existing venvs are
reused; `WUJI_PYTHON` chooses the interpreter when creating a new environment.

The UI and preview need no virtual machine. Hardware still requires a configured
Linux controller; see [controller setup](../controller/README.md). Preview uses
MuJoCo's offscreen CGL backend, not the interactive viewer. Keep the actual Mac
render check separate from the Linux shell-regression results.
