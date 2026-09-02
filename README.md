# Conference Countdown

一个原生 macOS 菜单栏 App，用于跟踪学术会议的投稿、评审意见、作者回复、最终结果和会期倒计时。

目前支持 WWW、ACL、ICLR、ACM MM、ICASSP、ICME、NeurIPS、ICML、CHI、EMNLP、KDD 和 AAAI。

## 功能

- 完整展示每个会议的所有里程碑。
- Tab 默认按下一个里程碑从近到远排列，也可隐藏会议并拖拽排序。
- 记住每个 Tab 上次选中的里程碑。
- 可开启 60、30、14、7、3、1 天和当天通知。
- 可设置登录时自动启动。
- 自动取得最新会议数据，也可手动检查更新。
- 官方日期尚未公布时，根据历届日程提供明确标记的预测日期。
- 不显示 Dock 图标，不占用普通应用窗口。

## 构建与运行

在 macOS 13 或更新版本上克隆仓库并构建：

```bash
git clone https://github.com/leverimmy/conference-countdown.git
cd conference-countdown
zsh build.sh
open "build/Conference-Countdown.app"
```

构建需要 Apple Command Line Tools。

## 日期数据

会议数据保存在 [`data/`](data/) 中。每个会议分别维护当前届日期、历届日期和官方来源：

```text
data/<conference-id>/
├── current.json
├── history.json
└── sources.json
```

App 会定期取得已核对并发布的数据。网络更新失败时，会继续使用上次成功下载的数据；没有缓存时使用 App 内置数据。

历史预测仅用于规划，不代表官方日期。所有预测都会在界面中以橙色和“预测”标识。

## 参与维护

字段说明、预测方法和数据维护流程见 [`data/README.md`](data/README.md) 与 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

仓库会在每次 push 到 `main` 后，以及每天 UTC+8 08:00，抓取官网和 OpenReview 的日期上下文；发现变化时创建 Draft PR，并可让 Copilot 在同一 PR 中提出 `current.json` 修改。所有数据更新仍需人工核对和合并，不会自动发布未经确认的日期。

## License

[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](LICENSE)（CC BY-NC-SA 4.0）。
