from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import csv
import json
import tempfile
import subprocess
from datetime import datetime
from pylatex import Document, Section, Subsection, Table, Tabular, Command
from pylatex.utils import italic, bold, NoEscape
from pylatex.base_classes import Environment
from pylatex.package import Package

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
    category = db.Column(db.String(120), nullable=True)  # New category field
    available_points = db.relationship('EquipmentTemplatePoint', backref='equipment_template', lazy='dynamic', cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "type_key": self.type_key, "name": self.name, "category": self.category, "points": [{"id": etp.point_template_id, "quantity": etp.quantity} for etp in self.available_points]}

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

# Equipment Preset Model
class EquipmentPreset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    equipment_template_id = db.Column(db.Integer, db.ForeignKey('equipment_template.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    selected_points_json = db.Column(db.Text, nullable=False)  # JSON array of selected point IDs
    created_at = db.Column(db.DateTime, nullable=False, default=db.func.current_timestamp())
    equipment_template = db.relationship('EquipmentTemplate')

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "name": self.name,
            "equipment_template_id": self.equipment_template_id,
            "equipment_type": self.equipment_template.type_key,
            "equipment_name": self.equipment_template.name,
            "quantity": self.quantity,
            "selectedPoints": json.loads(self.selected_points_json),
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

# Document Customization Model
class DocumentCustomization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="Default Template")
    document_header = db.Column(db.Text, default="Building Management System\nProject Documentation\nGenerated by BMS Selection Tool")
    document_footer = db.Column(db.Text, default="Confidential - Property of [Company Name]\nGenerated on: \\today\nPage \\thepage\\ of \\pageref{LastPage}")
    company_info = db.Column(db.Text, default="[Company Name]\n[Address]\n[Phone] | [Email]\n[Website]")
    header_image_path = db.Column(db.String(255))  # Path to uploaded header image
    footer_image_path = db.Column(db.String(255))  # Path to uploaded footer image
    company_logo_path = db.Column(db.String(255))  # Path to uploaded company logo
    table_primary_color = db.Column(db.String(7), default="#f8f9fa")  # Light gray
    table_secondary_color = db.Column(db.String(7), default="#ffffff")  # White
    table_header_color = db.Column(db.String(7), default="#343a40")  # Dark gray
    table_header_text_color = db.Column(db.String(7), default="#ffffff")  # White text
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "document_header": self.document_header,
            "document_footer": self.document_footer,
            "company_info": self.company_info,
            "header_image_path": self.header_image_path,
            "footer_image_path": self.footer_image_path,
            "company_logo_path": self.company_logo_path,
            "table_primary_color": self.table_primary_color,
            "table_secondary_color": self.table_secondary_color,
            "table_header_color": self.table_header_color,
            "table_header_text_color": self.table_header_text_color,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

# Controller Selection Models
class ControllerType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    part_number = db.Column(db.String(120), nullable=False, unique=True)
    ai_capacity = db.Column(db.Integer, default=0)
    ao_capacity = db.Column(db.Integer, default=0)
    di_capacity = db.Column(db.Integer, default=0)
    do_capacity = db.Column(db.Integer, default=0)
    ui_capacity = db.Column(db.Integer, default=0)  # Universal Inputs
    uo_capacity = db.Column(db.Integer, default=0)  # Universal Outputs
    uio_capacity = db.Column(db.Integer, default=0)  # Universal I/O
    cost = db.Column(db.Float, nullable=False)
    is_server = db.Column(db.Boolean, default=False)  # True for server controllers

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "part_number": self.part_number,
            "ai_capacity": self.ai_capacity,
            "ao_capacity": self.ao_capacity,
            "di_capacity": self.di_capacity,
            "do_capacity": self.do_capacity,
            "ui_capacity": self.ui_capacity,
            "uo_capacity": self.uo_capacity,
            "uio_capacity": self.uio_capacity,
            "cost": self.cost,
            "is_server": self.is_server
        }

class ServerModule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    part_number = db.Column(db.String(120), nullable=False, unique=True)
    ai_capacity = db.Column(db.Integer, default=0)
    ao_capacity = db.Column(db.Integer, default=0)
    di_capacity = db.Column(db.Integer, default=0)
    do_capacity = db.Column(db.Integer, default=0)
    ui_capacity = db.Column(db.Integer, default=0)
    uo_capacity = db.Column(db.Integer, default=0)
    uio_capacity = db.Column(db.Integer, default=0)
    cost = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "part_number": self.part_number,
            "ai_capacity": self.ai_capacity,
            "ao_capacity": self.ao_capacity,
            "di_capacity": self.di_capacity,
            "do_capacity": self.do_capacity,
            "ui_capacity": self.ui_capacity,
            "uo_capacity": self.uo_capacity,
            "uio_capacity": self.uio_capacity,
            "cost": self.cost
        }

class Accessory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_part_number = db.Column(db.String(120), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    part_number = db.Column(db.String(120), nullable=False)
    cost = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "parent_part_number": self.parent_part_number,
            "name": self.name,
            "part_number": self.part_number,
            "cost": self.cost
        }

class ControllerSelection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    panel_id = db.Column(db.Integer, db.ForeignKey('panel.id'), nullable=False)
    controller_type_id = db.Column(db.Integer, db.ForeignKey('controller_type.id'), nullable=True)
    quantity = db.Column(db.Integer, default=1)
    is_server_selection = db.Column(db.Boolean, default=False)  # User selected as server
    is_auto_optimized = db.Column(db.Boolean, default=False)  # Auto-optimized selection
    
    # Server solution tracking
    server_modules = db.Column(db.Text)  # JSON string of selected modules for server panels
    total_cost = db.Column(db.Float, default=0)  # Total cost including accessories
    
    controller_type = db.relationship('ControllerType')
    panel = db.relationship('Panel')

    def to_dict(self):
        result = {
            "id": self.id,
            "project_id": self.project_id,
            "panel_id": self.panel_id,
            "panel_name": self.panel.panel_name,
            "quantity": self.quantity,
            "is_server_selection": self.is_server_selection,
            "is_auto_optimized": self.is_auto_optimized,
            "total_cost": self.total_cost or 0
        }
        
        if self.controller_type:
            result["controller_type"] = self.controller_type.to_dict()
        
        if self.server_modules:
            import json
            result["server_modules"] = json.loads(self.server_modules)
            
        return result

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

# --- DOCUMENT CUSTOMIZATION ROUTES ---

@app.route('/api/admin/document-customization', methods=['GET'])
@login_required
@admin_required
def get_document_customization():
    """Get current document customization settings."""
    customization = DocumentCustomization.query.filter_by(is_active=True).first()
    if not customization:
        # Create default customization if none exists
        customization = DocumentCustomization()
        db.session.add(customization)
        db.session.commit()
    return jsonify(customization.to_dict())

@app.route('/api/admin/document-customization', methods=['POST'])
@login_required
@admin_required
def update_document_customization():
    """Update document customization settings."""
    data = request.get_json()
    
    # Get current active customization or create new one
    customization = DocumentCustomization.query.filter_by(is_active=True).first()
    if not customization:
        customization = DocumentCustomization()
        db.session.add(customization)
    
    # Update fields
    if 'name' in data:
        customization.name = data['name']
    if 'document_header' in data:
        customization.document_header = data['document_header']
    if 'document_footer' in data:
        customization.document_footer = data['document_footer']
    if 'company_info' in data:
        customization.company_info = data['company_info']
    if 'table_primary_color' in data:
        customization.table_primary_color = data['table_primary_color']
    if 'table_secondary_color' in data:
        customization.table_secondary_color = data['table_secondary_color']
    if 'table_header_color' in data:
        customization.table_header_color = data['table_header_color']
    if 'table_header_text_color' in data:
        customization.table_header_text_color = data['table_header_text_color']
    
    db.session.commit()
    return jsonify({"success": True, "message": "Document customization updated successfully"})

@app.route('/api/admin/upload-image', methods=['POST'])
@login_required
@admin_required
def upload_document_image():
    """Upload image for document customization (header, footer, or logo)."""
    import os
    from werkzeug.utils import secure_filename
    
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    image_type = request.form.get('type')  # 'header', 'footer', or 'logo'
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not image_type or image_type not in ['header', 'footer', 'logo']:
        return jsonify({"error": "Invalid image type"}), 400
    
    # Check file extension
    allowed_extensions = {'png', 'jpg', 'jpeg', 'pdf', 'eps'}
    if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"error": "Invalid file type. Allowed: PNG, JPG, PDF, EPS"}), 400
    
    # Create uploads directory if it doesn't exist
    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'documents')
    os.makedirs(upload_dir, exist_ok=True)
    
    # Save file with secure filename
    filename = secure_filename(file.filename)
    import time
    timestamp = str(int(time.time()))
    filename = f"{image_type}_{timestamp}_{filename}"
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)
    
    # Update database
    customization = DocumentCustomization.query.filter_by(is_active=True).first()
    if not customization:
        customization = DocumentCustomization()
        db.session.add(customization)
    
    # Update appropriate image path
    relative_path = f"/static/uploads/documents/{filename}"
    if image_type == 'header':
        customization.header_image_path = relative_path
    elif image_type == 'footer':
        customization.footer_image_path = relative_path
    elif image_type == 'logo':
        customization.company_logo_path = relative_path
    
    db.session.commit()
    
    return jsonify({
        "success": True, 
        "message": f"{image_type.title()} image uploaded successfully",
        "file_path": relative_path
    })

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
    # Equipment presets for this project
    presets = [preset.to_dict() for preset in EquipmentPreset.query.filter_by(project_id=project_id).order_by(EquipmentPreset.created_at.desc()).all()]
    
    return jsonify({
        "panels": panels,
        "scheduledEquipment": scheduled_equipment,
        "pointTemplates": point_templates,
        "equipmentTemplates": equipment_templates,
        "parts": parts,
        "presets": presets
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
        'panels': [],
        'equipment_summary': {}  # New equipment summary
    }
    
    total_summary = {}
    equipment_summary = {}
    
    for panel in panels:
        panel_summary = {}
        equipments = ScheduledEquipment.query.filter_by(project_id=project_id, panel_id=panel.id).all()
        
        for equip in equipments:
            equip_qty = equip.quantity or 1
            template = equip.equipment_template
            
            # Add to equipment summary
            category = template.category or 'Uncategorized'
            if category not in equipment_summary:
                equipment_summary[category] = {
                    'count': 0,
                    'equipment': []
                }
            equipment_summary[category]['count'] += equip_qty
            equipment_summary[category]['equipment'].append({
                'name': equip.instance_name,
                'type': template.name,
                'quantity': equip_qty
            })
            
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
    project_summary['equipment_summary'] = equipment_summary
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

@app.route('/controller_selection/<int:project_id>')
@login_required
def controller_selection_page(project_id):
    """Render the controller selection page."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        flash("You do not have permission to access this project.", "danger")
        return redirect(url_for('project_selection'))
    return render_template('controller_selection.html', project_id=project.id)

@app.route('/reports_output/<int:project_id>')
@login_required
def reports_output_page(project_id):
    """Render the reports output page for PDF generation."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        flash("You do not have permission to access this project.", "danger")
        return redirect(url_for('project_selection'))
    return render_template('reports_output.html', project_id=project.id)
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

@app.route('/api/panel/<int:project_id>/<int:panel_id>/rename', methods=['PUT'])
@login_required
def rename_panel(project_id, panel_id):
    """Rename a panel."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    
    panel = Panel.query.get_or_404(panel_id)
    if panel.project_id != project_id:
        return jsonify({"error": "Panel does not belong to project"}), 400
    
    data = request.get_json()
    new_name = data.get('name', '').strip()
    
    if not new_name:
        return jsonify({"error": "Panel name is required"}), 400
    
    if len(new_name) > 120:  # Assuming there's a length limit
        return jsonify({"error": "Panel name too long"}), 400
    
    # Check if name already exists in this project
    existing = Panel.query.filter_by(project_id=project_id, panel_name=new_name).first()
    if existing and existing.id != panel_id:
        return jsonify({"error": "A panel with this name already exists in this project"}), 409
    
    old_name = panel.panel_name
    panel.panel_name = new_name
    db.session.commit()
    broadcast_update(project_id)
    
    return jsonify({
        "message": f"Panel renamed from '{old_name}' to '{new_name}'",
        "panel": panel.to_dict()
    }), 200

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

    new_template = EquipmentTemplate(
        type_key=data['typeKey'], 
        name=data['name'], 
        category=data.get('category'),  # Add category support
        project_id=project_id
    )  # project_id retained for ownership metadata only
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
    template.category = data.get('category')  # Add category support
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
    
    replicated = EquipmentTemplate(
        type_key=new_key, 
        name=new_name, 
        category=original.category,  # Copy category
        project_id=project_id
    )
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

# --- CONTROLLER SELECTION API ENDPOINTS ---

@app.route('/api/controller_types', methods=['GET'])
@login_required
def list_controller_types():
    """Get all available controller types."""
    controller_types = ControllerType.query.all()
    return jsonify([ct.to_dict() for ct in controller_types]), 200

@app.route('/api/controller_types', methods=['POST'])
@login_required
def add_controller_type():
    """Add a new controller type (admin only)."""
    if not current_user.is_admin:
        return jsonify({"error": "Admin access required"}), 403
    
    data = request.get_json()
    
    # Check if part number already exists
    if ControllerType.query.filter_by(part_number=data['part_number']).first():
        return jsonify({"error": f"Controller part number '{data['part_number']}' already exists."}), 409
    
    controller_type = ControllerType(
        name=data['name'],
        part_number=data['part_number'],
        ai_capacity=data.get('ai_capacity', 0),
        ao_capacity=data.get('ao_capacity', 0),
        di_capacity=data.get('di_capacity', 0),
        do_capacity=data.get('do_capacity', 0),
        ui_capacity=data.get('ui_capacity', 0),
        cost=data['cost'],
        is_server=data.get('is_server', False),
        max_points_total=data.get('max_points_total', 0)
    )
    
    db.session.add(controller_type)
    db.session.commit()
    
    return jsonify(controller_type.to_dict()), 201

@app.route('/api/projects/<int:project_id>/controller_selection', methods=['GET'])
@login_required
def get_controller_selection_data(project_id):
    """Get controller selection data for a project."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    # Get panel summary data (reuse existing endpoint logic)
    panels = Panel.query.filter_by(project_id=project_id).all()
    panel_data = []
    
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
                else:
                    for sp in sub_points:
                        panel_summary[sp.point_type] = panel_summary.get(sp.point_type, 0) + point_repeat

        panel_data.append({
            'id': panel.id,
            'name': panel.panel_name,
            'floor': panel.floor,
            'points': panel_summary
        })

    # Get existing controller selections
    existing_selections = ControllerSelection.query.filter_by(project_id=project_id).all()
    selections = [sel.to_dict() for sel in existing_selections]

    # Get all available controller types and server modules
    servers = ControllerType.query.filter_by(is_server=True).all()
    controllers = ControllerType.query.filter_by(is_server=False).all()
    server_modules = ServerModule.query.all()
    
    # Generate optimal server solutions for each panel
    server_solutions = {}
    for panel in panel_data:
        if panel['points']:  # Only generate solutions for panels with points
            server_solutions[panel['id']] = generate_optimal_server_solutions(panel['points'])
    
    return jsonify({
        'panels': panel_data,
        'servers': [s.to_dict() for s in servers],
        'controllers': [c.to_dict() for c in controllers],
        'server_modules': [m.to_dict() for m in server_modules],
        'existing_selections': selections,
        'server_solutions': server_solutions
    }), 200

@app.route('/api/projects/<int:project_id>/controller_selection/optimize', methods=['POST'])
@login_required
def optimize_controller_selection(project_id):
    """Optimize controller selection for panels."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    server_panels = data.get('server_panels', [])  # List of panel IDs selected as servers
    selected_solutions = data.get('selected_solutions', {})  # Selected solution for each server panel
    spare_percentage = data.get('spare_percentage', 0)  # Spare percentage for point calculations
    
    # Clear existing selections for this project
    ControllerSelection.query.filter_by(project_id=project_id).delete()
    
    # Handle server panels with selected solutions
    for panel_id in server_panels:
        panel_id = int(panel_id)
        if str(panel_id) in selected_solutions:
            solution = selected_solutions[str(panel_id)]
            
            # Create modules list from the solution
            modules = solution.get('modules', [])
            modules_json = json.dumps(modules)
            
            selection = ControllerSelection(
                project_id=project_id,
                panel_id=panel_id,
                controller_type_id=solution['server_id'],
                quantity=1,
                is_server_selection=True,
                is_auto_optimized=False,
                server_modules=modules_json,
                total_cost=solution['total_cost']
            )
            db.session.add(selection)

    # Get panels that need optimization (not server panels)
    panels_to_optimize = Panel.query.filter(
        Panel.project_id == project_id,
        ~Panel.id.in_(server_panels)
    ).all()

    # Run optimization algorithm for non-server panels
    optimization_result = run_controller_optimization(project_id, panels_to_optimize, spare_percentage)
    
    # Save optimization results
    for panel_id, controller_selection in optimization_result.items():
        total_cost = calculate_controller_cost_with_accessories(controller_selection['controller_type_id'])
        
        selection = ControllerSelection(
            project_id=project_id,
            panel_id=panel_id,
            controller_type_id=controller_selection['controller_type_id'],
            quantity=controller_selection['quantity'],
            is_server_selection=False,
            is_auto_optimized=True,
            total_cost=total_cost
        )
        db.session.add(selection)

    db.session.commit()
    
    # Return updated selection data
    return get_controller_selection_data(project_id)

def calculate_server_solution_cost(server_type_id, selected_modules):
    """Calculate total cost for a server solution including modules and accessories."""
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.info(f"calculate_server_solution_cost called with server_type_id: {server_type_id}")
    import json
    
    total_cost = 0
    
    # Add server cost
    server = ControllerType.query.get(server_type_id)
    if server:
        total_cost += server.cost
        
        # Add server accessories
        server_accessories = Accessory.query.filter_by(parent_part_number=server.part_number).all()
        for accessory in server_accessories:
            total_cost += accessory.cost
    
    # Add modules cost
    for module_data in selected_modules:
        module = ServerModule.query.get(module_data.get('id'))
        if module:
            quantity = module_data.get('quantity', 1)
            total_cost += module.cost * quantity
            
            # Add module accessories
            module_accessories = Accessory.query.filter_by(parent_part_number=module.part_number).all()
            for accessory in module_accessories:
                total_cost += accessory.cost * quantity
    
    return total_cost

def calculate_controller_cost_with_accessories(controller_type_id):
    """Calculate total cost for a controller including accessories."""
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.info(f"calculate_controller_cost_with_accessories called with controller_type_id: {controller_type_id}")
    total_cost = 0
    
    controller = ControllerType.query.get(controller_type_id)
    if controller:
        total_cost += controller.cost
        
        # Add controller accessories
        accessories = Accessory.query.filter_by(parent_part_number=controller.part_number).all()
        for accessory in accessories:
            total_cost += accessory.cost
    
    return total_cost

def generate_optimal_server_solutions(panel_points):
    """Generate optimal server solutions for a panel based on its I/O requirements."""
    solutions = []
    
    # Convert point requirements to standard format
    requirements = {
        'AI': panel_points.get('AI', 0),
        'AO': panel_points.get('AO', 0),
        'DI': panel_points.get('DI', 0),
        'DO': panel_points.get('DO', 0),
        'UI': panel_points.get('UI', 0)
    }
    
    # Get all servers and modules
    servers = ControllerType.query.filter_by(is_server=True).all()
    server_modules = ServerModule.query.all()
    
    # Generate AS-P solutions (scalable with modules)
    asp_server = next((s for s in servers if 'AS-P' in s.name), None)
    if asp_server:
        try:
            asp_solution = generate_asp_solution(asp_server, requirements, server_modules)
            if asp_solution:
                solutions.append(asp_solution)
        except Exception as e:
            import logging
            logging.basicConfig(level=logging.ERROR)
            logging.error(f"Error generating AS-P solution: {e}")
    
    # Generate AS-B solutions (fixed capacity)
    asb_servers = [s for s in servers if 'AS-B' in s.name]
    for asb_server in asb_servers:
        asb_solution = generate_asb_solution(asb_server, requirements)
        if asb_solution:
            solutions.append(asb_solution)
    
    # Sort by cost
    solutions.sort(key=lambda x: x['total_cost'])
    
    return solutions

def generate_asp_solution(asp_server, requirements, server_modules):
    """Generate AS-P solution with optimal module configuration."""
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.info(f"generate_asp_solution called with requirements: {requirements}")
    logging.info(f"server_modules: {[m.to_dict() for m in server_modules]}")
    if not asp_server:
        return None

    # Start with base server cost and accessories
    total_cost = asp_server.cost
    accessories = Accessory.query.filter_by(parent_part_number=asp_server.part_number).all()
    for accessory in accessories:
        total_cost += accessory.cost

    # Find optimal module combination
    required_modules = []
    remaining_requirements = requirements.copy()

    # Sort modules by a composite score of efficiency and specificity
    module_scores = []
    for module in server_modules:
        total_points = module.ai_capacity + module.ao_capacity + module.di_capacity + module.do_capacity + module.ui_capacity + module.uio_capacity
        if total_points > 0:
            # Prioritize modules that are more specific first
            specificity = (module.ai_capacity + module.ao_capacity + module.di_capacity + module.do_capacity) / total_points
            efficiency = total_points / module.cost
            score = 0.7 * specificity + 0.3 * efficiency
            module_scores.append((module, score))

    module_scores.sort(key=lambda x: x[1], reverse=True)
    sorted_modules = [m for m, s in module_scores]

    # Greedily select modules to meet requirements
    for module in sorted_modules:
        while True:
            # Check if this module helps with any remaining requirement
            helps = False
            if (remaining_requirements['AI'] > 0 and (module.ai_capacity > 0 or module.ui_capacity > 0 or module.uio_capacity > 0)) or \
               (remaining_requirements['AO'] > 0 and (module.ao_capacity > 0 or module.ui_capacity > 0 or module.uio_capacity > 0)) or \
               (remaining_requirements['DI'] > 0 and (module.di_capacity > 0 or module.ui_capacity > 0 or module.uio_capacity > 0)) or \
               (remaining_requirements['DO'] > 0 and (module.do_capacity > 0 or module.ui_capacity > 0 or module.uio_capacity > 0)) or \
               (remaining_requirements['UI'] > 0 and (module.ui_capacity > 0 or module.uio_capacity > 0)):
                helps = True

            if not helps:
                break

            # Add module
            required_modules.append({
                'id': module.id,
                'name': module.name,
                'part_number': module.part_number,
                'quantity': 1,
                'cost': module.cost
            })

            # Update costs
            total_cost += module.cost
            module_accessories = Accessory.query.filter_by(parent_part_number=module.part_number).all()
            for accessory in module_accessories:
                total_cost += accessory.cost

            # Update remaining requirements
            # Prioritize specific inputs first
            rem_ai = max(0, remaining_requirements['AI'] - module.ai_capacity)
            rem_ao = max(0, remaining_requirements['AO'] - module.ao_capacity)
            rem_di = max(0, remaining_requirements['DI'] - module.di_capacity)
            rem_do = max(0, remaining_requirements['DO'] - module.do_capacity)
            rem_ui = max(0, remaining_requirements['UI'] - module.ui_capacity)

            # Use flexible UI points
            ui_flex = module.ui_capacity
            if rem_ai > 0:
                take = min(rem_ai, ui_flex)
                rem_ai -= take
                ui_flex -= take
            if rem_ao > 0:
                take = min(rem_ao, ui_flex)
                rem_ao -= take
                ui_flex -= take
            if rem_di > 0:
                take = min(rem_di, ui_flex)
                rem_di -= take
                ui_flex -= take
            if rem_do > 0:
                take = min(rem_do, ui_flex)
                rem_do -= take
                ui_flex -= take
            
            # Use flexible UIO points
            uio_flex = module.uio_capacity
            if rem_ai > 0:
                take = min(rem_ai, uio_flex)
                rem_ai -= take
                uio_flex -= take
            if rem_ao > 0:
                take = min(rem_ao, uio_flex)
                rem_ao -= take
                uio_flex -= take
            if rem_di > 0:
                take = min(rem_di, uio_flex)
                rem_di -= take
                uio_flex -= take
            if rem_do > 0:
                take = min(rem_do, uio_flex)
                rem_do -= take
                uio_flex -= take
            if rem_ui > 0:
                take = min(rem_ui, uio_flex)
                rem_ui -= take
                uio_flex -= take

            # Check if adding the module made any change
            if remaining_requirements['AI'] == rem_ai and \
               remaining_requirements['AO'] == rem_ao and \
               remaining_requirements['DI'] == rem_di and \
               remaining_requirements['DO'] == rem_do and \
               remaining_requirements['UI'] == rem_ui:
                # This module doesn't help, remove it and break
                required_modules.pop()
                total_cost -= module.cost
                for accessory in module_accessories:
                    total_cost -= accessory.cost
                break

            remaining_requirements['AI'] = rem_ai
            remaining_requirements['AO'] = rem_ao
            remaining_requirements['DI'] = rem_di
            remaining_requirements['DO'] = rem_do
            remaining_requirements['UI'] = rem_ui

            # Check if all requirements are met
            if sum(remaining_requirements.values()) == 0:
                break
        
        if sum(remaining_requirements.values()) == 0:
            break


    # Check if solution is feasible
    if sum(remaining_requirements.values()) > 0:
        return None  # Cannot meet requirements

    # Consolidate modules
    consolidated_modules = {}
    for module in required_modules:
        if module['part_number'] not in consolidated_modules:
            consolidated_modules[module['part_number']] = module.copy()
        else:
            consolidated_modules[module['part_number']]['quantity'] += 1
    
    final_modules = list(consolidated_modules.values())

    return {
        'type': 'AS-P',
        'server_id': asp_server.id,
        'server_name': asp_server.name,
        'server_part_number': asp_server.part_number,
        'modules': final_modules,
        'total_cost': total_cost,
        'description': f"AS-P Server with {len(final_modules)} module types"
    }


def generate_asb_solution(asb_server, requirements):
    """Generate AS-B solution if it can meet requirements."""
    if not asb_server:
        return None
    
    # Check if AS-B server can handle requirements
    can_handle = True
    total_capacity = {
        'AI': asb_server.ai_capacity,
        'AO': asb_server.ao_capacity,
        'DI': asb_server.di_capacity,
        'DO': asb_server.do_capacity,
        'UI': asb_server.ui_capacity,
        'UIO': asb_server.uio_capacity
    }
    
    # Check direct capacity matches
    remaining = requirements.copy()
    for point_type in ['AI', 'AO', 'DI', 'DO', 'UI']:
        if remaining[point_type] > total_capacity[point_type]:
            # Check if UIO can cover the difference
            deficit = remaining[point_type] - total_capacity[point_type]
            if deficit > total_capacity['UIO']:
                can_handle = False
                break
            else:
                total_capacity['UIO'] -= deficit
                remaining[point_type] = 0
        else:
            remaining[point_type] = 0
    
    if not can_handle:
        return None
    
    # Calculate total cost with accessories
    total_cost = asb_server.cost
    accessories = Accessory.query.filter_by(parent_part_number=asb_server.part_number).all()
    for accessory in accessories:
        total_cost += accessory.cost
    
    return {
        'type': 'AS-B',
        'server_id': asb_server.id,
        'server_name': asb_server.name,
        'server_part_number': asb_server.part_number,
        'modules': [],  # AS-B doesn't use modules
        'total_cost': total_cost,
        'description': f"AS-B {asb_server.name} (fixed capacity)"
    }

def run_controller_optimization(project_id, panels, spare_percentage=0):
    """
    Optimize controller selection for given panels.
    This is a simplified optimization algorithm that selects the most cost-effective
    controllers based on point requirements, with optional spare point percentage.
    """
    optimization_result = {}
    
    # Get all non-server controller types, sorted by cost efficiency
    controller_types = ControllerType.query.filter_by(is_server=False).all()
    
    for panel in panels:
        # Get panel point requirements
        panel_points = get_panel_point_requirements(project_id, panel.id, spare_percentage)

        # Skip panels with no points
        if sum(panel_points.values()) == 0:
            continue
        
        # Find best controller for this panel
        best_controller = find_optimal_controller(panel_points, controller_types)
        
        if best_controller:
            optimization_result[panel.id] = {
                'controller_type_id': best_controller['controller_id'],
                'quantity': best_controller['quantity']
            }
    
    return optimization_result

def get_panel_point_requirements(project_id, panel_id, spare_percentage=0):
    """Get point requirements for a specific panel, with optional spare percentage."""
    requirements = {
        'AI': 0, 'AO': 0, 'DI': 0, 'DO': 0, 'UI': 0
    }
    
    equipments = ScheduledEquipment.query.filter_by(project_id=project_id, panel_id=panel_id).all()
    
    for equip in equipments:
        equip_qty = equip.quantity or 1
        template = equip.equipment_template
        
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points

        for pt in selected_points:
            etp = EquipmentTemplatePoint.query.filter_by(equipment_template_id=template.id, point_template_id=pt.id).first()
            per_template_qty = etp.quantity if etp and etp.quantity else 1
            point_repeat = (pt.quantity or 1) * per_template_qty * equip_qty

            sub_points = pt.sub_points.all() if hasattr(pt.sub_points, 'all') else pt.sub_points
            for sp in sub_points:
                point_type = sp.point_type.upper()
                if point_type in requirements:
                    requirements[point_type] += point_repeat
    
    # Apply spare percentage to each point type
    if spare_percentage > 0:
        import math
        for point_type in requirements:
            if requirements[point_type] > 0:
                spare_points = math.ceil(requirements[point_type] * spare_percentage / 100)
                requirements[point_type] += spare_points
    
    return requirements

def find_optimal_controller(point_requirements, controller_types):
    """Find the most cost-effective controller (possibly multiple units) for given point requirements.

    This enhanced version computes the minimum quantity of a controller type needed to cover
    the panel I/O when a single controller isn't sufficient. It accounts for flexible UI/UO/UIO.
    """
    import math

    def can_cover_with_n(controller, reqs, n):
        # Make mutable copies
        rem_reqs = {
            'AI': reqs.get('AI', 0), 'AO': reqs.get('AO', 0), 'DI': reqs.get('DI', 0),
            'DO': reqs.get('DO', 0), 'UI': reqs.get('UI', 0)
        }
        rem_cap = {
            'AI': controller.ai_capacity * n,
            'AO': controller.ao_capacity * n,
            'DI': controller.di_capacity * n,
            'DO': controller.do_capacity * n,
            'UI': controller.ui_capacity * n,
            'UO': controller.uo_capacity * n,
            'UIO': controller.uio_capacity * n,
        }

        # 1) Use dedicated capacities first
        for pt in ['AI', 'AO', 'DI', 'DO', 'UI']:
            take = min(rem_reqs[pt], rem_cap.get(pt, 0))
            rem_reqs[pt] -= take
            rem_cap[pt] -= take

        # 2) Use UI for remaining INPUTS (AI, DI)
        for pt in ['AI', 'DI']:
            take = min(rem_reqs[pt], rem_cap['UI'])
            rem_reqs[pt] -= take
            rem_cap['UI'] -= take

        # 3) Use UO for remaining OUTPUTS (AO, DO)
        for pt in ['AO', 'DO']:
            take = min(rem_reqs[pt], rem_cap['UO'])
            rem_reqs[pt] -= take
            rem_cap['UO'] -= take

        # 4) Use UIO for anything left
        for pt in ['AI', 'AO', 'DI', 'DO', 'UI']:
            take = min(rem_reqs[pt], rem_cap['UIO'])
            rem_reqs[pt] -= take
            rem_cap['UIO'] -= take

        return all(v <= 0 for v in rem_reqs.values())

    def lower_bound_n(controller, reqs):
        # Quick lower bound based on effective per-unit capacities (optimistic)
        eff = {
            'AI': controller.ai_capacity + controller.ui_capacity + controller.uio_capacity,
            'AO': controller.ao_capacity + controller.uo_capacity + controller.uio_capacity,
            'DI': controller.di_capacity + controller.ui_capacity + controller.uio_capacity,
            'DO': controller.do_capacity + controller.uo_capacity + controller.uio_capacity,
            'UI': controller.ui_capacity + controller.uio_capacity,
        }
        n_lb = 1
        for pt, need in reqs.items():
            cap = eff.get(pt, 0)
            if need > 0:
                if cap == 0:
                    return math.inf
                n_lb = max(n_lb, math.ceil(need / cap))
        return n_lb

    best_option = None
    best_cost = float('inf')

    for controller in controller_types:
        n_start = lower_bound_n(controller, point_requirements)
        if n_start == math.inf:
            continue

        # Try from lower bound up to a reasonable cap
        for n in range(int(n_start), int(n_start) + 20):
            if can_cover_with_n(controller, point_requirements, n):
                total_cost = calculate_controller_cost_with_accessories(controller.id) * n
                if total_cost < best_cost:
                    best_cost = total_cost
                    best_option = {
                        'controller_id': controller.id,
                        'quantity': n,
                        'cost': total_cost
                    }
                break  # No need to try larger n for this controller

    return best_option

@app.route('/api/projects/<int:project_id>/controller_selection/boq', methods=['GET'])
@login_required
def generate_controller_boq(project_id):
    """Generate Bill of Quantities for controllers and field devices."""
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.info("generate_controller_boq called")
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    # Get controller selections
    controller_selections = ControllerSelection.query.filter_by(project_id=project_id).all()
    
    # Generate comprehensive BOQ with controllers, modules, and accessories
    controller_boq = {}
    accessory_boq = {}
    module_boq = {}
    total_controller_cost = 0
    
    for selection in controller_selections:
        if selection.controller_type:
            controller = selection.controller_type
            part_num = controller.part_number
            
            # Add controller to BOQ
            if part_num not in controller_boq:
                controller_boq[part_num] = {
                    'name': controller.name,
                    'part_number': part_num,
                    'quantity': 0,
                    'unit_cost': controller.cost,
                    'total_cost': 0,
                    'is_server': controller.is_server,
                    'category': 'Server' if controller.is_server else 'Controller'
                }
            
            controller_boq[part_num]['quantity'] += selection.quantity
            controller_boq[part_num]['total_cost'] = controller_boq[part_num]['quantity'] * controller.cost
            total_controller_cost += selection.quantity * controller.cost
            
            # Add controller accessories
            controller_accessories = Accessory.query.filter_by(parent_part_number=controller.part_number).all()
            for accessory in controller_accessories:
                acc_part_num = accessory.part_number
                if acc_part_num not in accessory_boq:
                    accessory_boq[acc_part_num] = {
                        'name': accessory.name,
                        'part_number': acc_part_num,
                        'quantity': 0,
                        'unit_cost': accessory.cost,
                        'total_cost': 0,
                        'category': 'Accessory'
                    }
                
                accessory_boq[acc_part_num]['quantity'] += selection.quantity
                accessory_boq[acc_part_num]['total_cost'] = accessory_boq[acc_part_num]['quantity'] * accessory.cost
                total_controller_cost += selection.quantity * accessory.cost
        
        # Add server modules if this is a server selection
        if selection.is_server_selection and selection.server_modules:
            modules = json.loads(selection.server_modules)
            for module_data in modules:
                module = ServerModule.query.get(module_data.get('id'))
                if module:
                    module_part_num = module.part_number
                    module_qty = module_data.get('quantity', 1)
                    
                    if module_part_num not in module_boq:
                        module_boq[module_part_num] = {
                            'name': module.name,
                            'part_number': module_part_num,
                            'quantity': 0,
                            'unit_cost': module.cost,
                            'total_cost': 0,
                            'category': 'Server Module'
                        }
                    
                    module_boq[module_part_num]['quantity'] += module_qty
                    module_boq[module_part_num]['total_cost'] = module_boq[module_part_num]['quantity'] * module.cost
                    total_controller_cost += module_qty * module.cost
                    
                    # Add module accessories
                    module_accessories = Accessory.query.filter_by(parent_part_number=module.part_number).all()
                    for accessory in module_accessories:
                        acc_part_num = accessory.part_number
                        if acc_part_num not in accessory_boq:
                            accessory_boq[acc_part_num] = {
                                'name': accessory.name,
                                'part_number': acc_part_num,
                                'quantity': 0,
                                'unit_cost': accessory.cost,
                                'total_cost': 0,
                                'category': 'Accessory'
                            }
                        
                        accessory_boq[acc_part_num]['quantity'] += module_qty
                        accessory_boq[acc_part_num]['total_cost'] = accessory_boq[acc_part_num]['quantity'] * accessory.cost
                        total_controller_cost += module_qty * accessory.cost

    # Generate field devices BOQ (from scheduled equipment)
    field_devices_boq = {}
    total_field_cost = 0
    
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    for equip in scheduled_equipments:
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points
        
        for pt in selected_points:
            if pt.part:  # Only include points with associated parts
                part = pt.part
                part_num = part.part_number
                
                # Calculate quantity needed
                etp = EquipmentTemplatePoint.query.filter_by(
                    equipment_template_id=equip.equipment_template_id, 
                    point_template_id=pt.id
                ).first()
                per_template_qty = etp.quantity if etp and etp.quantity else 1
                total_qty = (pt.quantity or 1) * per_template_qty * (equip.quantity or 1)
                
                if part_num not in field_devices_boq:
                    field_devices_boq[part_num] = {
                        'name': part.description,
                        'part_number': part_num,
                        'category': part.category or 'Field Device',
                        'quantity': 0,
                        'unit_cost': part.cost or 0,
                        'total_cost': 0
                    }
                
                field_devices_boq[part_num]['quantity'] += total_qty
                field_devices_boq[part_num]['total_cost'] = field_devices_boq[part_num]['quantity'] * (part.cost or 0)
                total_field_cost += total_qty * (part.cost or 0)

    # Combine all BOQ items
    all_controller_items = (
        list(controller_boq.values()) + 
        list(module_boq.values()) + 
        list(accessory_boq.values())
    )

    return jsonify({
        'controller_boq': all_controller_items,
        'field_devices_boq': list(field_devices_boq.values()),
        'total_controller_cost': total_controller_cost,
        'total_field_cost': total_field_cost,
        'grand_total': total_controller_cost + total_field_cost
    }), 200

@app.route('/api/projects/<int:project_id>/controller_selection/point_list', methods=['GET'])
@login_required
def generate_point_list(project_id):
    """Generate detailed point list showing equipment-point breakdown by panel."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    # Get all scheduled equipment for the project
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    # Group by panel
    panels_data = {}
    
    for equip in scheduled_equipments:
        # Get panel information from relationship
        panel_name = equip.panel.panel_name if equip.panel else "Unknown Panel"
        floor = equip.panel.floor if equip.panel else "Unknown Floor"
        
        if panel_name not in panels_data:
            panels_data[panel_name] = {
                'panel_name': panel_name,
                'floor': floor,
                'equipment_points': [],
                'panel_totals': {'di': 0, 'do': 0, 'ai': 0, 'ao': 0, 'communication': 0}
            }
        
        # Get selected points for this equipment
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points
        
        for pt in selected_points:
            # Get quantity from equipment template point relationship
            etp = EquipmentTemplatePoint.query.filter_by(
                equipment_template_id=equip.equipment_template_id, 
                point_template_id=pt.id
            ).first()
            per_template_qty = etp.quantity if etp and etp.quantity else 1
            point_qty = (pt.quantity or 1) * per_template_qty * (equip.quantity or 1)
            
            # Get individual point data from sub_points
            for sub_point in pt.sub_points:
                point_type = sub_point.point_type.upper()
                
                # Determine communication type - only show protocol if it's a software point
                communication = ""
                is_software_point = False
                if hasattr(pt, 'part_number') and pt.part_number:
                    # Check if it's a software/network point based on part number patterns
                    part_num = pt.part_number.upper()
                    if any(software_indicator in part_num for software_indicator in ['MP300', 'TC303', 'VP228', 'BMS', 'SOFTWARE', 'NETWORK']):
                        is_software_point = True
                        if point_type in ['AI', 'AO']:
                            communication = "BACnet"
                        else:
                            communication = "Modbus"
                
                # Create individual point entry
                point_counts = {'di': 0, 'do': 0, 'ai': 0, 'ao': 0}
                if point_type == 'DI':
                    point_counts['di'] = point_qty
                elif point_type == 'DO':
                    point_counts['do'] = point_qty
                elif point_type == 'AI':
                    point_counts['ai'] = point_qty
                elif point_type == 'AO':
                    point_counts['ao'] = point_qty
                
                panels_data[panel_name]['equipment_points'].append({
                    'equipment_name': equip.instance_name,
                    'point_name': pt.name,
                    'point_type': point_type,
                    'part_number': getattr(pt, 'part_number', '') or '',
                    'di': point_counts['di'],
                    'do': point_counts['do'],
                    'ai': point_counts['ai'],
                    'ao': point_counts['ao'],
                    'communication': communication
                })
                
                # Add to panel totals
                panels_data[panel_name]['panel_totals']['di'] += point_counts['di']
                panels_data[panel_name]['panel_totals']['do'] += point_counts['do']
                panels_data[panel_name]['panel_totals']['ai'] += point_counts['ai']
                panels_data[panel_name]['panel_totals']['ao'] += point_counts['ao']
                if is_software_point:
                    panels_data[panel_name]['panel_totals']['communication'] += point_qty
    
    # Convert to list and sort by panel name
    panels_list = list(panels_data.values())
    panels_list.sort(key=lambda x: x['panel_name'])
    
    # Calculate grand totals
    grand_totals = {'di': 0, 'do': 0, 'ai': 0, 'ao': 0, 'communication': 0}
    total_equipment_points = 0
    
    for panel_data in panels_list:
        panel_totals = panel_data['panel_totals']
        grand_totals['di'] += panel_totals['di']
        grand_totals['do'] += panel_totals['do']
        grand_totals['ai'] += panel_totals['ai']
        grand_totals['ao'] += panel_totals['ao']
        grand_totals['communication'] += panel_totals.get('communication', 0)
        total_equipment_points += len(panel_data['equipment_points'])
    
    return jsonify({
        'panels': panels_list,
        'total_equipment_points': total_equipment_points,
        'grand_totals': grand_totals
    }), 200

# Equipment Preset API Routes
@app.route('/api/projects/<int:project_id>/presets', methods=['GET'])
@login_required
def get_equipment_presets(project_id):
    """Get all equipment presets for a project."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    
    presets = EquipmentPreset.query.filter_by(project_id=project_id).order_by(EquipmentPreset.created_at.desc()).all()
    return jsonify([preset.to_dict() for preset in presets]), 200

@app.route('/api/projects/<int:project_id>/presets', methods=['POST'])
@login_required
def create_equipment_preset(project_id):
    """Create a new equipment preset."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Validate required fields
    required_fields = ['name', 'equipment_template_id', 'quantity', 'selectedPoints']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # Check if equipment template exists and belongs to the project
    equipment_template = EquipmentTemplate.query.filter_by(
        id=data['equipment_template_id'], 
        project_id=project_id
    ).first()
    if not equipment_template:
        return jsonify({"error": "Equipment template not found or unauthorized"}), 404
    
    # Check if preset name already exists in this project
    existing_preset = EquipmentPreset.query.filter_by(
        project_id=project_id, 
        name=data['name']
    ).first()
    if existing_preset:
        return jsonify({"error": "A preset with this name already exists"}), 409
    
    import json
    new_preset = EquipmentPreset(
        project_id=project_id,
        name=data['name'],
        equipment_template_id=data['equipment_template_id'],
        quantity=data['quantity'],
        selected_points_json=json.dumps(data['selectedPoints'])
    )
    
    db.session.add(new_preset)
    db.session.commit()
    
    return jsonify(new_preset.to_dict()), 201

@app.route('/api/projects/<int:project_id>/presets/<int:preset_id>', methods=['DELETE'])
@login_required
def delete_equipment_preset(project_id, preset_id):
    """Delete an equipment preset."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    
    preset = EquipmentPreset.query.filter_by(id=preset_id, project_id=project_id).first()
    if not preset:
        return jsonify({"error": "Preset not found"}), 404
    
    db.session.delete(preset)
    db.session.commit()
    
    return jsonify({"message": "Preset deleted successfully"}), 200

@app.route('/api/projects/<int:project_id>/presets/<int:preset_id>/apply', methods=['POST'])
@login_required
def apply_equipment_preset(project_id, preset_id):
    """Apply an equipment preset to a panel."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    
    data = request.get_json()
    if not data or 'panel_id' not in data:
        return jsonify({"error": "Panel ID is required"}), 400
    
    panel_id = data['panel_id']
    instance_name = data.get('instance_name', '')
    
    # Validate panel belongs to the project
    panel = Panel.query.filter_by(id=panel_id, project_id=project_id).first()
    if not panel:
        return jsonify({"error": "Panel not found or unauthorized"}), 404
    
    # Get the preset
    preset = EquipmentPreset.query.filter_by(id=preset_id, project_id=project_id).first()
    if not preset:
        return jsonify({"error": "Preset not found"}), 404
    
    if not instance_name:
        return jsonify({"error": "Instance name is required"}), 400
    
    # Check if instance name already exists in this panel
    existing_equipment = ScheduledEquipment.query.filter_by(
        project_id=project_id,
        panel_id=panel_id,
        instance_name=instance_name
    ).first()
    if existing_equipment:
        return jsonify({"error": "Equipment with this instance name already exists in this panel"}), 409
    
    # Create new scheduled equipment based on the preset
    import json
    selected_point_ids = json.loads(preset.selected_points_json)
    
    new_equipment = ScheduledEquipment(
        project_id=project_id,
        instance_name=instance_name,
        quantity=preset.quantity,
        panel_id=panel_id,
        equipment_template_id=preset.equipment_template_id
    )
    
    db.session.add(new_equipment)
    db.session.flush()  # Get the ID without committing
    
    # Add selected points
    for point_id in selected_point_ids:
        point = PointTemplate.query.filter_by(id=point_id, project_id=project_id).first()
        if point:
            new_equipment.selected_points.append(point)
    
    db.session.commit()
    
    return jsonify(new_equipment.to_dict()), 201

@app.route('/api/projects/<int:project_id>/reports/generate', methods=['POST'])
@login_required
def generate_reports(project_id):
    """Generate LaTeX content for selected reports."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    selected_reports = data.get('reports', [])
    header = data.get('header', '')
    footer = data.get('footer', '')
    company_info = data.get('company_info', '')

    if not selected_reports:
        return jsonify({"error": "No reports selected"}), 400

    # Check if required data is available for selected reports
    validation_errors = validate_report_prerequisites(project_id, selected_reports)
    if validation_errors:
        return jsonify({"error": "Missing required data", "details": validation_errors}), 400

    try:
        latex_content = generate_latex_content(project_id, selected_reports, header, footer, company_info)
        
        # Try to generate PDF for preview using reportlab as fallback
        pdf_preview_url = None
        try:
            # First try pdflatex if available
            with tempfile.TemporaryDirectory() as temp_dir:
                tex_file = os.path.join(temp_dir, f"bms_reports_{project_id}.tex")
                pdf_file = os.path.join(temp_dir, f"bms_reports_{project_id}.pdf")
                
                # Write LaTeX content to file
                with open(tex_file, 'w', encoding='utf-8') as f:
                    f.write(latex_content)
                
                # Try to compile with pdflatex
                try:
                    result = subprocess.run(['pdflatex', '-interaction=nonstopmode', '-output-directory', temp_dir, tex_file], 
                                 check=True, capture_output=True, text=True)
                    
                    # Run again for references
                    subprocess.run(['pdflatex', '-interaction=nonstopmode', '-output-directory', temp_dir, tex_file], 
                                 check=True, capture_output=True, text=True)
                    
                    # Read PDF content and encode as base64 for preview
                    if os.path.exists(pdf_file):
                        import base64
                        with open(pdf_file, 'rb') as f:
                            pdf_content = f.read()
                        pdf_preview_url = f"data:application/pdf;base64,{base64.b64encode(pdf_content).decode('utf-8')}"
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    # pdflatex failed, try alternative PDF generation using reportlab
                    try:
                        pdf_preview_url = generate_pdf_with_reportlab(project_id, selected_reports, header, footer, company_info)
                    except Exception as reportlab_error:
                        # Both methods failed, will show LaTeX preview
                        print(f"Both PDF generation methods failed: pdflatex: {e}, reportlab: {reportlab_error}")
                        # Add detailed error logging
                        if hasattr(e, 'stderr') and e.stderr:
                            print(f"pdflatex stderr: {e.stderr}")
                        pass
        except Exception as e:
            print(f"Error in PDF preview generation: {e}")
            pass
        
        return jsonify({
            'latex_content': latex_content,
            'reports': selected_reports,
            'pdf_preview_url': pdf_preview_url
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def validate_report_prerequisites(project_id, selected_reports):
    """Validate that all required data is available for the selected reports."""
    errors = []
    
    # Check if controller selection optimization has been completed
    controller_selections = ControllerSelection.query.filter_by(project_id=project_id).all()
    has_optimization = len(controller_selections) > 0
    
    # Check which reports require controller optimization
    reports_requiring_optimization = ['field-devices-boq', 'controller-boq']
    
    for report_type in selected_reports:
        if report_type in reports_requiring_optimization and not has_optimization:
            errors.append(f"{report_type.replace('-', ' ').title()}: Requires controller selection optimization to be completed first")
        
        # Additional validations for specific reports
        if report_type == 'equipment-list':
            # Check if there are scheduled equipments
            scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
            if not scheduled_equipments:
                errors.append("Equipment List: No equipment scheduled in project")
        
        if report_type == 'point-list':
            # Check if there are scheduled equipments with selected points
            scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
            has_points = any(len(equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points) > 0 
                           for equip in scheduled_equipments)
            if not has_points:
                errors.append("Point List: No I/O points selected for equipment")
    
    return errors

def generate_pdf_with_reportlab(project_id, selected_reports, header, footer, company_info):
    """Generate PDF using reportlab as fallback when LaTeX is not available."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        import io
        import base64
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        story = []
        
        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            alignment=1,  # Center alignment
        )
        story.append(Paragraph("BMS Project Reports", title_style))
        story.append(Spacer(1, 20))
        
        # Company info
        if company_info:
            story.append(Paragraph(company_info.replace('\n', '<br/>'), styles['Normal']))
            story.append(Spacer(1, 20))
        
        # Generate each selected report
        for report_type in selected_reports:
            try:
                if report_type == 'equipment-list':
                    story.extend(generate_equipment_list_reportlab(project_id, styles))
                elif report_type == 'point-list':
                    story.extend(generate_point_list_reportlab(project_id, styles))
                elif report_type == 'field-devices-boq':
                    story.extend(generate_field_devices_boq_reportlab(project_id, styles))
                elif report_type == 'controller-boq':
                    story.extend(generate_controller_boq_reportlab(project_id, styles))
            except Exception as report_error:
                print(f"Error generating {report_type}: {report_error}")
                # Add error message to PDF instead of failing completely
                story.append(Paragraph(f"Error generating {report_type}: {str(report_error)}", styles['Normal']))
                story.append(Spacer(1, 20))
        
        # Build PDF
        doc.build(story)
        
        # Get PDF content and encode as base64
        pdf_content = buffer.getvalue()
        buffer.close()
        
        return f"data:application/pdf;base64,{base64.b64encode(pdf_content).decode('utf-8')}"
        
    except ImportError as e:
        raise Exception(f"ReportLab not available: {e}")
    except Exception as e:
        raise Exception(f"ReportLab PDF generation failed: {e}")

def generate_equipment_list_reportlab(project_id, styles):
    """Generate equipment list content for reportlab PDF."""
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    
    story = []
    story.append(Paragraph("Equipment List", styles['Heading2']))
    story.append(Spacer(1, 12))
    
    # Get equipment data
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    if not scheduled_equipments:
        story.append(Paragraph("No equipment scheduled in this project.", styles['Normal']))
        return story
    
    # Create table data
    data = [['Panel', 'Equipment Type', 'Instance Name', 'Quantity', 'Floor']]
    
    for equip in scheduled_equipments:
        panel_name = equip.panel.panel_name if equip.panel else "Unknown"
        floor = equip.panel.floor if equip.panel else "Unknown"
        equipment_type = equip.equipment_template.name if equip.equipment_template else "Unknown"
        
        data.append([panel_name, equipment_type, equip.instance_name, str(equip.quantity), floor])
    
    # Create table
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(table)
    story.append(Spacer(1, 30))
    return story

def generate_controller_boq_reportlab(project_id, styles):
    """Generate controller BOQ content for reportlab PDF."""
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    import json
    
    story = []
    story.append(Paragraph("Controller Bill of Quantities", styles['Heading2']))
    story.append(Spacer(1, 12))
    
    # Get controller selections and generate real BOQ data
    controller_selections = ControllerSelection.query.filter_by(project_id=project_id).all()
    
    if not controller_selections:
        story.append(Paragraph("No controller optimization completed. Please run controller selection optimization first.", styles['Normal']))
        return story
    
    controller_boq = {}
    accessory_boq = {}
    module_boq = {}
    total_controller_cost = 0
    
    for selection in controller_selections:
        if selection.controller_type:
            controller = selection.controller_type
            part_num = controller.part_number
            
            # Add controller to BOQ
            if part_num not in controller_boq:
                controller_boq[part_num] = {
                    'name': controller.name,
                    'part_number': part_num,
                    'quantity': 0,
                    'unit_cost': controller.cost,
                    'total_cost': 0,
                    'category': 'Server' if controller.is_server else 'Controller'
                }
            
            controller_boq[part_num]['quantity'] += selection.quantity
            controller_boq[part_num]['total_cost'] = controller_boq[part_num]['quantity'] * controller.cost
            total_controller_cost += selection.quantity * controller.cost
            
            # Add controller accessories
            controller_accessories = Accessory.query.filter_by(parent_part_number=controller.part_number).all()
            for accessory in controller_accessories:
                acc_part_num = accessory.part_number
                if acc_part_num not in accessory_boq:
                    accessory_boq[acc_part_num] = {
                        'name': accessory.name,
                        'part_number': acc_part_num,
                        'quantity': 0,
                        'unit_cost': accessory.cost,
                        'total_cost': 0,
                        'category': 'Accessory'
                    }
                
                accessory_boq[acc_part_num]['quantity'] += selection.quantity
                accessory_boq[acc_part_num]['total_cost'] = accessory_boq[acc_part_num]['quantity'] * accessory.cost
                total_controller_cost += selection.quantity * accessory.cost
        
        # Add server modules if this is a server selection
        if selection.is_server_selection and selection.server_modules:
            modules = json.loads(selection.server_modules)
            for module_data in modules:
                module = ServerModule.query.get(module_data.get('id'))
                if module:
                    module_part_num = module.part_number
                    module_qty = module_data.get('quantity', 1)
                    
                    if module_part_num not in module_boq:
                        module_boq[module_part_num] = {
                            'name': module.name,
                            'part_number': module_part_num,
                            'quantity': 0,
                            'unit_cost': module.cost,
                            'total_cost': 0,
                            'category': 'Server Module'
                        }
                    
                    module_boq[module_part_num]['quantity'] += module_qty
                    module_boq[module_part_num]['total_cost'] = module_boq[module_part_num]['quantity'] * module.cost
                    total_controller_cost += module_qty * module.cost
    
    # Create table data
    data = [['Part Number', 'Description', 'Category', 'Quantity', 'Unit Cost', 'Total Cost']]
    
    # Combine all controller items (controllers, modules, accessories)
    all_controller_items = (
        list(controller_boq.values()) + 
        list(module_boq.values()) + 
        list(accessory_boq.values())
    )
    
    # Add real controller data
    for item in all_controller_items:
        data.append([
            item['part_number'],
            item['name'],
            item['category'],
            str(item['quantity']),
            f"${item['unit_cost']:.2f}",
            f"${item['total_cost']:.2f}"
        ])
    
    # Add total row
    data.append(['', '', '', '', 'TOTAL:', f"${total_controller_cost:.2f}"])
    
    # Create table
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(table)
    story.append(Spacer(1, 30))
    return story

def generate_field_devices_boq_reportlab(project_id, styles):
    """Generate field devices BOQ content for reportlab PDF."""
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    
    story = []
    story.append(Paragraph("Field Devices Bill of Quantities", styles['Heading2']))
    story.append(Spacer(1, 12))
    
    # Generate field devices BOQ data
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    field_devices_boq = {}
    total_field_cost = 0
    
    for equip in scheduled_equipments:
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points
        
        for pt in selected_points:
            if pt.part:  # Only include points with associated parts
                part = pt.part
                part_num = part.part_number
                
                if part_num not in field_devices_boq:
                    field_devices_boq[part_num] = {
                        'name': part.description,
                        'part_number': part_num,
                        'category': part.category or 'Field Device',
                        'quantity': 0,
                        'unit_cost': part.cost or 0,
                        'total_cost': 0
                    }
                
                field_devices_boq[part_num]['quantity'] += 1
                field_devices_boq[part_num]['total_cost'] = field_devices_boq[part_num]['quantity'] * (part.cost or 0)
                total_field_cost += (part.cost or 0)
    
    if not field_devices_boq:
        story.append(Paragraph("No field devices with parts found in this project.", styles['Normal']))
        return story
    
    # Create table data
    data = [['Part Number', 'Description', 'Category', 'Quantity', 'Unit Cost', 'Total Cost']]
    
    # Add field devices data
    for item in field_devices_boq.values():
        data.append([
            item['part_number'],
            item['name'],
            item['category'],
            str(item['quantity']),
            f"${item['unit_cost']:.2f}",
            f"${item['total_cost']:.2f}"
        ])
    
    # Add total row
    data.append(['', '', '', '', 'TOTAL:', f"${total_field_cost:.2f}"])
    
    # Create table
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(table)
    story.append(Spacer(1, 30))
    return story

def generate_point_list_reportlab(project_id, styles):
    """Generate point list content for reportlab PDF."""
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    
    story = []
    story.append(Paragraph("Point List", styles['Heading2']))
    story.append(Spacer(1, 12))
    
    # Get point list data using the same logic as the API
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    if not scheduled_equipments:
        story.append(Paragraph("No equipment scheduled in this project.", styles['Normal']))
        return story
    
    # Group by panel (similar to the API logic)
    panels_data = {}
    
    for equip in scheduled_equipments:
        # Get panel information from relationship
        panel_name = equip.panel.panel_name if equip.panel else "Unknown Panel"
        floor = equip.panel.floor if equip.panel else "Unknown Floor"
        
        if panel_name not in panels_data:
            panels_data[panel_name] = {
                'panel_name': panel_name,
                'floor': floor,
                'equipment_points': [],
                'panel_totals': {'di': 0, 'do': 0, 'ai': 0, 'ao': 0, 'communication': 0}
            }
        
        # Get selected points for this equipment
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points
        
        for pt in selected_points:
            # Get quantity from equipment template point relationship
            etp = EquipmentTemplatePoint.query.filter_by(
                equipment_template_id=equip.equipment_template_id, 
                point_template_id=pt.id
            ).first()
            per_template_qty = etp.quantity if etp and etp.quantity else 1
            point_qty = (pt.quantity or 1) * per_template_qty * (equip.quantity or 1)
            
            # Get individual point data from sub_points
            for sub_point in pt.sub_points:
                point_type = sub_point.point_type.upper()
                
                # Determine communication type - only show protocol if it's a software point
                communication = ""
                is_software_point = False
                if hasattr(pt, 'part_number') and pt.part_number:
                    # Check if it's a software/network point based on part number patterns
                    part_num = pt.part_number.upper()
                    if any(software_indicator in part_num for software_indicator in ['MP300', 'TC303', 'VP228', 'BMS', 'SOFTWARE', 'NETWORK']):
                        is_software_point = True
                        if point_type in ['AI', 'AO']:
                            communication = "BACnet"
                        else:
                            communication = "Modbus"
                
                # Create individual point entry
                point_counts = {'di': 0, 'do': 0, 'ai': 0, 'ao': 0}
                if point_type == 'DI':
                    point_counts['di'] = point_qty
                elif point_type == 'DO':
                    point_counts['do'] = point_qty
                elif point_type == 'AI':
                    point_counts['ai'] = point_qty
                elif point_type == 'AO':
                    point_counts['ao'] = point_qty
                
                panels_data[panel_name]['equipment_points'].append({
                    'equipment_name': equip.instance_name,
                    'point_name': pt.name,
                    'point_type': point_type,
                    'part_number': getattr(pt, 'part_number', '') or '',
                    'di': point_counts['di'],
                    'do': point_counts['do'],
                    'ai': point_counts['ai'],
                    'ao': point_counts['ao'],
                    'communication': communication
                })
                
                # Add to panel totals
                panels_data[panel_name]['panel_totals']['di'] += point_counts['di']
                panels_data[panel_name]['panel_totals']['do'] += point_counts['do']
                panels_data[panel_name]['panel_totals']['ai'] += point_counts['ai']
                panels_data[panel_name]['panel_totals']['ao'] += point_counts['ao']
                if communication:
                    panels_data[panel_name]['panel_totals']['communication'] += 1
    
    # Create table data with panel grouping
    data = [['EQUIPMENT NAME', 'POINT NAME', 'PART NUMBER', 'Sum of DI', 'Sum of DO', 'Sum of AI', 'Sum of AO', 'SOFTWARE']]
    
    grand_totals = {'di': 0, 'do': 0, 'ai': 0, 'ao': 0, 'communication': 0}
    
    for panel_name, panel_data in panels_data.items():
        # Add panel header row
        data.append([f"{panel_name} ({panel_data['floor']})", '', '', '', '', '', '', ''])
        
        # Add equipment points for this panel
        for point in panel_data['equipment_points']:
            data.append([
                point['equipment_name'],
                point['point_name'], 
                point['part_number'],
                str(point['di']),
                str(point['do']),
                str(point['ai']),
                str(point['ao']),
                point['communication']
            ])
        
        # Add panel totals
        panel_totals = panel_data['panel_totals']
        data.append([
            f"Total for {panel_name}",
            '', '',
            str(panel_totals['di']),
            str(panel_totals['do']),
            str(panel_totals['ai']),
            str(panel_totals['ao']),
            str(panel_totals['communication']) if panel_totals['communication'] > 0 else ''
        ])
        
        # Add to grand totals
        grand_totals['di'] += panel_totals['di']
        grand_totals['do'] += panel_totals['do']
        grand_totals['ai'] += panel_totals['ai']
        grand_totals['ao'] += panel_totals['ao']
        grand_totals['communication'] += panel_totals['communication']
        
        # Add empty row for spacing
        data.append(['', '', '', '', '', '', '', ''])
    
    # Add grand total row
    data.append([
        'Grand Total',
        '', '',
        str(grand_totals['di']),
        str(grand_totals['do']),
        str(grand_totals['ai']),
        str(grand_totals['ao']),
        str(grand_totals['communication']) if grand_totals['communication'] > 0 else ''
    ])
    
    if len(data) == 1:  # Only header
        story.append(Paragraph("No I/O points selected for equipment in this project.", styles['Normal']))
        return story
    
    # Create table with special styling for headers and totals
    table = Table(data)
    table.setStyle(TableStyle([
        # Header row styling
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        # Data rows
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    # Add special styling for panel headers and totals
    for i, row in enumerate(data):
        if i > 0:  # Skip header row
            if row[0].startswith('Total for ') or row[0] == 'Grand Total':
                # Total rows - bold and light grey background
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, i), (-1, i), colors.lightgrey),
                    ('FONTNAME', (0, i), (-1, i), 'Helvetica-Bold'),
                ]))
            elif row[1] == '' and row[2] == '' and not row[0].startswith('Total'):
                # Panel header rows - darker background
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, i), (-1, i), colors.lightblue),
                    ('FONTNAME', (0, i), (-1, i), 'Helvetica-Bold'),
                ]))
    
    story.append(table)
    story.append(Spacer(1, 30))
    return story

@app.route('/api/projects/<int:project_id>/reports/pdf', methods=['POST'])
@login_required
def generate_pdf(project_id):
    """Generate PDF from LaTeX content."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    if not data or 'latex_content' not in data:
        return jsonify({"error": "No LaTeX content provided"}), 400

    try:
        # Create temporary files
        with tempfile.TemporaryDirectory() as temp_dir:
            tex_file = os.path.join(temp_dir, f"bms_reports_{project_id}.tex")
            pdf_file = os.path.join(temp_dir, f"bms_reports_{project_id}.pdf")
            
            # Write LaTeX content to file
            with open(tex_file, 'w', encoding='utf-8') as f:
                f.write(data['latex_content'])
            
            # Try to compile with pdflatex (if available)
            try:
                subprocess.run(['pdflatex', '-interaction=nonstopmode', '-output-directory', temp_dir, tex_file], 
                             check=True, capture_output=True, text=True)
                
                # Run again for references
                subprocess.run(['pdflatex', '-interaction=nonstopmode', '-output-directory', temp_dir, tex_file], 
                             check=True, capture_output=True, text=True)
                
                return send_file(pdf_file, as_attachment=True, 
                               download_name=f"bms_reports_project_{project_id}.pdf")
            except (subprocess.CalledProcessError, FileNotFoundError):
                # If pdflatex is not available, return error with suggestion
                return jsonify({
                    "error": "PDF generation requires LaTeX installation. Please install TeX Live or MiKTeX and try again. You can download the LaTeX source instead."
                }), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def generate_latex_content(project_id, selected_reports, header, footer, company_info):
    """Generate LaTeX content for the selected reports."""
    
    # Start building LaTeX document
    latex_content = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[english]{babel}
\usepackage{geometry}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{fancyhdr}
\usepackage{lastpage}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{hyperref}
\usepackage{ifthen}

\geometry{margin=1in}
\pagestyle{fancy}

"""

    # Enhanced header and footer setup with image support
    header_content = header.replace('\n', r'\\')
    footer_content = footer.replace('\n', r'\\')
    company_content = company_info.replace('\n', r'\\')
    
    # Check if header/footer contain image paths (simple check for common image extensions)
    header_has_image = any(ext in header.lower() for ext in ['.png', '.jpg', '.jpeg', '.pdf', '.eps'])
    footer_has_image = any(ext in footer.lower() for ext in ['.png', '.jpg', '.jpeg', '.pdf', '.eps'])
    
    latex_content += r"""
% Header and footer setup with image support
"""
    
    if header_has_image:
        # If header contains image path, treat it as an image
        latex_content += f"\\fancyhead[L]{{\\includegraphics[height=1cm]{{{header_content}}}}}\n"
    else:
        # Regular text header
        latex_content += f"\\fancyhead[L]{{{header_content}}}\n"
    
    latex_content += r"\fancyhead[R]{\today}" + "\n"
    
    if footer_has_image:
        # If footer contains image path, treat it as an image
        latex_content += f"\\fancyfoot[L]{{\\includegraphics[height=0.8cm]{{{company_content}}}}}\n"
        latex_content += f"\\fancyfoot[C]{{{footer_content}}}\n"
    else:
        # Regular text footer
        latex_content += f"\\fancyfoot[L]{{{company_content}}}\n"
        latex_content += f"\\fancyfoot[C]{{{footer_content}}}\n"
    
    latex_content += r"\fancyfoot[R]{\thepage\ of \pageref{LastPage}}" + "\n"

    latex_content += r"""
\title{BMS Project Reports}
\author{""" + (company_info.split('\n')[0] if company_info else 'BMS Selection Tool') + r"""}
\date{\today}

\begin{document}
\maketitle
\tableofcontents
\newpage

"""

    # Generate content for each selected report
    for report_type in selected_reports:
        if report_type == 'equipment-list':
            latex_content += generate_equipment_list_latex(project_id)
        elif report_type == 'point-list':
            latex_content += generate_point_list_latex(project_id)
        elif report_type == 'field-devices-boq':
            latex_content += generate_field_devices_boq_latex(project_id)
        elif report_type == 'controller-boq':
            latex_content += generate_controller_boq_latex(project_id)

    latex_content += r"""
\end{document}
"""

    return latex_content

def generate_equipment_list_latex(project_id):
    """Generate LaTeX content for equipment list."""
    
    # Get equipment data
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    latex_content = r"""
\section{Equipment List}

This section provides a complete list of all scheduled equipment in the project.

\begin{longtable}{|l|l|l|l|l|}
\hline
\textbf{Panel} & \textbf{Equipment Type} & \textbf{Instance Name} & \textbf{Quantity} & \textbf{Floor} \\
\hline
\endhead
"""

    for equip in scheduled_equipments:
        panel_name = equip.panel.panel_name if equip.panel else "Unknown"
        floor = equip.panel.floor if equip.panel else "Unknown"
        equipment_type = equip.equipment_template.name if equip.equipment_template else "Unknown"
        
        latex_content += f"{panel_name} & {equipment_type} & {equip.instance_name} & {equip.quantity} & {floor} \\\\\n\\hline\n"

    latex_content += r"""
\end{longtable}

"""
    return latex_content

def generate_point_list_latex(project_id):
    """Generate LaTeX content for point list."""
    
    # Get point list data using the same comprehensive logic as reportlab version
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    latex_content = r"""
\section{Point List}

This section provides a detailed breakdown of I/O points by equipment.

\begin{longtable}{|l|l|p{3cm}|l|l|l|l|l|}
\hline
\textbf{Equipment} & \textbf{Point Name} & \textbf{Part Number} & \textbf{Sum of DI} & \textbf{Sum of DO} & \textbf{Sum of AI} & \textbf{Sum of AO} & \textbf{Software} \\
\hline
\endhead
"""

    # Group by panel (same logic as reportlab version)
    panels_data = {}
    
    for equip in scheduled_equipments:
        # Get panel information from relationship
        panel_name = equip.panel.panel_name if equip.panel else "Unknown Panel"
        floor = equip.panel.floor if equip.panel else "Unknown Floor"
        
        if panel_name not in panels_data:
            panels_data[panel_name] = {
                'panel_name': panel_name,
                'floor': floor,
                'equipment_points': [],
                'panel_totals': {'di': 0, 'do': 0, 'ai': 0, 'ao': 0, 'communication': 0}
            }
        
        # Get selected points for this equipment
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points
        
        for pt in selected_points:
            # Get quantity from equipment template point relationship
            etp = EquipmentTemplatePoint.query.filter_by(
                equipment_template_id=equip.equipment_template_id, 
                point_template_id=pt.id
            ).first()
            per_template_qty = etp.quantity if etp and etp.quantity else 1
            point_qty = (pt.quantity or 1) * per_template_qty * (equip.quantity or 1)
            
            # Get individual point data from sub_points
            for sub_point in pt.sub_points:
                point_type = sub_point.point_type.upper()
                
                # Determine communication type - only show protocol if it's a software point
                communication = ""
                is_software_point = False
                if hasattr(pt, 'part_number') and pt.part_number:
                    # Check if it's a software/network point based on part number patterns
                    part_num = pt.part_number.upper()
                    if any(software_indicator in part_num for software_indicator in ['MP300', 'TC303', 'VP228', 'BMS', 'SOFTWARE', 'NETWORK']):
                        is_software_point = True
                        if point_type in ['AI', 'AO']:
                            communication = "BACnet"
                        else:
                            communication = "Modbus"
                
                # Create individual point entry
                point_counts = {'di': 0, 'do': 0, 'ai': 0, 'ao': 0}
                if point_type == 'DI':
                    point_counts['di'] = point_qty
                elif point_type == 'DO':
                    point_counts['do'] = point_qty
                elif point_type == 'AI':
                    point_counts['ai'] = point_qty
                elif point_type == 'AO':
                    point_counts['ao'] = point_qty
                
                panels_data[panel_name]['equipment_points'].append({
                    'equipment_name': equip.instance_name,
                    'point_name': pt.name,
                    'point_type': point_type,
                    'part_number': getattr(pt, 'part_number', '') or '',
                    'di': point_counts['di'],
                    'do': point_counts['do'],
                    'ai': point_counts['ai'],
                    'ao': point_counts['ao'],
                    'communication': communication
                })
                
                # Add to panel totals
                panels_data[panel_name]['panel_totals']['di'] += point_counts['di']
                panels_data[panel_name]['panel_totals']['do'] += point_counts['do']
                panels_data[panel_name]['panel_totals']['ai'] += point_counts['ai']
                panels_data[panel_name]['panel_totals']['ao'] += point_counts['ao']
                if communication:
                    panels_data[panel_name]['panel_totals']['communication'] += 1

    # Generate LaTeX table content with panel grouping
    grand_totals = {'di': 0, 'do': 0, 'ai': 0, 'ao': 0, 'communication': 0}
    
    for panel_name, panel_data in panels_data.items():
        # Add panel header row (spanning all columns)
        latex_content += f"\\multicolumn{{8}}{{|l|}}{{\\textbf{{{panel_name} ({panel_data['floor']})}}}} \\\\\n\\hline\n"
        
        # Add equipment points for this panel
        for point in panel_data['equipment_points']:
            # Escape special LaTeX characters in strings
            equipment_name = point['equipment_name'].replace('&', '\\&').replace('_', '\\_')
            point_name = point['point_name'].replace('&', '\\&').replace('_', '\\_')
            part_number = point['part_number'].replace('&', '\\&').replace('_', '\\_')
            communication = point['communication'].replace('&', '\\&').replace('_', '\\_')
            
            latex_content += f"{equipment_name} & {point_name} & {part_number} & {point['di']} & {point['do']} & {point['ai']} & {point['ao']} & {communication} \\\\\n\\hline\n"
        
        # Add panel totals
        panel_totals = panel_data['panel_totals']
        latex_content += f"\\textbf{{Total for {panel_name}}} & & & {panel_totals['di']} & {panel_totals['do']} & {panel_totals['ai']} & {panel_totals['ao']} & {panel_totals['communication'] if panel_totals['communication'] > 0 else ''} \\\\\n\\hline\n"
        
        # Add to grand totals
        grand_totals['di'] += panel_totals['di']
        grand_totals['do'] += panel_totals['do']
        grand_totals['ai'] += panel_totals['ai']
        grand_totals['ao'] += panel_totals['ao']
        grand_totals['communication'] += panel_totals['communication']
        
        # Add empty row for spacing
        latex_content += " & & & & & & & \\\\\n\\hline\n"
    
    # Add grand total row
    latex_content += f"\\textbf{{Grand Total}} & & & {grand_totals['di']} & {grand_totals['do']} & {grand_totals['ai']} & {grand_totals['ao']} & {grand_totals['communication'] if grand_totals['communication'] > 0 else ''} \\\\\n\\hline\n"

    latex_content += r"""
\end{longtable}

"""
    return latex_content

def generate_field_devices_boq_latex(project_id):
    """Generate LaTeX content for field devices BOQ using real data."""
    
    # Get real BOQ data from the existing API
    from urllib.parse import urljoin
    import requests
    
    # Instead of making HTTP request, directly call the BOQ generation logic
    scheduled_equipments = ScheduledEquipment.query.filter_by(project_id=project_id).all()
    
    field_devices_boq = {}
    total_field_cost = 0
    
    for equip in scheduled_equipments:
        selected_points = equip.selected_points.all() if hasattr(equip.selected_points, 'all') else equip.selected_points
        
        for pt in selected_points:
            if pt.part:  # Only include points with associated parts
                part = pt.part
                part_num = part.part_number
                
                # Calculate quantity needed
                etp = EquipmentTemplatePoint.query.filter_by(
                    equipment_template_id=equip.equipment_template_id, 
                    point_template_id=pt.id
                ).first()
                per_template_qty = etp.quantity if etp and etp.quantity else 1
                total_qty = (pt.quantity or 1) * per_template_qty * (equip.quantity or 1)
                
                if part_num not in field_devices_boq:
                    field_devices_boq[part_num] = {
                        'name': part.description,
                        'part_number': part_num,
                        'category': part.category or 'Field Device',
                        'quantity': 0,
                        'unit_cost': part.cost or 0,
                        'total_cost': 0
                    }
                
                field_devices_boq[part_num]['quantity'] += total_qty
                field_devices_boq[part_num]['total_cost'] = field_devices_boq[part_num]['quantity'] * (part.cost or 0)
                total_field_cost += total_qty * (part.cost or 0)
    
    latex_content = r"""
\section{Field Devices Bill of Quantities}

This section provides a bill of quantities for all field devices based on the scheduled equipment and selected I/O points.

\begin{longtable}{|l|l|l|l|l|l|}
\hline
\textbf{Part Number} & \textbf{Description} & \textbf{Category} & \textbf{Quantity} & \textbf{Unit Cost} & \textbf{Total Cost} \\
\hline
\endhead
"""

    # Add real field devices data
    for item in field_devices_boq.values():
        part_num = item['part_number']
        description = item['name']
        category = item['category']
        quantity = item['quantity']
        unit_cost = item['unit_cost']
        total_cost = item['total_cost']
        
        latex_content += f"{part_num} & {description} & {category} & {quantity} & \\${unit_cost:.2f} & \\${total_cost:.2f} \\\\\n\\hline\n"

    latex_content += f"""
\\hline
\\textbf{{TOTAL}} & & & & & \\textbf{{\\${total_field_cost:.2f}}} \\\\
\\hline
\\end{{longtable}}

"""
    return latex_content

def generate_controller_boq_latex(project_id):
    """Generate LaTeX content for controller BOQ using real data."""
    
    # Get controller selections and generate real BOQ data
    controller_selections = ControllerSelection.query.filter_by(project_id=project_id).all()
    
    controller_boq = {}
    accessory_boq = {}
    module_boq = {}
    total_controller_cost = 0
    
    for selection in controller_selections:
        if selection.controller_type:
            controller = selection.controller_type
            part_num = controller.part_number
            
            # Add controller to BOQ
            if part_num not in controller_boq:
                controller_boq[part_num] = {
                    'name': controller.name,
                    'part_number': part_num,
                    'quantity': 0,
                    'unit_cost': controller.cost,
                    'total_cost': 0,
                    'is_server': controller.is_server,
                    'category': 'Server' if controller.is_server else 'Controller'
                }
            
            controller_boq[part_num]['quantity'] += selection.quantity
            controller_boq[part_num]['total_cost'] = controller_boq[part_num]['quantity'] * controller.cost
            total_controller_cost += selection.quantity * controller.cost
            
            # Add controller accessories
            controller_accessories = Accessory.query.filter_by(parent_part_number=controller.part_number).all()
            for accessory in controller_accessories:
                acc_part_num = accessory.part_number
                if acc_part_num not in accessory_boq:
                    accessory_boq[acc_part_num] = {
                        'name': accessory.name,
                        'part_number': acc_part_num,
                        'quantity': 0,
                        'unit_cost': accessory.cost,
                        'total_cost': 0,
                        'category': 'Accessory'
                    }
                
                accessory_boq[acc_part_num]['quantity'] += selection.quantity
                accessory_boq[acc_part_num]['total_cost'] = accessory_boq[acc_part_num]['quantity'] * accessory.cost
                total_controller_cost += selection.quantity * accessory.cost
        
        # Add server modules if this is a server selection
        if selection.is_server_selection and selection.server_modules:
            modules = json.loads(selection.server_modules)
            for module_data in modules:
                module = ServerModule.query.get(module_data.get('id'))
                if module:
                    module_part_num = module.part_number
                    module_qty = module_data.get('quantity', 1)
                    
                    if module_part_num not in module_boq:
                        module_boq[module_part_num] = {
                            'name': module.name,
                            'part_number': module_part_num,
                            'quantity': 0,
                            'unit_cost': module.cost,
                            'total_cost': 0,
                            'category': 'Server Module'
                        }
                    
                    module_boq[module_part_num]['quantity'] += module_qty
                    module_boq[module_part_num]['total_cost'] = module_boq[module_part_num]['quantity'] * module.cost
                    total_controller_cost += module_qty * module.cost
    
    latex_content = r"""
\section{Controller Bill of Quantities}

This section provides a bill of quantities for controllers, servers, modules, and accessories based on the optimization results.

\begin{longtable}{|l|l|l|l|l|l|}
\hline
\textbf{Part Number} & \textbf{Description} & \textbf{Category} & \textbf{Quantity} & \textbf{Unit Cost} & \textbf{Total Cost} \\
\hline
\endhead
"""

    # Combine all controller items
    all_controller_items = (
        list(controller_boq.values()) + 
        list(module_boq.values()) + 
        list(accessory_boq.values())
    )
    
    # Add real controller data
    for item in all_controller_items:
        part_num = item['part_number']
        name = item['name']
        category = item['category']
        quantity = item['quantity']
        unit_cost = item['unit_cost']
        total_cost = item['total_cost']
        
        latex_content += f"{part_num} & {name} & {category} & {quantity} & \\${unit_cost:.2f} & \\${total_cost:.2f} \\\\\n\\hline\n"

    latex_content += f"""
\\hline
\\textbf{{TOTAL}} & & & & & \\textbf{{\\${total_controller_cost:.2f}}} \\\\
\\hline
\\end{{longtable}}

"""
    return latex_content

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

@app.route('/projects/<int:project_id>/rename', methods=['PUT'])
@login_required
def rename_project(project_id):
    """Rename a project."""
    project = Project.query.get_or_404(project_id)
    if project.owner != current_user:
        return jsonify({"error": "Unauthorized"}), 403
    
    data = request.get_json()
    new_name = data.get('name', '').strip()
    
    if not new_name:
        return jsonify({"error": "Project name is required"}), 400
    
    if len(new_name) > 120:  # Assuming there's a length limit
        return jsonify({"error": "Project name too long"}), 400
    
    # Check if name already exists for this user
    existing = Project.query.filter_by(user_id=current_user.id, name=new_name).first()
    if existing and existing.id != project_id:
        return jsonify({"error": "A project with this name already exists"}), 409
    
    old_name = project.name
    project.name = new_name
    db.session.commit()
    
    return jsonify({
        "message": f"Project renamed from '{old_name}' to '{new_name}'",
        "project": {
            "id": project.id,
            "name": project.name,
            "user_id": project.user_id
        }
    }), 200

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
        
        # Load controller types from CSV files
        load_csv_data()
        
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

def load_csv_data():
    """Load controller data from CSV files."""
    import csv
    import os
    
    basedir = os.path.abspath(os.path.dirname(__file__))
    
    # Load servers (automation servers)
    servers_file = os.path.join(basedir, 'servers.csv')
    if os.path.exists(servers_file) and ControllerType.query.filter_by(is_server=True).count() == 0:
        with open(servers_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                server = ControllerType(
                    name=row['Name'],
                    part_number=row['PartNumber'],
                    di_capacity=int(row.get('DI', 0)),
                    do_capacity=int(row.get('DO', 0)),
                    ai_capacity=int(row.get('AI', 0)),
                    ao_capacity=int(row.get('AO', 0)),
                    ui_capacity=int(row.get('UI', 0)),
                    uo_capacity=int(row.get('UO', 0)),
                    uio_capacity=int(row.get('UIO', 0)),
                    cost=float(row['Cost']),
                    is_server=True
                )
                db.session.add(server)
        print("Loaded automation servers from CSV")
    
    # Load controllers
    controllers_file = os.path.join(basedir, 'controllers.csv')
    if os.path.exists(controllers_file) and ControllerType.query.filter_by(is_server=False).count() == 0:
        with open(controllers_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                controller = ControllerType(
                    name=row['Name'],
                    part_number=row['PartNumber'],
                    di_capacity=int(row.get('DI', 0)),
                    do_capacity=int(row.get('DO', 0)),
                    ai_capacity=int(row.get('AI', 0)),
                    ao_capacity=int(row.get('AO', 0)),
                    ui_capacity=int(row.get('UI', 0)),
                    uo_capacity=int(row.get('UO', 0)),
                    uio_capacity=int(row.get('UIO', 0)),
                    cost=float(row['Cost']),
                    is_server=False
                )
                db.session.add(controller)
        print("Loaded controllers from CSV")
    
    # Load server modules
    modules_file = os.path.join(basedir, 'server_modules.csv')
    if os.path.exists(modules_file) and ServerModule.query.count() == 0:
        with open(modules_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                module = ServerModule(
                    name=row['Name'],
                    part_number=row['PartNumber'],
                    di_capacity=int(row.get('DI', 0)),
                    do_capacity=int(row.get('DO', 0)),
                    ai_capacity=int(row.get('AI', 0)),
                    ao_capacity=int(row.get('AO', 0)),
                    ui_capacity=int(row.get('UI', 0)),
                    uo_capacity=int(row.get('UO', 0)),
                    uio_capacity=int(row.get('UIO', 0)),
                    cost=float(row['Cost'])
                )
                db.session.add(module)
        print("Loaded server modules from CSV")
    
    # Load accessories
    accessories_file = os.path.join(basedir, 'accessories.csv')
    if os.path.exists(accessories_file) and Accessory.query.count() == 0:
        with open(accessories_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                accessory = Accessory(
                    parent_part_number=row['ParentPartNumber'],
                    name=row['AccessoryName'],
                    part_number=row['AccessoryPartNumber'],
                    cost=float(row['AccessoryCost'])
                )
                db.session.add(accessory)
        print("Loaded accessories from CSV")
    
    db.session.commit()

if __name__ == '__main__':
    setup_database(app)
    socketio.run(app, debug=True, port=5001, allow_unsafe_werkzeug=True)
