"""Import parts into the global catalog from a CSV.

Expected CSV columns (case-sensitive):
  part_number,description,category,cost,country_of_origin,cable_recommendation

Required: part_number, description
Others optional. Existing part_numbers are skipped (global uniqueness).

Usage:
  python import_parts.py --file parts.csv

Add --dry-run to validate without committing.
"""
import csv
import argparse
from app import db, Part, app


def import_parts_from_csv(filename: str, dry_run: bool = False):
    created = 0
    skipped = 0
    with app.app_context():
        with open(filename, 'r', newline='', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            required = {'part_number', 'description'}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise SystemExit(f"Missing required columns: {', '.join(missing)}")
            for i, row in enumerate(reader, start=2):
                part_number = (row.get('part_number') or '').strip()
                description = (row.get('description') or '').strip()
                if not part_number or not description:
                    print(f"Line {i}: Missing part_number or description. Skipping.")
                    skipped += 1
                    continue
                existing = Part.query.filter_by(part_number=part_number).first()
                if existing:
                    print(f"Line {i}: Part '{part_number}' exists. Skipping.")
                    skipped += 1
                    continue
                cost_raw = (row.get('cost') or '').strip()
                try:
                    cost_val = float(cost_raw) if cost_raw else None
                except ValueError:
                    print(f"Line {i}: Invalid cost '{cost_raw}'. Using None.")
                    cost_val = None
                part = Part(
                    part_number=part_number,
                    description=description,
                    category=(row.get('category') or '').strip() or None,
                    cost=cost_val,
                    country_of_origin=(row.get('country_of_origin') or '').strip() or None,
                    cable_recommendation=(row.get('cable_recommendation') or '').strip() or None,
                )
                db.session.add(part)
                created += 1
        if dry_run:
            db.session.rollback()
            print(f"Dry run complete: would create {created}, skipped {skipped}.")
        else:
            db.session.commit()
            print(f"Import complete: created {created}, skipped {skipped}.")


def main():
    parser = argparse.ArgumentParser(description="Import global parts from CSV")
    parser.add_argument('--file', required=True, help='CSV file path')
    parser.add_argument('--dry-run', action='store_true', help='Validate without committing changes')
    args = parser.parse_args()
    import_parts_from_csv(args.file, args.dry_run)


if __name__ == '__main__':
    main()
