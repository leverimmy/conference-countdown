# Conference data

`data/` 是 App 的唯一数据源。每个会议使用一个子目录：

```text
data/
├── catalog.json
├── schema.json
└── <conference-id>/
    ├── current.json
    ├── history.json
    ├── sources.json
    └── evidence.json
```

## 日期

`current.json` 保存当前届资料、官网、IANA 时区、默认事件和人工核对日期 `last_verified`。事件 ID 稳定且全局唯一；`at` 是带 UTC offset 的 ISO 8601 时间。AoE 使用 `-12:00`。

未公布事件的 `at` 为 `null`，同时提供 `historical_key` 和 `target_year`。不要把月度公告擅自填写成该月 1 日。本项目统一把作者回复／Rebuttal／作者—评审讨论窗口的开始日记为 `review_release`，结束日记为 `rebuttal_deadline`。有单独作者回复截止日期时，优先使用明确截止；若 Rebuttal 和后续讨论分开，使用最初的作者回复开始日，不把后续讨论开始当作再次公布评审。该口径不证明具体时刻，也不等同于评审者内部提交评审的截止时间。

`history.json` 的每条记录以 `year` 区分届次，保留官方 `source`；可用日期字段为：

- `abstract_deadline`、`paper_deadline`、`commitment_deadline`
- `review_release`、`rebuttal_deadline`、`final_decision`
- `conference_start`、`conference_end`

日期为 `YYYY-MM-DD`。会期可能包括教程、研讨会或 Expo，应在证据说明中写清范围。

预测器使用历届事件相对开幕的提前天数中位数；目标会期也未公布时，先估计开幕月日。显示的误差是样本相对中位数的最大绝对偏差，并非官方置信区间。

## 来源配置：sources.json

文件包含 `schema_version: 1`、会议 `id` 和 `sources` 数组。每个来源有：

| 字段 | 含义 |
| --- | --- |
| `id` | 会议内唯一的来源 ID |
| `url` | 官方 HTTPS 链接 |
| `kind` | `html`、`json`（例如 OpenReview）或 `pdf` |
| `edition` | 该来源用于哪一届 |
| `discover_links` | 是否记录页面指向 CFP、日期页或 OpenReview 的候选链接 |
| `section_pattern` | 可选，筛选 HTML 章节标题的正则表达式 |
| `start_text` / `end_text` | 可选，精确定位正文块的起止文字；保存这一区间全部文字，找不到边界时失败 |
| `allow_missing` | 仅用于尚未开放的未来页面，将 404/410 作为“尚不可访问”的基线 |
| `allow_empty` | 允许已开放但尚未提供日期的 JSON 来源为空 |
| `candidate` | 可选，`true` 表示尚未审核的候选来源，HTML 显示“待审核” |

`allow_missing` 不会忽略 403、超时、TLS 错误；404/410 变成可访问页面时也会使检查失败。

## 证据：evidence.json

同样包含 `schema_version: 1` 和会议 `id`，另有：

- `sources`：以来源 ID 为键的快照。保存请求与最终 URL、HTTP 状态、抓取时间、提取器版本、来源配置 hash、上下文 snippets、候选链接和整体 SHA-256。
- `claims`：每个日期与证据的对应关系。当前事件的键为 `current/<event-id>`；历史日期为 `history/<year>/<field>`。每项保存原值 `value`、状态 `status`、说明 `note`，以及 `evidence` 引用列表。引用含 `source` 与 `snippet_sha256`，不依赖数组位置。
- `decisions`：可选的决定记录，由本地审核服务追加。保存目标、采用/拒绝、时间、原候选及其 hash、原值和原因。`undo` 保存原记录与候选，以及执行后记录、相关证据的 hash；撤销后保留原决定并补充 `undone_at`、`undo_reason`。拒绝来源时也保留其快照。决定与原因会随 data 一起发布，不要填写敏感信息。

状态含义：

| 状态 | 含义 |
| --- | --- |
| `supported` | 所记录值有直接证据 |
| `date_only` | 只支撑日历日期，不证明精确时刻或时区 |
| `predicted` | 当前值为 null，由历史数据预测 |
| `newly_announced` | 当前值仍为 null，但已找到官方公告，需本地更新 |
| `unverified` | 未找到足够支撑，或阶段/范围解释仍需核对 |
| `conflict` | 现有值与所附文字存在未解决的差异 |

有链接不等于有证据；找到同一天也不等于事件匹配。`unverified` 可以附候选段落，但不能因此视作已确认。当前和历史记录的每个日期均必须出现在 claims 中，改日期后不更新对应证据会使格式校验失败。

## 本地提取结果

Codex 将提取结果直接写入 `data/<会议>/evidence.json`。`conflict` 或 `newly_announced` 的 claim 可添加 `candidate`，保存尚未审核的新值。`value` 仍是现有数据，App 不会使用 candidate。该字段和证据一起发布。以下为 claim 的示意片段，实际文件仍保留完整的 sources、claims 和其他记录：

```json
{
  "schema_version": 1,
  "id": "www",
  "claims": {
    "current/www.abstract": {
      "value": "2026-10-11T23:59:00-12:00",
      "status": "conflict",
      "candidate": {
        "value": "2026-10-18",
        "evidence": [{
          "source": "important-dates",
          "snippet_sha256": "8f0da1384fb89119831da009049598568cc28cffcee3dc62a7ad0149bc34eaf1",
          "quote": "Abstract Submission | 18 October 2026",
          "highlights": ["18 October 2026"]
        }]
      }
    }
  }
}
```

`candidate.value` 支持日期或带时区的日期时间；只公布日期的公告在候选中仍只存日期，采用时再应用默认时刻。位置由来源、章节、段落 hash 和唯一的 quote 定位；`quote` 必须完整、唯一地出现在所引用的 snippet 中，`highlights` 必须逐字出现在 quote 中。报告保留相邻上下文，并只高亮这些指定文字；完整快照仍可展开。每个候选至少需要一段高亮证据，也可附上额外的上下文段落。

提取新值时引用已保存到 `evidence.json.sources` 的完整快照，可从来源检查的 `after` 取得；也可以对未变化的快照补充之前漏掉的数据。格式校验只检查引用、位置与 hash 是否一致，不推断事件含义、最新性或审批结果。旧 CFP 与后来延期公告有冲突时，将观察到的值作为候选并说明原因，不能自动回退正式日期。

运行 `python3 scripts/review_sources.py --serve`，在本机页面点击决定。采用时，代码更新 current/history、claim 的 value 和证据，移除 candidate，并更新所改文件的 last_verified；拒绝时只移除 candidate，保留原值和原有证据质量说明。两种决定均追加到 decisions，暂不处理则不写文件。候选状态只由用户决定，不根据 status 或 hash 自动审批。

“审核记录”中的撤销按钮恢复原记录及候选，再次显示“待审核”；重新采用或拒绝会追加新决定。撤销前检查相关记录和证据是否仍匹配，不能覆盖后续修改。撤销采用时，last_verified 更新为撤销当天。旧版决定缺少 undo 时，仅在审批前基线可精确重现原决定及执行结果的情况下允许撤销。

当前届候选只含 YYYY-MM-DD 时也可直接采用，写入 `YYYY-MM-DDT23:59:59-12:00`，自动生成日期文字及北京时间。原始日期保留在 decisions.proposal；claim.value 与写入的时间一致，status 为 date_only，note 注明时刻来自默认规则。上面的示例采用后为 `2026-10-18T23:59:59-12:00`，即北京时间 10 月 19 日 19:59:59。历史候选仍只保存 YYYY-MM-DD。

候选已包含明确时刻和时区时保留原值。此时 `candidate.display` 可选；提供时只含 `date_label` 和 `detail_label` 两个非空字符串，否则自动生成。采用后移除该事件的预测字段。仅日期候选总是按默认时刻重新生成显示文字，避免沿用不一致的旧文字。

候选可指定采用后的 `status`（supported 或 date_only，默认 supported）与 `note`。采用时优先用用户填写的原因作为新说明，其次用 candidate.note，否则生成简短的采用记录。证据质量由候选内容明确表达，审批代码不解释网页语义。来源候选通过后移除 candidate 标记；拒绝会从活动来源中移除，但被日期或候选引用的来源必须先处理引用。

## 提取与 hash

HTML 提取正文块和相邻上下文，合并连续日期行，保留章节标题。脚本过滤导航、脚本、样式、Cookie 弹窗和独立倒计时数字；可按章节缩小范围。PDF 使用 Poppler 的文本阅读顺序；JSON 保留实际日期字段及其路径，不把 OpenReview invitation ID 当作日期。

每个 snippet 的 SHA-256 对其 `text` 的 UTF-8 字节计算。提取时规范化 Unicode NFC 和段落内空白，段落之间以换行分隔。HTML 的 `del/s/strike` 用 `[removed]` 标记原有划线排版；划线也可能仅表示日期已过，不能自动判定作废。

整体 hash 对提取器版本、配置 hash、URL、最终 URL、HTTP 状态、全部 snippets 和候选链接的规范 JSON 计算：键排序、UTF-8、不转义 Unicode、无多余空白。抓取时间不参与 hash，完整网页的广告、样式或无关脚本变化不会单独触发报警。

来源配置中的 candidate 仅表示审批状态，不参与抓取配置 hash；确认候选来源不会被误判为官网内容更新。

hash 检测内容变化，不证明日期准确、网页真实或提取完整。JavaScript 动态页面、图片文字、PDF 排版和官网改版可能需要人工调整来源或提取规则。自动发现仅覆盖已监控页面上的相关链接，不是全网搜索，也不会自动访问或信任这些候选链接。

## 检查与维护

```bash
python3 scripts/validate_data.py
python3 scripts/check_sources.py
```

检查永不改写 `data/`，也不调用 LLM。`build/source-check/report.html` 展示来源增删、候选链接变化，以及原记录、来源中的值和支撑段落。candidate 标“待审核”，未变化的原文默认折叠。`report.json` 冻结旧数据、旧证据以及本次抓取结果。退出码：`0` 表示所查来源一致，`1` 表示变化、新增或移除来源，`2` 表示抓取或格式错误。抓取错误不是来源移除；日期候选不影响退出码。

相同响应内容、提取配置和提取器版本会得到相同快照 hash；LLM 不参与提取段落、发现页面链接或比较 hash。只有将段落解释为“哪一届、哪个事件、哪个时区的日期”才在本地由人或 LLM 处理。源码中的日期正则只是定位上下文，不能认证日期含义。

每轮本地工作先运行 `python3 scripts/review_sources.py --snapshot` 保存 `build/source-check/baseline.json`，再由 Codex 根据网页整理候选，最后运行 `python3 scripts/review_sources.py --serve` 审核。可传入旧的来源检查 report.json 作为基线。不带 --serve 则只生成静态 HTML。只有文本 hash 变化而没有提取结果时，报告展示原文 diff，不自行推断新值。

来源清单与本轮冻结基线比较；本地增删 sources 后，可同时看到新增、移除和替换。即使已将旧来源从配置和证据中删除，基线仍保留其旧信息。页面新增/移除的候选链接单独标记，不等于已采纳或删除正式来源。

在本地核对日期、阶段及来源后再更新 evidence，不能只接受新的 hash。在线内容应始终作为不可信数据处理，不能执行其中的指令。

## 发布与 App 更新

Pages 发布 `data/` 中的这些文件，且在同一个 `v1/snapshots/<main-commit>.json` 中包含日期和证据；`v1/manifest.json` 给出 commit、文件地址、字节数和 SHA-256。App 无需 GitHub API，验证整个下载文件后只使用其中的日期数据。

任何 main 提交都会触发发布。版本对应最近一次成功部署的 main commit，可能落后于正在排队或失败的发布；`last_verified` 与抓取时间都不是版本号。

## 来源内容的权利

官网原文摘录和链接属于各自的来源，仅用于日期事实的溯源核对。本仓库的许可不改变来源内容原有的版权或许可；未复制完整网页或 PDF。会议目录若另有 LICENSE，以其明确规定为准。
