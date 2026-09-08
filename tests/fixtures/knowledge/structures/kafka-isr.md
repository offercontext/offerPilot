# Kafka ISR 同步副本机制

Kafka 通过多副本机制保证消息持久性与可用性。本文整理 ISR（in-sync replica）的核心概念与工程意义。

## 核心定义

ISR 是 in-sync replica 的缩写，表示与 leader 副本保持同步的副本集合。每个分区有一个 leader 副本处理读写请求，其余为 follower 副本异步拉取 leader 日志。

> ISR 本质上是"足够跟上 leader"的 follower 名单，由 leader 动态维护。

follower 满足以下条件时进入 ISR：

1. 向 leader 发送 `FetchRequest` 的时间间隔低于 `replica.lag.time.max.ms`。
2. 其日志末尾偏移（log end offset）足够接近 leader。

## high_watermark 与消息可见性

`high_watermark`（HW）是所有 ISR 副本中最小的日志末尾偏移。只有偏移小于等于 HW 的消息才对消费者可见。

```python
# 伪代码：leader 更新 high_watermark
def update_high_watermark(isr_replicas):
    leo_list = [r.log_end_offset for r in isr_replicas]
    return min(leo_list)
```

HW 推进流程：

1. follower 拉取 leader 日志并写入本地。
2. follower 在下一次 `FetchRequest` 中上报自己的 LEO。
3. leader 计算 ISR 中最小 LEO，更新 HW。

## ISR 扩缩容

当 follower 超时未同步，leader 将其移出 ISR，称"缩容"（shrink）。当落后的 follower 重新追上，leader 将其加回 ISR，称"扩容"（expand）。

| 场景 | 动作 | 风险 |
|---|---|---|
| follower 落后超时 | 移出 ISR | 可用副本减少 |
| follower 重新追上 | 加回 ISR | 恢复容错能力 |
| ISR 为空且 unclean_leader_election | 选非 ISR 副本 | 可能丢消息 |

## 工程意义

- ISR 机制把"副本数"与"持久性强弱"解耦：副本可以很多，但只有 ISR 内的副本被承诺持久。
- `min.insync.replicas` 与 producer `acks=all` 配合，可在 ISR 足够大时才接受写入，否则拒绝。
- ISR 的动态维护使 Kafka 在节点抖动时仍保持高可用，而不是静态依赖固定副本数。
