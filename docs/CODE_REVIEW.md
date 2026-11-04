# 工作台代码审查

**当前状态（2026-11-04）：** 本地检查入口、PR 核对表与 `src/AGENTS.md` 已上传。新增工作流此前被 GitHub 授权拒绝，远端 `.github/workflows/code-review.yml` 仍不存在。完整内容作为[非执行模板](ci/code-review.yml)一并保存；模板和本机工作流字节相同，但不触发 Actions。远端自动检查、Codex 账号自动审查和分支规则均不能据此标成已开启。项目待办与验收范围见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

每次改动先复现具体问题，再检查共享调用链和不同回复顺序。作者完成修复与真实源流程验证后，由非作者换一个失败位置或顺序复查。检查设备/人员/左右手/模型身份、异步请求归属、退出未知保护、资料保留，以及页面资源和实际入口；不要只凭测试总数或截图放行。原设计和导航沿用 [QA_WORKFLOW.md](QA_WORKFLOW.md) 的约束。

## 本地与 CI 使用同一入口

安装仓库已有 `requirements.txt`，使用 Python 3.12 和 Node 24，然后运行：

```powershell
python scripts/run_review.py
```

默认创建一个新的系统临时验收目录并打印位置。也可传入一个**尚不存在**的目录，避免覆盖原证据：

```powershell
python scripts/run_review.py --record-dir "C:/workbench-qa/review-example"
```

`--only python` 执行完整 `src/test_*.py`；`--only node` 执行当前全部 `src/test_*.cjs`、JS 语法与既有连接命令检查；`--only syntax` 执行 Python AST、作者维护的 JS 语法与运行器自身契约测试。默认运行所有部分。缺少依赖、检查失败、超时、意外外网访问或检查期间所记录的源码变化均返回非零。

运行器从自身位置识别仓库，不依赖本机 checkout 名称或外部 QA 脚本。应用/CLI/组件的默认资料、受检运行时、默认 TEMP 和运行器输出位于该次隔离目录；不改 HOME、USERPROFILE 或 CODEX_HOME。少量既有单测仍显式创建仓库相对路径下的独有临时目录，运行器不静默重写这些路径。

Python 主测试进程拒绝真实默认 SDK 资料读取、真实 `wuji_sdk` 导入及意外非回环 DNS/连接，SDK 测试须提供隔离替身。测试可使用本机回环 HTTP 与合成进程；子进程不会继承 Python 审计钩子，Node 与子进程仍依靠既有合成测试边界。运行器不是在个人电脑执行不可信代码的安全沙箱。

完整 Python 集合包含已有 MuJoCo 静态模型/几何检查，不能称其未加载 MuJoCo；它们不代表实物动作、标定质量、渲染或物理稳定性验收。第三方原版前端资源仍由既有摘要/打包检查负责，JS 语法检查不改写 `vendor`。

目录保留 `summary.json`、`python.json` 和各阶段日志，记录源码前后摘要、隔离根、失败/跳过与被拒绝操作。摘要覆盖 `src`、`scripts`、`.github` 下的 Python、JS/MJS/CJS、YAML、Markdown、HTML、CSS、SVG、JSON，以及构建入口、依赖清单和本审查文档。其他二进制资源仍需既有摘要/打包和实际界面验收。CI 只收取这些摘要和日志，不上传隔离用户目录。分享本机证据前仍须排除私有数据。

## PR 审查与 GitHub 设置

模板在取得工作流写入授权并放入 `.github/workflows/code-review.yml` 后，才会在 push、PR 和手动触发时运行 Windows 检查。它仅有 `contents: read`，不构建/发布安装包、不写入仓库、不启动设备。启用后仍须核对一次实际运行结果。PR 模板要求记录实际复现、正常与异常流程、非作者复查和未验证范围。CI 结果应关联本次提交；旧报告不能替代本次流程。

若要阻止未通过检查或未获批准的合并，还需在 GitHub 仓库规则中要求 **Windows source checks** 和非作者批准。提交工作流文件本身不会创建分支保护或证明远端检查已经成功。

## Codex 自动审查单独开启

这套 CI 是确定性的测试，**不是 AI 审查服务**，无需新增模型 API key。要让 Codex 在 GitHub PR 上审查，还需把仓库连接到 Codex，在 [Codex code review 设置](https://chatgpt.com/settings/code-review) 选择仓库并开启 Automatic review；自动触发还受个人偏好控制。配置后可在代表性 PR 中请求 `@codex review`，核实实际收到审查结果。这里不声称账号开关已经开启，也不代发评论。

`src/AGENTS.md` 的四条规则覆盖源码审查；根目录本机 AGENTS 不强行提交。Codex 规则和 AI 反馈不能代替测试、完整流程、非作者审批或 GitHub 合并规则。设置步骤与规则作用参照 [OpenAI 官方 GitHub 审查说明](https://developers.openai.com/codex/integrations/github/) 和 [自定义审查规则](https://developers.openai.com/blog/custom-code-review-rules-for-codex)。
