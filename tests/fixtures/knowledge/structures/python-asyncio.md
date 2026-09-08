# Python asyncio 协程与并发

`asyncio` 是 Python 3.4+ 引入的异步 IO 标准库，使用单线程事件循环调度协程。本文整理协程、任务与并发的核心用法。

## 协程定义

协程用 `async def` 定义，内部可用 `await` 挂起等待其他协程或可等待对象。

```python
async def fetch_data(url):
    await asyncio.sleep(0.1)
    return {"url": url}
```

> 协程函数调用后返回一个 coroutine 对象，不会立即执行，必须由事件循环调度。

## 任务与并发

`asyncio.create_task` 将协程包装为 Task 并立即调度。`asyncio.gather` 并发运行多个可等待对象。

```python
async def main():
    results = await asyncio.gather(
        fetch_data("a"),
        fetch_data("b"),
        fetch_data("c"),
    )
    return results
```

`asyncio.gather` 的行为：

1. 默认按传入顺序返回结果。
2. 任一任务抛异常时，默认向上传播该异常。
3. 传入 `return_exceptions=True` 时，异常作为结果返回而不中断其他任务。

## 事件循环

事件循环（event loop）负责调度就绪的 Task 与回调。获取/运行方式：

| API | 作用 |
|---|---|
| `asyncio.run(coro)` | 创建新循环并运行顶层协程 |
| `loop.create_task(coro)` | 在当前循环中调度 Task |
| `asyncio.get_running_loop()` | 获取当前运行中的循环 |

## 同步原语

`asyncio` 提供协程友好的同步原语：

- `asyncio.Lock`：异步互斥锁。
- `asyncio.Semaphore`：限制并发数。
- `asyncio.Queue`：生产者-消费者队列。

```python
sem = asyncio.Semaphore(4)
async def worker(i):
    async with sem:
        await asyncio.sleep(0.1)
        return i
```

## 取消与超时

`asyncio.wait_for` 为可等待对象设置超时，超时抛出 `TimeoutError` 并取消底层任务。`Task.cancel` 触发协程内部抛出 `CancelledError`，协程可选择捕获清理资源或向上传播。
