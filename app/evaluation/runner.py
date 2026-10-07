import json
from pathlib import Path
from typing import Any

import httpx


async def evaluate(client: httpx.AsyncClient) -> dict[str, Any]:
    dataset = json.loads(Path(__file__).with_name("dataset.json").read_text())
    results = []
    for case in dataset:
        response = await client.post(
            "/sessions", json={"agent_id": "evaluation", "token_budget": 512}
        )
        response.raise_for_status()
        session_id = response.json()["session_id"]
        labels = {}
        for item in case["items"]:
            response = await client.post(
                f"/sessions/{session_id}/events",
                json={"kind": item["kind"], "content": item["content"], "importance": 0.8},
            )
            response.raise_for_status()
            labels[response.json()["event_id"]] = item["group"]
        response = await client.post(
            "/context/build",
            json={"session_id": session_id, "query": case["query"], "token_budget": case["budget"]},
        )
        response.raise_for_status()
        context = response.json()
        groups = [labels[item["event_id"]] for item in context["items"]]
        relevant = set(case["relevant_groups"])
        tool = await client.post(
            "/tools/calculator/execute",
            json={"session_id": session_id, "arguments": {"expression": "(12 + 8) * 3"}},
        )
        tool.raise_for_status()
        tool_result = tool.json()
        facts_found = sum(
            fact.lower() in context["text"].lower() for fact in case["required_facts"]
        )
        results.append(
            {
                "name": case["name"],
                "relevant_context_recall": len(set(groups) & relevant) / len(relevant),
                "irrelevant_context_rate": sum(g not in relevant for g in groups)
                / max(1, len(groups)),
                "duplicate_context_rate": (len(groups) - len(set(groups))) / max(1, len(groups)),
                "required_fact_coverage": facts_found / len(case["required_facts"]),
                "estimated_token_reduction": 1
                - context["estimated_tokens_after"] / max(1, context["estimated_tokens_before"]),
                "tool_call_success": int(tool_result["success"]),
                "deterministic_task_success": int(
                    tool_result["success"] and tool_result["result"]["value"] == 60
                ),
                "context_items": context["selected"],
                "duplicates_removed": context["duplicates_removed"],
                "estimated_tokens_before": context["estimated_tokens_before"],
                "estimated_tokens_after": context["estimated_tokens_after"],
            }
        )
    metrics = [
        "relevant_context_recall",
        "irrelevant_context_rate",
        "duplicate_context_rate",
        "required_fact_coverage",
        "estimated_token_reduction",
        "tool_call_success",
        "deterministic_task_success",
    ]
    return {
        "dataset": "synthetic-v1",
        "cases": len(results),
        "means": {name: sum(row[name] for row in results) / len(results) for name in metrics},
        "results": results,
    }
