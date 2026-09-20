# 展示动作与速度 / Motions and speed

## 0.1.5

动作选择独占一行，文字/数字入口放在下一行。幅度、循环为两列，速度独占一行。Windows 11 22621+ 通过 DWM Desktop Acrylic 合成窗口后面的桌面与其他窗口，WebView2 的透明边缘与导航区域露出系统材质；内容区域保持白色。旧 Windows、减少透明度或高对比模式使用不透明回退。系统可能因性能、电源或自身透明度设置使材质不透明。

这实现的是**系统背景模糊/透色，不是苹果的边缘折射变形**。真正的外部背景像素位移尚未实现：WebView 的 CSS 不能采样其他应用，Win2D 的 DisplacementMapEffect 标注为 NoComposition，不能直接接入系统背景合成。不使用桌面截屏贴图伪装该效果，也不捕获其他应用供模型查看。代码接口 `material` 字段给出 DWM 属性读回、WebView 透明通道及 `refraction: false`；接口接受材质设置不等于已对外部画面完成截图验收。

- [Windows DWM backdrop types](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type)
- [Win2D DisplacementMapEffect limitations](https://microsoft.github.io/Win2D/WinUI3/html/T_Microsoft_Graphics_Canvas_Effects_DisplacementMapEffect.htm)

页面标题、菜单、弹窗和分段控件使用一致的 140–260 ms 短过渡与缓出曲线；只动画界面，不延迟控制命令。减少动态效果与系统偏好可关闭过渡。该界面参考苹果的简洁层次与反馈方式，不使用苹果原生系统控件。

手指舞现有 9 套：水母舒展、逐指波浪、指节涟漪、逆向卷浪、空中钢琴、交替律动、折扇开合、花苞绽放、指节阶梯。支持选择、循环预览与 1000 Hz 目标关节 CSV 导出。真人教程提供波浪、正反逐指开合、指节分离等节奏参考；轨迹由项目重新编排，未下载或提取视频运动数据。钢琴、交替、折扇、花苞和阶梯是这些基本元素的项目组合命名，不代表教程逐一演示了同名动作。

- [El Tiro · Finger Tutting](https://www.brambilabong.com/blogs/popping/learn-3-finger-tutting-dance-moves-tutorial)
- [Jamal Cumberbatch / Wilson Hong · Waving in Tutting](https://howcast.com/videos/493866-how-to-do-waving-tutting/)

所有新舞蹈均为固定底座改编，没有手腕、手臂或双手配合。轨迹限位、连续性与定时已做离线检查；尚无实机接触/碰撞验收。二代动作使用当前姿态作为进入/返回起点；一代新增舞蹈保持仅预览。

## 字母来源

字母姿态由本项目编写，参考 [ASL University 的指拼说明](https://www.lifeprint.com/asl101/pages-layout/fingerspelling.htm)，不是 WUJI 官方动作、全球通用手语或经手语使用者验证的标准表达。不同手语的指拼体系不同。ASL 的 J、Z 带运动，因此旧名称“24 个静态字母”指其余 24 个字母；它不意味着只有 24 个字母，也不意味着其余姿态都已正确复现。掌心朝向、手腕位置和指形共同影响表达。固定底座无法完整复现这些差别。

输入框支持 A–Z、0–9 和空格，按字符顺序播放，空格停顿、重复字母分隔；J/Z 是指尖运动近似。请将其用于造型展示，而不要标为手语翻译或无障碍沟通工具。

## 倍速

二代实机和模型预览可选择 0.25×、0.5×、1×、1.25×、1.5×、2×。默认仍为原值，用户选择只作用于下一次播放。倍速缩放全部轨迹时间（包含过渡、停留与回位）、速度上限和 Hermite 速度导数，不改变目标角度、Kp、Kd、电流设置或指令发送频率。2× 轨迹用时约为 1× 的一半，不保证机械手真实跟随速度翻倍。

1× 轨迹峰值由 PATH_SPEED_RAD_S 与 COMMAND_SPEED_RAD_S 中较小者及最短过渡/停留共同决定；所选倍速再作用于该轨迹。界面在连接后显示控制端实际加载的峰值设定。官方控制指南规定 PUB 最高 1000 Hz，未在该页给出最高关节运动速度；两种频率/速度不可混淆。[官方控制指南](https://docs.wuji.tech/docs/en/wuji-hand/latest/control-guide/)

新增舞蹈或超过 1× 的二代实机播放需要动作库版本 3 的控制端。旧控制端会明确拒绝，不会静默假装播放。安装目录 `controller/source` 包含对应源文件，可部署到独立 Linux 控制目录，再在连接设置中选择该目录。保留自己的 `motion_parameters.py`，不要用源码默认值覆盖调试参数。一代实机仍为原有速度档。

## Implementation notes

One app only: Hand Workbench. The public package contains no private model weights. Claude `claude-fable-5-1 --effort max` supplied bounded CSS suggestions; the main assistant reviewed DOM compatibility, accessibility and integration. Choreography and command retiming were reviewed by the main assistant. No hardware motion is implied by software tests or preview captures.
