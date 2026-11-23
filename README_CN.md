# llm-quota-router（中文说明）

[English](README.md) | **简体中文**

[![CI](https://github.com/huotianyyy/llm-quota-router/actions/workflows/ci.yml/badge.svg)](https://github.com/huotianyyy/llm-quota-router/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

面向多账号/多配额桶 LLM 算力池的**零依赖 Python 路由与熔断故障转移引擎**，专为长时间运行的 AI Agent 循环打造。

## 核心能力

- **滚动配额桶追踪（QuotaBucket）**：精确管理多周期限额窗口与自动回满重置。
- **粘性缓存亲和（Sticky Affinity）**：主槽位健康时优先复用，最大化命中 Prompt Cache 省成本。
- **429 冷却退避 + 三态熔断器（Closed / Open / Half-Open）**：上游触发限流或连续超时自动漂移到剩余额度最充裕的槽位，冷却后自动半开探测恢复。

## 快速开始

```python
from llm_quota_router import ProviderAccount, QuotaBucket, QuotaRouter

router = QuotaRouter(
    accounts=[
        ProviderAccount("primary-1", "openai-compatible", {"gpt-4o", "claude-sonnet"},
                        buckets=[QuotaBucket("hourly", 1000, 850, 3600)]),
        ProviderAccount("backup-2", "openai-compatible", {"gpt-4o", "claude-sonnet"},
                        buckets=[QuotaBucket("hourly", 1000, 1000, 3600)]),
    ]
)

res = router.execute_with_failover(
    model="claude-sonnet",
    preferred_account="primary-1",
    fn=lambda d: call_upstream(d.account_id, d.model),
)
```

## 开源协议

MIT
