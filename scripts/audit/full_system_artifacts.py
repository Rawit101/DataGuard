"""Check persisted browser-created artifacts after restarting the local service."""

import csv
import io
import sqlite3
from pathlib import Path

import duckdb

WORKSPACE = Path(__file__).resolve().parents[2] / "work/full-system-workspace"
database = sqlite3.connect(f"file:{WORKSPACE / 'metadata.sqlite3'}?mode=ro", uri=True)
database.row_factory = sqlite3.Row
artifacts = [dict(row) for row in database.execute(
    "SELECT a.*, p.name AS project_name FROM artifacts a "
    "JOIN projects p ON p.id=a.project_id")]

csv_artifact = next(a for a in artifacts if a["name"] == "browser_orders.csv")
content = Path(csv_artifact["path"]).read_bytes()
assert content.startswith(b"\xef\xbb\xbf")
rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
assert len(rows) == 9 and rows[3]["amount"] == "bad" and rows[0]["customer"].strip() == "กานต์"

audit_parquet = next(a for a in artifacts if a["name"] == "output.parquet"
                     and a["project_name"] == "Full system audit verified")
with duckdb.connect() as con:
    totals = dict(con.execute("SELECT zone_zone_name, total_amount FROM read_parquet(?)",
                              [audit_parquet["path"]]).fetchall())
assert totals == {"เหนือ": 350.0, "ใต้": 150.0, "ไม่ทราบ": 400.0}

sample_parquet = next(a for a in artifacts if a["project_name"] == "Real estate demo"
                      and a["kind"] == "pipeline_output")
with duckdb.connect() as con:
    sample = con.execute("SELECT count(*), max(avg_price_per_sqm) FILTER "
                         "(WHERE province_province_name='Bangkok') FROM read_parquet(?)",
                         [sample_parquet["path"]]).fetchone()
assert sample == (5, 75000.0)
print({"browser_csv_rows": len(rows), "browser_csv_bytes": len(content),
       "audit_parquet_totals": totals, "sample_rows": sample[0],
       "sample_bangkok_average": sample[1]})
