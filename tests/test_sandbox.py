import pytest

from insightagent.sandbox import QueryError, run_query


def q(env, sql, **kw):
    return run_query(env[4], sql, **kw)


def test_select_works(env):
    r = q(env, "SELECT district, COUNT(*) AS n FROM data GROUP BY 1 ORDER BY 1")
    assert r.columns == ["district", "n"] and sum(row[1] for row in r.rows) == 400


def test_trailing_semicolon_ok(env):
    assert q(env, "SELECT 1;").rows == [[1]]


def test_row_cap_flags_truncation(env):
    r = q(env, "SELECT * FROM data", max_rows=10)
    assert len(r.rows) == 10 and r.truncated


def test_dates_are_json_safe(env):
    import json

    json.dumps(q(env, "SELECT visit_date FROM data LIMIT 1").rows)


@pytest.mark.parametrize("sql", [
    "DROP TABLE data", "DELETE FROM data", "UPDATE data SET duration_min = 0",
    "INSERT INTO data SELECT * FROM data", "CREATE TABLE x AS SELECT 1", "ALTER TABLE data ADD y INT",
    "COPY data TO '/tmp/x.csv'", "ATTACH '/tmp/other.db'", "SET threads = 1",
    "SELECT 1; SELECT 2", "SELECT 1; DROP TABLE data", "", "   ", "SELEC nonsense",
])
def test_rejected(env, sql):
    with pytest.raises(QueryError):
        q(env, sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM read_text('/etc/hostname')",
    "SELECT * FROM glob('/*')", "SELECT * FROM read_parquet('/tmp/none.parquet')",
])
def test_filesystem_functions_blocked(env, sql):
    with pytest.raises(QueryError):
        q(env, sql)


def test_data_survives_attempted_writes(env):
    for sql in ("DROP TABLE data", "DELETE FROM data"):
        with pytest.raises(QueryError):
            q(env, sql)
    assert q(env, "SELECT COUNT(*) FROM data").rows == [[400]]


def test_timeout(env):
    with pytest.raises(QueryError, match="time limit"):
        q(env, "SELECT COUNT(*) FROM range(3000000000) a, range(1000) b", timeout_s=1)


def test_long_query_rejected(env):
    with pytest.raises(QueryError, match="too long"):
        q(env, "SELECT " + "1+" * 3000 + "1")


def test_error_message_is_single_line(env):
    with pytest.raises(QueryError) as e:
        q(env, "SELECT nope FROM data")
    assert "\n" not in str(e.value)
