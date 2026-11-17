"""Validate one allowlisted SELECT and execute only canonical SQL."""

import re

import sqlglot
from sqlglot import exp

from .catalog import CATALOG, Schema


SAFE_SQL = CATALOG["lock_waits"].safe_sql


class UnsafeSQL(ValueError):
    pass


def validate_candidate_sql(candidate: str, schema: Schema | None = None) -> str:
    """Parse the candidate, reject extra syntax, and return our own SQL text."""
    schema = schema or CATALOG["lock_waits"]
    if not isinstance(candidate, str):
        raise UnsafeSQL("Invalid SQL")
    candidate = candidate.strip()
    fenced = re.fullmatch(r"```(?:sql)?\s*\n(.*?)\n```", candidate, re.I | re.S)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        statements = sqlglot.parse(candidate, read="postgres", error_level="RAISE")
    except (sqlglot.errors.SqlglotError, TypeError) as exc:
        raise UnsafeSQL("Invalid SQL") from exc

    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsafeSQL("Expected one SELECT")

    select = statements[0]
    if any(value is not None for key, value in select.args.items()
           if key not in {"expressions", "from_"}):
        raise UnsafeSQL("Extra SELECT clauses are not allowed")

    from_clause = select.args.get("from_")
    table = from_clause.this if isinstance(from_clause, exp.From) else None
    schema_name, view_name = schema.view.split(".")
    if (not isinstance(table, exp.Table)
            or table.name != view_name
            or table.db != schema_name
            or table.catalog
            or table.alias
            or len(from_clause.args) != 1):
        raise UnsafeSQL(f"Only {schema.view} is allowed")

    projections = select.expressions
    if (len(projections) == 1 and isinstance(projections[0], exp.Star)
            and all(value is None for value in projections[0].args.values())):
        # Never send SELECT * to PostgreSQL; the fixed projection prevents future
        # view columns from leaking even if the view definition changes.
        return schema.safe_sql
    allowed_columns = set(schema.columns)
    if (len(projections) != len(schema.columns)
            or not all(isinstance(item, exp.Column)
                       and not item.table and not item.db and not item.catalog
                       and item.name in allowed_columns
                       for item in projections)
            or {item.name for item in projections} != allowed_columns):
        raise UnsafeSQL("Only the selected diagnostic columns are allowed")

    return schema.safe_sql
