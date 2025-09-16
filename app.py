from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import csv
from datetime import datetime

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
    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=db.func.current_timestamp())
    projects = db.relationship('Project', backref='owner', lazy=True)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Part(db.Model):
    __table_args__ = (db.UniqueConstraint('part_number', name='uq_part_number'),)
    id = db.Column(db.Integer, primary_key=True)
    # Global catalog: project_id dropped logically (legacy column may linger in sqlite file)
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
            if not user.is_approved:
                return jsonify({"success": False, "error": "Your account is pending approval. Please contact an administrator."}), 401
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
        
        # Check if user already exists
        existing_user = User.query.filter_by(username=data['username']).first()
        if existing_user:
            return jsonify({"success": False, "error": "Username already exists"}), 400
            
        try:
            hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
            user = User(username=data['username'], password=hashed_password, is_approved=False)
            db.session.add(user)
            db.session.commit()
            flash('Your account has been created and is pending approval. You will be notified when you can log in.', 'success')
            return jsonify({"success": True, "redirect": url_for('login')})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": "Registration failed"}), 500
    return render_template('register.html')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- ADMIN ROUTES ---

def admin_required(f):
    """Decorator to require admin privileges"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for('project_selection'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    users = User.query.order_by(User.created_at.desc()).all()
    pending_users = User.query.filter_by(is_approved=False).count()
    total_users = User.query.count()
    total_projects = Project.query.count()
    
    stats = {
        'total_users': total_users,
        'pending_users': pending_users,
        'approved_users': total_users - pending_users,
        'total_projects': total_projects
    }
    
    return render_template('admin.html', users=users, stats=stats)

@app.route('/admin/users/<int:user_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    flash(f'User {user.username} has been approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/toggle-admin', methods=['POST'])
@login_required
@admin_required
def toggle_user_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot modify your own admin status.", "danger")
        return redirect(url_for('admin_dashboard'))
    
    user.is_admin = not user.is_admin
    db.session.commit()
    
    action = "granted" if user.is_admin else "revoked"
    flash(f'Admin privileges {action} for user {user.username}.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for('admin_dashboard'))
    
    data = request.get_json() or {}
    if data.get('confirmUsername') != user.username:
        return jsonify({"error": "Username confirmation mismatch"}), 400
    
    # Delete user's projects first
    for project in user.projects:
        # Delete project data as in the existing delete_project route
        ScheduledEquipment.query.filter_by(project_id=project.id).delete(synchronize_session=False)
        for panel in Panel.query.filter_by(project_id=project.id).all():
            db.session.delete(panel)
        db.session.delete(project)
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {username} and all associated data has been deleted.', 'success')
    return jsonify({"success": True})

# --- PROJECT ROUTES ---

@app.route('/projects')
@login_required
def project_selection():
    projects = Project.query.filter_by(user_id=current_user.id).all()
    
    # Add stats for each project
    project_stats = []
    for project in projects:
        panels_count = Panel.query.filter_by(project_id=project.id).count()
        equipment_count = ScheduledEquipment.query.filter_by(project_id=project.id).count()
        
        # Count total selected points across all equipment in the project
        points_count = db.session.query(db.func.count(selected_points_association.c.point_template_id))\
            .join(ScheduledEquipment, selected_points_association.c.scheduled_equipment_id == ScheduledEquipment.id)\
            .filter(ScheduledEquipment.project_id == project.id).scalar() or 0
        
        project_stats.append({
            'project': project,
            'panels_count': panels_count,
            'equipment_count': equipment_count,
            'points_count': points_count
        })
    
    return render_template('projects.html', project_stats=project_stats)

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
    # Point & equipment templates now treated as global (still retain stored project_id for provenance)
    point_templates = {pt.id: pt.to_dict() for pt in PointTemplate.query.all()}
    equipment_templates = {et.type_key: et.to_dict() for et in EquipmentTemplate.query.all()}
    # Global parts (sorted for stable UI)
    parts = {p.id: p.to_dict() for p in Part.query.order_by(Part.part_number).all()}
    
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

@app.route('/api/project/<int:project_id>/summary', methods=['GET'])
@login_required
def project_summary(project_id):
    """Get cumulative I/O point summary for all panels in a project."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    panels = Panel.query.filter_by(project_id=project_id).all()
    project_summary = {
        'project_name': project.name,
        'total_points': {},
        'panels': []
    }
    
    total_summary = {}
    
    for panel in panels:
        panel_summary = {}
        equipments = ScheduledEquipment.query.filter_by(project_id=project_id, panel_id=panel.id).all()
        
        for equip in equipments:
            equip_qty = equip.quantity or 1
            template = equip.equipment_template
            selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points

            for pt in selected_points:
                etp = EquipmentTemplatePoint.query.filter_by(equipment_template_id=template.id, point_template_id=pt.id).first()
                per_template_qty = etp.quantity if etp and etp.quantity else 1
                point_repeat = (pt.quantity or 1) * per_template_qty * equip_qty

                sub_points = pt.sub_points.all() if hasattr(pt.sub_points, 'all') else pt.sub_points
                if not sub_points:
                    panel_summary['UNKNOWN'] = panel_summary.get('UNKNOWN', 0) + point_repeat
                    total_summary['UNKNOWN'] = total_summary.get('UNKNOWN', 0) + point_repeat
                else:
                    for sp in sub_points:
                        panel_summary[sp.point_type] = panel_summary.get(sp.point_type, 0) + point_repeat
                        total_summary[sp.point_type] = total_summary.get(sp.point_type, 0) + point_repeat
        
        project_summary['panels'].append({
            'id': panel.id,
            'name': panel.panel_name,
            'floor': panel.floor,
            'points': panel_summary
        })
    
    project_summary['total_points'] = total_summary
    return jsonify(project_summary), 200

@app.route('/summary/<int:project_id>')
@login_required
def summary_page(project_id):
    """Render the project summary page."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        flash("You do not have permission to access this project.", "danger")
        return redirect(url_for('project_selection'))
    return render_template('summary.html', project_id=project.id)
@app.route('/api/panel/<int:project_id>/<int:panel_id>', methods=['DELETE'])
@login_required
def delete_panel(project_id, panel_id):
    """Delete a panel (and its scheduled equipment via cascade) after strong confirmation."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    panel = Panel.query.get_or_404(panel_id)
    if panel.project_id != project_id:
        return jsonify({"error": "Panel does not belong to project"}), 400
    data = request.get_json() or {}
    if data.get('confirmName') != panel.panel_name or data.get('confirmWord') != 'DELETE':
        return jsonify({"error": "Confirmation mismatch"}), 400
    panel_name = panel.panel_name
    db.session.delete(panel)
    db.session.commit()
    broadcast_update(project_id)
    return jsonify({"message": f"Panel '{panel_name}' deleted"}), 200
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
    template = EquipmentTemplate.query.filter_by(type_key=data['type']).first_or_404()
    
    new_equip = ScheduledEquipment(
        instance_name=data['instanceName'],
        quantity=data.get('quantity', 1),
        panel_id=panel.id,
        equipment_template_id=template.id,
        project_id=project_id
    )
    
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['selectedPoints'])).all()
    # Ensure uniqueness to avoid duplicate association rows
    seen = set()
    for p in points:
        if p.id not in seen:
            new_equip.selected_points.append(p)
            seen.add(p.id)
    
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
    template = EquipmentTemplate.query.filter_by(type_key=data['type']).first_or_404()

    equip.instance_name = data['instanceName']
    equip.quantity = data.get('quantity', 1)
    equip.panel_id = panel.id
    equip.equipment_template_id = template.id
    
    # Clear existing associations safely (dynamic relationship supports clear())
    if hasattr(equip.selected_points, 'clear'):
        equip.selected_points.clear()
    else:
        equip.selected_points = []
    points = PointTemplate.query.filter(PointTemplate.id.in_(data['selectedPoints'])).all()
    seen = set()
    for p in points:
        if p.id not in seen:
            equip.selected_points.append(p)
            seen.add(p.id)
        
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
    broadcast_global_catalog()
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
    broadcast_global_catalog()
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
    broadcast_global_catalog()
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
    
    existing = EquipmentTemplate.query.filter_by(type_key=data['typeKey']).first()
    if existing:
        return jsonify({"error": f"Equipment type key '{data['typeKey']}' already exists."}), 409

    new_template = EquipmentTemplate(type_key=data['typeKey'], name=data['name'], project_id=project_id)  # project_id retained for ownership metadata only
    for point_data in data['points']:
        point = PointTemplate.query.get(point_data['id'])
        if point:
            etp = EquipmentTemplatePoint(point=point, quantity=point_data.get('quantity', 1))
            new_template.available_points.append(etp)
    
    db.session.add(new_template)
    db.session.commit()
    broadcast_update(project_id)
    broadcast_global_catalog()
    return jsonify({new_template.id: new_template.to_dict()}), 201

@app.route('/api/equipment_templates/<int:project_id>/<string:key>', methods=['PUT'])
@login_required
def update_equipment_template(project_id, key):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    template = EquipmentTemplate.query.filter_by(type_key=key).first_or_404()

    new_key = data['typeKey']
    # Only enforce uniqueness check if the key is actually changing
    if template.type_key != new_key:
        existing = EquipmentTemplate.query.filter_by(type_key=new_key).first()
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
    broadcast_global_catalog()
    return jsonify({template.id: template.to_dict()}), 200

@app.route('/api/equipment_templates/<int:project_id>/<int:id>/replicate', methods=['POST'])
@login_required
def replicate_equipment_template(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    original = EquipmentTemplate.query.get_or_404(id)
    # Replication allowed across projects (global templates)
    
    i = 1
    while True:
        new_key = f"{original.type_key}_copy{i}"
        if not EquipmentTemplate.query.filter_by(type_key=new_key).first():
            break
        i += 1
    new_name = f"{original.name} (Copy {i})"
    
    replicated = EquipmentTemplate(type_key=new_key, name=new_name, project_id=project_id)
    for etp in original.available_points:
        replicated.available_points.append(EquipmentTemplatePoint(point=etp.point, quantity=etp.quantity))
    
    db.session.add(replicated)
    db.session.commit()
    broadcast_update(project_id)
    broadcast_global_catalog()
    return jsonify({replicated.id: replicated.to_dict()}), 201

@app.route('/api/parts/<int:project_id>', methods=['POST'])
@login_required
def add_part(project_id):
    # Keep project ownership check for authorization gating
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    if Part.query.filter_by(part_number=data['part_number']).first():
        return jsonify({"error": f"Part number '{data['part_number']}' already exists globally."}), 409
    new_part = Part(
        part_number=data['part_number'],
        description=data['description'],
        category=data.get('category'),
        cost=data.get('cost'),
        country_of_origin=data.get('country_of_origin'),
        cable_recommendation=data.get('cable_recommendation')
    )
    db.session.add(new_part)
    db.session.commit()
    broadcast_update(project_id)
    broadcast_global_catalog()
    return jsonify(new_part.to_dict()), 201

@app.route('/api/parts/<int:project_id>/<int:id>', methods=['PUT'])
@login_required
def update_part(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    part = Part.query.get_or_404(id)
    if part.part_number != data['part_number'] and Part.query.filter_by(part_number=data['part_number']).first():
        return jsonify({"error": f"Part number '{data['part_number']}' already exists globally."}), 409
    part.part_number = data['part_number']
    part.description = data['description']
    part.category = data.get('category')
    part.cost = data.get('cost')
    part.country_of_origin = data.get('country_of_origin')
    part.cable_recommendation = data.get('cable_recommendation')
    db.session.commit()
    broadcast_update(project_id)
    broadcast_global_catalog()
    return jsonify(part.to_dict()), 200

@app.route('/api/parts/<int:project_id>/<int:id>', methods=['DELETE'])
@login_required
def delete_part(project_id, id):
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    part = Part.query.get_or_404(id)
    if PointTemplate.query.filter_by(part_id=id).first():
        return jsonify({"error": "Part is referenced by one or more point templates."}), 409
    db.session.delete(part)
    db.session.commit()
    broadcast_update(project_id)
    broadcast_global_catalog()
    return jsonify({"message": "Part deleted"}), 200

# --- GLOBAL READ-ONLY CATALOG ENDPOINTS ---
@app.route('/api/equipment_templates', methods=['GET'])
@login_required
def list_equipment_templates():
    return jsonify({et.type_key: et.to_dict() for et in EquipmentTemplate.query.all()}), 200

@app.route('/api/point_templates', methods=['GET'])
@login_required
def list_point_templates():
    return jsonify({pt.id: pt.to_dict() for pt in PointTemplate.query.all()}), 200

# --- SOCKET.IO ---

# Store active users per project
active_users = {}

@socketio.on('join')
def on_join(data):
    room = data['project_id']
    username = current_user.username if current_user.is_authenticated else 'Anonymous'
    join_room(room)
    
    # Track active users
    if room not in active_users:
        active_users[room] = set()
    active_users[room].add(username)
    
    # Notify others that user joined
    emit('user_joined', {
        'username': username,
        'message': f'{username} joined the project',
        'active_users': list(active_users[room])
    }, room=room, include_self=False)
    
    # Send current active users to the user who just joined
    emit('active_users_update', {'active_users': list(active_users[room])})

@socketio.on('leave')
def on_leave(data):
    room = data['project_id']
    username = current_user.username if current_user.is_authenticated else 'Anonymous'
    leave_room(room)
    
    # Remove from active users
    if room in active_users:
        active_users[room].discard(username)
        if len(active_users[room]) == 0:
            del active_users[room]
        else:
            # Notify others that user left
            emit('user_left', {
                'username': username,
                'message': f'{username} left the project',
                'active_users': list(active_users[room])
            }, room=room)

@socketio.on('disconnect')
def on_disconnect():
    username = current_user.username if current_user.is_authenticated else 'Anonymous'
    # Remove user from all rooms
    for room in list(active_users.keys()):
        if username in active_users[room]:
            active_users[room].discard(username)
            if len(active_users[room]) == 0:
                del active_users[room]
            else:
                emit('user_left', {
                    'username': username,
                    'message': f'{username} disconnected',
                    'active_users': list(active_users[room])
                }, room=room)

@socketio.on('user_action')
def on_user_action(data):
    """Handle real-time user actions like editing, adding equipment, etc."""
    room = data.get('project_id')
    username = current_user.username if current_user.is_authenticated else 'Anonymous'
    action_type = data.get('action_type')
    action_data = data.get('data', {})
    
    # Broadcast action to other users in the room
    emit('real_time_action', {
        'username': username,
        'action_type': action_type,
        'data': action_data,
        'timestamp': data.get('timestamp')
    }, room=room, include_self=False)

def broadcast_update(project_id):
    """Enhanced broadcast with user action info"""
    socketio.emit('update', {
        'project_id': project_id,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'updated_by': current_user.username if current_user.is_authenticated else 'System'
    }, room=project_id)

def broadcast_global_catalog():
    """Emit event notifying clients that global catalogs (templates/points/parts) changed."""
    socketio.emit('global_catalog_update', {
        'updated_by': current_user.username if current_user.is_authenticated else 'System',
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/projects/<int:project_id>', methods=['DELETE'])
@login_required
def delete_project(project_id):
    """Delete an entire project and all related data after strong confirmation."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json() or {}
    if data.get('confirmName') != project.name or data.get('confirmWord') != 'DELETE':
        return jsonify({"error": "Confirmation mismatch"}), 400

    # Delete dependent objects explicitly to ensure cleanup
    # Order: ScheduledEquipment -> Panels / Templates / Points / Parts -> Project
    ScheduledEquipment.query.filter_by(project_id=project_id).delete(synchronize_session=False)

    # Do NOT delete point/equipment templates; they are global now

    # Parts are global now; do not delete parts when deleting a project

    # Panels (cascade removes ScheduledEquipment already removed above for safety)
    for panel in Panel.query.filter_by(project_id=project_id).all():
        db.session.delete(panel)

    db.session.delete(project)
    db.session.commit()
    return jsonify({"success": True, "redirect": url_for('project_selection')}), 200

# --- DB INITIALIZATION & RUN ---

def setup_database(app):
    with app.app_context():
        db.create_all()
        
        # Create default admin user if no users exist
        if User.query.count() == 0:
            admin_password = bcrypt.generate_password_hash('admin123').decode('utf-8')
            admin_user = User(
                username='admin',
                password=admin_password,
                is_approved=True,
                is_admin=True
            )
            db.session.add(admin_user)
            db.session.commit()
            print("Created default admin user: admin/admin123")
        
        # Deduplicate global parts on first run after migration (keep lowest id)
        from sqlalchemy import func
        dups = (db.session.query(Part.part_number, func.count(Part.id))
                .group_by(Part.part_number).having(func.count(Part.id) > 1).all())
        for pn, _ in dups:
            rows = Part.query.filter_by(part_number=pn).order_by(Part.id).all()
            keeper = rows[0]
            for obsolete in rows[1:]:
                # Repoint any point templates
                PointTemplate.query.filter_by(part_id=obsolete.id).update({PointTemplate.part_id: keeper.id})
                db.session.delete(obsolete)
        if dups:
            db.session.commit()
        # Deduplicate selected_points association table to prevent StaleDataError on updates
        try:
            from sqlalchemy import text
            db.session.execute(text("""
                DELETE FROM selected_points
                WHERE rowid NOT IN (
                  SELECT MIN(rowid) FROM selected_points
                  GROUP BY scheduled_equipment_id, point_template_id
                )
            """))
            db.session.commit()
        except Exception as e:
            # Log and continue; non-fatal if cleanup fails
            print(f"Warning: failed to deduplicate selected_points table: {e}")

if __name__ == '__main__':
    setup_database(app)
    socketio.run(app, debug=True, port=5001)
