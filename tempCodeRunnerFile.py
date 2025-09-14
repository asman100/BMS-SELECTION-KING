from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import os

# --- APP SETUP ---
app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'bms_tool.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- DATABASE MODELS (The Schema) ---

template_points_association = db.Table('template_points',
    db.Column('equipment_template_id', db.Integer, db.ForeignKey('equipment_template.id')),
    db.Column('point_template_id', db.Integer, db.ForeignKey('point_template.id'))
)

selected_points_association = db.Table('selected_points',
    db.Column('scheduled_equipment_id', db.Integer, db.ForeignKey('scheduled_equipment.id')),
    db.Column('point_template_id', db.Integer, db.ForeignKey('point_template.id'))
)

class Panel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    panel_name = db.Column(db.String(80), unique=True, nullable=False)
    floor = db.Column(db.String(80), nullable=False)
    equipment = db.relationship('ScheduledEquipment', backref='panel', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "panelName": self.panel_name, "floor": self.floor}

class PointTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    # UPDATED: More specific point types
    point_type = db.Column(db.String(50), nullable=False) # e.g., "AI", "AO", "DI", "DO", "Modbus", "BACnet"
    # NEW: Optional part number
    part_number = db.Column(db.String(100), nullable=True)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "point_type": self.point_type, "part_number": self.part_number}

class EquipmentTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type_key = db.Column(db.String(50), unique=True, nullable=False) 
    name = db.Column(db.String(120), nullable=False)
    available_points = db.relationship('PointTemplate', secondary=template_points_association, backref='equipment_templates', lazy='dynamic')

    def to_dict(self):
        return {"id": self.id, "name": self.name, "points": [point.id for point in self.available_points]}

class ScheduledEquipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    instance_name = db.Column(db.String(120), nullable=False)
    # NEW: Quantity field
    quantity = db.Column(db.Integer, nullable=False, default=1)
    panel_id = db.Column(db.Integer, db.ForeignKey('panel.id'), nullable=False)
    equipment_template_id = db.Column(db.Integer, db.ForeignKey('equipment_template.id'), nullable=False)
    equipment_template = db.relationship('EquipmentTemplate')
    selected_points = db.relationship('PointTemplate', secondary=selected_points_association, lazy='dynamic')

    def to_dict(self):
        return {
            "id": self.id,
            "panelName": self.panel.panel_name,
            "instanceName": self.instance_name,
            "quantity": self.quantity,
            "type": self.equipment_template.type_key,
            "selectedPoints": [point.id for point in self.selected_points]
        }


# --- API ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data', methods=['GET'])
def get_all_data():
    panels = [p.to_dict() for p in Panel.query.all()]
    scheduled_equipment = [e.to_dict() for e in ScheduledEquipment.query.all()]
    point_templates = {pt.id: pt.to_dict() for pt in PointTemplate.query.all()}
    equipment_templates = {et.type_key: et.to_dict() for et in EquipmentTemplate.query.all()}
    
    return jsonify({
        "panels": panels,
        "scheduledEquipment": scheduled_equipment,
        "pointTemplates": point_templates,
        "equipmentTemplates": equipment_templates
    })

@app.route('/api/panel', methods=['POST'])
def add_panel():
    data = request.get_json()
    new_panel = Panel(panel_name=data['panelName'], floor=data['floor'])
    db.session.add(new_panel)
    db.session.commit()
    return jsonify(new_panel.to_dict()), 201

@app.route('/api/equipment', methods=['POST'])
def add_equipment():
    """Add a new piece of scheduled equipment."""
    data = request.get_json()
    
    # FIX: Find the panel, OR create it if it doesn't exist.
    panel = Panel.query.filter_by(panel_name=data['panelName']).first()
    if not panel:
        panel = Panel(panel_name=data['panelName'], floor=data['floor'])
        db.session.add(panel)
        db.session.commit()

    template = EquipmentTemplate.query.filter_by(type_key=data['type']).first_or_404()
    
    new_equip = ScheduledEquipment(
        instance_name=data['instanceName'],
        quantity=data.get('quantity', 1),
        panel_id=panel.id,
        equipment_template_id=template.id
    )
    
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['selectedPoints'])).all()
    new_equip.selected_points.extend(points)
    
    db.session.add(new_equip)
    db.session.commit()
    return jsonify(new_equip.to_dict()), 201

@app.route('/api/equipment/<int:id>', methods=['PUT'])
def update_equipment(id):
    """Update an existing piece of scheduled equipment."""
    data = request.get_json()
    equip = ScheduledEquipment.query.get_or_404(id)
    
    # FIX: Find the panel, OR create it if it doesn't exist.
    panel = Panel.query.filter_by(panel_name=data['panelName']).first()
    if not panel:
        panel = Panel(panel_name=data['panelName'], floor=data['floor'])
        db.session.add(panel)
        db.session.commit()

    template = EquipmentTemplate.query.filter_by(type_key=data['type']).first_or_404()

    equip.instance_name = data['instanceName']
    equip.quantity = data.get('quantity', 1)
    equip.panel_id = panel.id
    equip.equipment_template_id = template.id
    
    equip.selected_points = []
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['selectedPoints'])).all()
    equip.selected_points.extend(points)
        
    db.session.commit()
    return jsonify(equip.to_dict()), 200

# --- NEW/UPDATED ROUTES FOR LIBRARY MANAGEMENT ---

@app.route('/api/points', methods=['POST'])
def add_point():
    data = request.get_json()
    
    # FIX: Check for existing point name before saving
    existing = PointTemplate.query.filter_by(name=data['name']).first()
    if existing:
        return jsonify({"error": f"A point named '{data['name']}' already exists."}), 409

    new_point = PointTemplate(name=data['name'], point_type=data['point_type'], part_number=data.get('part_number'))
    db.session.add(new_point)
    db.session.commit()
    return jsonify(new_point.to_dict()), 201

@app.route('/api/points/<int:id>', methods=['PUT'])
def update_point(id):
    data = request.get_json()
    point = PointTemplate.query.get_or_404(id)
    point.name = data['name']
    point.point_type = data['point_type']
    point.part_number = data.get('part_number')
    db.session.commit()
    return jsonify(point.to_dict()), 200

@app.route('/api/points/<int:id>', methods=['DELETE'])
def delete_point(id):
    point = PointTemplate.query.get_or_404(id)
    # Check if point is used in any template
    if db.session.query(template_points_association).filter_by(point_template_id=id).first():
        return jsonify({"error": "Point is currently used by an equipment template and cannot be deleted."}), 409
    db.session.delete(point)
    db.session.commit()
    return jsonify({"message": "Point deleted"}), 200

@app.route('/api/equipment_templates', methods=['POST'])
def add_equipment_template():
    """Create a new equipment template."""
    data = request.get_json()
    if not all(k in data for k in ['typeKey', 'name', 'pointIds']):
        return jsonify({"error": "Missing data"}), 400
    
    # FIX: Check for existing key before saving to prevent crashing
    existing = EquipmentTemplate.query.filter_by(type_key=data['typeKey']).first()
    if existing:
        return jsonify({"error": f"Equipment type key '{data['typeKey']}' already exists."}), 409

    new_template = EquipmentTemplate(type_key=data['typeKey'], name=data['name'])
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['pointIds'])).all()
    new_template.available_points.extend(points)
    
    db.session.add(new_template)
    db.session.commit()
    return jsonify({data['typeKey']: new_template.to_dict()}), 201

@app.route('/api/equipment_templates/<string:key>', methods=['PUT'])
def update_equipment_template(key):
    """Update an existing equipment template."""
    data = request.get_json()
    template = EquipmentTemplate.query.filter_by(type_key=key).first_or_404()

    # FIX: Check if the new key is already taken by another template
    new_key = data['typeKey']
    if key != new_key:
        existing = EquipmentTemplate.query.filter_by(type_key=new_key).first()
        if existing:
            return jsonify({"error": f"Equipment type key '{new_key}' already exists."}), 409

    template.name = data['name']
    template.type_key = new_key
    template.available_points = []
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['pointIds'])).all()
    template.available_points.extend(points)
    
    db.session.commit()
    return jsonify({template.type_key: template.to_dict()}), 200

@app.route('/api/equipment_templates/<string:key>/replicate', methods=['POST'])
def replicate_equipment_template(key):
    original = EquipmentTemplate.query.filter_by(type_key=key).first_or_404()
    
    # Find a unique new key and name
    i = 1
    while True:
        new_key = f"{original.type_key}_copy{i}"
        if not EquipmentTemplate.query.filter_by(type_key=new_key).first():
            break
        i += 1
    new_name = f"{original.name} (Copy {i})"
    
    replicated = EquipmentTemplate(type_key=new_key, name=new_name)
    replicated.available_points.extend(original.available_points)
    
    db.session.add(replicated)
    db.session.commit()
    return jsonify({replicated.type_key: replicated.to_dict()}), 201

# --- DB INITIALIZATION & RUN ---

def setup_database(app):
    with app.app_context():
        db.create_all()
        if PointTemplate.query.first() is None:
            print("Database is empty. Populating with initial data...")
            points = [
                PointTemplate(id=1, name="Supply Air Temp", point_type="AI", part_number="T-S-10k"),
                PointTemplate(id=2, name="Return Air Temp", point_type="AI", part_number="T-S-10k"),
                PointTemplate(id=3, name="Filter Status", point_type="DI", part_number="P-SWITCH-1"),
                PointTemplate(id=4, name="Fan Status", point_type="DI"),
                PointTemplate(id=5, name="Compressor Status", point_type="DI"),
                PointTemplate(id=6, name="Fan Start/Stop", point_type="DO"),
                PointTemplate(id=7, name="Cooling Valve", point_type="AO", part_number="V-MOD-1"),
                PointTemplate(id=8, name="Heating Valve", point_type="AO", part_number="V-MOD-2"),
                PointTemplate(id=9, name="Reversing Valve", point_type="DO"),
                PointTemplate(id=10, name="Zone CO2 Level", point_type="BACnet"),
                PointTemplate(id=11, name="VFD Speed", point_type="Modbus")
            ]
            db.session.add_all(points)
            
            et_ahu = EquipmentTemplate(type_key='ahu', name='Air Handling Unit')
            et_ahu.available_points.extend([p for p in points if p.id in [1, 2, 3, 4, 6, 7, 8, 11]])
            et_fcu = EquipmentTemplate(type_key='fcu', name='Fan Coil Unit')
            et_fcu.available_points.extend([p for p in points if p.id in [1, 4, 6, 7]])
            et_hp = EquipmentTemplate(type_key='hp', name='Heat Pump')
            et_hp.available_points.extend([p for p in points if p.id in [1, 2, 5, 6, 9]])
            db.session.add_all([et_ahu, et_fcu, et_hp])

            p1 = Panel(panel_name="LP-GF-01", floor="Ground Floor")
            p2 = Panel(panel_name="LP-L1-01", floor="Level 1")
            db.session.add_all([p1, p2])
            db.session.commit()

            se1 = ScheduledEquipment(instance_name="AHU-GF-01", quantity=1, panel_id=p1.id, equipment_template_id=et_ahu.id)
            se1.selected_points.extend([p for p in points if p.id in [1,3,4,6,8]])
            se2 = ScheduledEquipment(instance_name="VAV-GF-Zone", quantity=5, panel_id=p1.id, equipment_template_id=et_fcu.id)
            se2.selected_points.extend([p for p in points if p.id in [4,6,10]])
            db.session.add_all([se1, se2])
            db.session.commit()
            print("Database populated successfully.")


if __name__ == '__main__':
    setup_database(app)
    app.run(debug=True)