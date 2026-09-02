# Conference data

`data/` 是 App、GitHub Pages Feed 和自动监测流程共同使用的唯一数据源。每个会议使用一个独立子目录，目录名必须与文件中的 `id` 一致。

## 文件结构

```text
data/
├── README.md
├── catalog.json
├── schema.json
├── www/
│   ├── current.json
│   ├── history.json
│   └── sources.json
└── <other-conference-id>/
    ├── current.json
    ├── history.json
    └── sources.json
```

- `catalog.json`：会议全集和默认顺序。新增或删除会议时必须同步修改。
- `current.json`：App 当前显示的会议资料与里程碑。
- `history.json`：历届主赛道日期和官方来源，供预测器使用。
- `sources.json`：GitHub Actions 定期检查的官方网页/OpenReview API。
- `schema.json`：当前格式的机器可读说明；最终约束由 `scripts/validate_data.py` 执行。

## `current.json`

会议级字段包括稳定 `id`、届次、显示名称、官网、IANA 时区、默认事件和 `last_verified`。每个事件具有：

- `id`：全局唯一，格式为 `<conference-id>.<event-id>`。
- `title` / `compact_title`：页面标题与菜单栏短标题。
- `at`：带 UTC offset 的 ISO 8601 时间；官方日期尚未公布时为 `null`。
- `date_label` / `detail_label`：用户可读的官方时区与本地时间说明。
- `symbol`：SF Symbols 名称。
- `historical_key` / `target_year`：仅未公布且需要预测的事件必填。

官方日期一旦公布，应填写 `at` 并删除预测字段，同时更新 `last_verified`。AoE 截止时间保留 `-12:00`，不要预先换算成北京时间。

## `history.json` 与预测

历史记录支持：

- `abstract_deadline`
- `paper_deadline`
- `commitment_deadline`
- `review_release`
- `rebuttal_deadline`
- `final_decision`
- `conference_start` / `conference_end`

每条记录必须附官方 `source`，日期使用 `YYYY-MM-DD`。各事件字段可以缺省，因为会议流程不同。

预测器计算历届事件相对当届会期开幕的提前天数，取中位数后从目标会期倒推；如果目标会期也未公布，则先取历届开幕月日的中位数。界面中的误差是样本相对中位数的最大绝对偏差，不是官方置信区间。所有预测都必须标为预测，不能伪装成官方日期。

## `sources.json`

每个 watcher 使用稳定 `id`、`kind`（`html` 或 `openreview`）和 HTTPS `url`。不稳定或可能尚未建立的端点可标记 `optional: true`。

自动监测会从 HTML 保留日期所在章节及相邻文本块，从 OpenReview 保留字段路径、同级字段和值；这些证据写入 `.github/source-state/` 后再比较摘要。变化会创建或更新 Draft PR，并可在 PR 中自动 `@copilot`，请它只对证据明确支持的 `current.json` 值提出修改。

抓取脚本本身永远不会直接修改 `current.json` 或 `history.json`，Copilot 也不会自动合并。网页文字和 API 响应均按不可信证据处理，最终日期、届次、时区和修改范围必须由人核对。

## 校验

```bash
python3 scripts/validate_data.py
python3 scripts/build_feed.py --revision local
```

提交前同时确认官网链接、时区、北京时间说明和事件先后顺序均正确。
