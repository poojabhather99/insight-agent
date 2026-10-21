"""Deterministic planner used when no LLM key is configured.

It maps a narrow set of question shapes to SQL (counts, averages, sums, rates, top-N, trends).
It is a stand-in that exercises the same agent loop, not a language model, and it says so when
it cannot map a question.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .llm import AgentContext

NUMERIC = ("INT", "DOUBLE", "DECIMAL", "FLOAT", "HUGEINT")
STOP = {"the", "a", "of", "in", "by", "per", "for", "each", "is", "are", "what", "how", "many",
        "much", "show", "me", "total", "number", "average", "mean", "sum", "count", "top", "and"}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_num(c: dict) -> bool:
    return any(k in c["type"].upper() for k in NUMERIC)


def _is_date(c: dict) -> bool:
    return c["type"].upper().startswith(("DATE", "TIMESTAMP"))


def _is_bool(c: dict) -> bool:
    return c["type"].upper() == "BOOLEAN"


def _score(text: str, col: dict) -> tuple[int, int]:
    """(score, position) of a column mention in text. Score 0 means no mention."""
    text = text.lower()
    name = col["name"].lower()
    words = [w for w in name.split("_") if w]
    for cand in (name, " ".join(words), "".join(words)):
        m = re.search(rf"\b{re.escape(cand)}", text)
        if m:
            return 3, m.start()
    stems = [w[:5] for w in words if len(w) > 2]
    hits = [re.search(rf"\b{re.escape(w)}", text) for w in stems]
    if stems and all(hits):
        return 2, min(h.start() for h in hits)
    if name.endswith("id") or len(words) == 1 and words[0] == "id":
        return 0, -1
    for w, h in zip(stems, hits, strict=True):
        if h and len(w) > 3 and w not in STOP:
            return 1, h.start()
    return 0, -1


def _mentions(text: str, col: dict) -> int:
    sc, pos = _score(text, col)
    return pos if sc else -1


def _best(text: str, cols: list[dict]) -> dict | None:
    best = None
    for c in cols:
        sc, pos = _score(text, c)
        if sc and (best is None or (sc, -pos) > (best[0], -best[1])):
            best = (sc, pos, c)
    return best[2] if best else None


def _group_col(question: str, cols: list[dict]) -> dict | None:
    ql = question.lower()
    cats = [c for c in cols if not (_is_num(c) and not c["name"].endswith("id"))]
    m = re.search(r"\btop\s+\d*\s*([a-z_ ]+?)\s+(?:by|with|having|in)\b", ql)
    if m:
        c = _best(m.group(1), cats)
        if c:
            return c
    m = re.search(r"\b(?:by|per|for each|each|across|among)\s+([a-z_ ]+)", ql)
    if not m:
        return None
    frag = re.sub(r"^(number|count|total)\s+of\s+", "", m.group(1))
    return _best(frag, cats)


def plan_sql(question: str, cols: list[dict]) -> tuple[str, str] | None:
    """Return (sql, description) or None."""
    ql = question.lower().strip().rstrip("?.")
    group = _group_col(ql, cols)
    dates = [c for c in cols if _is_date(c)]
    nums = [c for c in cols if _is_num(c) and c["distinct"] > 1 and not c["name"].endswith("id")]
    bools = [c for c in cols if _is_bool(c) or (c["type"] == "VARCHAR" and
             set(c.get("values", [])) <= {"true", "false", "True", "False"} and c.get("values"))]

    where = []
    for c in cols:
        for v in c.get("values", []) or []:
            if v.lower() in ("true", "false") or c["type"] != "VARCHAR":
                continue
            if re.search(rf"\b{re.escape(v.lower())}\b", ql):
                where.append(f"{q(c['name'])} = '{v.replace(chr(39), chr(39) * 2)}'")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""

    if re.search(r"\b(median|percentile|weekday|day of the week|above|below|greater|more than|"
                 r"less than|correlat|whose|having|compared?|versus|vs)\b", ql):
        return None
    if not group and re.search(r"\b(how many (rows|records)|number of (rows|records))\b", ql):
        return "SELECT COUNT(*) AS rows FROM data" + wsql, "row count"

    if re.search(r"\b(trend|over time|monthly|per month|by month|each month)\b", ql) and dates:
        d = dates[0]["name"]
        metric = next((c for c in nums if _mentions(ql, c) >= 0), None)
        agg = f"SUM({q(metric['name'])})" if metric else "COUNT(*)"
        label = f"total_{metric['name']}" if metric else "records"
        return (f"SELECT strftime(date_trunc('month', {q(d)}), '%Y-%m') AS month, {agg} AS {label}"
                f" FROM data{wsql} GROUP BY 1 ORDER BY 1", "monthly trend")

    rate_col = next((c for c in bools if _mentions(ql, c) >= 0), None)
    if re.search(r"\b(rate|percent|percentage|share|proportion)\b", ql) and rate_col:
        expr = f"ROUND(100.0 * AVG(CAST({q(rate_col['name'])} AS BOOLEAN)::INT), 1)"
        label = f"{rate_col['name']}_rate_pct"
        if group:
            return (f"SELECT {q(group['name'])}, {expr} AS {label} FROM data{wsql} "
                    f"GROUP BY 1 ORDER BY 2 DESC", f"{rate_col['name']} rate by {group['name']}")
        return f"SELECT {expr} AS {label} FROM data{wsql}", f"overall {rate_col['name']} rate"

    top = re.search(r"\btop (\d+)?\s*", ql)
    metric = _best(re.sub(r"\b(by|per|each)\b.*", "", ql), nums)
    fn = None
    for word, f in (("average", "AVG"), ("mean", "AVG"), ("avg", "AVG"), ("sum", "SUM"),
                    ("total", "SUM"), ("maximum", "MAX"), ("highest", "MAX"), ("max", "MAX"),
                    ("minimum", "MIN"), ("lowest", "MIN"), ("min", "MIN")):
        if re.search(rf"\b{word}\b", ql):
            fn = f
            break
    if metric and fn:
        expr = f"ROUND({fn}({q(metric['name'])}), 2)" if fn in ("AVG", "SUM") else \
            f"{fn}({q(metric['name'])})"
        label = f"{fn.lower()}_{metric['name']}"
        if group:
            lim = ""
            if top:
                lim = f" LIMIT {int(top.group(1) or 5)}"
            return (f"SELECT {q(group['name'])}, {expr} AS {label} FROM data{wsql} "
                    f"GROUP BY 1 ORDER BY 2 DESC{lim}", f"{fn.lower()} {metric['name']} by "
                    f"{group['name']}")
        return f"SELECT {expr} AS {label} FROM data{wsql}", f"{fn.lower()} {metric['name']}"

    if re.search(r"\b(how many|count|number of|most|least|top)\b", ql):
        if group:
            lim = f" LIMIT {int(top.group(1) or 5)}" if top else ""
            return (f"SELECT {q(group['name'])}, COUNT(*) AS records FROM data{wsql} "
                    f"GROUP BY 1 ORDER BY 2 DESC{lim}", f"records by {group['name']}")
        return "SELECT COUNT(*) AS records FROM data" + wsql, "record count"
    return None


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def summarize(result: dict, desc: str) -> str:
    cols, rows = result["columns"], result["rows"]
    if not rows:
        return f"The query for {desc} returned no rows."
    if len(rows) == 1 and len(cols) == 1:
        return f"{cols[0].replace('_', ' ')}: {_fmt(rows[0][0])}."
    top = rows[0]
    head = f"{len(rows)} rows for {desc}. Highest: {_fmt(top[0])} with {_fmt(top[-1])}."
    if len(rows) > 1:
        low = rows[-1]
        head += f" Lowest: {_fmt(low[0])} with {_fmt(low[-1])}."
    return head


class OfflineLLM:
    name = "offline"

    def complete(self, messages: list[dict[str, str]], ctx: AgentContext) -> str:
        ql = ctx.question.lower()
        if re.search(r"\b(drop|delete|truncate|alter|insert|update|attach|copy)\b\s+\w*", ql) \
                and re.search(r"\b(table|data|rows|from|into|database)\b", ql):
            return self._final("I only run read-only analysis, so I will not change or delete "
                               "data.", refused=True)
        obs = ctx.observations
        if not obs:
            plan = plan_sql(ctx.question, ctx.schema)
            if plan is None:
                cols = ", ".join(c["name"] for c in ctx.schema[:12])
                return self._final("Offline mode understands counts, averages, sums, rates, top-N "
                                   f"and monthly trends over these columns: {cols}. Set an LLM key "
                                   "for open-ended questions.", refused=True)
            sql, desc = plan
            return json.dumps({"thought": f"Compute {desc}.", "action": "run_sql",
                               "input": {"sql": sql}})
        last = obs[-1]
        if not last["ok"]:
            return self._final("The query failed: " + str(last["error"]), refused=True)
        res = next((o["result"] for o in reversed(obs) if o["ok"] and "rows" in o["result"]), None)
        if res is None:
            return self._final("No query result is available.", refused=True)
        plan = plan_sql(ctx.question, ctx.schema)
        desc = plan[1] if plan else "the question"
        if ("chart" not in ctx.actions and len(res["columns"]) == 2 and len(res["rows"]) >= 2
                and isinstance(res["rows"][0][1], (int, float))):
            kind = "line" if res["columns"][0] == "month" else "bar"
            return json.dumps({"thought": "A chart makes the comparison easier to read.",
                               "action": "chart",
                               "input": {"type": kind, "x": res["columns"][0],
                                         "y": res["columns"][1], "title": desc.capitalize()}})
        return self._final(summarize(res, desc))

    @staticmethod
    def _final(answer: str, refused: bool = False) -> str:
        return json.dumps({"thought": "Done.", "action": "final",
                           "input": {"answer": answer, "refused": refused}})
