import pytest

from insightagent.offline import plan_sql


@pytest.mark.parametrize("question,expected", [
    ("How many rows are there?", "COUNT(*) AS rows"),
    ("How many records per district?", 'GROUP BY 1'),
    ("Average duration_min by visit type", 'AVG("duration_min")'),
    ("Total duration min in North", "WHERE \"district\" = 'North'"),
    ("What is the completion rate by district?", "completed_rate_pct"),
    ("Monthly trend of visits", "date_trunc('month'"),
    ("Top 3 districts by number of visits", "LIMIT 3"),
])
def test_plans(env, question, expected):
    sql, _ = plan_sql(question, env[3]["columns"])
    assert expected in sql


@pytest.mark.parametrize("question", [
    "Who is the president of France?", "What is the median duration?",
    "Which district has workers whose completion rate is above 85 percent?",
])
def test_unsupported_questions_return_none(env, question):
    assert plan_sql(question, env[3]["columns"]) is None


def test_quote_in_value_is_escaped(env):
    cols = [{"name": "city", "type": "VARCHAR", "distinct": 2, "nulls": 0, "values": ["O'Neil"]}]
    sql, _ = plan_sql("how many in O'Neil", cols)
    assert "'O''Neil'" in sql
