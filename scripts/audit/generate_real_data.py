"""Deterministic physical CSV fixtures and a Python-only independent oracle."""
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/e2e'


def main():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    values = {
        'customers.csv': 'customer_id,name,province,amount\n1,Somchai,Bangkok,1500\n2,Suda,Chiang Mai,2500\n3,Anan,Bangkok,1000\n4,Mali,,3000\n4,Mali,,3000\n5,Nida,Phuket,2000\n',
        'provinces.csv': 'province,region\nBangkok,Central\nChiang Mai,North\nPhuket,South\n',
        'invalid_types.csv': 'id,amount,date\n1,10,2026-01-01\n2,bad,not-a-date\n3,-5,2026-02-01\n4,,\n',
        'empty_dataset.csv': 'id,amount\n',
        'duplicate_records.csv': 'id,label\n1,กานต์\n1,กานต์\n2,เชียงใหม่\n2,เชียงใหม่\n2,เชียงใหม่\n',
        'malformed.csv': 'id,name\n1,Accepted\n2,Too,Many\n3,Also accepted\n4,"unterminated\n5,tail preserved\n',
    }
    for name, content in values.items():
        (FIXTURES / name).write_text(content, encoding='utf-8')
    (FIXTURES / 'zero_bytes.csv').write_bytes(b'')
    (FIXTURES / 'invalid_encoding.csv').write_bytes(b'id,label\n1,\xff\xfe\n')
    (FIXTURES / 'unsupported.txt').write_text('id\n1\n')
    total = 0
    with (FIXTURES / 'large_dataset.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f); writer.writerow(['id', 'label', 'amount'])
        for i in range(1, 50001):
            amount = i % 997
            total += amount
            writer.writerow([i, f'ลูกค้า_{i:05}', amount])
    rows = list(csv.DictReader(io.StringIO(values['customers.csv'])))
    duplicates = sum(n-1 for n in Counter(tuple(row.items()) for row in rows).values())
    expected = {'row_count': len(rows), 'duplicates_beyond_first': duplicates,
        'missing_province': sum(not row['province'] for row in rows),
        'total_amount': sum(int(row['amount']) for row in rows),
        'amount_gt_1500': sum(int(row['amount']) > 1500 for row in rows),
        'distinct_customer_ids': len({row['customer_id'] for row in rows}),
        'bangkok': sum(row['province']=='Bangkok' for row in rows),
        'province_groups': [
            {'province': name or None, 'n': count} for name,count in sorted(Counter(row['province'] for row in rows).items())],
        'cleaned_rows': [dict(row) for i,row in enumerate(rows) if int(row['amount']) > 1500 and row not in rows[:i]],
        'large': {'rows': 50000, 'sum_amount': total, 'last_label': 'ลูกค้า_50000'}}
    (FIXTURES / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2)+'\n')
    manifest = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(FIXTURES.iterdir()) if p.suffix in {'.csv','.txt'}}
    (FIXTURES / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(expected, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
