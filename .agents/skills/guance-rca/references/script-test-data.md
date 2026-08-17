# 脚本测试数据说明

本文档说明 `scripts/series_windows.py` 和 `scripts/normalize_owl_series.py` 的测试数据、运行命令和可观察行为。测试数据放在 `testdata/`，用于理解算法行为，不依赖真实观测云环境。

## 文件说明

- `testdata/normalized-latency-series.json`：已经归一化的耗时时序。`p99_duration` 有两段异常，`avg_duration` 只有轻微抬升，`request_count` 基本稳定。
- `testdata/normalized-chunks-overlap.json`：模拟分段查询后的重叠数据，用于观察 `stitch` 对重复时间点的处理。
- `testdata/owl-table-result.json`：模拟 `owl.data.query` 返回 `columns/rows` 表格，用于测试归一化脚本。
- `testdata/owl-native-series-result.json`：模拟 `owl.data.query` 已经返回时序结构的情况，用于测试归一化脚本对数组点和 ISO 时间字符串的处理。
- `testdata/normalized-noisy-series.json`：异常点比例过高，用于观察 `analysis_status=too_noisy`。
- `testdata/normalized-sparse-series.json`：数据点太少，用于观察 `analysis_status=too_sparse`。
- `testdata/normalized-similarity-series.json`：整体 p99 与多个服务 p99 的对比样例，用于观察趋势相似性评分。
- `testdata/owl-complex-payload.json`：复杂嵌套 owl payload，用于观察 `--inspect-shape` 输出。
- `testdata/owl-real-latency-trend-sample.json`：来自真实 `owl.data.query` 文件内容的耗时趋势样例，字段名保持 `avg(duration)`、`percentile(duration, 99)` 等真实形态。
- `testdata/owl-query-file-wrapper.json`：模拟 `owl.data.query` 外层返回，仅包含数据文件路径，用于验证归一化脚本自动读取文件。
- 真实 `BY service` 查询可能把 tag 编进字段名，例如 `percentile(duration, 99){"service":"vota-api-core"}`；归一化脚本会拆成 `name=percentile(duration, 99)` 和 `tags.service=vota-api-core`。
- `testdata/chart-normalized-latency-series.txt`：文本趋势图输出快照。

## series_windows.py chunk

示例：

```bash
guance-rca/scripts/series_windows.py chunk \
  --start-ms 1767180000000 \
  --end-ms 1767183600000 \
  --interval-ms 60000 \
  --target-points 12 \
  --max-points 18 \
  --overlap-points 2
```

真实行为：

- 如果总点数不超过 `max_points`，只输出 1 个 chunk。
- 如果超过 `max_points`，按 `target_points * interval_ms` 切段。
- 相邻 chunk 会重叠 `overlap_points * interval_ms`，便于后续抵消边界效应。
- 输出只描述时间切片，不查询数据。

## series_windows.py stitch

示例：

```bash
guance-rca/scripts/series_windows.py stitch \
  guance-rca/testdata/normalized-chunks-overlap.json
```

真实行为：

- 按 `name + tags` 合并同一条时序。
- 按 `ts` 去重。
- 默认 `--prefer last`，重叠点采用后一个 chunk 的值。
- `--prefer first` 会保留先出现的值。
- `--prefer mean` 会对两个重叠值取均值。

本测试数据有 2 个重复时间点：

- `1767180300000`：第一个 chunk 为 `121`，第二个 chunk 为 `126`。
- `1767180360000`：第一个 chunk 为 `480`，第二个 chunk 为 `482`。

因此默认输出中：

- `meta.duplicate_points` 应为 `2`。
- `1767180300000` 的值应为 `126`。
- `1767180360000` 的值应为 `482`。

## series_windows.py detect

示例：

```bash
guance-rca/scripts/series_windows.py detect \
  --input guance-rca/testdata/normalized-latency-series.json \
  --threshold 3.5 \
  --window-points 8 \
  --min-baseline-points 5
```

真实行为：

- 默认只检测名称匹配 `avg|max|p75|p90|p95|p99|duration|latency|elapsed|response|cost` 的时序。
- `request_count` 不会被默认规则选中，因为它是辅助信号，不是耗时指标。
- 默认 `--profile auto` 会按点数选择检测参数：短窗口使用更少基线点，避免漏掉靠近开头的尖峰；长窗口使用更稳的滚动窗口。
- 默认每个点会用前后邻近点的 rolling median 作为基线，更贴近离线图表中识别局部尖峰的方式；如需复现旧版在线检测口径，使用 `--baseline-mode past`。
- 波动尺度优先使用 MAD；当 MAD 太小时，使用 `baseline * min_relative_scale` 或 `min_abs_scale` 作为下限，避免稳定时序被轻微波动误判。
- 满足硬阈值的相邻异常点会合并为窗口；多个指标在同一窗口达到软阈值时，也会作为候选窗口输出，用于捕捉 `avg`、`p75`、`p99` 同步轻中度抬升的情况。
- 候选窗口排序会给跨指标共振加分；默认 `--cross-metric-bonus 2.0`，让多条耗时指标同时抬升的窗口优先于单指标孤立尖峰。
- 输出 `meta.analysis_status`：
  - `ok`：候选异常段适合继续自动下钻。
  - `no_anomaly`：没有超过阈值的异常段。
  - `too_noisy`：候选窗口过多或异常点比例过高，应停止自动下钻。
  - `too_sparse`：点数太少，应停止自动下钻。

对 30 个点以内的短时序，`auto` 通常等价于 `--window-points 5 --min-baseline-points 3`。如果要复现旧版长窗口默认行为，使用 `--profile standard --baseline-mode past --soft-threshold 3.5`。

本测试数据的预期现象：

- 第一段候选窗口应覆盖 `1767180600000` 到 `1767180780000` 附近，对应 `p99_duration` 从约 `120ms` 升到 `480ms+`。
- 第二段候选窗口应覆盖 `1767181080000` 到 `1767181260000` 附近，对应 `p99_duration` 升到约 `300ms`。
- `avg_duration` 只有小幅抬升，通常不会主导异常窗口。
- `request_count` 稳定，因此不应成为异常原因。

解读建议：

- `candidate_windows[].max_score` 是相对当前 rolling baseline 的稳健分数，不是业务严重等级。
- `confidence` 是算法置信度，只说明时序异常是否清晰，不等同于 RCA 结论置信度。
- 在真实 RCA 中，算法输出只用于选候选时间窗，根因仍需通过服务、接口、trace/span 和跨域证据确认。

## series_windows.py chart

示例：

```bash
guance-rca/scripts/series_windows.py chart \
  --input guance-rca/testdata/normalized-latency-series.json \
  --threshold 3.5 \
  --window-points 8 \
  --min-baseline-points 5 \
  --width 48 \
  --timezone Asia/Shanghai
```

真实行为：

- 输出纯文本趋势图。
- `plot` 行用 `#` 标出异常段。
- `mark` 行用 `A1`、`A2` 标注异常段编号。
- `windows` 列出每个异常段的时间范围、分数和算法置信度。
- 默认用 UTC 输出时间；用户给定东八区时间时应显式加 `--timezone Asia/Shanghai`。
- 该命令的预期输出保存在 `testdata/chart-normalized-latency-series.txt`。

## series_windows.py similarity

示例：

```bash
guance-rca/scripts/series_windows.py similarity \
  --input guance-rca/testdata/normalized-similarity-series.json \
  --candidate-tag-key service \
  --top 3
```

真实行为：

- 默认选择名称最像 p99、且 tags 最少的时序作为目标，因此会选整体 `percentile(duration, 99)`。
- 候选时序必须和目标时序有足够多相同时间点，默认至少 5 个。
- 算法先对目标和候选做 median/MAD 稳健标准化，再计算值相关性 `value_corr` 和变化相关性 `delta_corr`。
- `score` 只累加正相关部分，避免反向趋势被排到前面。

本测试数据的预期现象：

- `checkout-api` 应排名第一，因为它和整体 p99 在两段异常上同步升高。
- `billing-api` 基本平稳，得分应接近 0。
- `inventory-api` 有尖峰但时间错位，得分应低于 `checkout-api`。

在真实 RCA 中，`similarity` 用于筛选贡献者，不直接证明根因。网关、异步调用和共享下游依赖仍需结合完整 trace 解释。

## 噪声和稀疏数据

持续抬升示例：

```bash
guance-rca/scripts/series_windows.py detect \
  --input guance-rca/testdata/normalized-noisy-series.json
```

预期行为：

- 默认 `centered` 基线更关注局部尖峰，这类长平台可能不输出候选窗口。
- 若要识别“从正常水位切到持续高水位”的入口点，使用 `--baseline-mode past`。

稀疏数据示例：

```bash
guance-rca/scripts/series_windows.py detect \
  --input guance-rca/testdata/normalized-sparse-series.json
```

预期行为：

- `meta.analysis_status` 应为 `too_sparse`。
- RCA 应停止自动下钻，并请求用户缩小时间范围或补充上下文。

## normalize_owl_series.py 表格归一化

示例：

```bash
guance-rca/scripts/normalize_owl_series.py \
  --input guance-rca/testdata/owl-table-result.json \
  --tag-field service \
  --tag-field env \
  --strict
```

真实行为：

- 识别 `time` 作为时间字段。
- 将数值列转换为时序。
- `service` 和 `env` 被保留为 tags。
- `status_code` 会被默认跳过，避免把状态码误当成趋势指标。
- 该测试数据应生成 8 条时序：2 个服务乘以 4 个数值指标，分别是 `avg_duration`、`p99_duration`、`request_count`、`error_count`。

## normalize_owl_series.py 原生时序归一化

示例：

```bash
guance-rca/scripts/normalize_owl_series.py \
  --input guance-rca/testdata/owl-native-series-result.json \
  --strict
```

真实行为：

- 保留已有 `name` 和 `tags`。
- 支持 `[timestamp, value]` 数组点。
- 支持 `{"time": "...", "value": ...}` 对象点。
- ISO 时间字符串会被转换为 13 位毫秒时间戳。

## normalize_owl_series.py 结构检查

示例：

```bash
guance-rca/scripts/normalize_owl_series.py \
  --input guance-rca/testdata/owl-complex-payload.json \
  --inspect-shape \
  --inspect-max-depth 4 \
  --inspect-max-items 6
```

真实行为：

- 不做归一化，只输出 payload 的结构摘要。
- 对疑似可归一化对象标记 `normalization_hint`，例如 `series_candidate` 或 `table_candidate`。
- 用于真实 owl 输出结构未知时的第一步调试。

## normalize_owl_series.py 读取 owl 文件包装结果

示例：

```bash
guance-rca/scripts/normalize_owl_series.py \
  --input guance-rca/testdata/owl-query-file-wrapper.json \
  --strict
```

真实行为：

- 识别外层 `file.absolutePath` 或 `file.path`。
- 自动读取指向的数据文件。
- 再从数据文件的 `data.items` 中归一化真实时序。

## 组合验证流程

可以用以下流程模拟真实使用方式：

```bash
guance-rca/scripts/normalize_owl_series.py \
  --input guance-rca/testdata/owl-table-result.json \
  --tag-field service \
  --tag-field env \
  --strict \
| guance-rca/scripts/series_windows.py detect \
  --threshold 3.5 \
  --window-points 5 \
  --min-baseline-points 3 \
  --min-points 4
```

这个组合流程展示了从 owl 风格结果到异常窗口检测的完整路径。由于 `owl-table-result.json` 只有 4 个点，结果更适合验证管道是否通，而不是评估异常检测稳定性；观察检测算法建议使用 `normalized-latency-series.json`。
