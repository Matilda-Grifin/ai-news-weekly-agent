# Agent harness 示例页抓取测试

运行：

```bash
cd /path/to/ai_news_skill
python3 test_harness_crawl/crawl_and_save.py
```

依赖：项目根 `.env` 中 `ARK_API_KEY`（LLM 回退用）；已安装 `playwright` 与 `chromium`。

结果写入 `out/` 目录；摘要见 `out/_summary.json`。

**本次实测（本地）**

- **Microsoft Dev Blog**、**martinfowler.com**：Playwright + trafilatura 可拿到长正文（约 1.2 万 / 7k 字量级）。
- **知乎专栏**：无登录 Cookie 时易被反爬，页面返回 JSON 错误（如 40362），**无法当公开网页一样全文抓取**；需登录态或合规数据源。LLM 回退需配置有效的 `ARK_ENDPOINT_ID`（否则 Ark 接口会 404）。
