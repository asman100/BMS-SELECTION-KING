import csv
from app import db, Part, app

def import_parts_from_csv(filename):
    with app.app_context():
        with open(filename, 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                part_number = row['part_number']
                if Part.query.filter_by(part_number=part_number).first():
                    print(f"Part number '{part_number}' already exists. Skipping.")
                    continue
                part = Part(
                    part_number=part_number,
                    description=row['description'],
                    category=row.get('category'),
                    cost=float(row.get('cost', 0))
                )
                db.session.add(part)
            db.session.commit()

if __name__ == '__main__':
    # You will need to create a file named 'parts.csv' with the appropriate columns
    # Example parts.csv:
    # part_number,description,category,cost
    # T-S-10k,10k Thermistor Sensor,Sensor,10.50
    # P-SWITCH-1,Pressure Switch,Switch,25.00
    import_parts_from_csv('parts.csv')
    print("Parts imported successfully!")
