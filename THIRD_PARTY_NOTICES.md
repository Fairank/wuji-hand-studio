# Attribution / 来源

This is an unofficial personal tool. Original application code is MIT licensed; third-party components retain their own licenses. Trademarks/logos are not licensed by the application's MIT grant and do not imply vendor endorsement.

- Native left-hand geometry: [wuji-technology/wuji-description](https://github.com/wuji-technology/wuji-description), hand2/hand2_beta2/body. Original MIT notice retained at `src/assets/LICENSE`.
- Hand 2 example adaptations and `left.replay`: [wuji-technology/wuji-sdk](https://github.com/wuji-technology/wuji-sdk), example commit `b0e48652dd94f4bc33df61cdc23a6d5dc598f93d`. Original MIT notice retained at `src/official_data/LICENSE-wuji-sdk.txt`. Local changes include start/return transitions, scaling, explicit selection and the web control session. The sample is not claimed to be an independently learned policy.
- WUJI logo: owner-supplied official complete wordmark, aspect ratio preserved. Original SHA256 `e44fe4f7c0ae125d594b0f6f40fa6f90f5b9a12cf79cfb1a567230453c10dbee`. Name/logo remain the property of Wuji Technology. No Apple imagery or marks are included.
- Runtime dependencies: MuJoCo (Apache-2.0), NumPy (BSD), Pillow (HPND), GLFW (zlib), Paramiko (LGPL-2.1), and their dependencies; installed distribution notices are collected into each binary bundle by the build tooling. PyInstaller is GPL with a bootloader distribution exception. See each upstream package for its terms; those terms are not replaced by the application license.
- Claude `claude-fable-5-1`, effort `max`, assisted with generic user-data-directory code, its tests, and bilingual documentation. Output was reviewed. No private model weights, device data or credentials were sent as part of this assistance.
# Windows desktop additions (0.1.4)

The Windows shell uses [pywebview](https://github.com/r0x0r/pywebview) (BSD-3-Clause),
[Python.NET](https://github.com/pythonnet/pythonnet) and clr-loader (MIT).
Packaged dependency license files are under `third-party-licenses`.
Microsoft WebView2 Runtime is a separately licensed Microsoft runtime; the installer
embeds Microsoft's signed Evergreen bootstrapper and runs it only if the runtime is absent.
The installation executable is built using Inno Setup 6.4.3; its generated installer
license applies. The Simplified Chinese translation is from that version's official
source repository, `Files/Languages/Unofficial/ChineseSimplified.isl`.
The original Wuji logo and the existing attribution below are retained.
Liquid Glass is an Apple design concept; this software uses its own Windows/WebView2
CSS material implementation and is not an Apple product or native Apple framework.


## Windows edge refraction and managed runtime (0.1.6)

windows-capture 2.0.0 (MIT) and OpenCV Python are used only for the optional Windows edge effect. Dependency licenses are bundled under third-party-licenses. The optional WSL user-space image is based on Canonical Ubuntu Base 24.04.5 and preserves package copyright/license files under /usr/share/doc. Official Wuji SDK/CLI 2026.8.31 are included without modification. Microsoft WSL itself is obtained separately from Microsoft if missing. See docs/BUILT_IN_CONTROLLER.md for sources and scope. No private models, account credentials or personal device records are included.
