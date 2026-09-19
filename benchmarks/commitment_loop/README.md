# commitment_loop — 三臂 benchmark（骨架）

**这是基础设施验证，不是研究结果。** `run_benchmark.py` 的输出里带 `infrastructure_only: true`
和 `research_claim: "none"`，请照此引用。

## 三臂

| 臂 | 行为 | 有冻结预期 | 有机器结算 | 需要 LLM |
|---|---|---|---|---|
| `fixed_pipeline` | 按声明顺序执行，忽略结果 | 否 | 否 | 否 |
| `free_llm_loop` | 依上一步结果自由选下一步，同样硬预算 | 否 | 否 | 是（缺失时如实标注） |
| `commitment_loop` | 内核：冻结预期 + 独立测量 + 机器结算 + 转向门 | 是 | 是 | 否 |

三臂共享同一 `Episode`：相同初始快照、相同预算、相同评价器实例、相同评价协议、相同 seed 集合。
这一点由 `tests/commitment/test_benchmark_parity.py` 断言，并写进 manifest 的 `parity` 段
（含 protocol_hash / budget_hash / snapshot_hashes）。

## 指标

`metrics.py` 定义并输出：任务质量、无证据转向率、强反证下坚持错误率、结算分类准确率、
无新证据无效迭代率、regret、人工干预次数、模型调用/token/墙钟、已结算经验消费率、
已验证能力退化率。每个指标都带分母与 `undefined_reason`；无分母时输出 `no_data` 而不是 0。

## 运行

```bash
python3 benchmarks/commitment_loop/run_benchmark.py \
    --workspace benchmark_runs/_ws \
    --out benchmark_runs/commitment_loop/<label> \
    [--seeds 11,12,13] [--arms fixed_pipeline,free_llm_loop,commitment_loop] [--use-llm]
```

产出：`manifest.json`（标识 + parity 证据）、`results.json`、`metrics.json`、`metrics.csv`。

## 为什么不能据此比较优劣

- fixture 是合成的、单步的、确定性的；真实项目有噪声、长程依赖和外部阻塞；
- 每臂每个 seed 只跑一个 episode，样本量不支持任何统计结论；
- `free_llm_loop` 在无 provider 时会退化为贪心重放，此时它并不是它要代表的那种臂
  （`dependency_missing=('llm_chooser',)` 会写进结果）。

真实分子纵向样本在 `molecular_vertical_sample.py`，它是**一个** bet，同样不支持研究结论。


## 语义纠偏后（commitment/2）

- 三个环境标签都**不可发布**；`production_canary` 也只有当重复证据与其他门都满足时才可能发布。
- `free_llm_loop` 在本轮**没有**真实 LLM client，运行的是确定性贪心退化路径。
  `run_benchmark.py` 的 manifest 里 `defining_behaviour.free_llm_loop.defining_behaviour_executed=false`，
  并把原因写进 `limitations` 数组；不要把这个结果描述成"三臂都按定义行为跑过"。
- 三臂仍然只用于**接口与可执行性验证**，不支持臂间性能结论。
