# Wuji Hand Studio · 非官方展示工作台

[English](#english)

**这是为个人方便展示而制作的非官方工具，不是舞肌科技发布、维护或背书的产品。** 基础版欢迎自行下载使用。硬件名称、官方示例与标识仅用于说明兼容对象和来源。

简洁的白色/浅灰界面，侧栏、选中控件和浮动窗口采用半透明玻璃材质；没有背景图片。支持中文 / English、一代/二代 × 左手/右手四种原生MuJoCo模型预览、相机调整、拖动小窗、动作循环、数字、当前时间HH:MM及24个近似静态字母。字母通过小弹窗选择，不铺满主界面；J/Z未包含，不属于认证手语。

## 下载与启动

在 [Releases](../../releases) 下载 Windows 或 Ubuntu 包并完整解压。Mac 源码启动方法见 [Mac 指南](docs/MACOS.md)；目前没有经过验证的 Mac 安装包。预览无需安装Python或连接机械手。首次启动不自动连接、不启动电机。

| 平台 | 启动入口 | 实机连接 |
|---|---|---|
| Windows x64 | `WujiStudio/WujiStudio.exe` | 配置Linux SDK控制端 |
| Ubuntu 22.04+ x64 | `WujiStudio/WujiStudio` | 本机SDK，无需SSH或虚拟机 |
| macOS Apple Silicon / Intel | [源码启动脚本](docs/MACOS.md)，安装包待Mac验证 | 配置Linux SDK控制端 |

首次构建产物没有代码签名或Apple公证；如果系统阻止打开，先核对来源与SHA256，再按系统界面的提示处理。Ubuntu需要图形会话与系统OpenGL库。界面优先用Chrome/Edge/Chromium应用窗口；没有这些浏览器则使用默认浏览器。它是本地软件加本地网页界面，不是云端控制服务。

下载包包含独立运行环境；关闭网页后本机服务保留供再次打开。端口默认8781；旧服务占用时双击启动会在8781–8800自动选择空闲端口，也可用环境变量 `WUJI_STUDIO_PORT` 固定端口。停止网页采集不等于硬件急停。

实机接入步骤见 [Linux控制端说明](controller/README.md)。控制链路沿用明确开始、暂停、停止和官方故障处理；没有为这次美化调整电机参数。软件设定的1000Hz是上位机发送目标节拍，实际频率单独报告，不是网页帧率或保证设备逐帧执行。

## 官方诊断

“官方诊断”页面提供CLI版本检查、官方诊断、分项结果和原始报告导出。先在控制端安装官方CLI；配置可留待实际使用的电脑完成。检查不自动驱动电机，跳过不计通过。详见[连接与诊断说明](controller/README.md)。

## 两个版本

| 内容 | 公开基础版 | 私有自用研究版 |
|---|---|---|
| 基础展示、官方示例适配、参数、反馈与3D查看 | 有 | 有 |
| 自行训练的识别权重 | **无** | 单独私有仓库与私有下载包 |
| 实机自动触碰反抓 | 未开放 | 仍需实机标定与验收，不因附带模型就自动开放 |

公开版不包含训练源码、训练记录、检查点、设备采集记录或SSH配置。私有仓库不通过公开发布流程构建或上传。各版本的本机数据目录相互独立。

新增设备支持：四种原生模型均可切换预览。二代左右手分别使用对应官方录制；新右手运行路径尚未实机验收。一代实机使用独立LowPass适配，仅开放官方张开/握拳，其他动作候选仅预览。此接口不提供的电流、设备时间戳频率显示为—，不虚构为1000Hz；一代不使用二代Kp/Kd设置。

官方资源适配：二代左手对指录制、归零及余弦扫动示例；并非声称已集成所有WUJI仓库中的所有动作。手套遥操作、第一代手动作不当作直接兼容的二代动作。新增字母等动作仅经过离线检查，不冒称全部通过实机验收。触碰流程预览是编排动作，不是已经学会抓取。

## 源码运行与构建

Python 3.12：`python -m pip install -r requirements.txt`，随后 `python src/desktop.py`。离线检查：`python src/desktop.py --self-check`；渲染检查加 `--render-check`。单元检查：`python -m unittest discover -s src`。

在目标平台安装 `requirements-build.txt` 后运行 `python build.py`。各平台在原生系统构建并自检。仓库提供`ci/build.yml.example`自动构建模板；当前发布账户未授权workflow权限，因此本次采用Windows与Ubuntu原生构建；Mac构建入口已准备，等待Mac验证。需要启用Actions时由仓库维护者将模板放入`.github/workflows/build.yml`。不是在Windows上把同一个可执行文件改名给三个平台。

用户配置位于Windows `%LOCALAPPDATA%/WujiStudio`、macOS `~/Library/Application Support/WujiStudio`、Linux `$XDG_DATA_HOME/WujiStudio`（缺省 `~/.local/share/WujiStudio`）。`WUJI_STUDIO_DATA`可指定独立数据目录。升级不覆盖现有参数。

本项目基础路径、官方诊断格式化、常规测试由Claude Fable 5.1（max）辅助；Mac启动脚本按用户指定由Claude Opus 5（max）辅助。均由主维护者审核调整，控制逻辑与发布内容由主维护者核验。详见 [第三方来源与许可证](THIRD_PARTY_NOTICES.md)。

## English

**Unofficial personal demonstration tool. Not published, maintained, sponsored, or endorsed by Wuji Technology.** Download the Basic edition for personal demonstrations. Vendor names and logos identify compatibility and sources; they do not imply endorsement.

Features: Chinese/English UI, a neutral minimal interface with frosted translucent controls, native Hand 1/Hand 2 left/right MuJoCo previews, camera controls, a movable/resizable viewer, loop playback, numbers, startup-time HH:MM and 24 approximate static letters (not certified sign language). No automatic connection or motor start.

Download the Windows x64 or Ubuntu x64 package under Releases. For macOS, use the [source launcher](docs/MACOS.md); no native macOS package has been validated or published yet. Extract the whole archive. The Python runtime is bundled. Packages are unsigned; macOS builds are not notarized. The UI opens as a Chrome/Edge/Chromium app window when available, otherwise in your default browser. Linux needs a graphical session and OpenGL libraries.

**Hardware requires a Linux SDK controller: local on Ubuntu, remote over SSH on Windows/macOS**; see [controller setup](controller/README.md). This does not promise native Windows/macOS SDK support. UI/render tests do not constitute hardware acceptance. Existing gains and motion/fault handling are not changed by the visual redesign.

The public Basic edition contains no trained weights, training code, device recordings or credentials. The Research edition is distributed through a separate private repository with the owner's experimental left-hand recognition model. Including a model does not validate automatic real-hand grasping; that function remains unavailable pending calibration and acceptance.

Sources: [official SDK examples](https://github.com/wuji-technology/wuji-sdk), [official hand geometry](https://github.com/wuji-technology/wuji-description). Only three Hand 2 examples are adapted here; glove teleoperation and first-generation examples are not presented as directly compatible motions. Local modifications and third-party licenses are described in THIRD_PARTY_NOTICES.md.
