# Owl 与 DQL 规则

即使存在其他与 owl 相关的技能，也使用本规则。本技能是独立技能，并将 DQL 校验视为强制闸门。

官方语法参考：https://docs.guance.com/dql/

## 启动检查

执行：

```bash
owl -h
owl list -f json
owl show <tool> -f json
```

需要导出完整函数 schema 时，使用 `owl schema -f json`。

## DQL 前置发现

编写手写 DQL 前：

1. 调用 `owl.data.show_dql_namespace`，确认 namespace 以及是否支持 index。
2. 使用数据域发现工具：
   - APM/链路：`owl.apm.list`
   - 指标：`owl.metric.list`
   - 日志：`owl.logging.list` 和 `owl.log_index.list`
   - RUM：`owl.rum.list`
   - 网络：`owl.network.list`
   - Profiling：`owl.profile.list`
3. 对选定 source 使用 `mode=field` 发现字段；对指标使用 `mode=tag` 发现标签。
4. 当语法或函数不确定时，使用 `owl.data.search_dql_docs` 搜索本地 DQL 文档。

不要因为某个 source、字段、index 或 namespace 出现在示例中，就假设它在当前环境中存在。

## 强制 DQL 闸门

对每条手写 DQL：

1. 编写查询。
2. 执行 `owl exec owl.data.check_dql -p '{"query_text":"..."}'`。
3. 只有结果明确确认 `valid=true` 时，才能继续。
4. 如果校验失败，重写并重新校验。最多允许重写 2 次。
5. 如果仍然校验失败，停止该分支并报告：
   - 最后一版 DQL，
   - 校验错误，
   - 该分支为什么必要，
   - 替代路径。
6. 使用 `owl.data.query` 执行已校验的 DQL。

Shell 退出码为 `0` 并不充分。必须读取返回 payload，并确认查询确实成功。

## owl 传参规则

- 所有 `owl exec` 调用统一使用 JSON 方式传参：`owl exec <tool> -p '{...}'`。
- 不使用非 JSON 参数模式，因为当前版本存在参数解析 bug。
- `owl.data.query` 经常返回 `file.absolutePath`，必须继续读取该文件内容，不能把外层 `success=true` 当成查询结果本身。
- 已观察到的 `owl.data.query` 文件内容形态为 `{"success":true,"data":{"items":[...]},...}`，真实数据在 `data.items` 内。

## 查询剧本使用规则

- `references/query-playbook.md` 给出耗时 RCA 的固定查询剧本；执行前替换占位符，并逐条走 `owl.data.check_dql`。
- 查询剧本中的命令可以直接作为执行形态使用，但不能跳过 namespace/source/字段发现和 DQL 校验。
- 每次真实运行后，报告记录实际命令、DQL 校验结果、返回结构、归一化命令和是否可用于结论。
- 如果真实返回结构无法被 `normalize_owl_series.py` 处理，先用 `normalize_owl_series.py --inspect-shape` 输出结构摘要，再调整归一化逻辑。

## 查询策略

- 优先使用小而明确的查询，避免宽泛扫描。
- 对长时间窗口，先查询粗粒度趋势，再放大到候选时间窗。
- 对高分辨率窗口，使用 `scripts/series_windows.py chunk` 并拼接结果。
- 在目标服务、资源或依赖实例尚未收敛前，避免按高基数字段聚合。
- 空结果也是证据。记录时间范围、namespace、source、过滤条件，以及查询是否经过校验。
- 当查询返回数据文件路径时，读取该文件并检查 payload；不要只凭路径假设查询成功。

## 可改写的 DQL 形态

这些形态用于临场扩展查询。在 `query` 前必须先完成发现和 `check_dql`。耗时 RCA 的固定查询优先使用 `references/query-playbook.md`。

整体 APM 趋势：

```text
<APM_NAMESPACE>::<APM_SOURCE>:(avg(duration), max(duration), percentile(duration, 75), percentile(duration, 90), percentile(duration, 99), count(*)) { <filters> } [<range>::<step>]
```

服务排名：

```text
<APM_NAMESPACE>::<APM_SOURCE>:(avg(duration), percentile(duration, 99), max(duration), count(*)) { <filters> } [<window>] BY service
```

入口资源排名：

```text
<APM_NAMESPACE>::<APM_SOURCE>:(avg(duration), percentile(duration, 99), max(duration), count(*)) { service = "<service>" AND span_type = "entry" } [<window>] BY resource
```

依赖影响：

```text
<APM_NAMESPACE>::<APM_SOURCE>:(avg(duration), percentile(duration, 99), max(duration), count(*)) { service = "<dependency_service>" AND <instance_filter> } [<window>] BY base_service, resource, status
```

慢样本搜索：

```text
<APM_NAMESPACE>::<APM_SOURCE>:(*) { service = "<service>" AND resource = "<resource>" AND duration > <threshold> } [<window>]
```

使用 `BY`，不要使用 SQL 风格的 `GROUP BY`。

## 报告记录

记录：

- 相关时记录 owl 版本或工具 schema 版本。
- 使用过的发现工具。
- DQL 校验状态和重试次数。
- 最终 DQL 或简要 DQL 摘要。
- 时间范围和时区。
- 分片大小、重叠范围和拼接方法。
- 采样、截断、超时、空结果或网络错误。
