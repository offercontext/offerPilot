# React Hooks 函数组件状态与副作用

React Hooks 让函数组件拥有状态与生命周期能力。本文整理 `useState`、`useEffect` 与 `useMemo` 的核心用法。

## useState 状态管理

`useState` 返回 `[state, setState]` 二元组，setState 触发重渲染。

```jsx
import { useState } from "react";

function Counter() {
  const [count, setCount] = useState(0);
  return <button onClick={() => setCount(count + 1)}>{count}</button>;
}
```

> setState 接受函数形式时基于上一次状态计算，避免批量更新时的闭包陷阱。

useState 要点：

1. 初始值只在首次渲染时使用。
2. setState 是异步批量执行的。
3. 对象状态需手动展开或返回新对象，不可直接 mutate。

## useEffect 副作用

`useEffect` 用于处理副作用：订阅、定时器、数据获取等。依赖数组控制执行时机。

```jsx
useEffect(() => {
  const id = setInterval(() => tick(), 1000);
  return () => clearInterval(id);
}, []);
```

依赖数组语义：

| 写法 | 执行时机 |
|---|---|
| `useEffect(fn, [])` | 仅挂载时执行一次 |
| `useEffect(fn, [a, b])` | a 或 b 变化时执行 |
| `useEffect(fn)` | 每次渲染都执行 |

清理函数（return 的函数）在组件卸载或下次 effect 执行前调用。

## useMemo 与性能

`useMemo` 缓存昂贵计算结果，仅在依赖变化时重算。

```jsx
const sorted = useMemo(() => items.slice().sort(), [items]);
```

- `useMemo` 返回计算值。
- `useCallback` 返回记忆化的回调函数，等价于 `useMemo(() => fn, deps)`。

## 自定义 Hook

自定义 Hook 以 `use` 开头，封装可复用的状态逻辑：

1. 提取状态与副作用到普通函数。
2. 返回需要的值或操作。
3. 在多个组件中按需调用。

> 自定义 Hook 不是 React 特殊 API，只是约定：以 `use` 开头的函数会被 lint 插件校验 Hooks 规则。
