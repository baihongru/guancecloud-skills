# 耗时 RCA 查询剧本

本文是耗时异常 RCA 的固定前置查询剧本。它把高确定性的取数步骤固化下来，让模型把主要精力放在趋势解释、span 全属性理解和跨域求证上。

## 全局规则

- 所有 `owl exec` 调用使用 JSON 参数：`owl exec <tool> -p '{...}'`。当前版本不要使用非 JSON 参数模式。
- 每条手写 DQL 必须先执行 `owl.data.check_dql`；返回明确有效后，才能执行 `owl.data.query`。
- `owl.data.query` 可能只返回 `file.absolutePath`；必须读取该文件，真实数据通常在 `data.items`。`normalize_owl_series.py` 可以直接读取外层 payload 并自动跟进文件路径。
- APM 链路 namespace 使用 `T`，source 使用 `re(\`.*\`)` 或 `RE(\`.*\`)`。
- 长时间范围按 `series_windows.py chunk` 分段查询，再归一化、拼接和检测。
- `<START_MS>`、`<END_MS>`、`<WINDOW_START_MS>`、`<WINDOW_END_MS>` 均为 13 位毫秒时间戳。

## 字段约定

- `service`：服务名。
- `env`：部署环境。
- `version`：服务版本。
- `span_type`：span 类型。`entry` 是请求进入服务的第一个 span，`local` 是服务内部处理，`exit` 是外部调用。
- `resource`：span 标识。HTTP entry span 通常是接口，local span 可能是方法名，exit span 是外部调用标识。
- `duration`：耗时。
- `status`：span 状态。`status != ok` 且存在 `error_type/error_message/error_stack` 时，通常表示当前 span 代表的代码中有未捕获异常。
- `trace_id`：trace 标识。
- `span_id`：span 标识。
- `parent_id`：父 span 标识。
- `pod_name`：发出 span 的 Pod，非 Kubernetes 部署可能没有。
- `host`：发出 span 的主机。
- `base_service`、`db_host` 等依赖字段：从慢 trace 的完整 span 样本中确认后使用。

## DQL 校验命令

对下面任一查询，把 `query_text` 替换为目标 DQL 后先执行：

```bash
owl exec owl.data.check_dql -p '{
  "query_text": "T::re(`.*`):(percentile(`duration`, 99)) [::1m]"
}'
```

如果校验失败，重写并复验，最多 2 次。仍失败则停止该查询分支，并在报告中记录失败 DQL、错误和替代路径。

## Q1：整体耗时趋势

用于判断 avg、p75、p90、p99 的整体走势，默认 1 分钟粒度。

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(avg(`duration`), percentile(`duration`, 75), percentile(`duration`, 90), percentile(`duration`, 99)) [::1m]"
}'
```

已观察到的返回外层结构：

```json
{
  "success": true,
  "type": "data",
  "file": {
    "absolutePath": "/Users/baihr/.owl/data/owl/..."
  }
}
```

已观察到的数据文件结构：

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "time": 1779953340000,
        "avg(duration)": 7912.074168885724,
        "percentile(duration, 75)": 3224.0348999013568,
        "percentile(duration, 90)": 10268.646965972126,
        "percentile(duration, 99)": 117843.1252355409
      }
    ]
  }
}
```

归一化：

```bash
guance-rca/scripts/normalize_owl_series.py \
  --input <OWL_QUERY_PAYLOAD_OR_DATA_FILE> \
  --strict
```

异常检测与趋势图：

```bash
guance-rca/scripts/series_windows.py detect \
  --input <NORMALIZED_JSON>

guance-rca/scripts/series_windows.py chart \
  --input <NORMALIZED_JSON> \
  --windows <DETECT_JSON> \
  --width 72 \
  --timezone Asia/Shanghai
```

`detect` 默认使用 `--profile auto --baseline-mode centered`：短时间窗会自动降低基线点数，并用前后邻近点识别离线图表上的局部尖峰；排序会给跨指标共振加分，优先保留 `avg`、`p75`、`p99` 同时抬升的窗口。如需复现旧版在线告警式口径，使用 `--baseline-mode past --soft-threshold 3.5`。

如果用户提供界面截图或明确给出图表粒度，必须按界面粒度补查单指标趋势。不要只用多指标合并查询决定异常窗口；`percentile` 在不同聚合粒度和不同查询形态下可能不完全一致。

20 秒 p99 示例：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99)) [::20s]"
}'
```

20 秒 avg 示例：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(avg(`duration`)) [::20s]"
}'
```

20 秒图上如果出现明确尖峰，后续 Q3/Q4 的服务和资源下钻应以尖峰附近窗口为准，例如 `15:23:40` 尖峰可先取 `15:23:00 ~ 15:25:00`。

## Q2：Span 数量、入口请求数量和 status 分布

用于判断耗时是否与请求量、错误量、调用放大或重试有关。

全部 span 数量按状态统计：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(count(`trace_id`)) BY `status`"
}'
```

全部 span 数量按 1 分钟趋势统计：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(count(`trace_id`)) [::1m] BY `status`"
}'
```

入口请求数量按状态统计。这里不用 `count distinct trace_id`，而是过滤 `span_type=entry` 后统计 `trace_id`，表示各服务收到的入口请求总和：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(count(`trace_id`)){ `span_type` IN [\"entry\"] } BY `status`"
}'
```

入口请求数量按 1 分钟趋势统计：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(count(`trace_id`)){ `span_type` IN [\"entry\"] } [::1m] BY `status`"
}'
```

分析要点：

- trace 数量平稳但 span 数量上涨，优先怀疑重试、调用放大或缓存穿透后新增下游访问。
- `status != ok` 上涨时，继续采样错误 span 并查看 `error_type/error_message/error_stack`。
- 入口请求数量上涨且耗时同步上涨时，考虑突发流量、排队、限流或下游容量问题。

## Q3：按服务查询 p99 耗时趋势

用于找到和整体 p99 异常最相似的服务。

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99)) [::1m] BY `service`"
}'
```

当异常由 20 秒图确定时，服务趋势也查询 20 秒粒度：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99)) [::20s] BY `service`"
}'
```

将 Q1 归一化结果和 Q3 归一化结果拼接到同一个 JSON 后，使用趋势相似性算法：

```bash
guance-rca/scripts/series_windows.py similarity \
  --input <COMBINED_NORMALIZED_JSON> \
  --candidate-tag-key service \
  --top 10
```

选择规则：

- 优先选择 `score` 高、`value_corr` 和 `delta_corr` 同时为正的服务。
- 只按相似度会把低流量高 p99 服务排到前面；需要再补 `count(trace_id) [::20s] BY service`，结合异常点上的服务量级判断贡献。
- 真实 `BY service` 返回可能把 tag 嵌入字段名，例如 `percentile(duration, 99){"service":"vota-api-core"}`；归一化脚本会拆成 `name=percentile(duration, 99)` 和 `tags.service`。
- 网关、BFF、facade 服务默认后置，除非慢 span 证明它自身处理耗时异常。
- 如果 avg 上升但 p99 相似服务不明显，再分别对 avg、p90 做服务维度查询和相似性比较。

## Q4：按入口资源查询 p99 趋势

用于在候选服务内找到贡献异常的接口或资源。

```bash
owl exec owl.data.query -p '{
  "start_time": <WINDOW_START_MS>,
  "end_time": <WINDOW_END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99)){ `service` = \"<SERVICE>\" AND `span_type` IN [\"entry\"] } [::1m] BY `resource`"
}'
```

如需同时观察服务内部和外部调用资源，去掉 `span_type` 过滤：

```bash
owl exec owl.data.query -p '{
  "start_time": <WINDOW_START_MS>,
  "end_time": <WINDOW_END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99)){ `service` = \"<SERVICE>\" } [::1m] BY `resource`"
}'
```

可把 Q3 中该服务的趋势和 Q4 的资源趋势拼接后比较：

```bash
guance-rca/scripts/series_windows.py similarity \
  --input <SERVICE_AND_RESOURCE_NORMALIZED_JSON> \
  --target-tag service=<SERVICE> \
  --candidate-tag-key resource \
  --top 10
```

## Q5：代表性慢 span 样本

用于拿到典型慢 span 和 `trace_id`。`<THRESHOLD>` 应来自真实耗时分布，例如 p99 附近或异常窗口内慢请求下界。

```bash
owl exec owl.data.query -p '{
  "start_time": <WINDOW_START_MS>,
  "end_time": <WINDOW_END_MS>,
  "dql_namespace": "T",
  "query_text": "T::RE(`.*`):(`*`){ `service` = \"<SERVICE>\" AND `resource` = \"<RESOURCE>\" AND `duration` > <THRESHOLD> } LIMIT 5"
}'
```

样本读取重点：

- 保留完整原始字段，不要只抽取少数字段。
- 至少覆盖最慢样本、p99 附近样本、错误样本和异常前基线样本。
- 单个 span 不足以判定根因，它只是进入完整 trace 的入口。

## Q6：按 trace_id 拉取完整链路

用于还原调用树、分析父子 span 耗时贡献和异步/同步关系。

```bash
owl exec owl.data.query -p '{
  "start_time": <WINDOW_START_MS>,
  "end_time": <WINDOW_END_MS>,
  "dql_namespace": "T",
  "query_text": "T::RE(`.*`):(`*`){ `trace_id` = \"<TRACE_ID>\" }"
}'
```

分析要点：

- 用 `span_id` 和 `parent_id` 还原调用树。
- 找出 `duration` 长、且能解释父级耗时的子 span。
- `entry` 慢说明入口服务对外表现慢；`local` 慢说明服务内部逻辑慢；`exit` 慢说明外部依赖慢。
- 异步调用要谨慎。例如异步发送 Kafka 的 span 很慢，不一定影响业务响应耗时，需要结合父子关系和调用语义判断。

## Q7：依赖实例影响范围

当完整 trace 指向 Redis、DB、HTTP/RPC 下游、Kafka 等依赖后，用慢 span 中实际存在的字段收敛查询。优先使用 `base_service`、`resource`、`status`，如果样本存在 `db_host`、`host`、`pod_name` 等实例字段，再加入过滤或分组。

按依赖资源和状态看异常窗口影响：

```bash
owl exec owl.data.query -p '{
  "start_time": <WINDOW_START_MS>,
  "end_time": <WINDOW_END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(avg(`duration`), percentile(`duration`, 99), count(`trace_id`)){ `base_service` = \"<BASE_SERVICE>\" } BY `resource`, `status`"
}'
```

按依赖资源和状态看 1 分钟趋势：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99), count(`trace_id`)){ `base_service` = \"<BASE_SERVICE>\" } [::1m] BY `resource`, `status`"
}'
```

如果慢 span 中存在实例字段，例如 `db_host`：

```bash
owl exec owl.data.query -p '{
  "start_time": <START_MS>,
  "end_time": <END_MS>,
  "dql_namespace": "T",
  "query_text": "T::re(`.*`):(percentile(`duration`, 99), count(`trace_id`)){ `base_service` = \"<BASE_SERVICE>\" AND `db_host` = \"<DB_HOST>\" } [::1m] BY `resource`, `status`"
}'
```

## Q8：跨域证据

跨域查询只在 trace/span 目标已经收敛后执行。

错误类：

- 如果 `status != ok` 且有错误字段，查询错误中心或日志聚类。
- 优先使用 `owl.errors.list`、`owl.logging.cluster_task.create/get` 或日志 DQL；具体字段从错误 span 中带出的 `service/env/trace_id/pod_name/error_message` 收敛。

基础设施类：

- 如果慢 span 集中在 `pod_name` 或 `host`，使用 `owl.infrastructure.list/get` 查询对象详情，再补指标 DQL。
- 如果集中在 `version`，查询事件和发布时间，判断是否发布相关。

依赖类：

- Redis/DB/HTTP/RPC 变慢时，先用 Q7 证明同依赖资源或实例在异常窗口内也变慢，再查对应指标、慢查询、连接数、CPU、内存或服务端日志。

Profiling 类：

- `local` span 变慢且没有下游依赖证据时，使用 `owl.profile.list` 发现 profiling 数据，再用 `owl.profiling.get_summary/parse` 分析热点。

结论分级：

- `已确认`：trace/span 证据和至少一个独立数据域或运维事实闭环。
- `疑似`：trace/span 证据强，但跨域证据不足或暂不可用。
- `待验证`：只有趋势或单条样本。
