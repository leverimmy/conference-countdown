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

每次 push 到 `main` 后，以及每天 UTC+8 08:00，Action 会把官网与 OpenReview 中包含日期前后文的证据保存到 `.github/source-state/`，并创建或更新一个 Draft PR。若仓库配置了 `COPILOT_TRIGGER_TOKEN`，Action 会以维护者身份在 PR 中 `@copilot`；Copilot 只负责提出 `current.json` 修改，不能替代人工核对。

自动 PR 中的内容只是“网页可能变化”的证据，不等于新的官方日期。逐项访问来源并审阅 Copilot 的 diff 后：

- 确有日期变化：确认 `current.json` 中的日期、时区、标签和官方/预测状态均正确；需要时直接修正。
- 页面变化但日期未变：勾选 PR 清单并说明无需更新。
- 页面无法可靠解析：可调整 `sources.json` 或 `scripts/check_sources.py`，但不要降低官方来源要求。

确认无误后把 PR 标记为 Ready for review 并合并；自动流程不会自行合并。

## 启用 Copilot 自动提议

此项只需要仓库维护者配置一次：

1. 使用对本仓库有 write 权限且已启用 Copilot coding agent 的账号，创建一个 fine-grained personal access token。Resource owner 选择自己的账号，只授权本仓库，并设置合理的过期时间。
2. Repository permissions 只授予 `Pull requests: Read and write`。
3. 打开仓库的 **Settings → Secrets and variables → Actions → New repository secret**，将 secret 命名为 `COPILOT_TRIGGER_TOKEN`，值为刚创建的 token。

token 只能存放在 GitHub Actions secret 中，不要写进代码、配置文件、`.env`、提交记录或 App。它只用于让 Action 以你的 GitHub 身份发布 PR 评论，与本机 Apple 签名无关。

没有配置 token 时，监测和 Draft PR 仍会正常运行，只会跳过 `@copilot`。配置完成后，可在 **Actions → Collect conference source evidence → Run workflow** 手动重跑一次；同一份观测证据不会重复通知 Copilot。

## 新增会议

复制一个已有会议目录作为格式参考，创建 `current.json`、`history.json`、`sources.json`，再把 ID 加入 `data/catalog.json`。事件 ID、默认事件、官网、IANA 时区、至少一条历史记录和至少一个监测来源都是必需的。

App 会从 catalog 动态生成界面，新会议不需要再写一份 Swift 常量。
