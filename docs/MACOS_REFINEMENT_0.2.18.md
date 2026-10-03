# macOS 0.2.18 精修验收

2026-10-04 · Apple Silicon · macOS 26.6.2。基于 `port/macos-0.2.17`，Windows 安装包未修改。

## 改动和验证边界

语言改为支持方向键的 `中文 / EN` 双段按钮；共享 12 px 控件、22 px 阅读面/弹窗圆角，统一设置、节目单、连接弹层、浮动和独立查看器。90/100/110% 字体偏好只改变排版，不扫描动态 DOM、不改变控制频率。

玻璃使用公开 `NSVisualEffectView.underWindowBackground + behindWindow` 底层及 macOS 26 `NSGlassEffectView.Clear` 表层，去掉全窗重叠的深色填充；较旧系统回退 vibrancy，减少透明时恢复实色。底层用途依据 [Apple 材质文档](https://developer.apple.com/documentation/appkit/nsvisualeffectview/material-swift.enum/underwindowbackground) 和 [窗后模糊文档](https://developer.apple.com/documentation/appkit/nsvisualeffectview/blendingmode-swift.enum/behindwindow)。没有产品内的桌面截图、私有合成器接口、壁纸贴图或宣称自制外部折射。

**玻璃视觉尚未完整验收**：原生接口确认已附着、非不透明窗口、Clear 表层、窗后磨砂；隔离窗口截图依然偏白/灰，未证实对真实窗外背景达到用户要求的强度。仓库 `capture` 明确只截 WKWebView 内容，不能替代 WindowServer 合成视觉验收；也不能据此断言其他截图工具一定有同样限制。保留 `visual_acceptance_pending`，需在实际桌面可见状态进一步确认，不把 API 成功当成外观成功。

修复本轮发现的 Mac 代码预览接口：普通 WebKit evaluator 不能序列化 Promise，导致预览已开始却报失败。固定预览表达式改由公开异步 API 等待结果；不开放任意 JS，不启用 unsafe-eval。原生调用验证返回 `ok=true, hardware_motion=false`。

## 数字与新动作

数字手势沿用中国大陆常用约定；0 为握拳，7 为三指聚拢，9 为食指弯钩。针对官方 Hand 2 几何调整 0/9 的拇指收拢与各指不同弯曲程度，张开手指保留轻微自然屈曲。是项目创作、固定手腕的机械手近似，不是官方真人动作或用户触碰实测。

静态模型 FK 中：数字 0 拇指—食指尖从约 35–36 mm 缩到约 11–13 mm；数字 9 拇指—中指尖从约 36–37 mm 缩到约 7–8 mm。这只说明模型几何变化，不证明触碰、辨识准确率或实机安全。

新增三个项目自编范围探索：逐指屈伸、逐指侧摆、拇指范围。均为 **仅画面预览**，轨迹有限、在官方模型限位内保留裕量并返回张开姿态。未验证无碰撞，不能称为实体极限角度测试。

全部实机入口使用 `CATALOG.hardware` metadata 拒绝：控制器和硬件节目单在创建 lease/active 状态及下发前拒绝，设备试运行二次拒绝，已核验 profile 与 JSON/CSV 实机轨迹导出拒绝。多手/group/fleet 子工作区复用同一边界；预览节目单仍可用。没有真实连接、电机使能/发送、固件更新、SDK 标定、原始 EMF 或用户安全限值修改。

## 运行验收

- Python 全套：512 项，510 通过、2 项跳过。跳过不算实机验收。
- Node UI 检查：5 项；更改的 JavaScript 语法检查、Git whitespace 检查通过。
- 原生可执行程序 self-check：四套模型可加载、20 关节、61 个动作、`hardware_connected=false`；离线渲染通过。
- 真实 Cocoa / WKWebView 窗口：中英文胶囊及方向键切换、深浅主题、减少透明切换与恢复、960×680 和 1320×838、设置三分类、菜单/节目单与独立查看器检查。
- 浏览器操作验证三种大幅度预览播放，真实手模式启动禁用且不显示实机轨迹下载；字体 90/100/110% 状态与尺寸变化可见。接口检查无水平溢出、页面错误列表为空。
- 包验证检查 Apple Silicon 架构、deep/strict 签名、随包 Linux 镜像摘要和 ZIP SHA-256。Ad-hoc 签名，未公证；未启动 Linux 或复测真实手套/手。

设计与实际窗口比较记录见 [设计验收账本](design/mac-polish-0.2.18.md)。包测试报告与人工 UI/材质验收分开，不能用软件单测替代硬件测试。
