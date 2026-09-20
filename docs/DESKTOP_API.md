# 通过代码检视和升级 / Code-only desktop maintenance

Windows 0.1.4 使用独立原生窗口，内嵌 WebView2。检视接口仅处理工作台自身，不发送鼠标键盘输入，不截取桌面或其他软件。无需桌面控制工具。

本机端口记录在用户数据目录的 `native-window.json`。服务只监听 `127.0.0.1`；预览服务不会伪装成原生窗口。`GET /api/desktop` 返回版本、版本类型、窗口就绪状态和支持的操作。

```powershell
python scripts/workbenchctl.py --port 8792 info
python scripts/workbenchctl.py --port 8792 inspect
python scripts/workbenchctl.py --port 8792 capture output/app.png
python scripts/workbenchctl.py --port 8792 page glove
python scripts/workbenchctl.py --port 8792 resize 1200 850
python scripts/workbenchctl.py --port 8792 appearance language en
python scripts/workbenchctl.py --port 8792 menu open
```

`inspect` 返回当前页、控件标签/可用状态/位置、玻璃材质的实际样式、页面溢出和界面错误；不返回输入框值或 SSH 密钥内容。`capture` 使用 WebView2 自身的画面导出接口，画面范围仅为该软件内容。`appearance` 支持 `language`、`reduceTransparency`、`reduceMotion`。`viewer` 打开工作台所属的独立模型窗口。没有任意脚本、任意点击或外部程序控制接口。

所有操作通过 `POST /api/desktop`，JSON 中设置 `operation`。请求必须带有从本机 `/api/state` 读取的 `X-Console-Token`，Origin 必须匹配本机地址（无 Origin 的本机客户端也可使用）。工具自动处理校验。接口不启用跨域访问。

## 本地安装包升级

先从可信的对应版本 Releases 下载安装包及 SHA-256 文件。只有一个软件和安装包：`HandWorkbench-版本-windows-x64-setup.exe`。不接受低于当前版本的安装包。SHA-256 只检查文件完整性，不能替代可信发布来源或代码签名。

```powershell
# 仅暂存和校验；把示例值替换为实际下载文件及其 64 位 SHA-256。
python scripts/workbenchctl.py --port 8792 upgrade PATH_TO_INSTALLER --sha256 RELEASE_SHA256
# 校验、正常关闭空闲工作台，然后执行本机安装器。
python scripts/workbenchctl.py --port 8792 upgrade PATH_TO_INSTALLER --sha256 RELEASE_SHA256 --apply
```

文件暂存在本机数据目录 `updates`。设备已连接、动作/采集/手套会话或参数同步/诊断未结束时，不执行代码升级或 `close-idle`。升级不自动停止正在进行的工作。正常退出后再次校验文件，安装器也会核对本版本进程锁。配置和采集记录存放在安装目录以外；升级保留它们。安装后由用户重新打开，不自动连接硬件。该接口不自动联网下载，不携带 GitHub 凭据。

Windows installer packages are unsigned. Use trusted release assets and published checksums. The maintenance CLI inspects only this application's WebView; it never controls other applications. Updates require an idle, disconnected workbench and retain per-user data. No automatic motor start or hardware acceptance is implied by desktop UI tests.


## 私有模型为可选数据

同一软件的菜单提供“导入私有模型包”。也可使用 `python scripts/workbenchctl.py --port 8792 import-model PATH_TO_MODEL_ZIP`。模型只保存到本机数据目录 `models`；公开安装包没有私有权重。模型包仅接受固定格式的 JSON 与 NPZ 数据，不运行 Python 插件。导入会校验文件哈希、数组形状和参考输出。现有二代左手模型仅支持离线仿真反馈，不会因导入而启用真实手抓取。
