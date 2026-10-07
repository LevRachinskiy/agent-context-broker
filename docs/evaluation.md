# Evaluation methodology

The committed synthetic-v1 dataset contains four short infrastructure debugging cases: stale cache, payment retry, tool timeout, and durable events. Each has two relevant equivalence groups, one duplicate, unrelated distractors, and two required fact substrings. Every item is inserted through the real broker API before context selection.

The script uses the offline hash embedding provider. The dataset deliberately has lexical overlap and is not representative of paraphrases, adversarial prompts, multilingual retrieval, or general reasoning. Scores describe this dataset only.

| Metric | Definition |
| --- | --- |
| Relevant-context recall | Selected labeled relevant equivalence groups / all relevant groups |
| Irrelevant-context rate | Selected items outside the relevant groups / selected items |
| Duplicate-context rate | Selected items repeating a labeled group / selected items |
| Required-fact coverage | Required substrings present in rendered context / required substrings |
| Estimated token reduction | `1 - optimized estimated tokens / candidate estimated tokens` |
| Tool-call success | Calculator execution reported success |
| Deterministic task success | Calculator returned the known result 60 for `(12+8)*3` |

Means are macro averages over the four cases. Relevant-group recall alone can hide information lost by truncation, so required-fact coverage is measured separately. Token estimates include the rendered source/identifier labels. No hallucination rate or LLM task-success rate is measured. The calculator task is separate from the context-quality labels and does not depend on a model.

Recorded baseline: 100% relevant-group recall, 100% required-fact coverage, 0% irrelevant selected items, 0% duplicate selected items, 47.3% mean estimated token reduction, and 4/4 deterministic tool tasks successful.

```bash
python scripts/evaluate.py --output docs/evaluation-results.json
```

Raw case-level results: [evaluation-results.json](evaluation-results.json). Assertions about this fixed fixture are in `tests/test_evaluation.py`. Broader evaluations should use held-out human labels and include tight-budget, paraphrase, contradiction, and old-memory cases.
