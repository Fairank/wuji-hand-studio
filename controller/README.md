# Linux controller / Linux 控制端

The desktop UI and MuJoCo preview work without a hand. Actual hardware uses an explicit SSH connection to a Linux computer with the official `wuji-sdk`. No SSH address, key, password, device ID, or trained model is distributed.

桌面界面与MuJoCo预览无需接手。实机通过SSH连接你自己的Linux电脑，该电脑需要安装官方SDK并能发现左手。

1. On Linux, install Python 3.12, OpenSSH server, and `python3 -m venv ~/wuji-studio-venv`.
2. Install `~/wuji-studio-venv/bin/pip install wuji-sdk numpy`.
3. Copy the **contents** of `controller/source/` (in a download package), or repository `src/`, into `~/wuji-studio-controller/`. Keep `official_data/` and `trial_sdk_poses.json` with the scripts.
4. Verify the controller host fingerprint through a normal SSH login. Set up your own SSH key or agent. Do not copy anyone else's private key.
5. In **Connection → Linux controller settings**, enter the host, SSH user, absolute controller directory and absolute venv Python path. Select local key/known_hosts paths when needed; empty uses your normal SSH configuration. Save, then explicitly connect the left hand.
6. SDK gains are edited on the Parameters page, then explicitly synchronized while disconnected. Reconnect to load them. Saving never starts motion.

Ubuntu can use itself as its SSH controller (`localhost`) after the same setup. Windows/macOS can use a Linux PC or VM. This release does not claim native Windows/macOS SDK hardware compatibility. The UI is cross-platform; the SDK controller remains Linux.

Ubuntu可配置本机为SSH控制端。Windows/macOS使用Linux电脑或虚拟机；不把三个平台的界面包宣传为三个平台原生SDK均已支持。

No script here auto-enables a hand. Do not manually start `console_agent.py` to run a demonstration; the desktop owns its explicit session and browser lease.
