from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import csv

# --- APP SETUP ---
app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'bms_tool.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'  # Change this!
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
socketio = SocketIO(app)

# --- DATABASE MODELS (The Schema) ---

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    projects = db.relationship('Project', backref='owner', lazy=True)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Part(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    part_number = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(120))
    country_of_origin = db.Column(db.String(120))
    cable_recommendation = db.Column(db.String(120))
    cost = db.Column(db.Float)

    def to_dict(self):
        return {
            "id": self.id,
            "part_number": self.part_number,
            "description": self.description,
            "category": self.category,
            "country_of_origin": self.country_of_origin,
            "cable_recommendation": self.cable_recommendation,
            "cost": self.cost
        }

class SubPointTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    point_type = db.Column(db.String(50), nullable=False)
    parent_point_template_id = db.Column(db.Integer, db.ForeignKey('point_template.id'), nullable=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "point_type": self.point_type}

class PointTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    part_id = db.Column(db.Integer, db.ForeignKey('part.id'), nullable=True)
    part = db.relationship('Part')
    sub_points = db.relationship('SubPointTemplate', backref='parent_point', lazy='dynamic', cascade="all, delete-orphan")

    def to_dict(self):
        point_name = self.name
        if self.part:
            point_name = f"{self.part.part_number} - {self.part.description}"
            if self.part.country_of_origin:
                point_name += f" (Made in {self.part.country_of_origin})"
            if self.part.cable_recommendation:
                 point_name += f" [Cable: {self.part.cable_recommendation}]"

        return {
            "id": self.id, 
            "name": point_name, 
            "quantity": self.quantity, 
            "sub_points": [sp.to_dict() for sp in self.sub_points],
            "part_id": self.part_id
        }

class EquipmentTemplatePoint(db.Model):
    equipment_template_id = db.Column(db.Integer, db.ForeignKey('equipment_template.id'), primary_key=True)
    point_template_id = db.Column(db.Integer, db.ForeignKey('point_template.id'), primary_key=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    point = db.relationship('PointTemplate')

class EquipmentTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    type_key = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    available_points = db.relationship('EquipmentTemplatePoint', backref='equipment_template', lazy='dynamic', cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "type_key": self.type_key, "name": self.name, "points": [{"id": etp.point_template_id, "quantity": etp.quantity} for etp in self.available_points]}

selected_points_association = db.Table('selected_points',
    db.Column('scheduled_equipment_id', db.Integer, db.ForeignKey('scheduled_equipment.id')),
    db.Column('point_template_id', db.Integer, db.ForeignKey('point_template.id'))
)

class ScheduledEquipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    instance_name = db.Column(db.String(120), nullable=False)
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

class Panel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    panel_name = db.Column(db.String(80), nullable=False)
    floor = db.Column(db.String(80), nullable=False)
    equipment = db.relationship('ScheduledEquipment', backref='panel', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "panelName": self.panel_name, "floor": self.floor}

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- AUTH ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('project_selection'))
    if request.method == 'POST':
        data = request.get_json()
        user = User.query.filter_by(username=data['username']).first()
        if user and bcrypt.check_password_hash(user.password, data['password']):
            login_user(user)
            return jsonify({"success": True, "redirect": url_for('project_selection')})
        else:
            return jsonify({"success": False, "error": "Login Unsuccessful. Please check username and password"}), 401
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('project_selection'))
    if request.method == 'POST':
        data = request.get_json()
        hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
        user = User(username=data['username'], password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in', 'success')
        return jsonify({"success": True, "redirect": url_for('login')})
    return render_template('register.html')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- PROJECT ROUTES ---

@app.route('/projects')
@login_required
def project_selection():
    projects = Project.query.filter_by(user_id=current_user.id).all()
    return render_template('projects.html', projects=projects)

@app.route('/projects/create', methods=['POST'])
@login_required
def create_project():
    data = request.get_json()
    project = Project(name=data['name'], owner=current_user)
    db.session.add(project)
    db.session.commit()
    return jsonify({"success": True, "redirect": url_for('index', project_id=project.id)})

# --- MAIN APP ROUTES ---

@app.route('/')
@login_required
def index():
    project_id = request.args.get('project_id', type=int)
    if not project_id:
        return redirect(url_for('project_selection'))
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        flash("You do not have permission to access this project.", "danger")
        return redirect(url_for('project_selection'))
    return render_template('index.html', project_id=project.id)

@app.route('/api/data/<int:project_id>', methods=['GET'])
@login_required
def get_all_data(project_id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    panels = [p.to_dict() for p in Panel.query.filter_by(project_id=project_id).all()]
    scheduled_equipment = [e.to_dict() for e in ScheduledEquipment.query.filter_by(project_id=project_id).all()]
    point_templates = {pt.id: pt.to_dict() for pt in PointTemplate.query.filter_by(project_id=project_id).all()}
    equipment_templates = {et.type_key: et.to_dict() for et in EquipmentTemplate.query.filter_by(project_id=project_id).all()}
    parts = {p.id: p.to_dict() for p in Part.query.filter_by(project_id=project_id).all()}
    
    return jsonify({
        "panels": panels,
        "scheduledEquipment": scheduled_equipment,
        "pointTemplates": point_templates,
        "equipmentTemplates": equipment_templates,
        "parts": parts
    })

@app.route('/api/panel/<int:project_id>', methods=['POST'])
@login_required
def add_panel(project_id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    new_panel = Panel(panel_name=data['panelName'], floor=data['floor'], project_id=project_id)
    db.session.add(new_panel)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify(new_panel.to_dict()), 201
@app.route('/api/panel/<int:panel_id>/point_summary', methods=['GET'])
@login_required
def panel_point_summary(panel_id):
    panel = Panel.query.get_or_404(panel_id)
    project = Project.query.get_or_404(panel.project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    summary = {}

    equipments = ScheduledEquipment.query.filter_by(project_id=project.id, panel_id=panel_id).all()
    for equip in equipments:
        equip_qty = equip.quantity or 1
        template = equip.equipment_template

        # selected_points is lazy='dynamic' so use .all() when available
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points

        for pt in selected_points:
            # find the per-template quantity for this point (if any)
            etp = EquipmentTemplatePoint.query.filter_by(equipment_template_id=template.id, point_template_id=pt.id).first()
            per_template_qty = etp.quantity if etp and etp.quantity else 1
            point_repeat = (pt.quantity or 1) * per_template_qty * equip_qty

            sub_points = pt.sub_points.all() if hasattr(pt.sub_points, 'all') else pt.sub_points
            if not sub_points:
                # fallback for points without defined sub-points
                summary['UNKNOWN'] = summary.get('UNKNOWN', 0) + point_repeat
            else:
                for sp in sub_points:
                    summary[sp.point_type] = summary.get(sp.point_type, 0) + point_repeat

    return jsonify(summary), 200
@app.route('/api/equipment/<int:project_id>', methods=['POST'])
@login_required
def add_equipment(project_id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    
    panel = Panel.query.filter_by(panel_name=data['panelName'], project_id=project_id).first()
    if not panel:
        panel = Panel(panel_name=data['panelName'], floor=data['floor'], project_id=project_id)
        db.session.add(panel)
        db.session.commit()

    # Frontend sends equipment type as the template's type_key
    template = EquipmentTemplate.query.filter_by(type_key=data['type'], project_id=project_id).first_or_404()
    
    new_equip = ScheduledEquipment(
        instance_name=data['instanceName'],
        quantity=data.get('quantity', 1),
        panel_id=panel.id,
        equipment_template_id=template.id,
        project_id=project_id
    )
    
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['selectedPoints'])).all()
    new_equip.selected_points.extend(points)
    
    db.session.add(new_equip)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify(new_equip.to_dict()), 201

@app.route('/api/equipment/<int:project_id>/<int:id>', methods=['PUT'])
@login_required
def update_equipment(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    equip = ScheduledEquipment.query.get_or_404(id)
    
    panel = Panel.query.filter_by(panel_name=data['panelName'], project_id=project_id).first()
    if not panel:
        panel = Panel(panel_name=data['panelName'], floor=data['floor'], project_id=project_id)
        db.session.add(panel)
        db.session.commit()

    # Frontend sends equipment type as the template's type_key
    template = EquipmentTemplate.query.filter_by(type_key=data['type'], project_id=project_id).first_or_404()

    equip.instance_name = data['instanceName']
    equip.quantity = data.get('quantity', 1)
    equip.panel_id = panel.id
    equip.equipment_template_id = template.id
    
    equip.selected_points = []
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['selectedPoints'])).all()
    equip.selected_points.extend(points)
        
    db.session.commit()
    broadcast_update(project_id)
    return jsonify(equip.to_dict()), 200

@app.route('/api/points/<int:project_id>', methods=['POST'])
@login_required
def add_point(project_id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    
    new_point = PointTemplate(name=data['name'], quantity=data.get('quantity', 1), part_id=data.get('part_id'), project_id=project_id)
    for sp_data in data.get('sub_points', []):
        sp = SubPointTemplate(name=sp_data['name'], point_type=sp_data['point_type'])
        new_point.sub_points.append(sp)

    db.session.add(new_point)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify(new_point.to_dict()), 201

@app.route('/api/points/<int:project_id>/<int:id>', methods=['PUT'])
@login_required
def update_point(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    point = PointTemplate.query.get_or_404(id)
    point.name = data['name']
    point.quantity = data.get('quantity', 1)
    point.part_id = data.get('part_id')
    
    # Remove old sub-points
    for sp in point.sub_points:
        db.session.delete(sp)

    # Add new sub-points
    for sp_data in data.get('sub_points', []):
        sp = SubPointTemplate(name=sp_data['name'], point_type=sp_data['point_type'])
        point.sub_points.append(sp)

    db.session.commit()
    broadcast_update(project_id)
    return jsonify(point.to_dict()), 200

@app.route('/api/points/<int:project_id>/<int:id>', methods=['DELETE'])
@login_required
def delete_point(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    point = PointTemplate.query.get_or_404(id)
    if EquipmentTemplatePoint.query.filter_by(point_template_id=id).first():
        return jsonify({"error": "Point is currently used by an equipment template and cannot be deleted."}), 409
    db.session.delete(point)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify({"message": "Point deleted"}), 200

@app.route('/api/equipment_templates/<int:project_id>', methods=['POST'])
@login_required
def add_equipment_template(project_id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    if not all(k in data for k in ['typeKey', 'name', 'points']):
        return jsonify({"error": "Missing data"}), 400
    
    existing = EquipmentTemplate.query.filter_by(type_key=data['typeKey'], project_id=project_id).first()
    if existing:
        return jsonify({"error": f"Equipment type key '{data['typeKey']}' already exists."}), 409

    new_template = EquipmentTemplate(type_key=data['typeKey'], name=data['name'], project_id=project_id)
    for point_data in data['points']:
        point = PointTemplate.query.get(point_data['id'])
        if point:
            etp = EquipmentTemplatePoint(point=point, quantity=point_data.get('quantity', 1))
            new_template.available_points.append(etp)
    
    db.session.add(new_template)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify({new_template.id: new_template.to_dict()}), 201

@app.route('/api/equipment_templates/<int:project_id>/<string:key>', methods=['PUT'])
@login_required
def update_equipment_template(project_id, key):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    template = EquipmentTemplate.query.filter_by(type_key=key, project_id=project_id).first_or_404()

    new_key = data['typeKey']
    if template.type_key != new_key:
        existing = EquipmentTemplate.query.filter_by(type_key=new_key, project_id=project_id).first()
        if existing:
            return jsonify({"error": f"Equipment type key '{new_key}' already exists."}), 409

    template.name = data['name']
    template.type_key = new_key
    template.available_points = []
    for point_data in data['points']:
        point = PointTemplate.query.get(point_data['id'])
        if point:
            etp = EquipmentTemplatePoint(point=point, quantity=point_data.get('quantity', 1))
            template.available_points.append(etp)
    
    db.session.commit()
    broadcast_update(project_id)
    return jsonify({template.id: template.to_dict()}), 200

@app.route('/api/equipment_templates/<int:project_id>/<int:id>/replicate', methods=['POST'])
@login_required
def replicate_equipment_template(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    original = EquipmentTemplate.query.get_or_404(id)
    
    i = 1
    while True:
        new_key = f"{original.type_key}_copy{i}"
        if not EquipmentTemplate.query.filter_by(type_key=new_key, project_id=project_id).first():
            break
        i += 1
    new_name = f"{original.name} (Copy {i})"
    
    replicated = EquipmentTemplate(type_key=new_key, name=new_name, project_id=project_id)
    for etp in original.available_points:
        replicated.available_points.append(EquipmentTemplatePoint(point=etp.point, quantity=etp.quantity))
    
    db.session.add(replicated)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify({replicated.id: replicated.to_dict()}), 201

@app.route('/api/parts/<int:project_id>', methods=['POST'])
@login_required
def add_part(project_id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    if Part.query.filter_by(part_number=data['part_number'], project_id=project_id).first():
        return jsonify({"error": f"Part number '{data['part_number']}' already exists."}), 409
    new_part = Part(
        part_number=data['part_number'],
        description=data['description'],
        category=data.get('category'),
        cost=data.get('cost'),
        country_of_origin=data.get('country_of_origin'),
        cable_recommendation=data.get('cable_recommendation'),
        project_id=project_id
    )
    db.session.add(new_part)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify(new_part.to_dict()), 201

@app.route('/api/parts/<int:project_id>/<int:id>', methods=['PUT'])
@login_required
def update_part(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    part = Part.query.get_or_404(id)
    if part.part_number != data['part_number'] and Part.query.filter_by(part_number=data['part_number'], project_id=project_id).first():
        return jsonify({"error": f"Part number '{data['part_number']}' already exists."}), 409
    part.part_number = data['part_number']
    part.description = data['description']
    part.category = data.get('category')
    part.cost = data.get('cost')
    part.country_of_origin = data.get('country_of_origin')
    part.cable_recommendation = data.get('cable_recommendation')
    db.session.commit()
    broadcast_update(project_id)
    return jsonify(part.to_dict()), 200

@app.route('/api/parts/<int:project_id>/<int:id>', methods=['DELETE'])
@login_required
def delete_part(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    part = Part.query.get_or_404(id)
    db.session.delete(part)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify({"message": "Part deleted"}), 200

# --- SOCKET.IO ---

@socketio.on('join')
def on_join(data):
    room = data['project_id']
    join_room(room)

@socketio.on('leave')
def on_leave(data):
    room = data['project_id']
    leave_room(room)

def broadcast_update(project_id):
    socketio.emit('update', {'project_id': project_id}, room=project_id)

# --- DB INITIALIZATION & RUN ---

def setup_database(app):
    with app.app_context():
        db.create_all()

if __name__ == '__main__':
    setup_database(app)
    socketio.run(app, debug=True)
