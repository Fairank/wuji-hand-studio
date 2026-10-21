# 通过代码检视和升级 / Code-only desktop maintenance

工作台使用独立原生窗口，Windows 内嵌 WebView2。检视接口仅处理工作台自身，不发送鼠标键盘输入，不截取桌面或其他软件。无需桌面控制工具。0.2.19 继续使用同一产品与安装身份。

本机端口记录在用户数据目录的 `native-window.json`。服务只监听 `127.0.0.1`；预览服务不会伪装成原生窗口。`GET /api/desktop` 返回版本、版本类型、窗口就绪状态和支持的操作。

0.2.20 新增 `page official-viz`，仅导航到「连接与校准 → 官方可视化」，不会自动查找或连接设备。`GET /api/official-viz?theme=light|dark` 返回进程和会话状态；`GET /api/official-cli` 返回版本及更新状态。明确的查找、打开、停止和更新动作仍经过现有 Origin / CSRF 校验；检视接口没有任意命令或脚本执行能力。原版 iframe 的内部页面保持官方实现。

0.2.19 取消 Studio 页面、安装及打开入口，`page official-studio` 不再是受支持页面。当前官方组件入口是「设置 → 连接与设备 → 官方 CLI / SDK」；维护接口可使用 `page settings` 打开设置，但没有任意点击或脚本执行能力。历史 Studio 后端只保留不含凭据的 `GET /api/official-studio` 快照，以及经过 CSRF/Origin 校验的 `official_studio_probe`、`official_studio_close` 迁移兼容。其他 `official_studio_*` 动作被拒绝。查询或旧软件退出均不代表电机停用已确认，详见 [历史与迁移说明](OFFICIAL_STUDIO.md)。

```powershell
python scripts/workbenchctl.py --port 8792 info
python scripts/workbenchctl.py --port 8792 inspect
python scripts/workbenchctl.py --port 8792 capture output/app.png
python scripts/workbenchctl.py --port 8792 page glove
python scripts/workbenchctl.py --port 8792 resize 1200 850
python scripts/workbenchctl.py --port 8792 appearance language en
python scripts/workbenchctl.py --port 8792 menu open
```

`inspect` 返回当前页、控件标签/可用状态/位置、原生磨砂界面样式、页面溢出和界面错误；不返回输入框值或 SSH 密钥内容。`capture` 使用 WebView2 自身的画面导出接口，画面范围仅为该软件内容。`appearance` 支持 `language`、`reduceTransparency`、`reduceMotion`。`viewer` 打开工作台所属的独立模型窗口。没有任意脚本、任意点击或外部程序控制接口。0.2.7 的 `page` 另支持 `settings` 和 `devices`，移除实验 `refraction_check`。

## 官方 CLI / SDK 软件状态

`GET /api/control-tools` 返回 `target`（随包配套 CLI/SDK 版本）、`runtime`（内置环境安装记录）和 `check`（实际软件检查结果）。只读取快照不会自动完成一次新检查。配套版本是 CLI / SDK 2026.10.12、控制环境 1.1.0；旧安装标记或目标清单不能填充为实际版本。

受本机 `/api/action` 校验的 `control_tools_check` 发起独立软件检查：在已有控制环境中导入 `wuji_sdk` 并读取包版本，执行官方 CLI `--version`，记录实际版本、错误和检查时间。它不扫描、连接、订阅设备或发送电机数据，不执行标定。软件可导入或版本相符不等于具体设备通过验收。

卡片使用原有 `runtime_install`、`runtime_select` 安装/更新和选用内置组件；配置前仍要求设备与相关工作空闲。Windows 系统组件操作可能要求管理员确认和重启。界面读取失败显示待核对并提供重读，保留的旧结果不会被标成当前在线结果。安装与检查是不同操作。

代码维护操作通过 `POST /api/desktop`，JSON 中设置 `operation`；官方组件的软件检查另按上文使用 `/api/action`。请求必须带有从本机 `/api/state` 读取的 `X-Console-Token`，Origin 必须匹配本机地址（无 Origin 的本机客户端也可使用）。工具自动处理校验。接口不启用跨域访问。

0.1.5 增加两个限定的界面检查操作：`{"operation":"picker","kind":"letters"}`（也支持 `numbers`、`closed`）只打开/关闭选择窗；`{"operation":"preview","action":"dance_piano","speed":1.5}` 只播放模型预览，需要设备断开且工作台空闲。预览复用现有仿真命令，不发送电机命令。`inspect` 同时返回页面滚动位置。

## 本地安装包升级

先从可信的对应版本 Releases 下载安装包及 SHA-256 文件。只有一个软件和安装包：`HandWorkbench-版本-windows-x64-setup.exe`。不接受低于当前版本的安装包。SHA-256 只检查文件完整性，不能替代可信发布来源或代码签名。

```powershell
# 仅暂存和校验；把示例值替换为实际下载文件及其 64 位 SHA-256。
python scripts/workbenchctl.py --port 8792 upgrade PATH_TO_INSTALLER --sha256 RELEASE_SHA256
# 校验、正常关闭空闲工作台，然后执行本机安装器。
python scripts/workbenchctl.py --port 8792 upgrade PATH_TO_INSTALLER --sha256 RELEASE_SHA256 --apply
```

文件暂存在本机数据目录 `updates`。设备已连接、动作/采集/手套会话或参数同步/诊断未结束时，不执行代码升级或 `close-idle`。升级不自动停止正在进行的工作。正常退出后再次校验文件，安装器也会核对本版本进程锁。配置和采集记录存放在安装目录以外；升级保留它们。安装后由用户重新打开，不自动连接硬件。该接口不自动联网下载，不携带 GitHub 凭据。

停用未知也不满足空闲条件。0.2.19 将未知记录按设备和会话保存在用户数据中；一次新的只读连接、不同设备的确认、网页刷新或进程结束不能把旧记录清为已停用。正常退出和代码升级需要核对相应确认，不能通过删除记录或换工作区绕过。

0.2.19 修复中断复制后缓存安装器无法重试的问题：旧缓存缺失或哈希不符时，先写入临时文件并校验完整内容，再原子替换目标缓存。复制失败不发布半个安装器；关闭失败、退出等待超时或文件再次变化均不启动安装。安装器非零退出按失败报告，不等于升级完成。

构建器同样核对缓存可执行文件。`--self-check` 返回实际产品身份和版本，构建器通过独立 `--self-check-output` 文件读取并与当前版本配置比较；缺失、无效或不一致时拒绝打包。这是构建身份检查，不是安装版运行或设备验收。Windows CI 模板覆盖模拟失败和选定隔离回归，当前没有启用远端工作流；详见 [0.2.19 验证记录](VALIDATION_0.2.19.md)。

Windows installer packages are unsigned. Use trusted release assets and published checksums. The maintenance CLI inspects only this application's WebView; it never controls other applications. Updates require an idle, disconnected workbench and retain per-user data. No automatic motor start or hardware acceptance is implied by desktop UI tests.


## 私有模型为可选数据

同一软件的菜单提供“导入私有模型包”。也可使用 `python scripts/workbenchctl.py --port 8792 import-model PATH_TO_MODEL_ZIP`。模型只保存到本机数据目录 `models`；公开安装包没有私有权重。模型包仅接受固定格式的 JSON 与 NPZ 数据，不运行 Python 插件。导入会校验文件哈希、数组形状和参考输出。现有二代左手模型仅支持离线仿真反馈，不会因导入而启用真实手抓取。

## 0.1.8 additions

`connection_panel` accepts `state: "open"` or `"closed"` and only changes the app’s device popover. `reload_ui` reloads assets only while disconnected and idle. Neither sends motor commands. Inspection now includes toolbar state, button contrast and bounds.
