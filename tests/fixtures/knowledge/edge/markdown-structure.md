# Markdown 结构综合样本

本文用于验证 Evidence 生成覆盖 heading、paragraph、list、table、code、blockquote 与嵌套结构。

## 二级标题段落

这是一个普通段落，用于生成 paragraph Evidence。它包含足够内容以独立成块。

### 三级标题与列表

无序列表：

- 第一项
- 第二项
  - 嵌套子项 A
  - 嵌套子项 B
- 第三项

有序列表：

1. 步骤一
2. 步骤二
3. 步骤三

## 引用块

> 这是一个 blockquote Evidence。
> 它应该作为独立的引用块 Evidence 被提取。

## 表格

| 列 A | 列 B | 列 C |
|---|---|---|
| a1 | b1 | c1 |
| a2 | b2 | c2 |

## 代码块

```python
def hello(name):
    return f"hello {name}"
```

## 纯文本

以 .txt 形式上传时不解析 Markdown，按段落切分。
