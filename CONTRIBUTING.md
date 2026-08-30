# Contributing

感谢帮助维护 Conference Countdown。日期错误会直接影响他人的投稿安排，所以所有官方日期修改都需要可核查来源。

## 修改现有会议

1. 打开 `data/<conference-id>/current.json`。
2. 只根据官网、官方 Call for Papers 或官方 OpenReview venue 修改数据；不要用第三方 deadline 聚合站作为最终来源。
3. 更新 `last_verified`。如果新日期也适合补入历史样本，同步修改 `history.json` 并附官方 `source`。
4. 运行：

   ```bash
   python3 scripts/validate_data.py
   zsh build.sh
   ```

5. 在 PR 中说明哪些值改变、使用什么时区，并链接到来源。

## 处理自动监测 PR

自动 PR 中的片段只是“网页可能变化”的证据，不等于新的官方日期。逐项访问来源后：

- 确有日期变化：直接在该 PR 分支修改 `current.json`/`history.json`。
- 页面变化但日期未变：勾选 PR 清单并说明无需更新。
- 页面无法可靠解析：可调整 `sources.json` 或 `scripts/check_sources.py`，但不要降低官方来源要求。

## 新增会议

复制一个已有会议目录作为格式参考，创建 `current.json`、`history.json`、`sources.json`，再把 ID 加入 `data/catalog.json`。事件 ID、默认事件、官网、IANA 时区、至少一条历史记录和至少一个监测来源都是必需的。

App 会从 catalog 动态生成界面，新会议不需要再写一份 Swift 常量。
