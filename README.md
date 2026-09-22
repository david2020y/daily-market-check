# daily-market-check v2 — 部署三步

1. 新建 GitHub 私有仓库，把本文件夹全部内容推上去（含 `.github/workflows/daily.yml`）。
2. 仓库 Settings → Actions → General → Workflow permissions 选 "Read and write"。
   可选：Settings → Secrets 加 `TG_TOKEN`、`TG_CHAT`（Telegram 推送）；Variables 加 `POSITION=long`（有仓时）、`RISK=1`。
3. Actions 页手动 Run 一次 → 仓库出现 `docs/latest.md` 与 `docs/latest.json`。
   之后每天 08:15（新加坡）自动更新。原始地址：
   `https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/docs/latest.md`
   把这个地址填进「每日加密简报」任务提示词第 0 步。

同一文件夹压成 zip 也可上传到 Claude Skills，覆盖旧版 daily-market-check（对话里说"看盘"即跑）。
