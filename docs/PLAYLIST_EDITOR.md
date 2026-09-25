# 节目单 / Playlist

从「单手展示 → 节目单」进入。编辑区与手部画面分开，启动成功后回到手部展示；展示页保留正在播放的动作、暂停、继续和停止入口。

1. **动作列表**：从下拉框添加动作。每条分别显示速度和重复次数；箭头调整顺序，删除按钮移除本条。可以重复添加同一动作。「补齐全部动作」仅追加缺少的手指舞，保留已有顺序、速度和次数，上限仍为 64 条。
2. **播放设置**：选择按列表或随机播放，设置整单播放遍数。持续循环用单独开关表示，不再要求输入 0。随机种子仅在随机模式的高级设置中出现；相同种子会复现顺序。
3. **播放到**：选择屏幕预览或真实手。底部只显示当前模式的启动按钮，并说明尚未就绪的原因。真实手模式显示动作幅度及原有的现场确认，使用已连接手的既有参数。

编辑自动保存在此工作区浏览器配置中，升级沿用旧节目单及播放设置。播放中查看的是锁定的节目单，停止后再修改，避免把下一次编辑误当成正在执行的内容。正在播放时关闭编辑框不停止任务；暂停或停止由展示页和编辑框共用同一命令入口。

## Implementation boundary

`playlist_editor.js` owns presentation, draft persistence and the existing `program_*` request path. `playlist_rows.js` is a generic DOM component: no network, device commands, timers or storage. The Python `ProgramRunner`, plan validation, stop/lease handling, hardware parameters, SDK and calibration behavior are unchanged.

The desktop maintenance API accepts `picker` with `kind: "playlist"` to open the editor and `kind: "closed"` to close it. This operation cannot start playback, change device settings or send motor commands.
