# 链路字段指南

模型在分析完整 span 样本前必须阅读本文。目标是帮助模型理解字段语义，而不是机械套查询模板。

## 调用树字段

- `trace_id`：同一条调用链的唯一标识。所有属于同一 Trace 的 span 应共享相同 `trace_id`。
- `span_id`：Trace 内单个 span 的唯一标识。
- `parent_id`：当前 span 的父 span。根 span 通常没有父级或 `parent_id=0`。
- `start_time`：span 开始时间。结合 `duration` 可判断父子 span 的时序关系和重叠关系。
- `duration`：span 持续时间。判断慢请求、慢依赖、父子耗时贡献的核心字段。

使用方式：

- 用 `span_id` 和 `parent_id` 还原调用树。
- 用 `duration(child) / duration(parent)` 判断慢子 span 是否能解释父 span。
- 用 `start_time` 判断慢点是否发生在异常窗口内，以及多个慢 span 是否同时出现。

## 服务与资源字段

- `service`：上报 span 的服务名。中间件也可能作为服务出现，例如 Redis、MySQL、Kafka。
- `env`：服务部署环境。
- `version`：服务版本。
- `resource`：资源名称，可能是 HTTP route、RPC 方法、数据库命令、Redis 命令、MQ topic 或内部方法。
- `operation`：执行该 span 的模块或操作类型，例如 HTTP、Redis、SQL、Servlet、gRPC。
- `span_type`：span 类型。
  - `entry`：服务入站请求，适合分析服务接口耗时。
  - `exit`：服务出站请求，适合分析下游依赖耗时。
  - `local`：服务内部处理，适合分析应用内部方法、线程池或本地逻辑。

使用方式：

- 分析服务对用户或上游的响应耗时时，优先看 `span_type=entry`。
- 分析依赖调用时，优先看 `span_type=exit` 或中间件服务 span。
- `resource` 的基数可能很高。只有在服务或依赖已收敛后，再按 `resource` 做深入聚合。

## 状态与错误字段

- `status`：span 状态。`ok` 通常代表无显性错误，非 `ok` 是显性异常信号。
- `error_type`：错误类型，例如异常类名。
- `error_message`：错误消息。
- `error_stack` 或类似字段：错误堆栈。

使用方式：

- `status != ok` 是明确异常，但 `status=ok` 不代表性能正常。
- 超时、取消、重试、连接拒绝、慢查询等信息可能只在错误消息或日志里。
- 如果错误集中在某个依赖实例、版本或 pod，应优先沿这些字段求证。

## 依赖定位字段

- `base_service`：中间件或依赖 span 的调用方服务，常用于判断依赖影响范围。
- `db_host`：数据库或 Redis 主机，适合定位实例级问题。
- `db_name`、`db_system`、`db_statement`：数据库相关字段。
- peer host、endpoint、remote service：HTTP/RPC 下游定位字段，字段名以实际数据为准。
- topic、queue、broker、consumer group：消息队列定位字段，字段名以实际数据为准。

使用方式：

- Redis、MySQL 这类依赖本应很快；即使 `status=ok`，`duration` 明显上涨也可能是关键异常。
- 如果同一 `db_host` 影响多个 `base_service`，更像共享依赖问题。
- 如果只有一个 `base_service` 调用某个依赖变慢，也可能是该调用方新增了高成本命令或不合理访问模式。

## 环境与发布字段

- `env`：部署环境，避免把测试、预发和生产混在一起。
- `version`：服务版本，适合判断是否和发布相关。
- `pod_name`、`host`、`container_name`：基础设施定位字段。
- region、az、cluster、namespace：部署拓扑字段，字段名以实际数据为准。

使用方式：

- 异常集中在单个 `version`，优先查发布和版本差异。
- 异常集中在单个 `pod_name` 或 `host`，优先查基础设施和容器状态。
- 异常只出现在某个 `env`，不要扩大结论到其他环境。

## 读 span 样本的顺序

1. 先看 `trace_id`、根 span、入口 `entry` span，确认这条链路代表哪个服务接口。
2. 看 `duration` 最大的子 span，判断它是否能解释父级慢。
3. 看慢子 span 的 `service`、`resource`、`operation`、`span_type`，判断是内部逻辑还是外部依赖。
4. 看 `status`、错误字段和超时迹象，判断是否有显性错误。
5. 看 `base_service`、实例字段、`pod_name`、`env`、`version`，判断影响范围和可能关联。
6. 再决定补查日志、指标、事件、基础设施或 profiling。

## 请求量与 Span 量

- 一个 trace 至少包含一个 span，所以 span 数量和请求数量不是同一个概念。
- `count(trace_id)` 统计所有 span 时，更接近 span 数量；如果错误重试、缓存失效或下游调用放大，span 数量可能明显上涨，但入口请求数量不一定上涨。
- 将 `span_type='entry'` 后再统计 `count(trace_id)`，更接近每个服务接收到的入口请求数量，因为 entry 是请求进入服务后的第一个 span。
- 按 `status` 聚合入口请求和全部 span，可以快速区分“请求量上涨”“调用链内部 span 放大”“错误率上涨”这几类现象。

## 异步调用提示

- 不要假设所有慢 span 都影响业务响应耗时。
- 例如程序埋点后异步发送 Kafka，Kafka span 可能很慢，但如果它不在用户请求的同步关键路径上，就不一定解释入口接口耗时。
- 判断是否影响业务耗时时，要结合父子关系、`start_time`、`duration`、span 是否在 entry 的同步路径内，以及调用语义。
