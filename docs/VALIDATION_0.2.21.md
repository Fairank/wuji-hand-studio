# 0.2.21 验证记录

本版提供不接设备即可打开的本地原版空 Viewer，并收拢界面入口。Windows 0.2.21 已完成本地构建、安装和软件验收；源码与 GitHub 二进制 Release 的发布状态单独记录。

## 本地原版空 Viewer

“连接与校准 → 官方可视化 → 直接打开”使用官方固定 Rerun 0.36.0 CDN 资源。JS、WASM 保持原版，仅从 HTML 壳移除 Google Fonts 外网标签，随包保留官方 MIT / Apache 许可。CDN WASM 并非 CLI 内嵌版本的逐字节副本，不声称从 CLI 完整提取。

`src/official_viewer_manifest.json` 固定来源、大小和 SHA-256；`prepare_official_viewer.py` 准备资源，构建前校验。`src/official_viewer.py` 使用独立回环地址和临时端口，只提供静态资源。HTML 响应清除此独立地址的存储，避免恢复旧记录、数据源或凭据，不影响工作台配置；该隔离不依赖 `persist=0`。欢迎示例关闭，CSP 只允许本地资源连接。

真实 Edge 已启动原版 WASM 与空 canvas，重载后存储哨兵删除，外部请求为 0，页面异常为 0。原版遥测被 CSP 拦截，其控制台提示保留。证据为 `outputs/workbench-ui-0221/original-empty-viewer-result.json` 及配套截图。该 Edge 结果不代替安装版验收；最终安装版的独立确认见下文。实际设备流尚未验收。

## 软件回归与界面范围

最终完整回归执行 **752 项，750 通过、2 项环境跳过**。其中 29 项静态资源与回环服务合成回归通过，覆盖来源校验、资源篡改、存储响应头、CSP 和 API 隔离。缓存校验的原始失败及沙箱回环访问失败均保留；修复后的针对结果另存，不覆盖旧证据。记录见 `tests-parent-port-final.log`、`official-viewer-tests/dynamic-parent-port/result.json`。

侧栏原 6 个次级入口收为“反馈、参数、诊断”3 项，记录与触觉实验入口收至设置。Doctor 的版本按钮转到统一 CLI / SDK 卡片。导航动画经主助手审核后采用；23 组连续导航检查及 1320/960/700 宽度、中英文和明暗主题下的普通、错误与专注布局均通过。快速改选从 compositor 当前坐标接续，回弹约 0.98px，减少动态与键盘焦点检查通过。模型识别也未据此宣称完成。

## 最终安装版与初测修复

用户本轮只验收软件，不扫描、连接真实手或手套，不执行触觉清零、标定或运动。空 Viewer 没有模拟或实测设备数据，不代表骨架、触觉、反馈新鲜度或实机功能已验收。

安装版 WebView2 已收到原版 Viewer 的正式 `READY` 回执，页面中只有一个 Viewer，未发生扫描、连接或设备会话。161 个 Web 资产、7 个 Viewer 资源、89 个冻结代码模块及 98 份控制端源码与最终源码一致；4 份配置文件摘要和语言、外观偏好保留。原版画面以应用自身截图保存，未截取桌面。

首版安装测试发现 Viewer 父来源校验仍取默认 8781，而实际窗口监听 8783，原版 iframe 因此被拦截。修复改为从实际服务器监听端口生成精确父来源，未放宽为通配；6 个新增用例和真实本机父子页面复验通过，随后重新打包、安装并收到启动回执。最初失败截图、响应头与旧产物摘要保留。一次模拟父页面还触发浏览器本地网络检查，改为真实本机父测试服务器复验，没有关闭浏览器检查。

普通窗口查看工具与模式选择同行，移除重复的可见副标题；专注模式铺满可用宽度。原选中背景残影、无变化文字导致的动画重置，以及专注布局收缩到 300px 的问题均在模拟渲染中发现并修正；原始失败证据保留。

| 本地产物 | SHA-256 |
| --- | --- |
| 最终 EXE | `a08034e9121e5b157973e1e8bb509419ae7169cd961225d19f797c64ccbb2670` |
| Windows 安装包 | `1ffcedc9cf013fbc8c73107ccdce34afa011d44122cec8f949a1f77b23bb04ad` |

完整本机记录在 `outputs/workbench-ui-0221/`：`native-final-0221.json`、`frozen-source-verification.json`、`original-embedded-viewer-result.json`、`navigation-qa-result.json`、`official-pane-latest-result.json` 与 `build-result.json`。本地安装包已生成；本轮没有发布新的 GitHub 二进制 Release。源码提交与远端确认另见本机 `publication.json`。
