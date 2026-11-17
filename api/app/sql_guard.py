"""Accept only the temporary lock-waits contract from model output."""

import sqlglot
from sqlglot import exp


SAFE_SQL = (
    "SELECT waiting_pid, blocking_pid, wait_seconds "
    "FROM diagnostics.lock_waits"
)
ALLOWED_COLUMNS = {"waiting_pid", "blocking_pid", "wait_seconds"}


class UnsafeSQL(ValueError):
    pass


def validate_candidate_sql(candidate: str) -> str:
    """Parse the candidate, reject extra syntax, and return our own SQL text."""
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
    if (not isinstance(table, exp.Table)
            or table.name != "lock_waits"
            or table.db != "diagnostics"
            or table.catalog
            or table.alias
            or len(from_clause.args) != 1):
        raise UnsafeSQL("Only diagnostics.lock_waits is allowed")

    projections = select.expressions
    if (len(projections) != 3
            or not all(isinstance(item, exp.Column)
                       and not item.table and not item.db and not item.catalog
                       and item.name in ALLOWED_COLUMNS
                       for item in projections)
            or {item.name for item in projections} != ALLOWED_COLUMNS):
        raise UnsafeSQL("Only the three lock columns are allowed")

    return SAFE_SQL
