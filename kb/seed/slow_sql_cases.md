# 慢 SQL 案例库

## 案例 A：未命中索引的大表 COUNT

**现象**：`SELECT COUNT(*) FROM orders WHERE YEAR(created_at)=2023` 单次扫描 800ms+。  
**原因**：对列做函数包裹导致无法使用 `created_at` 上索引，退化为全表扫描。  
**处理**：改写为范围条件 `created_at >= '2023-01-01' AND created_at < '2024-01-01'`，并确认 `created_at` 索引。  
**关键词**：函数导致索引失效、COUNT、全表扫描

## 案例 B：深分页 LIMIT 大偏移

**现象**：`SELECT * FROM logs ORDER BY id LIMIT 100000, 20` 随页码增大明显变慢。  
**原因**：优化器仍需扫描/排序大量行以满足 OFFSET。  
**处理**：改为基于游标 `WHERE id > :last_id ORDER BY id LIMIT 20`，或限制最大页、用搜索引擎承接深翻页。  
**关键词**：LIMIT OFFSET、深分页、排序

## 案例 C：SELECT * 宽表 + 大结果集

**现象**：报表接口 `SELECT * FROM wide_user_profile WHERE dept_id=?` 网络与缓冲暴涨。  
**原因**：拉取大量不需要的列，放大 I/O 与序列化成本。  
**处理**：只选业务列；必要时分页；对冷字段拆垂直子表。  
**关键词**：SELECT *、宽表、I/O

## 案例 D：OR 条件拆索引

**现象**：`WHERE status=1 OR user_id=?` 计划走全表。  
**原因**：单列索引难以同时优化 OR 两侧。  
**处理**：拆成 `UNION ALL` 两段各自走索引，或建立合适复合索引（需评估选择性）。  
**关键词**：OR、UNION、索引选择
