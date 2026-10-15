"""Decode downloaded artifacts for independent full-record comparison."""
import csv
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.suffix == '.parquet':
    import duckdb
    with duckdb.connect() as con:
        cursor = con.execute('SELECT * FROM read_parquet(?)', [str(path)])
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
else:
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        rows = list(reader)
print(json.dumps({'columns': columns, 'rows': rows}, ensure_ascii=False, default=str))
