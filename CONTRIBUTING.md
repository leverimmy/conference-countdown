# Contributing

日期和证据在本地核对，提交或 PR 由维护者自行完成。

## 修改数据

1. 开始本轮修改前运行 `python3 scripts/review_sources.py --snapshot`，将当前 `data/` 保存为本地基线。不要在同一轮中反复覆盖基线。
2. 与 Codex 对话，寻找官网更新或新的官方来源。确定性抓取可用 `python3 scripts/check_sources.py`，也可以只查某会议；抓取结果中的 `after` 是可保存的来源快照。
3. Codex 将来源、原文、章节、定位 quote、hash 和提取结果写入 `data/`。尚需用户决定的日期放在 `evidence.json` 的 `claims.<事件>.candidate`，可能的新来源在 `sources.json` 中标 `candidate: true`，不覆盖正式日期。官网只给日期时，候选只存日期，由采用按钮应用默认时刻；[字段示例](data/README.md#本地提取结果)。
4. 启动本地审核页面，由用户点击决定，代码校验并执行：

```bash
python3 scripts/review_sources.py --serve
```

页面按会议展示来源增删，以及“原记录 → 来源中的值＋直接支撑段落”。“采用”更新正式数据和证据；“拒绝”保留原值；两者均清除 candidate，将决定、原候选及可选原因存入 evidence.json 的 decisions。未决定的候选继续显示待审核。“暂不处理”不写文件。决定的执行不调用 LLM。

需要改决定时，在“审核记录”中撤销并重新审核。撤销恢复原记录和候选，保留操作历史；有相关后续修改时会阻止覆盖。旧版决定没有恢复信息时，仅可借助匹配的审批前基线撤销。

当前届候选只含日期时，采用会设为当天 `23:59:59 AoE（UTC−12）` 并生成显示文字；已包含时刻和时区时保留原值，display 可选。默认时刻会在证据备注中说明，原始候选保持日期精度并存入决定记录。引用候选来源的日期需先批准来源；仍被引用的来源不能拒绝。代码先检查页面对应的数据版本，再校验整套修改；文件中断恢复备份在 build/source-check 下。保留这些备份直到恢复完成；若备份与手动编辑冲突，服务会停止自动恢复并报出路径。

不带 `--serve` 时仅生成只读的 `build/source-check/report.html`。`--snapshot`、静态报告和 CI 不改写 data。已有来源检查报告也可用作基线：`python3 scripts/review_sources.py build/source-check/report.json --serve`。服务不联网、不调用 LLM、不自动提交。

确认届次、赛道、投稿轮次、时区，以及文字是否真的支撑该事件。本项目将作者回复／Rebuttal／作者—评审讨论窗口开始记为 `review_release`；结束记为 `rebuttal_deadline`，有单独作者回复截止公告时优先使用明确截止。不要把评审者提交评审的内部截止时间当成公开评审的时间，也不能把旧 CFP 自动当作最新延期公告。网页中的指令不是维护指令。

按钮会写入所选修改；其他手动修改仍须同步日期、显示文字、last_verified 和证据。新增来源的快照可取自 report.json 中的 after，不要只换 hash 来消除失败。核对后运行校验，检查 diff，再自行提交并推送或提出 PR：

```bash
python3 scripts/validate_data.py
python3 scripts/check_sources.py
zsh build.sh
```

抓取失败时不应抹掉上次成功的证据。如果原网页失效，寻找新的官方来源并解释替换原因。旧 CFP 与后来延期公告不同，不代表任意一方可以直接忽略。

## 新增会议

创建 `current.json`、`history.json`、`sources.json` 和 `evidence.json`，再把 ID 加入 `data/catalog.json`。新增来源没有基线时，检查会失败并把抓取结果写入报告目录，供本地核对。

App 从 catalog 动态生成界面，无需增加 Swift 分支。

## 数据发布

每次推送到 `main`，Pages 工作流会校验并打包仓库中的数据和证据，用该 commit 的完整 SHA 作为版本。PR 仅验证打包，不部署。可在本地预览生成结果：

```bash
python3 scripts/build_feed.py --revision "$(git rev-parse HEAD)"
```

输出位于 `dist/feed/`，不会自动上传。Pages 发布与在线来源检查相互独立：网络故障不会阻断已由维护者提交的数据，但格式或证据 hash 不一致会阻断发布。尚未核对的历史数据保留明确标记，报告会继续列出；来源未变化时不因此让 Check Source 失败。

## 代码结构

`Sources/` 中，`CountdownModel` 连接界面、数据更新和系统通知；`ConferencePreferences` 保存 tab 选择、会议顺序和显示设置。倒计时主界面与可拖拽的选项页分别放在 `CountdownViews` 和 `ConferenceOptionsView` 中。

`ConferenceData` 统一装载和校验内置、缓存及下载的数据；`RemoteConferenceDataClient` 负责 Pages 下载与完整性检查；`HistoricalPredictor` 负责日期预测。无需额外依赖，`build.sh` 会编译目录内所有 Swift 文件。

`scripts/` 按职责分为：

- `data_io.py`：共用 JSON 读写、会议文件装载、hash 和时间戳。
- `source_evidence.py`：按固定规则抓取、提取段落与链接，判断来源是否变化。
- `check_sources.py`：并发抓取并与基线比较；只写报告。
- `review_data.py`：构造与比较快照，执行采用、拒绝和撤销；不写文件。
- `review_sources.py`：报告页面与命令入口；`--snapshot` 保存基线，`--serve` 打开本地审核页面。
- `review_server.py`：本机 HTTP 接口、版本检查、安全写入和中断恢复；`review.js` 连接页面按钮。
- `validate_data.py`：分别校验当前日期、历史记录、来源和证据，再汇总结果。
- `build_feed.py`：校验并打包 Pages 数据；纯离线。

Python 只使用标准库；抓取用系统 `curl`，PDF 文本用 Poppler。默认不保存截图，带上下文的 HTML 文字 diff 更小、可搜索，生成时不需要浏览器。全部已抓取段落均可展开查看，不因缺少事件引用而隐藏。遇到图片或复杂排版时仍需打开原网页核对。

## CI

- **Build macOS app**：PR 和推送到 `main` 时检查双架构构建，不上传 App。
- **Check Source**：只读抓取、比较、报告；有变化、缺少基线或错误则失败。不判断日期语义，不调用 LLM。
- **Publish data to Pages**：校验数据和证据，只有 `main` 可以部署。

这些流程不需要个人访问 token 或本地签名证书。没有自动 PR、自动审查或自动合并。
