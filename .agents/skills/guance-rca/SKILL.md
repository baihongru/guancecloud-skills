---
name: guance-rca
description: 当 Codex 需要使用 owl CLI 对观测云 APM 耗时异常做根因分析时使用此技能，包括耗时尖峰、慢请求、duration 上升、响应时间异常、trace/span 下钻、DQL 校验、候选异常时间窗识别，以及基于证据的 RCA 报告。适用于观测云、owl、APM、耗时升高、慢请求、latency RCA、根因分析。
---

# Guance RCA

使用此技能执行只读的观测云 APM 耗时 RCA。目标是产出基于证据的诊断结论，而不是堆叠原始 `owl` 输出。

## 必读资料

- 在编写或执行任何 DQL 前，先阅读 `references/owl-dql-rules.md`。
- 在开始 RCA 前，先阅读 `references/latency-rca-workflow.md` 和 `references/query-playbook.md`。
- 在分析完整 span 样本前，必须阅读 `references/trace-field-guide.md`。
- 在选择跨域求证路径前，阅读 `references/span-evidence-guide.md`。
- 将 `耗时异常上升分析最佳实践.pdf` 作为方法论来源；只有当引用资料缺少细节时，才从 PDF 中加载或抽取内容。

## 硬性规则

- 除非用户明确要求写操作，否则只以只读方式使用 `owl`。此技能不应创建监控器、仪表盘、事件、评论或外部事件。
- 每次执行 `owl` 任务前，使用 `owl -h`、`owl list -f json` 和 `owl show <tool>` 检查真实工具能力。
- 编写 DQL 前先发现 namespace、source 和字段。按需使用 `owl.apm.list`、`owl.metric.list`、`owl.logging.list`、`owl.data.show_dql_namespace` 以及相关发现工具。
- 每条手写 DQL 都必须用 `owl.data.check_dql` 校验。只有校验响应确认 `valid=true` 后，才能用 `owl.data.query` 执行。
- 所有 `owl exec` 调用使用 JSON 参数：`owl exec <tool> -p '{...}'`。不要使用非 JSON 参数模式。
- `references/query-playbook.md` 是可执行查询剧本；执行时必须替换占位符、先校验 DQL，再查询。
- 如果 DQL 校验失败，最多重写并重新校验 2 次。仍失败时，停止该查询分支，并在报告中记录失败 DQL、失败原因和替代路径。
- 最终报告中的相对时间范围必须换算为绝对时间戳。
- 对长时间、高分辨率窗口，必须先切分时间范围；除非用户明确接受慢查询或采样结果，否则不要一次性查询 24 小时、1 分钟粒度的数据。
- 明确说明结果是否为空、被截断、发生采样、由分段拼接而来，或受到工具和网络失败影响。

## 工具

- 使用 `scripts/series_windows.py chunk` 为趋势查询规划安全的时间切片。
- 使用 `scripts/normalize_owl_series.py` 将 `owl.data.query` 的外层 JSON 或数据文件归一化为通用时序格式；外层 payload 包含 `file.absolutePath` 时脚本会自动读取数据文件。
- 使用 `scripts/series_windows.py stitch` 合并分段时序，并按重叠点去重。
- 使用 `scripts/series_windows.py detect` 从归一化耗时时序中产出候选异常时间窗和 `analysis_status`。
- 使用 `scripts/series_windows.py chart` 输出纯文本趋势图，并标记 `[A1]`、`[A2]` 等异常段。
- 使用 `scripts/series_windows.py similarity` 对整体趋势与服务/资源趋势做相似性排序，辅助找贡献者。
- 使用 `scripts/normalize_owl_series.py --inspect-shape` 检查真实 `owl.data.query` payload 结构，辅助调试归一化。
- 需要理解脚本算法行为或运行离线样例时，阅读 `references/script-test-data.md` 并使用 `testdata/` 下的测试数据。

通用归一化时序格式：

```json
{
  "series": [
    {
      "name": "p99_duration",
      "tags": {"service": "checkout"},
      "points": [{"ts": 1767181200000, "value": 523.4}]
    }
  ]
}
```

## RCA 流程

1. 确认服务、环境和绝对时间范围。若只有宽泛问题时间，先按完整范围做粗粒度趋势检测。
2. 按 `references/query-playbook.md` 执行固定前置查询：整体耗时趋势、span/entry 请求数量、status 分布。
3. 对趋势结果做归一化、拼接、异常检测和文本图输出。若 `analysis_status=too_noisy` 或 `too_sparse`，停止自动下钻，请用户指定关注时间段。
4. 对每个可分析异常段，使用服务/资源 p99 趋势查询和 `series_windows.py similarity` 找候选服务与入口接口。
5. 采样代表性慢 span；在读取 `trace-field-guide.md` 后，按 `trace_id` 拉取完整 trace，还原调用树和慢环节。
6. 根据 `span-evidence-guide.md` 判断异常语义，选择依赖实例影响范围和跨域证据查询。
7. 报告先写事实，再写推断。只有当 trace/span 证据得到至少一个独立数据域或运维确认支持时，才使用“已确认”；否则使用“疑似”或“待验证”。

## 报告要求

默认报告路径：调用方工作目录下的 `reports/guance-rca-<timestamp>.md`。

报告应包含：

- 时间范围、时区，以及所有收敛后的异常时间窗。
- 纯文本趋势图，按 `[A1]`、`[A2]` 标注异常段。
- 查询策略、DQL 校验状态、分段策略，以及已知数据质量问题。
- 影响范围：受影响服务、资源、依赖实例、请求和错误行为，以及代表性样本。
- 每段异常的固定查询结果、span 属性解释、跨域证据、判断、置信度、缺口和后续动作。
- 最终回复只给出简短主结论和报告路径；不要在对话中粘贴大段 JSON 或完整查询输出。
