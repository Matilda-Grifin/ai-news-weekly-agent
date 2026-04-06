# Playwright 爬虫实测（2026-04-06）

测试目标（按你给的 4 个）：
- AnthropicNewsCrawler（`Anthropic Blog(OpenRSS)`）
- HuggingFaceBlogCrawler（`Hugging Face Blog`）
- TechCrunchAICrawler（`TechCrunch AI`）
- VentureBeatAICrawler（`VentureBeat AI`）

测试命令：
- `python project/scripts/benchmark_playwright_site_crawlers.py --only "<crawler 名称>"`

测试参数（脚本默认）：
- `per_source=8`
- `window_hours=24`
- `allow_insecure_ssl=True`

---

## 结果总览

| Crawler | 是否抓到内容 | 抓到条数 | 单次耗时 |
|---|---:|---:|---:|
| Anthropic Blog(OpenRSS) | 否 | 0 | 5.05s |
| Hugging Face Blog | 否 | 0 | 5.20s |
| TechCrunch AI | 是 | 1 | 386.93s |
| VentureBeat AI | 是 | 1 | 132.09s |

---

## 并行/串行说明

- `benchmark_playwright_site_crawlers.py` 内部是按 `for c in crawlers:` 逐个执行，**串行**。
- 每个 crawler 在详情抓取阶段也是 `for href in links:` 逐条处理，**串行**。
- 也就是说：默认运行方式是 **串行 + 串行（crawler 间串行，crawler 内详情页也串行）**。

---

## 跑到的东西（按本次实测）

- Anthropic Blog(OpenRSS)：无（0 条）
- Hugging Face Blog：无（0 条）
- TechCrunch AI：有（1 条）
- VentureBeat AI：有（1 条）

> 备注：`TechCrunch AI` 本次非常慢（~387s），与其抓取逻辑里“列表页请求 + 可能回退 Playwright 滚动 + 详情页逐条抓取”相符；网络抖动/连接重置会进一步拉长时间。

---

## 原始控制台摘要

- `Anthropic Blog(OpenRSS): 0 items in 5.05s`
- `Hugging Face Blog: 0 items in 5.20s`
- `TechCrunch AI: 1 items in 386.93s`
- `VentureBeat AI: 1 items in 132.09s`
