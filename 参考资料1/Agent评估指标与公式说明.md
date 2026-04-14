# Agent评估指标与公式说明（行业通行版，适配当前固定流水线）

本版按你的反馈修正：不把项目硬说成“自主规划Agent”，而是按“固定流程新闻任务系统”来评估。

---

## 1. 先回答你提的4个问题

### 1.1 固定流程下，还要不要`Step效率`？

如果你的执行链路是固定编排、无LLM自主决策，`Step效率`不是优先指标，可以降级或移除。  
行业上对这类系统更常用的是：
- 阶段成功率（抓取成功率、解析成功率、去重后保留率）；
- 信源覆盖率；
- 结果新鲜度；
- 重试恢复率；
- 时延分位数（P50/P95）。

结论：你这个项目现阶段可以不以“步数”作为核心看板指标。

### 1.2 `N_success`可能长期接近0，怎么办？

你的判断非常现实。因为多信源场景常有部分源失败，使用“全成功”会失真。  
建议改成三态结果，并单独区分“无新闻”与“执行失败”：
- `SUCCESS_WITH_NEWS`：流程成功，且返回新闻数 >= 最小阈值；
- `SUCCESS_NO_NEWS`：流程成功，但返回0条且各环节执行正常（例如当天确实无新内容）；
- `FAILED`：流程未完成或结果不可用（超时、关键模块异常、字段不合格）。

### 1.3 “自动修复错误”在当前系统怎么定义？

不使用“自反思修复”这种Agent语义，改成可观测的工程语义：
- 错误发生后通过已有机制恢复（重试、切换备用源、跳过坏源继续聚合）并最终产出可用结果，算“恢复成功”；
- 没有这类机制就不要统计该指标，避免造概念。

### 1.4 `P95 Latency`大白话

把一天所有请求耗时从快到慢排队：
- 中间那个是`P50`（一半请求比它快）；
- 排到95%位置那个是`P95`（95%的请求都不慢于它，只有最慢5%更慢）。

所以`P95=12s`的意思是：100个请求里，大约95个都能在12秒内完成。  
它比平均值更能反映“有没有一批用户特别慢”。

---

## 2. 北极星指标（修正版）

## 2.1 北极星：有效交付率 EDR（Effective Delivery Rate）

定义：
- 总请求数：`N_all`
- 有效交付数：`N_delivered`

公式：
- `EDR = N_delivered / N_all`

其中`N_delivered`建议定义为：
- 状态为`SUCCESS_WITH_NEWS`，且
- 质量达标（字段完整率、去重、时延、合规通过）。

说明：`SUCCESS_NO_NEWS`不计入失败，但不计入“有效交付”。

---

## 3. 指标与公式（可直接上看板）

## 3.1 任务结果分层（先解决“0成功率”问题）

设：
- `N_with_news`: `SUCCESS_WITH_NEWS`数量
- `N_no_news`: `SUCCESS_NO_NEWS`数量
- `N_failed`: `FAILED`数量
- `N_all = N_with_news + N_no_news + N_failed`

公式：
- `Delivery Rate = N_with_news / N_all`
- `No-News Rate = N_no_news / N_all`
- `Failure Rate = N_failed / N_all`
- `Pipeline Success Rate = (N_with_news + N_no_news) / N_all`

解释：
- `No-News`不是失败；
- 真失败看`Failure Rate`；
- 业务交付看`Delivery Rate`。

## 3.2 信源可用性与覆盖（爬虫场景核心）

设单任务`i`：
- 计划抓取信源数：`S_plan_i`
- 实际成功信源数：`S_ok_i`

公式：
- `Source Coverage_i = S_ok_i / S_plan_i`
- `Avg Source Coverage = (1/M) * Σ(Source Coverage_i)`

按信源`k`统计稳定性：
- `Source Success Rate_k = N_source_ok_k / N_source_attempt_k`

## 3.3 解析质量与数据完整度

设：
- `N_fetch_ok`: 抓取成功页面数
- `N_parse_ok`: 解析成功页面数
- `N_record`: 产出新闻条数
- `N_record_required_ok`: 必填字段完整条数

公式：
- `Parse Success Rate = N_parse_ok / N_fetch_ok`
- `Field Completeness = N_record_required_ok / N_record`

## 3.4 去重与有效内容率

设：
- `N_raw`: 去重前条数
- `N_unique`: 去重后条数

公式：
- `Dedup Keep Rate = N_unique / N_raw`
- `Duplicate Ratio = 1 - Dedup Keep Rate`

## 3.5 错误恢复（工程语义，不用“自我修复”）

设：
- `N_recoverable_error`: 可恢复错误次数（超时、429、单源失败等）
- `N_recovered`: 通过重试/回退后恢复成功次数

公式：
- `Recovery Rate = N_recovered / N_recoverable_error`

补充一个更业务的结果指标：
- `Fallback Save Rate = N_task_saved_by_fallback / N_task_with_primary_failure`

## 3.6 时延与成本

单任务时延：
- `L_i = t_end_i - t_start_i`

分位数：
- `P50 = percentile(L, 50)`
- `P95 = percentile(L, 95)`

成本：
- `Cost_i = C_token_i + C_api_i + C_infra_i`
- `Avg Cost = (1/M) * Σ(Cost_i)`

## 3.7 工具选择准确率（仅在存在“可选工具”时启用）

如果流程写死（每阶段只会调唯一工具），该指标恒为1，信息价值低，可不统计。  
只有在“同一子任务有多个可选工具”时才保留：

设决策点`j`：
- 选择工具：`tool_j`
- 该子任务允许集合：`A_j`

公式：
- `Tool Selection Accuracy = Σ 1(tool_j ∈ A_j) / N_decision`

工程替代指标（更实用）：
- `Wrong Tool Invocation Rate = N_wrong_tool_call / N_tool_call`

---

## 4. 行业通行做法（用于口径对齐）

在业界落地中，通常不是只看一个“成功率”，而是分三层：
- 结果层：任务交付率、失败率、无结果率；
- 系统层：P50/P95时延、成本、可用性；
- 数据层：覆盖率、解析成功率、字段完整率、新鲜度。

当系统有自主决策能力时，才强化“轨迹质量、工具选择准确率、推理质量”这类Agent指标。  
你当前系统以固定编排为主，优先按“数据流水线质量 + 服务SLO”来评估最稳妥。

---

## 5. 建议先上线的5个指标（MVP）

- `EDR`（有效交付率，北极星）
- `Failure Rate`
- `No-News Rate`
- `P95 Latency`
- `Avg Source Coverage`

这5个能直接回答：有没有交付、为什么没交付、慢不慢、是不是某些信源掉链子。

---

## 6. 最小埋点事件（支持上述公式）

- `task_started(task_id, user_id, intent, ts)`
- `source_attempt(task_id, source, ts)`
- `source_result(task_id, source, status, error_type, latency_ms, ts)`
- `parse_result(task_id, source, parse_ok, required_fields_ok, ts)`
- `dedup_result(task_id, raw_count, unique_count, ts)`
- `fallback_used(task_id, from_source, to_source, recovered, ts)`
- `task_finished(task_id, final_status, news_count, latency_ms, total_cost, violation_flag, ts)`

`final_status`建议固定枚举：
- `SUCCESS_WITH_NEWS`
- `SUCCESS_NO_NEWS`
- `FAILED`

