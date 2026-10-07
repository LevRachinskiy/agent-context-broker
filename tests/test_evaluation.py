from app.evaluation.runner import evaluate


async def test_synthetic_quality_and_deterministic_tasks(client):
    result = await evaluate(client)
    assert result["cases"] == 4
    assert result["means"]["relevant_context_recall"] == 1.0
    assert result["means"]["required_fact_coverage"] == 1.0
    assert result["means"]["duplicate_context_rate"] == 0.0
    assert result["means"]["deterministic_task_success"] == 1.0
