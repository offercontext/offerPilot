# Java 集合框架核心接口

Java 集合框架（Collections Framework）统一了容器类型的接口、实现与算法。本文整理 `java.util` 下最常用的接口与实现。

## Map 接口族

`Map` 表示键值对映射。核心实现有 `HashMap`、`TreeMap` 与 `LinkedHashMap`。

- `HashMap`：基于哈希表，查找平均 O(1)，不保证迭代顺序。
- `TreeMap`：基于红黑树，按键排序，查找 O(log n)。
- `LinkedHashMap`：在 `HashMap` 基础上维护插入或访问顺序的双向链表。

> `HashMap` 与 `TreeMap` 都实现 `Map` 接口，但底层结构决定它们适合不同场景。

## HashMap 内部结构

`java.util.HashMap` 使用数组 + 链表/红黑树实现。桶下标计算：

```java
// 简化的桶下标计算
int hash = key.hashCode();
int index = (n - 1) & hash;
```

扩容阈值计算：

| 参数 | 默认值 | 含义 |
|---|---|---|
| initial_capacity | 16 | 初始桶数 |
| load_factor | 0.75f | 扩容阈值比例 |
| treeify_threshold | 8 | 链表转红黑树阈值 |

## TreeMap 与排序

`TreeMap` 要求键实现 `Comparable`，或构造时传入 `Comparator`。常见操作：

1. `firstKey()` / `lastKey()`：返回最小/最大键。
2. `subMap(from, to)`：返回区间视图。
3. `headMap(to)` / `tailMap(from)`：返回前缀/后缀视图。

## 迭代与 fail-fast

`HashMap` 的迭代器是 fail-fast 的：在迭代过程中若检测到结构性修改（非迭代器自身的 remove），抛出 `ConcurrentModificationException`。

```java
Map<String, Integer> map = new HashMap<>();
map.put("a", 1);
// 迭代时直接 map.remove 会抛 ConcurrentModificationException
```

## 选择建议

| 场景 | 推荐 |
|---|---|
| 通用键值存储 | `HashMap` |
| 需要按键排序 | `TreeMap` |
| 记录访问顺序（LRU） | `LinkedHashMap` |
