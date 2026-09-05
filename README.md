# Conference Countdown

一个原生 macOS 菜单栏 App，用于跟踪学术会议的投稿、评审、作者回复、最终结果和会期倒计时。

支持 WWW、ACL、ICLR、ACM MM、ICASSP、ICME、NeurIPS、ICML、CHI、EMNLP、KDD 和 AAAI。

## 功能

- 完整展示会议里程碑；已截止事件显示为灰色。
- Tab 默认按下一个里程碑排序，也可隐藏会议、拖拽调整顺序。
- 记住每个 Tab 上次选中的里程碑。
- 可开启倒计时通知和登录时启动。
- 启动时、持续运行每 24 小时自动检查数据，也可手动更新。
- 尚未公布的日期根据历史数据预测，并明确标为“预测”。

## 构建与运行

需要 macOS 13 或更新版本、Apple Command Line Tools 和 Python 3.9+。

```bash
git clone https://github.com/leverimmy/conference-countdown.git
cd conference-countdown
zsh build.sh
open "build/Conference-Countdown.app"
```

如果没有 Command Line Tools，先运行 `xcode-select --install`。构建会在本机生成并临时签名 App，不需要 Apple 开发者账号。App、签名和本地缓存不会由这些命令上传到 GitHub。

## 数据与来源

`data/` 是唯一数据源。每个会议维护四个文件：

```text
data/<conference-id>/
├── current.json
├── history.json
├── sources.json
└── evidence.json
```

`sources.json` 保存官网、官方 CFP、OpenReview 等来源和抓取规则。`evidence.json` 保存带上下文的原文段落、链接、SHA-256，以及它们对应的日期。证据不足、存在冲突、仅支撑日期而未说明时刻的情况会分别标明。

App 从 [GitHub Pages 数据清单](https://leverimmy.github.io/conference-countdown/v1/manifest.json)更新，不访问 GitHub API，也不需要 token。版本号是生成该数据的 `main` commit SHA；每次推送到 `main` 都会重新发布，包括仅修改代码或文档的提交。

App 先比较版本，再下载并验证文件大小、SHA-256 和数据格式，全部成功后才替换缓存。发布尚未完成时仍会取得上次发布的数据；网络或校验失败时继续使用缓存，没有缓存则使用构建时内置的数据。历史预测仅用于规划，不代表官方日期。

## 检查官方来源

日常更新时，先保存修改前的数据，与 Codex 对话寻找来源并整理候选，再打开本地页面审核。

```bash
# 开始这一轮修改前
python3 scripts/review_sources.py --snapshot

# Codex 将候选和证据存入 data/ 后，打开审核页面
python3 scripts/review_sources.py --serve
```

浏览器会打开仅监听 `127.0.0.1` 的审核页面。每个候选展示原记录、来源中的值和高亮证据，可点击“采用”“拒绝”或“暂不处理”。采用会更新正式记录及证据，拒绝保留原值，两者均移除 candidate 并保存决定；暂不处理不修改数据。原因可选，会随 data 一起发布。Ctrl+C 停止服务。

决定后可在“审核记录”中点击“撤销并重新审核”，恢复原值及候选，再重新采用或拒绝。原决定与撤销记录都会保留；相关记录或证据后来发生变化时，不能直接撤销覆盖。

只有日期的当前届候选也可直接采用：默认设为当天 `23:59:59 AoE（UTC−12）`，自动生成 App 显示文字。候选已包含明确时刻和时区时保留原值；缺少显示文字时自动生成。历史记录仍只保存日期。

写入由确定性 Python 代码完成，不调用 LLM。页面过期会拒绝保存；写入前校验完整数据，失败时回滚。中断备份保存在 `build/source-check/`，下次启动时恢复。不会自动 commit、push 或发布。

来源、证据原文、章节、定位 quote、hash、候选和决定都存放在 `data/`，候选值不会被 App 用作正式日期。具体字段见 [本地提取结果](data/README.md#本地提取结果)。网页自动发现的链接只是线索，需先整理成 sources.json 中的候选，才能点击审核。

只需要静态报告时，运行 `python3 scripts/review_sources.py`，输出为 `build/source-check/report.html`，不联网、不修改数据，也没有写入按钮。CI 报告同样只读。

### 确定性来源检查

来源检查需要 `curl`；检查 PDF 还需要 Poppler（macOS 可用 `brew install poppler`）。

```bash
python3 scripts/check_sources.py
# 也可以只查一个会议
python3 scripts/check_sources.py --conference iclr
```

每次生成两个文件，均不会改写 `data/`：

- `build/source-check/report.html`：按会议列出来源增删、替换和页面中的候选链接变化。每项数据更新展示“现有数据 → 提取结果”，下方直接展示支撑段落，只高亮对应的新日期；区分“补全日期”和“更新已有日期”。尚不能确认的新值、抓取异常单独列出，其他日期及未变化的原文默认折叠。
- `build/source-check/report.json`：保存检查时的旧数据、旧证据和本次原文，供本地提取与复核。后续修改数据不会改变报告里的“原来数据”。

来源检查是确定性的：相同页面内容与提取规则得到相同 hash；抓取时间不参与比较。段落、候选链接、HTTP 状态或提取配置改变，以及新增来源缺少基线，都会使检查失败。抓取或格式错误单独报错，不会冒充“未变化”。已有的人工核对备注仍显示在报告中，但不影响来源是否变化的判定。

“来源快照未变化”只表示本次抓取结果与已保存的证据基线相同，不表示官网日期与仓库日期一致，也不是整页内容比较。已记录的日期冲突或新公告仍默认展开，不受来源快照是否变化影响。

检查发现网页变化后，由 Codex 在本地理解原文并更新数据及候选，不由检查脚本猜测新日期。也可以把已有 `report.json` 作为修改前的基线：`python3 scripts/review_sources.py build/source-check/report.json`。

Check Source CI 在 PR、推送到 `main`、每天北京时间 08:00 和手动触发时运行，Actions Summary 显示检查状态；下载 artifact 后在浏览器打开 `report.html` 查看完整报告（GitHub Summary 不展示此 HTML 页面）。定时任务可能被 GitHub 延迟，失败通知取决于你的 GitHub 通知设置。

核对后由你修改日期及证据，自行提交或提出 PR。CI 不调用 LLM、不创建 PR，也不自动接受新的 hash。具体流程见 [维护指南](CONTRIBUTING.md) 和 [数据格式](data/README.md)。

## GitHub Pages 设置

在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**，再推送到 `main` 或手动运行 **Publish data to Pages**。

Pages 只发布数据及来源证据，不发布 App。PR 只校验打包，不部署。App 的 macOS 双架构构建检查独立运行。

Fork 后如需让 App 使用自己的数据，将 `Info.plist` 中的 `ConferenceDataManifestURL` 改为你的 Pages 地址，再本地构建。工作流设置参考 [GitHub Pages 文档](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

## License

[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](LICENSE)（CC BY-NC-SA 4.0）。来源摘录的权利说明见 [data/README.md](data/README.md)。
