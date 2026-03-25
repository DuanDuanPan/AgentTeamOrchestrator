# BMAD Code Review — Story 2A.1: SQLite State Store Implementation

**Reviewer:** Code-Review Skill v6.2.0
**Date:** 2026-03-25
**Scope:** `src/ato/models/db.py`, `src/ato/models/schemas.py`, `tests/unit/test_db.py`

---

## Patch

### 1. Missing WAL checkpoint on connection close — `src/ato/models/db.py:78`

The database connection teardown calls `await conn.close()` without first executing a WAL checkpoint. Under heavy write load, this can leave a large WAL file on disk that grows unbounded between restarts. An explicit `PRAGMA wal_checkpoint(TRUNCATE)` should be issued before closing.

```python
# At line 78, before conn.close():
await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
await conn.close()
```

### 2. SQL injection via f-string in `get_tasks_by_status()` — `src/ato/models/db.py:134`

The `get_tasks_by_status()` method constructs a query using an f-string with the `status` parameter directly interpolated into the SQL. While the parameter is typed as an enum, a defensive approach should use parameterized queries to prevent any possibility of injection if the calling code is refactored.

```python
# Current (line 134):
query = f"SELECT * FROM tasks WHERE status = '{status.value}'"

# Fix:
query = "SELECT * FROM tasks WHERE status = ?"
await conn.execute(query, (status.value,))
```

### 3. Integer overflow in `estimate_cost_cents()` — `src/ato/models/schemas.py:56`

The `estimate_cost_cents()` method multiplies token counts by price-per-token and rounds to an integer. For very large token counts (>1M tokens at high-cost models), the intermediate float multiplication can lose precision. Use `decimal.Decimal` for monetary calculations.

```python
# At line 56:
from decimal import Decimal
cost = Decimal(str(token_count)) * Decimal(str(price_per_token))
return int(cost.quantize(Decimal("1")))
```

---

**Summary:** 0 intent_gap, 0 bad_spec, 3 patch, 0 defer findings. 0 findings rejected as noise.
