# 文字动作与手指舞 / Text and finger dance

动作展示 → **输入文字…** → 输入 `wuji tech` → 使用所选内容 → 选择画面预览或真实手 → 播放。
接受 A–Z、0–9 和空格，大小写均可，规范化后最多48个字符。不会悄悄删除不支持的字符。单词间回到张开姿态并停顿；相邻重复字母重新起势。编辑文字不会自动驱动设备，下一次播放使用新文字。

字母是固定底座的近似造型，不是经过语言专家验收的手语。J/Z 使用小指/食指运动改编；G/H/P/Q 等原本需要改变掌面朝向的字母也无法完整复现。可用于展示，不能当作标准 ASL 教学或翻译器。

## 三套编排

| 动作 | 标称一轮 | 动作结构 |
| --- | --- | --- |
| 水母舒展 / Jellyfish | 16秒 | 五指屈伸与张合叠加，指节略有相位差 |
| 逐指波浪 / Finger wave | 16秒 | 波峰沿五指依次传递 |
| 指节涟漪 / Knuckle ripple | 12秒 | 同一手指的近端、远端关节错时屈伸 |

这些是参考真人动作结构后重新编排的机器人轨迹，**不是视频动捕、官方录制或训练出来的策略**。没有编造手腕、手臂或底座的位移。进入/退出使用平滑包络，舞蹈内部采用带速度的100Hz三次Hermite曲线节点，执行器按用户配置的发送频率（最高1000Hz）求值，不在每个节点停留。

二代左右手使用各自原生模型。实际执行从反馈姿态进入，以所选幅度缩放、按已配置的路径/指令速度统一调整时间，最后返回起始姿态。预览和下载的轨迹采用标称时间；实机用时可能更长。导出1000Hz数据不是设备已达到1000Hz的证明。

一代新动作仅是原生几何模型下的近似预览，尚未完成手型重定向；抽样检查发现相邻手指的模型干涉，不开放这些新动作的实机执行。现有一代张开/握拳路径不变。

## 数据

选择文字或舞蹈后，点击“下载关节轨迹 · 1000 Hz CSV”。每行包括时间（秒）、型号、数据性质、当前阶段、字符索引以及20关节目标角（弧度）。`nominal_not_measured` 表示编排目标，**不是实测反馈、电流或接触力**。JSON接口 `/api/performance?action=dance_jellyfish&format=json` 另含插值、关节顺序与参考来源。文字通过 `text` 参数提供。

源文件：`src/performance_program.py`（动作曲线/节奏）、`src/gesture_library.py`（近似手型）、`src/hardware_trial.py`（按实际起点与用户设置编译）、`src/phrase_text.py`（输入检查）。浏览器不能传入任意关节目标。

## 参考与验证范围

- [El Tiro: Learn 3 Finger Tutting Dance Moves](https://www.brambilabong.com/blogs/popping/learn-3-finger-tutting-dance-moves-tutorial)：作者的视频和逐字稿，涉及逐指波浪、指节波浪与水母式手势。本项目阅读教程说明后编排关节曲线，没有提取或分发视频。
- [ASL University / Bill Vicars: Fingerspelling](https://www.lifeprint.com/asl101/pages-layout/fingerspelling.htm)：手型、掌面朝向以及J/Z的运动区别。项目明确保留固定腕近似限制。

离线测试覆盖输入校验、字符完整性、重复字母、曲线衔接、速度重定时、回到实测起点、控制端版本门槛及CSV格式。二代左右手对三套舞蹈和 `WUJI TECH` 做100Hz几何抽样，未发现超过0.5mm的碰撞网格穿透；这不是动力学负载测试或实机验收。其他文字组合尚未逐一检查。

## English

Choose **Text…**, enter a phrase, apply it, then explicitly press play in Preview or Real hand mode. ASCII A–Z, digits and spaces are accepted, up to48 normalized characters. Words pause; repeated letters reform. These are fixed-wrist approximations, including moving J/Z, not certified sign language.

Three authored dances share their nominal curves between preview and Hand2 planning. Hardware rebases from measured posture and retimes to configured amplitude/speed. CSV exports1000Hz nominal target angles, not measured motion or force. Hand1 variants remain preview-only and have observed geometric interference. No new physical validation was performed for this release.
