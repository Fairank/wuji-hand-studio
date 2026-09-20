# 外部背景折射（实验）

Windows 菜单增加独立的“外部背景折射”开关，每次启动默认关闭。开启后使用 Windows Graphics Capture 读取工作台所在显示器，排除工作台自身，然后立即裁成四周窄边纹理，在本机 WebGL 中按曲面位移重新采样。这不同于系统 Acrylic 的背景模糊；也不是苹果的系统 Liquid Glass API。

操作系统捕获接口会提供显示器帧，应用仅保留边缘裁切结果；不写入文件、不上传、不开放 HTTP 背景图片接口。Windows 会显示捕获指示。停用、最小化、页面不再请求画面或窗口关闭时停止会话；“减少透明效果”会停用折射。窗口跨两个显示器时回退到系统背景，完整移动到单个显示器后可恢复。捕获权限或显卡不支持时显示停止状态，保留普通界面。

代码检视截图会隐藏折射纹理，以免将用户其他应用的内容带入开发记录。已通过生成参考图案的着色器检查、四周裁切测试，以及仅捕获本工作台不透明窗口的 WGC 接口检查。**没有采集用户桌面做完整外部背景效果验收**；此功能保持实验标记，尤其是多屏移动、HDR 和受保护内容。

实现依赖：[windows-capture](https://github.com/NiiightmareXD/windows-capture)、[Windows 捕获排除接口](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)。
