# OpenAI News 抓取样例：关键词 *video model*

- **列表页**：https://openai.com/news
- **方式**：Playwright 滚动列表 + 逐篇 Playwright 打开详情，BeautifulSoup 解析
- **列表阶段收集链接数**（目标上限 100）：**11**（当次页面滚动后 DOM 中可见的文章链）
- **详情扫描篇数**：**11**（与列表链数量一致；脚本对详情有 `max_detail=60` 上限，本次未触顶）
- **命中关键词的条目数**：**1**（展示上限 25）

> **关键词规则**：英文含 `video model` 或同时含 `video` 与 `model`；或中文同时含「视频」「模型」。

---

## 命中条目

### 1. Creating with Sora safely

- **链接**：https://openai.com/index/creating-with-sora-safely
- **发布时间（页面解析）**：2026-03-25T00:00

**摘要**

To address the novel safety challenges posed by a state-of-the-art video model as well as a new social creation platform, we’ve built Sora 2 and the Sora app with safety at the foundation. Our approach is anchored in concrete protections.

**正文片段**

OpenAI March 23, 2026 Safety Creating with Sora safely Read previous version Loading… Share The Sora 2 model and the Sora app offer state-of-the-art video generation with a new way to create together, and we’ve made sure safety is built in from the very start. Our approach is anchored in concrete protections: Distinguishing AI content . Every video generated with Sora includes both visible and invisible provenance signals. All Sora videos also embed C2PA metadata—an industry-standard signature—and we maintain internal reverse-image and audio search tools that can trace videos back to Sora with high accuracy, building on successful systems from ChatGPT image generation and Sora 1. Many outputs also carry visible, dynamically moving watermarks which include the name of the creator. Image-to-video with real person likeness . As we continue to strengthen Sora’s guardrails, we’re enabling more creative expression and connection, including letting people create videos from photos of family and friends. Users can upload images with people to make videos in Sora, after attesting that they have consent from people featured and rights to upload the media. Image-to-video generations with p...

---

## 说明

- 本次为本地一次性脚本跑数（`project/scripts/run_openai_keyword_sample.py`），**不等同**于 Streamlit 管线里的时间窗与 `per_source` 截断逻辑。
- 若列表仅解析到少量链接，可适当增大脚本中的 `target`、滚动参数，或检查网络/无头是否被站点限流。
