# 界面回弹 / Interface motion

「设置 → 外观」中的“试一下”只播放一个圆点的回弹，不会连接设备。开启“减少动态效果”或系统减少动态偏好后，回弹关闭，正在播放的圆点立即复位；停止操作不等待动画。

## 设计与范围

- 按钮：按下 75 ms、缩至 97%，松开在 450 ms 内轻微回弹并停稳。按钮布局和点击行为不变，连续操作由浏览器接续当前过渡。
- 弹窗：最多 6 px 位移和 1.5% 缩放，透明度单独在 130 ms 内恢复。关闭沿用原有即时关闭，不拦截或延迟关闭事件。
- 页面：标题轻移 4 px；连接/设置分区只淡入，三维模型、拖拽窗口和滚动位置不添加弹簧。
- 折叠箭头与设置开关：局部阻尼过渡。停止按钮保持稳定，不缩放。
- 没有常驻动画计时器、帧循环、滚动接管、桌面像素采集或模糊半径动画。空闲时没有新增动画任务。不承诺固定 120 Hz，实际刷新取决于系统与屏幕。

## 可维护实现

弹簧为离线生成的二阶欠阻尼阶跃响应，阻尼比 0.72、固有频率 3.5 Hz，450 ms 采样 61 点，末点归一为 1。只生成静态 CSS `linear()`，旧内核回退短贝塞尔曲线；启动和交互中不运行物理求解。生成脚本支持 `--check` 检查源与生成结果一致。

界面参数与真实机械手的 Kp、Kd、电流、轨迹速度及 SDK 采样频率无关。相关控制、标定和设备会话代码未改。

依据：[CSS Easing Functions Level 2](https://www.w3.org/TR/css-easing-2/#linear-easing-function)。这是工作台自己的轻量交互实现，不宣称使用苹果私有动画代码。

## English

Settings → Appearance → Try it previews one short spring response. App and system Reduce Motion preferences disable movement and cancel any active preview. Button feedback, dialog entrance, local disclosure arrows and switches use finite compositor-friendly transforms. Scrolling, live 3D content, device command timing and all controller parameters are unchanged. Stop controls keep a stable target.
