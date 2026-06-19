# app.py - Complete Farm Management System
# Based on Kenyan 8-4-4 Secondary School Agriculture Syllabus

import os
import json
import uuid
import secrets
import datetime
from datetime import datetime, timedelta, date
from functools import wraps
from decimal import Decimal
import cloudinary
import cloudinary.uploader
import cloudinary.api
from flask import (
    Flask, render_template, request, redirect, url_for, flash, 
    session, jsonify, send_file, abort, make_response, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required, 
    logout_user, current_user
)
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc, asc, and_, or_, extract, case
from sqlalchemy.orm import joinedload, aliased
from io import BytesIO
import base64
import calendar
import hashlib
import csv
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'farm-management-secret-key-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///farm_management.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@farmmanager.com')

CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', 'bantuafricafarm')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY', '')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '')

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'
mail = Mail(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'receipts'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'reports'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'documents'), exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv'}
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

def upload_to_cloudinary(file, folder='farm_management'):
    try:
        if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
            result = cloudinary.uploader.upload(file, folder=folder, resource_type="auto")
            return result['secure_url']
        else:
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                return url_for('uploaded_file', filename=filename, _external=True)
            return None
    except Exception as e:
        print(f"Upload error: {str(e)}")
        return None

def generate_reference_number(prefix='REF'):
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    random_suffix = secrets.token_hex(3).upper()
    return f"{prefix}-{timestamp}-{random_suffix}"

def calculate_kra_paye(gross_pay):
    monthly_gross = float(gross_pay)
    if monthly_gross <= 24000:
        tax = monthly_gross * 0.10
    elif monthly_gross <= 32333:
        tax = 2400 + (monthly_gross - 24000) * 0.25
    elif monthly_gross <= 500000:
        tax = 2400 + 2083.25 + (monthly_gross - 32333) * 0.30
    elif monthly_gross <= 800000:
        tax = 2400 + 2083.25 + 140300.10 + (monthly_gross - 500000) * 0.325
    else:
        tax = 2400 + 2083.25 + 140300.10 + 97500 + (monthly_gross - 800000) * 0.35
    personal_relief = 2400
    tax = max(0, tax - personal_relief)
    return round(tax, 2)

def calculate_nssf(gross_pay, tier='I'):
    if tier == 'I':
        return min(float(gross_pay) * 0.06, 720)
    else:
        return min(float(gross_pay) * 0.06, 1440)

def calculate_nhif(gross_pay):
    monthly_gross = float(gross_pay)
    brackets = [
        (5999, 150), (7999, 300), (11999, 400), (14999, 500),
        (19999, 600), (24999, 750), (29999, 850), (34999, 900),
        (39999, 950), (44999, 1000), (49999, 1100), (59999, 1200),
        (69999, 1300), (79999, 1400), (89999, 1500), (99999, 1600)
    ]
    for threshold, amount in brackets:
        if monthly_gross <= threshold:
            return amount
    return 1700

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'manager']:
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def log_activity(user_id, action, description, module):
    try:
        activity = ActivityLog(
            user_id=user_id,
            action=action,
            description=description,
            module=module,
            ip_address=request.remote_addr if request else 'system'
        )
        db.session.add(activity)
        db.session.commit()
    except Exception as e:
        print(f"Error logging activity: {e}")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================================================
# DATABASE MODELS
# ============================================================================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone_number = db.Column(db.String(20))
    national_id = db.Column(db.String(20), unique=True)
    kra_pin = db.Column(db.String(20))
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(10))
    marital_status = db.Column(db.String(20))
    profile_picture = db.Column(db.String(500))
    role = db.Column(db.String(30), nullable=False, index=True)
    department = db.Column(db.String(50))
    employment_type = db.Column(db.String(30))
    employment_date = db.Column(db.Date)
    salary_grade = db.Column(db.String(20))
    basic_salary = db.Column(db.Numeric(10, 2))
    hourly_rate = db.Column(db.Numeric(10, 2))
    nssf_number = db.Column(db.String(30))
    nhif_number = db.Column(db.String(30))
    bank_name = db.Column(db.String(50))
    bank_account = db.Column(db.String(30))
    bank_branch = db.Column(db.String(50))
    emergency_contact_name = db.Column(db.String(100))
    emergency_contact_phone = db.Column(db.String(20))
    emergency_contact_relation = db.Column(db.String(30))
    home_county = db.Column(db.String(50))
    home_address = db.Column(db.Text)
    education_level = db.Column(db.String(50))
    certifications = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, index=True)
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(200))
    password_reset_token = db.Column(db.String(200))
    password_reset_expires = db.Column(db.DateTime)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    attendances = db.relationship('Attendance', backref='employee', lazy='dynamic', foreign_keys='Attendance.user_id')
    tasks_assigned = db.relationship('Task', foreign_keys='Task.assigned_to', lazy='dynamic', back_populates='assignee')
    tasks_reported = db.relationship('Task', backref='reporter', lazy='dynamic', foreign_keys='Task.reported_by')
    tasks_completed = db.relationship('Task', backref='completer', lazy='dynamic', foreign_keys='Task.completed_by')
    wages = db.relationship('Wage', backref='employee', lazy='dynamic', foreign_keys='Wage.user_id')
    advances = db.relationship('SalaryAdvance', backref='employee', lazy='dynamic', foreign_keys='SalaryAdvance.user_id')
    leaves = db.relationship('Leave', backref='employee', lazy='dynamic', foreign_keys='Leave.user_id')
    deductions = db.relationship('Deduction', backref='employee', lazy='dynamic', foreign_keys='Deduction.user_id')
    bonuses = db.relationship('Bonus', backref='employee', lazy='dynamic', foreign_keys='Bonus.user_id')
    overtime_records = db.relationship('Overtime', backref='employee', lazy='dynamic', foreign_keys='Overtime.user_id')
    asset_assignments = db.relationship('AssetAssignment', backref='employee', lazy='dynamic', foreign_keys='AssetAssignment.user_id')
    payrolls = db.relationship('Payroll', backref='employee_rel', lazy='dynamic', foreign_keys='Payroll.user_id')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"
    
    def get_profile_picture(self):
        if self.profile_picture:
            return self.profile_picture
        return url_for('static', filename='images/default-avatar.png')
    
    def __repr__(self):
        return f"<User {self.username}>"

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    action = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    module = db.Column(db.String(50), index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user = db.relationship('User', backref='activities')

class SystemSetting(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text)
    setting_type = db.Column(db.String(20))
    description = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(30))
    link = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='notifications')

class LivestockCategory(db.Model):
    __tablename__ = 'livestock_categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    common_breeds = db.Column(db.Text)
    icon = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    animals = db.relationship('Livestock', backref='category', lazy='dynamic')
    task_templates = db.relationship('LivestockTaskTemplate', backref='category', lazy='dynamic')
    breeds = db.relationship('Breed', foreign_keys='Breed.category_id', lazy='dynamic')

class Breed(db.Model):
    __tablename__ = 'breeds'
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('livestock_categories.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    origin = db.Column(db.String(100))
    purpose = db.Column(db.String(100))
    category = db.relationship('LivestockCategory', foreign_keys='Breed.category_id', backref='breeds_list')

class Livestock(db.Model):
    __tablename__ = 'livestock'
    id = db.Column(db.Integer, primary_key=True)
    tag_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100))
    category_id = db.Column(db.Integer, db.ForeignKey('livestock_categories.id'), nullable=False, index=True)
    breed = db.Column(db.String(100))
    sex = db.Column(db.String(10), index=True)
    date_of_birth = db.Column(db.Date)
    acquisition_date = db.Column(db.Date)
    acquisition_type = db.Column(db.String(30))
    acquisition_cost = db.Column(db.Numeric(12, 2))
    current_weight = db.Column(db.Numeric(10, 2))
    color = db.Column(db.String(50))
    markings = db.Column(db.Text)
    dam_tag = db.Column(db.String(50))
    sire_tag = db.Column(db.String(50))
    pregnancy_status = db.Column(db.String(30), default='Open')
    health_status = db.Column(db.String(30), default='Healthy', index=True)
    production_status = db.Column(db.String(30), default='Active', index=True)
    location = db.Column(db.String(100))
    shed_number = db.Column(db.String(20))
    image_url = db.Column(db.String(500))
    estimated_value = db.Column(db.Numeric(12, 2))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    feeding_records = db.relationship('LivestockFeeding', backref='animal', lazy='dynamic')
    health_records = db.relationship('LivestockHealth', backref='animal', lazy='dynamic')
    breeding_records = db.relationship('BreedingRecord', backref='animal', lazy='dynamic')
    production_records = db.relationship('LivestockProduction', backref='animal', lazy='dynamic')
    weight_records = db.relationship('WeightRecord', backref='animal', lazy='dynamic')
    movements = db.relationship('LivestockMovement', backref='animal', lazy='dynamic')
    milk_records = db.relationship('MilkRecord', backref='animal', lazy='dynamic')

class LivestockTaskTemplate(db.Model):
    __tablename__ = 'livestock_task_templates'
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('livestock_categories.id'), index=True)
    task_name = db.Column(db.String(200), nullable=False)
    task_category = db.Column(db.String(50), index=True)
    frequency = db.Column(db.String(30))
    priority = db.Column(db.String(20))
    standard_procedure = db.Column(db.Text)
    expected_duration_minutes = db.Column(db.Integer)
    required_tools = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)

class LivestockFeeding(db.Model):
    __tablename__ = 'livestock_feeding'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    feed_type = db.Column(db.String(100))
    feed_name = db.Column(db.String(200))
    quantity_kg = db.Column(db.Numeric(10, 3))
    feeding_time = db.Column(db.DateTime, nullable=False)
    feeding_schedule = db.Column(db.String(20))
    fed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    cost = db.Column(db.Numeric(10, 2))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    feeder = db.relationship('User', backref='feeding_records_given')

class LivestockHealth(db.Model):
    __tablename__ = 'livestock_health'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    record_type = db.Column(db.String(50), index=True)
    diagnosis = db.Column(db.Text)
    treatment = db.Column(db.Text)
    medication_used = db.Column(db.String(200))
    dosage = db.Column(db.String(100))
    withdrawal_period_days = db.Column(db.Integer)
    veterinary_officer = db.Column(db.String(100))
    cost = db.Column(db.Numeric(10, 2))
    next_action_date = db.Column(db.Date)
    next_action = db.Column(db.String(200))
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    performed_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    performer = db.relationship('User', backref='health_records_performed')

class BreedingRecord(db.Model):
    __tablename__ = 'breeding_records'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    record_type = db.Column(db.String(50))
    service_type = db.Column(db.String(30))
    service_date = db.Column(db.Date)
    bull_sire_id = db.Column(db.String(50))
    semen_batch = db.Column(db.String(50))
    technician = db.Column(db.String(100))
    pregnancy_check_date = db.Column(db.Date)
    pregnancy_result = db.Column(db.String(20))
    expected_calving_date = db.Column(db.Date)
    actual_birth_date = db.Column(db.Date)
    offspring_count = db.Column(db.Integer)
    offspring_details = db.Column(db.Text)
    birth_weight_kg = db.Column(db.Numeric(10, 3))
    complications = db.Column(db.Text)
    colostrum_fed = db.Column(db.Boolean)
    weaning_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorder = db.relationship('User', backref='breeding_records_recorded')

class LivestockProduction(db.Model):
    __tablename__ = 'livestock_production'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    product_type = db.Column(db.String(50))
    quantity = db.Column(db.Numeric(10, 3))
    unit = db.Column(db.String(20))
    production_time = db.Column(db.String(20))
    production_date = db.Column(db.Date, nullable=False, index=True)
    quality_grade = db.Column(db.String(20))
    value_ksh = db.Column(db.Numeric(10, 2))
    buyer = db.Column(db.String(200))
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorder = db.relationship('User', foreign_keys=[recorded_by], backref='production_records_recorded')

class MilkRecord(db.Model):
    __tablename__ = 'milk_records'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    milking_date = db.Column(db.Date, nullable=False, index=True)
    milking_time = db.Column(db.String(20))
    quantity_litres = db.Column(db.Numeric(6, 2))
    butter_fat_content = db.Column(db.Numeric(4, 2))
    somatic_cell_count = db.Column(db.Integer)
    milked_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    milker = db.relationship('User', backref='milk_records')

class WeightRecord(db.Model):
    __tablename__ = 'weight_records'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    weight_kg = db.Column(db.Numeric(10, 3), nullable=False)
    weigh_date = db.Column(db.Date, nullable=False)
    weight_gain = db.Column(db.Numeric(10, 3))
    weighed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    weigher = db.relationship('User', backref='weight_records_taken')

class LivestockMovement(db.Model):
    __tablename__ = 'livestock_movements'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    from_location = db.Column(db.String(100))
    to_location = db.Column(db.String(100))
    reason = db.Column(db.String(200))
    movement_date = db.Column(db.DateTime, default=datetime.utcnow)
    moved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    mover = db.relationship('User', foreign_keys=[moved_by])
    approver = db.relationship('User', foreign_keys=[approved_by])

class LivestockDeath(db.Model):
    __tablename__ = 'livestock_deaths'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False)
    death_date = db.Column(db.Date, nullable=False)
    cause_of_death = db.Column(db.String(200))
    post_mortem_findings = db.Column(db.Text)
    disposal_method = db.Column(db.String(100))
    value_at_death = db.Column(db.Numeric(12, 2))
    reported_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    animal = db.relationship('Livestock', backref='death_record')
    reporter = db.relationship('User')

class LivestockSale(db.Model):
    __tablename__ = 'livestock_sales'
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False)
    sale_date = db.Column(db.Date, nullable=False)
    buyer_name = db.Column(db.String(200))
    buyer_contact = db.Column(db.String(50))
    sale_price = db.Column(db.Numeric(12, 2))
    weight_at_sale_kg = db.Column(db.Numeric(10, 2))
    sale_reason = db.Column(db.String(200))
    payment_received = db.Column(db.Boolean, default=False)
    receipt_url = db.Column(db.String(500))
    sold_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    animal = db.relationship('Livestock', backref='sale_record')
    seller = db.relationship('User')

class CropCategory(db.Model):
    __tablename__ = 'crop_categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    crops = db.relationship('Crop', backref='category', lazy='dynamic')

class Crop(db.Model):
    __tablename__ = 'crops'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    scientific_name = db.Column(db.String(200))
    category_id = db.Column(db.Integer, db.ForeignKey('crop_categories.id'), index=True)
    variety = db.Column(db.String(100))
    growth_period_days = db.Column(db.Integer)
    planting_season = db.Column(db.String(30))
    spacing = db.Column(db.String(50))
    expected_yield_per_acre = db.Column(db.Numeric(10, 2))
    seed_rate_kg_per_acre = db.Column(db.Numeric(8, 2))
    description = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    plantings = db.relationship('CropPlanting', backref='crop', lazy='dynamic')

class FarmField(db.Model):
    __tablename__ = 'farm_fields'
    id = db.Column(db.Integer, primary_key=True)
    field_name = db.Column(db.String(100), nullable=False)
    field_code = db.Column(db.String(20), unique=True)
    size_acres = db.Column(db.Numeric(10, 3))
    soil_type = db.Column(db.String(50))
    ph_level = db.Column(db.Numeric(4, 2))
    drainage = db.Column(db.String(30))
    current_status = db.Column(db.String(30), default='Fallow')
    location_description = db.Column(db.Text)
    latitude = db.Column(db.String(30))
    longitude = db.Column(db.String(30))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    plantings = db.relationship('CropPlanting', backref='field', lazy='dynamic')
    soil_tests = db.relationship('SoilTest', backref='field', lazy='dynamic')
    irrigation_records = db.relationship('IrrigationRecord', backref='field', lazy='dynamic')

class CropPlanting(db.Model):
    __tablename__ = 'crop_plantings'
    id = db.Column(db.Integer, primary_key=True)
    crop_id = db.Column(db.Integer, db.ForeignKey('crops.id'), nullable=False, index=True)
    field_id = db.Column(db.Integer, db.ForeignKey('farm_fields.id'), nullable=False, index=True)
    planting_date = db.Column(db.Date, nullable=False)
    planting_method = db.Column(db.String(30))
    seed_rate_used = db.Column(db.Numeric(10, 3))
    area_planted = db.Column(db.Numeric(10, 3))
    seed_cost = db.Column(db.Numeric(10, 2))
    expected_harvest_date = db.Column(db.Date)
    actual_harvest_date = db.Column(db.Date)
    current_stage = db.Column(db.String(30))
    health_status = db.Column(db.String(30))
    expected_yield_kg = db.Column(db.Numeric(12, 2))
    actual_yield_kg = db.Column(db.Numeric(12, 2))
    status = db.Column(db.String(20), default='Active', index=True)
    planted_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    planter = db.relationship('User')
    activities = db.relationship('CropActivity', backref='planting', lazy='dynamic')
    harvests = db.relationship('Harvest', backref='planting', lazy='dynamic')
    pest_controls = db.relationship('PestControl', backref='planting', lazy='dynamic')
    fertilizer_applications = db.relationship('FertilizerApplication', backref='planting', lazy='dynamic')

class CropActivity(db.Model):
    __tablename__ = 'crop_activities'
    id = db.Column(db.Integer, primary_key=True)
    planting_id = db.Column(db.Integer, db.ForeignKey('crop_plantings.id'), nullable=False, index=True)
    activity_type = db.Column(db.String(50))
    activity_name = db.Column(db.String(200))
    activity_date = db.Column(db.DateTime, nullable=False)
    duration_hours = db.Column(db.Numeric(5, 2))
    workers_involved = db.Column(db.Integer)
    cost_incurred = db.Column(db.Numeric(10, 2))
    tools_used = db.Column(db.Text)
    inputs_used = db.Column(db.Text)
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    weather_conditions = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    performer = db.relationship('User')

class SoilTest(db.Model):
    __tablename__ = 'soil_tests'
    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey('farm_fields.id'), nullable=False)
    test_date = db.Column(db.Date, nullable=False)
    ph_level = db.Column(db.Numeric(4, 2))
    nitrogen = db.Column(db.String(20))
    phosphorus = db.Column(db.String(20))
    potassium = db.Column(db.String(20))
    organic_matter = db.Column(db.String(20))
    calcium = db.Column(db.String(20))
    magnesium = db.Column(db.String(20))
    recommendations = db.Column(db.Text)
    tested_by = db.Column(db.String(100))
    lab_name = db.Column(db.String(200))
    cost = db.Column(db.Numeric(10, 2))
    report_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PestControl(db.Model):
    __tablename__ = 'pest_controls'
    id = db.Column(db.Integer, primary_key=True)
    planting_id = db.Column(db.Integer, db.ForeignKey('crop_plantings.id'), nullable=False)
    control_type = db.Column(db.String(30))
    pest_or_disease = db.Column(db.String(200))
    chemical_used = db.Column(db.String(200))
    application_rate = db.Column(db.String(100))
    application_method = db.Column(db.String(50))
    application_date = db.Column(db.DateTime, nullable=False)
    weather_conditions = db.Column(db.String(100))
    safety_measures = db.Column(db.Text)
    re_entry_period_hours = db.Column(db.Integer)
    harvest_interval_days = db.Column(db.Integer)
    cost = db.Column(db.Numeric(10, 2))
    effectiveness = db.Column(db.String(30))
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    performer = db.relationship('User')

class FertilizerApplication(db.Model):
    __tablename__ = 'fertilizer_applications'
    id = db.Column(db.Integer, primary_key=True)
    planting_id = db.Column(db.Integer, db.ForeignKey('crop_plantings.id'), nullable=False, index=True)
    fertilizer_type = db.Column(db.String(100))
    application_type = db.Column(db.String(30))
    quantity_kg = db.Column(db.Numeric(10, 2))
    application_date = db.Column(db.DateTime, nullable=False)
    application_method = db.Column(db.String(50))
    cost = db.Column(db.Numeric(10, 2))
    applied_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    applier = db.relationship('User')

class IrrigationRecord(db.Model):
    __tablename__ = 'irrigation_records'
    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey('farm_fields.id'), nullable=False, index=True)
    irrigation_date = db.Column(db.DateTime, nullable=False)
    irrigation_method = db.Column(db.String(30))
    water_source = db.Column(db.String(100))
    duration_hours = db.Column(db.Numeric(5, 2))
    water_volume_litres = db.Column(db.Numeric(10, 1))
    cost = db.Column(db.Numeric(10, 2))
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    performer = db.relationship('User')

class Harvest(db.Model):
    __tablename__ = 'harvests'
    id = db.Column(db.Integer, primary_key=True)
    planting_id = db.Column(db.Integer, db.ForeignKey('crop_plantings.id'), nullable=False, index=True)
    harvest_date = db.Column(db.Date, nullable=False)
    quantity_kg = db.Column(db.Numeric(12, 3))
    quality_grade = db.Column(db.String(20))
    moisture_content = db.Column(db.Numeric(5, 2))
    harvesting_method = db.Column(db.String(30))
    workers_involved = db.Column(db.Integer)
    labor_cost = db.Column(db.Numeric(10, 2))
    harvested_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    storage_location = db.Column(db.String(100))
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    harvester = db.relationship('User')
    post_harvest = db.relationship('PostHarvest', backref='harvest', lazy='dynamic')

class PostHarvest(db.Model):
    __tablename__ = 'post_harvest'
    id = db.Column(db.Integer, primary_key=True)
    harvest_id = db.Column(db.Integer, db.ForeignKey('harvests.id'), nullable=False)
    activity_type = db.Column(db.String(50))
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    quantity_processed_kg = db.Column(db.Numeric(12, 3))
    loss_kg = db.Column(db.Numeric(10, 3))
    loss_cause = db.Column(db.String(200))
    storage_facility = db.Column(db.String(100))
    treatment_applied = db.Column(db.String(200))
    workers_involved = db.Column(db.Integer)
    cost = db.Column(db.Numeric(10, 2))
    handled_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    handler = db.relationship('User')

class ProduceSale(db.Model):
    __tablename__ = 'produce_sales'
    id = db.Column(db.Integer, primary_key=True)
    harvest_id = db.Column(db.Integer, db.ForeignKey('harvests.id'))
    sale_date = db.Column(db.Date, nullable=False)
    produce_type = db.Column(db.String(100))
    quantity_sold_kg = db.Column(db.Numeric(12, 3))
    unit_price_ksh = db.Column(db.Numeric(10, 2))
    total_amount = db.Column(db.Numeric(15, 2))
    buyer_name = db.Column(db.String(200))
    buyer_contact = db.Column(db.String(50))
    payment_status = db.Column(db.String(20))
    payment_method = db.Column(db.String(30))
    receipt_url = db.Column(db.String(500))
    sold_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    harvest = db.relationship('Harvest')
    seller = db.relationship('User')

class FinancialAccount(db.Model):
    __tablename__ = 'financial_accounts'
    id = db.Column(db.Integer, primary_key=True)
    account_code = db.Column(db.String(20), unique=True, nullable=False)
    account_name = db.Column(db.String(200), nullable=False)
    account_type = db.Column(db.String(30))
    category = db.Column(db.String(50))
    parent_account_id = db.Column(db.Integer, db.ForeignKey('financial_accounts.id'))
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    opening_balance = db.Column(db.Numeric(15, 2), default=0)
    current_balance = db.Column(db.Numeric(15, 2), default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    parent = db.relationship('FinancialAccount', remote_side=[id], backref='sub_accounts')

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    transaction_date = db.Column(db.Date, nullable=False, index=True)
    transaction_type = db.Column(db.String(30))
    reference_number = db.Column(db.String(50), unique=True, index=True)
    description = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    account_debit_id = db.Column(db.Integer, db.ForeignKey('financial_accounts.id'))
    account_credit_id = db.Column(db.Integer, db.ForeignKey('financial_accounts.id'))
    payment_method = db.Column(db.String(30))
    payment_reference = db.Column(db.String(100))
    paid_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(20), default='Pending')
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    receipt_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    debit_account = db.relationship('FinancialAccount', foreign_keys=[account_debit_id])
    credit_account = db.relationship('FinancialAccount', foreign_keys=[account_credit_id])
    recorder = db.relationship('User', foreign_keys=[recorded_by])
    approver = db.relationship('User', foreign_keys=[approved_by])

class FarmIncome(db.Model):
    __tablename__ = 'farm_income'
    id = db.Column(db.Integer, primary_key=True)
    income_date = db.Column(db.Date, nullable=False, index=True)
    income_source = db.Column(db.String(100))
    income_category = db.Column(db.String(50))
    description = db.Column(db.Text)
    quantity = db.Column(db.Numeric(12, 3))
    unit_price = db.Column(db.Numeric(10, 2))
    total_amount = db.Column(db.Numeric(15, 2), nullable=False)
    buyer_name = db.Column(db.String(200))
    buyer_contact = db.Column(db.String(50))
    payment_method = db.Column(db.String(30))
    payment_status = db.Column(db.String(20), default='Pending')
    receipt_number = db.Column(db.String(50))
    receipt_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorder = db.relationship('User', foreign_keys=[recorded_by])

class FarmExpense(db.Model):
    __tablename__ = 'farm_expenses'
    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, nullable=False, index=True)
    expense_category = db.Column(db.String(50), index=True)
    description = db.Column(db.Text)
    quantity = db.Column(db.Numeric(12, 3))
    unit_price = db.Column(db.Numeric(10, 2))
    total_amount = db.Column(db.Numeric(15, 2), nullable=False)
    supplier_name = db.Column(db.String(200))
    supplier_contact = db.Column(db.String(50))
    payment_method = db.Column(db.String(30))
    payment_status = db.Column(db.String(20), default='Pending')
    receipt_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorder = db.relationship('User', foreign_keys=[recorded_by])
    approver = db.relationship('User', foreign_keys=[approved_by])

class Payroll(db.Model):
    __tablename__ = 'payrolls'
    id = db.Column(db.Integer, primary_key=True)
    payroll_period = db.Column(db.String(30), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    basic_salary = db.Column(db.Numeric(10, 2))
    allowances = db.Column(db.Numeric(10, 2), default=0)
    overtime_amount = db.Column(db.Numeric(10, 2), default=0)
    bonuses = db.Column(db.Numeric(10, 2), default=0)
    gross_pay = db.Column(db.Numeric(10, 2))
    nssf_deduction = db.Column(db.Numeric(10, 2), default=0)
    nhif_deduction = db.Column(db.Numeric(10, 2), default=0)
    paye_tax = db.Column(db.Numeric(10, 2), default=0)
    advance_recovery = db.Column(db.Numeric(10, 2), default=0)
    other_deductions = db.Column(db.Numeric(10, 2), default=0)
    total_deductions = db.Column(db.Numeric(10, 2))
    net_pay = db.Column(db.Numeric(10, 2))
    payment_status = db.Column(db.String(20), default='Pending')
    payment_date = db.Column(db.Date)
    payment_method = db.Column(db.String(30))
    payment_reference = db.Column(db.String(100))
    paid_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    generated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    payslip_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    employee = db.relationship('User', foreign_keys=[user_id], backref='payroll_records')
    generator = db.relationship('User', foreign_keys=[generated_by])

class Wage(db.Model):
    __tablename__ = 'wages'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    wage_date = db.Column(db.Date, nullable=False, index=True)
    wage_type = db.Column(db.String(30))
    hours_worked = db.Column(db.Numeric(5, 1))
    rate_per_hour = db.Column(db.Numeric(10, 2))
    task_description = db.Column(db.Text)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_status = db.Column(db.String(20), default='Pending')
    payment_date = db.Column(db.Date)
    payment_method = db.Column(db.String(30))
    paid_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    payer = db.relationship('User', foreign_keys=[paid_by])

class SalaryAdvance(db.Model):
    __tablename__ = 'salary_advances'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    advance_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    repayment_period_months = db.Column(db.Integer)
    monthly_repayment = db.Column(db.Numeric(10, 2))
    amount_repaid = db.Column(db.Numeric(10, 2), default=0)
    balance = db.Column(db.Numeric(10, 2))
    status = db.Column(db.String(20), default='Active')
    reason = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approver = db.relationship('User', foreign_keys=[approved_by])

class Deduction(db.Model):
    __tablename__ = 'deductions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    deduction_type = db.Column(db.String(50))
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    recurring = db.Column(db.Boolean, default=False)
    frequency = db.Column(db.String(20))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Bonus(db.Model):
    __tablename__ = 'bonuses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bonus_date = db.Column(db.Date, nullable=False)
    bonus_type = db.Column(db.String(50))
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approver = db.relationship('User', foreign_keys=[approved_by])

class Overtime(db.Model):
    __tablename__ = 'overtime'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    overtime_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Numeric(5, 1), nullable=False)
    rate_multiplier = db.Column(db.Numeric(3, 1), default=1.5)
    amount = db.Column(db.Numeric(10, 2))
    reason = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(20), default='Pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approver = db.relationship('User', foreign_keys=[approved_by])

class Leave(db.Model):
    __tablename__ = 'leaves'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    leave_type = db.Column(db.String(30))
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days_requested = db.Column(db.Integer)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='Pending')
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_date = db.Column(db.Date)
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approver = db.relationship('User', foreign_keys=[approved_by])

class Attendance(db.Model):
    __tablename__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    time_in = db.Column(db.DateTime)
    time_out = db.Column(db.DateTime)
    hours_worked = db.Column(db.Numeric(5, 2))
    status = db.Column(db.String(20))
    check_in_method = db.Column(db.String(30))
    check_in_location = db.Column(db.String(200))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'attendance_date', name='unique_daily_attendance'),)

class FarmAsset(db.Model):
    __tablename__ = 'farm_assets'
    id = db.Column(db.Integer, primary_key=True)
    asset_code = db.Column(db.String(30), unique=True, nullable=False)
    asset_name = db.Column(db.String(200), nullable=False)
    asset_category = db.Column(db.String(50))
    description = db.Column(db.Text)
    purchase_date = db.Column(db.Date)
    purchase_price = db.Column(db.Numeric(12, 2))
    current_value = db.Column(db.Numeric(12, 2))
    useful_life_years = db.Column(db.Integer)
    depreciation_rate = db.Column(db.Numeric(5, 2))
    location = db.Column(db.String(100))
    condition = db.Column(db.String(30))
    status = db.Column(db.String(30))
    serial_number = db.Column(db.String(100))
    manufacturer = db.Column(db.String(200))
    model = db.Column(db.String(100))
    image_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    maintenance_records = db.relationship('AssetMaintenance', backref='asset', lazy='dynamic')
    assignments = db.relationship('AssetAssignment', backref='asset', lazy='dynamic')

class AssetMaintenance(db.Model):
    __tablename__ = 'asset_maintenance'
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('farm_assets.id'), nullable=False, index=True)
    maintenance_date = db.Column(db.Date, nullable=False)
    maintenance_type = db.Column(db.String(30))
    description = db.Column(db.Text)
    cost = db.Column(db.Numeric(10, 2))
    performed_by = db.Column(db.String(100))
    service_provider = db.Column(db.String(200))
    next_maintenance_date = db.Column(db.Date)
    parts_replaced = db.Column(db.Text)
    receipt_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AssetAssignment(db.Model):
    __tablename__ = 'asset_assignments'
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('farm_assets.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assignment_date = db.Column(db.Date, nullable=False)
    expected_return_date = db.Column(db.Date)
    actual_return_date = db.Column(db.Date)
    condition_at_assignment = db.Column(db.String(30))
    condition_at_return = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class FarmStructure(db.Model):
    __tablename__ = 'farm_structures'
    id = db.Column(db.Integer, primary_key=True)
    structure_name = db.Column(db.String(200), nullable=False)
    structure_type = db.Column(db.String(50))
    construction_date = db.Column(db.Date)
    dimensions = db.Column(db.String(100))
    capacity = db.Column(db.String(100))
    condition = db.Column(db.String(30))
    last_inspection_date = db.Column(db.Date)
    next_inspection_date = db.Column(db.Date)
    maintenance_cost_ytd = db.Column(db.Numeric(12, 2))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    task_title = db.Column(db.String(200), nullable=False)
    task_description = db.Column(db.Text)
    task_category = db.Column(db.String(50), index=True)
    task_type = db.Column(db.String(50))
    priority = db.Column(db.String(20))
    status = db.Column(db.String(30), default='Pending', index=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    assigned_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    reported_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    completed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    due_date = db.Column(db.Date)
    start_date = db.Column(db.DateTime)
    completion_date = db.Column(db.DateTime)
    estimated_hours = db.Column(db.Numeric(5, 1))
    actual_hours = db.Column(db.Numeric(5, 1))
    location = db.Column(db.String(200))
    tools_required = db.Column(db.Text)
    livestock_id = db.Column(db.Integer, db.ForeignKey('livestock.id'))
    planting_id = db.Column(db.Integer, db.ForeignKey('crop_plantings.id'))
    field_id = db.Column(db.Integer, db.ForeignKey('farm_fields.id'))
    completion_photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    cost = db.Column(db.Numeric(10, 2))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    livestock = db.relationship('Livestock')
    planting = db.relationship('CropPlanting')
    field = db.relationship('FarmField')
    assignee = db.relationship('User', foreign_keys=[assigned_to], back_populates='tasks_assigned')

class TaskComment(db.Model):
    __tablename__ = 'task_comments'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    photo_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    task = db.relationship('Task', backref='comments')
    user = db.relationship('User')

class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'
    id = db.Column(db.Integer, primary_key=True)
    item_code = db.Column(db.String(30), unique=True)
    item_name = db.Column(db.String(200), nullable=False)
    item_category = db.Column(db.String(50))
    description = db.Column(db.Text)
    unit_of_measure = db.Column(db.String(20))
    quantity_in_stock = db.Column(db.Numeric(12, 3), default=0)
    reorder_level = db.Column(db.Numeric(12, 3))
    unit_price = db.Column(db.Numeric(10, 2))
    total_value = db.Column(db.Numeric(15, 2))
    supplier_name = db.Column(db.String(200))
    supplier_contact = db.Column(db.String(50))
    storage_location = db.Column(db.String(100))
    expiry_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    stock_movements = db.relationship('StockMovement', backref='item', lazy='dynamic')

class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False, index=True)
    movement_type = db.Column(db.String(20))
    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    reference_number = db.Column(db.String(50))
    movement_date = db.Column(db.DateTime, default=datetime.utcnow)
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    reason = db.Column(db.Text)
    cost = db.Column(db.Numeric(10, 2))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    performer = db.relationship('User')

class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    supplier_name = db.Column(db.String(200), nullable=False)
    supplier_code = db.Column(db.String(30), unique=True)
    contact_person = db.Column(db.String(100))
    phone_number = db.Column(db.String(20))
    email = db.Column(db.String(120))
    physical_address = db.Column(db.Text)
    county = db.Column(db.String(50))
    town = db.Column(db.String(50))
    category = db.Column(db.String(50))
    payment_terms = db.Column(db.String(100))
    bank_name = db.Column(db.String(50))
    bank_account = db.Column(db.String(30))
    kra_pin = db.Column(db.String(20))
    rating = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class GeneratedReport(db.Model):
    __tablename__ = 'generated_reports'
    id = db.Column(db.Integer, primary_key=True)
    report_name = db.Column(db.String(200), nullable=False)
    report_type = db.Column(db.String(50))
    report_period = db.Column(db.String(50))
    parameters = db.Column(db.Text)
    file_path = db.Column(db.String(500))
    generated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    generator = db.relationship('User')

class DailyFarmLog(db.Model):
    __tablename__ = 'daily_farm_logs'
    id = db.Column(db.Integer, primary_key=True)
    log_date = db.Column(db.Date, nullable=False, index=True)
    weather_conditions = db.Column(db.Text)
    temperature_min = db.Column(db.Numeric(5, 1))
    temperature_max = db.Column(db.Numeric(5, 1))
    rainfall_mm = db.Column(db.Numeric(6, 1))
    activities_summary = db.Column(db.Text)
    issues_identified = db.Column(db.Text)
    recommendations = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    recorder = db.relationship('User', foreign_keys=[recorded_by])

class DailyProductionSummary(db.Model):
    """Daily production summary for each animal"""
    __tablename__ = 'daily_production_summaries'
    
    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey('livestock.id'), nullable=False, index=True)
    production_date = db.Column(db.Date, nullable=False, index=True)
    product_type = db.Column(db.String(50))  # Milk, Eggs, Wool, etc.
    quantity = db.Column(db.Numeric(10, 3))
    unit = db.Column(db.String(20))
    value_ksh = db.Column(db.Numeric(10, 2))
    image_url = db.Column(db.String(500))
    notes = db.Column(db.Text)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    animal = db.relationship('Livestock', backref='daily_production')
    recorder = db.relationship('User', foreign_keys=[recorded_by])


# ============================================================================
# CONTEXT PROCESSORS
# ============================================================================

@app.context_processor
def utility_processor():
    def format_currency(amount):
        if amount is None:
            return "Ksh 0.00"
        return f"Ksh {float(amount):,.2f}"
    
    def format_date(date_obj, format='%d-%m-%Y'):
        if date_obj:
            return date_obj.strftime(format)
        return ""
    
    def get_status_color(status):
        colors = {
            'Active': 'success', 'Pending': 'warning', 'Completed': 'primary',
            'Cancelled': 'danger', 'Healthy': 'success', 'Sick': 'danger',
            'Under Observation': 'warning'
        }
        return colors.get(status, 'secondary')
    
    return dict(
        format_currency=format_currency,
        format_date=format_date,
        get_status_color=get_status_color,
        now=datetime.now,
        current_year=datetime.now().year
    )

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Contact admin.', 'danger')
                return redirect(url_for('login'))
            
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            log_activity(user.id, 'LOGIN', f'User {user.username} logged in', 'Authentication')
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user.role == 'manager':
                return redirect(url_for('dashboard'))
            else:
                return redirect(url_for('worker_dashboard'))
        else:
            flash('Invalid username or password. Please try again.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_activity(current_user.id, 'LOGOUT', f'User {current_user.username} logged out', 'Authentication')
    logout_user()
    flash('You have been successfully logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if user:
            token = secrets.token_urlsafe(32)
            user.password_reset_token = token
            user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
            db.session.commit()
            
            reset_url = url_for('reset_password', token=token, _external=True)
            
            try:
                msg = Message('Password Reset Request', recipients=[user.email])
                msg.body = f'''To reset your password, visit the following link:
{reset_url}

If you did not make this request, please ignore this email.
'''
                mail.send(msg)
                flash('Password reset instructions sent to your email.', 'info')
            except:
                flash('Unable to send email. Please contact admin.', 'warning')
        else:
            flash('Email address not found.', 'danger')
        
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.query.filter_by(password_reset_token=token).first()
    
    if not user or user.password_reset_expires < datetime.utcnow():
        flash('Invalid or expired reset token.', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', token=token)
        
        user.set_password(password)
        user.password_reset_token = None
        user.password_reset_expires = None
        db.session.commit()
        
        flash('Your password has been reset. Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)

# ============================================================================
# DASHBOARD ROUTES
# ============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    total_livestock = Livestock.query.filter_by(is_active=True).count()
    total_crops = CropPlanting.query.filter_by(status='Active').count()
    total_employees = User.query.filter_by(is_active=True).count()
    pending_tasks = Task.query.filter_by(status='Pending').count()
    
    livestock_categories = db.session.query(
        LivestockCategory.name, func.count(Livestock.id)
    ).join(Livestock).filter(Livestock.is_active==True).group_by(LivestockCategory.name).all()
    
    recent_activities = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(10).all()
    
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    monthly_income = db.session.query(func.sum(FarmIncome.total_amount)).filter(
        extract('month', FarmIncome.income_date) == current_month,
        extract('year', FarmIncome.income_date) == current_year
    ).scalar() or 0
    
    monthly_expenses = db.session.query(func.sum(FarmExpense.total_amount)).filter(
        extract('month', FarmExpense.expense_date) == current_month,
        extract('year', FarmExpense.expense_date) == current_year
    ).scalar() or 0
    
    milk_production = db.session.query(func.sum(MilkRecord.quantity_litres)).filter(
        extract('month', MilkRecord.milking_date) == current_month,
        extract('year', MilkRecord.milking_date) == current_year
    ).scalar() or 0
    
    task_stats = db.session.query(Task.status, func.count(Task.id)).group_by(Task.status).all()
    
    upcoming_tasks = Task.query.filter(
        Task.due_date >= date.today(),
        Task.status.in_(['Pending', 'In Progress'])
    ).order_by(Task.due_date.asc()).limit(5).all()
    
    sick_animals = Livestock.query.filter_by(health_status='Sick', is_active=True).count()
    
    return render_template('dashboard.html',
                         total_livestock=total_livestock,
                         total_crops=total_crops,
                         total_employees=total_employees,
                         pending_tasks=pending_tasks,
                         livestock_categories=livestock_categories,
                         recent_activities=recent_activities,
                         monthly_income=monthly_income,
                         monthly_expenses=monthly_expenses,
                         milk_production=milk_production,
                         task_stats=task_stats,
                         upcoming_tasks=upcoming_tasks,
                         sick_animals=sick_animals)

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    stats = {
        'total_users': User.query.count(),
        'active_users': User.query.filter_by(is_active=True).count(),
        'total_livestock': Livestock.query.count(),
        'active_crops': CropPlanting.query.filter_by(status='Active').count(),
        'monthly_revenue': db.session.query(func.sum(FarmIncome.total_amount)).filter(
            extract('month', FarmIncome.income_date) == datetime.now().month
        ).scalar() or 0,
        'monthly_expenses': db.session.query(func.sum(FarmExpense.total_amount)).filter(
            extract('month', FarmExpense.expense_date) == datetime.now().month
        ).scalar() or 0,
        'pending_payroll': Payroll.query.filter_by(payment_status='Pending').count(),
        'pending_approvals': Leave.query.filter_by(status='Pending').count()
    }
    
    dept_performance = db.session.query(
        User.department, func.count(Task.id)
    ).join(Task, Task.assigned_to == User.id).filter(
        Task.status == 'Completed'
    ).group_by(User.department).all()
    
    monthly_trends = []
    for i in range(11, -1, -1):
        month_date = date.today().replace(day=1) - timedelta(days=i*30)
        month_income = db.session.query(func.sum(FarmIncome.total_amount)).filter(
            extract('month', FarmIncome.income_date) == month_date.month,
            extract('year', FarmIncome.income_date) == month_date.year
        ).scalar() or 0
        month_expense = db.session.query(func.sum(FarmExpense.total_amount)).filter(
            extract('month', FarmExpense.expense_date) == month_date.month,
            extract('year', FarmExpense.expense_date) == month_date.year
        ).scalar() or 0
        monthly_trends.append({
            'month': month_date.strftime('%b %Y'),
            'income': float(month_income),
            'expense': float(month_expense)
        })
    
    return render_template('admin/dashboard.html', 
                         stats=stats,
                         dept_performance=dept_performance,
                         monthly_trends=monthly_trends)

@app.route('/worker/dashboard')
@login_required
def worker_dashboard():
    my_tasks = Task.query.filter_by(assigned_to=current_user.id).order_by(Task.due_date.asc()).limit(10).all()
    today_attendance = Attendance.query.filter_by(user_id=current_user.id, attendance_date=date.today()).first()
    
    monthly_wages = db.session.query(func.sum(Wage.amount)).filter(
        Wage.user_id == current_user.id,
        extract('month', Wage.wage_date) == datetime.now().month,
        extract('year', Wage.wage_date) == datetime.now().year
    ).scalar() or 0
    
    approved_leaves = Leave.query.filter(
        Leave.user_id == current_user.id,
        Leave.status == 'Approved',
        extract('year', Leave.start_date) == datetime.now().year
    ).all()
    
    leave_days_taken = sum(leave.days_requested for leave in approved_leaves)
    leave_balance = 21 - leave_days_taken
    
    return render_template('worker/dashboard.html',
                         my_tasks=my_tasks,
                         today_attendance=today_attendance,
                         monthly_wages=monthly_wages,
                         leave_balance=leave_balance)

# ============================================================================
# USER MANAGEMENT ROUTES
# ============================================================================

@app.route('/users')
@login_required
@admin_required
def user_list():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    users = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    departments = db.session.query(User.department).distinct().all()
    roles = db.session.query(User.role).distinct().all()
    
    return render_template('users/list.html', 
                         users=users,
                         departments=[d[0] for d in departments if d[0]],
                         roles=[r[0] for r in roles if r[0]])

@app.route('/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_user():
    if request.method == 'POST':
        year = datetime.now().year
        count = User.query.count() + 1
        employee_id = f"EMP{year}{count:04d}"
        
        profile_pic = request.files.get('profile_picture')
        profile_pic_url = None
        if profile_pic and allowed_image(profile_pic.filename):
            profile_pic_url = upload_to_cloudinary(profile_pic, 'employee_photos')
        
        user = User(
            employee_id=employee_id,
            username=request.form.get('username'),
            email=request.form.get('email'),
            first_name=request.form.get('first_name'),
            last_name=request.form.get('last_name'),
            phone_number=request.form.get('phone_number'),
            national_id=request.form.get('national_id'),
            kra_pin=request.form.get('kra_pin'),
            date_of_birth=datetime.strptime(request.form.get('date_of_birth'), '%Y-%m-%d') if request.form.get('date_of_birth') else None,
            gender=request.form.get('gender'),
            marital_status=request.form.get('marital_status'),
            profile_picture=profile_pic_url,
            role=request.form.get('role', 'worker'),
            department=request.form.get('department'),
            employment_type=request.form.get('employment_type'),
            employment_date=datetime.strptime(request.form.get('employment_date'), '%Y-%m-%d') if request.form.get('employment_date') else date.today(),
            salary_grade=request.form.get('salary_grade'),
            basic_salary=request.form.get('basic_salary'),
            hourly_rate=request.form.get('hourly_rate'),
            nssf_number=request.form.get('nssf_number'),
            nhif_number=request.form.get('nhif_number'),
            bank_name=request.form.get('bank_name'),
            bank_account=request.form.get('bank_account'),
            bank_branch=request.form.get('bank_branch'),
            emergency_contact_name=request.form.get('emergency_contact_name'),
            emergency_contact_phone=request.form.get('emergency_contact_phone'),
            emergency_contact_relation=request.form.get('emergency_contact_relation'),
            home_county=request.form.get('home_county'),
            home_address=request.form.get('home_address'),
            education_level=request.form.get('education_level'),
            certifications=request.form.get('certifications')
        )
        user.set_password(request.form.get('password'))
        db.session.add(user)
        db.session.commit()
        log_activity(current_user.id, 'CREATE_USER', f'Created user {user.full_name} ({employee_id})', 'HR')
        flash(f'Employee {user.full_name} added successfully!', 'success')
        return redirect(url_for('user_list'))
    
    return render_template('users/add.html')

@app.route('/users/<int:user_id>')
@login_required
def user_profile(user_id):
    user = User.query.get_or_404(user_id)
    tasks_completed = Task.query.filter_by(assigned_to=user.id, status='Completed').count()
    attendance_rate = calculate_attendance_rate(user.id)
    total_wages = db.session.query(func.sum(Wage.amount)).filter(Wage.user_id == user.id).scalar() or 0
    
    return render_template('users/profile.html',
                         user=user,
                         tasks_completed=tasks_completed,
                         attendance_rate=attendance_rate,
                         total_wages=total_wages)

@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form.get('username', user.username)
        user.email = request.form.get('email', user.email)
        user.first_name = request.form.get('first_name', user.first_name)
        user.last_name = request.form.get('last_name', user.last_name)
        user.phone_number = request.form.get('phone_number', user.phone_number)
        user.role = request.form.get('role', user.role)
        user.department = request.form.get('department', user.department)
        user.employment_type = request.form.get('employment_type', user.employment_type)
        user.salary_grade = request.form.get('salary_grade', user.salary_grade)
        user.basic_salary = request.form.get('basic_salary', user.basic_salary)
        user.hourly_rate = request.form.get('hourly_rate', user.hourly_rate)
        user.is_active = request.form.get('is_active') == 'on'
        
        profile_pic = request.files.get('profile_picture')
        if profile_pic and allowed_image(profile_pic.filename):
            user.profile_picture = upload_to_cloudinary(profile_pic, 'employee_photos')
        
        new_password = request.form.get('new_password')
        if new_password:
            user.set_password(new_password)
        
        db.session.commit()
        log_activity(current_user.id, 'UPDATE_USER', f'Updated user {user.full_name}', 'HR')
        flash('User updated successfully!', 'success')
        return redirect(url_for('user_profile', user_id=user.id))
    
    return render_template('users/edit.html', user=user)

@app.route('/users/<int:user_id>/deactivate', methods=['POST'])
@login_required
@admin_required
def deactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = False
    db.session.commit()
    log_activity(current_user.id, 'DEACTIVATE_USER', f'Deactivated user {user.full_name}', 'HR')
    flash(f'User {user.full_name} has been deactivated.', 'warning')
    return redirect(url_for('user_list'))

# ============================================================================
# LIVESTOCK MANAGEMENT ROUTES
# ============================================================================

@app.route('/livestock')
@login_required
def livestock_list():
    category_filter = request.args.get('category')
    health_filter = request.args.get('health')
    status_filter = request.args.get('status')
    
    query = Livestock.query.filter_by(is_active=True)
    
    if category_filter:
        query = query.join(LivestockCategory).filter(LivestockCategory.name == category_filter)
    if health_filter:
        query = query.filter(Livestock.health_status == health_filter)
    if status_filter:
        query = query.filter(Livestock.production_status == status_filter)
    
    livestock = query.order_by(Livestock.tag_number).all()
    categories = LivestockCategory.query.all()
    
    total_count = Livestock.query.filter_by(is_active=True).count()
    healthy_count = Livestock.query.filter_by(health_status='Healthy', is_active=True).count()
    sick_count = Livestock.query.filter_by(health_status='Sick', is_active=True).count()
    pregnant_count = Livestock.query.filter_by(pregnancy_status='Pregnant', is_active=True).count()
    
    return render_template('livestock/list.html',
                         livestock=livestock,
                         categories=categories,
                         total_count=total_count,
                         healthy_count=healthy_count,
                         sick_count=sick_count,
                         pregnant_count=pregnant_count)

@app.route('/livestock/add', methods=['GET', 'POST'])
@login_required
def add_livestock():
    if request.method == 'POST':
        tag_number = request.form.get('tag_number')
        
        existing = Livestock.query.filter_by(tag_number=tag_number).first()
        if existing:
            flash('Tag number already exists!', 'danger')
            return redirect(url_for('add_livestock'))
        
        image_file = request.files.get('image')
        image_url = None
        if image_file and allowed_image(image_file.filename):
            image_url = upload_to_cloudinary(image_file, 'livestock_photos')
        
        livestock = Livestock(
            tag_number=tag_number,
            name=request.form.get('name'),
            category_id=request.form.get('category_id'),
            breed=request.form.get('breed'),
            sex=request.form.get('sex'),
            date_of_birth=datetime.strptime(request.form.get('date_of_birth'), '%Y-%m-%d') if request.form.get('date_of_birth') else None,
            acquisition_date=datetime.strptime(request.form.get('acquisition_date'), '%Y-%m-%d') if request.form.get('acquisition_date') else date.today(),
            acquisition_type=request.form.get('acquisition_type'),
            acquisition_cost=request.form.get('acquisition_cost'),
            current_weight=request.form.get('current_weight'),
            color=request.form.get('color'),
            markings=request.form.get('markings'),
            dam_tag=request.form.get('dam_tag'),
            sire_tag=request.form.get('sire_tag'),
            location=request.form.get('location'),
            shed_number=request.form.get('shed_number'),
            notes=request.form.get('notes'),
            image_url=image_url,
            estimated_value=request.form.get('estimated_value')
        )
        
        db.session.add(livestock)
        db.session.commit()
        log_activity(current_user.id, 'ADD_LIVESTOCK', f'Added livestock {tag_number}', 'Livestock')
        flash('Livestock added successfully!', 'success')
        return redirect(url_for('livestock_list'))
    
    categories = LivestockCategory.query.all()
    return render_template('livestock/add.html', categories=categories)

@app.route('/livestock/<int:livestock_id>')
@login_required
def livestock_detail(livestock_id):
    animal = Livestock.query.get_or_404(livestock_id)
    
    feeding_records = LivestockFeeding.query.filter_by(animal_id=livestock_id).order_by(LivestockFeeding.feeding_time.desc()).limit(20).all()
    health_records = LivestockHealth.query.filter_by(animal_id=livestock_id).order_by(LivestockHealth.performed_date.desc()).limit(20).all()
    breeding_records = BreedingRecord.query.filter_by(animal_id=livestock_id).order_by(BreedingRecord.service_date.desc()).limit(10).all()
    production_records = LivestockProduction.query.filter_by(animal_id=livestock_id).order_by(LivestockProduction.production_date.desc()).limit(20).all()
    weight_records = WeightRecord.query.filter_by(animal_id=livestock_id).order_by(WeightRecord.weigh_date.desc()).limit(20).all()
    milk_records = MilkRecord.query.filter_by(animal_id=livestock_id).order_by(MilkRecord.milking_date.desc()).limit(30).all()
    
    avg_milk = db.session.query(func.avg(MilkRecord.quantity_litres)).filter(MilkRecord.animal_id == livestock_id).scalar() or 0
    weight_gain = calculate_weight_gain(livestock_id)
    
    return render_template('livestock/detail.html',
                         animal=animal,
                         feeding_records=feeding_records,
                         health_records=health_records,
                         breeding_records=breeding_records,
                         production_records=production_records,
                         weight_records=weight_records,
                         milk_records=milk_records,
                         avg_milk=avg_milk,
                         weight_gain=weight_gain)

@app.route('/livestock/<int:livestock_id>/feeding/add', methods=['GET', 'POST'])
@login_required
def add_feeding_record(livestock_id):
    animal = Livestock.query.get_or_404(livestock_id)
    
    if request.method == 'POST':
        photo = request.files.get('photo')
        photo_url = None
        if photo and allowed_image(photo.filename):
            photo_url = upload_to_cloudinary(photo, 'feeding_photos')
        
        feeding = LivestockFeeding(
            animal_id=livestock_id,
            feed_type=request.form.get('feed_type'),
            feed_name=request.form.get('feed_name'),
            quantity_kg=request.form.get('quantity_kg'),
            feeding_time=datetime.strptime(request.form.get('feeding_time'), '%Y-%m-%dT%H:%M'),
            feeding_schedule=request.form.get('feeding_schedule'),
            fed_by=current_user.id,
            notes=request.form.get('notes'),
            photo_url=photo_url,
            cost=request.form.get('cost')
        )
        db.session.add(feeding)
        db.session.commit()
        log_activity(current_user.id, 'ADD_FEEDING', f'Added feeding record for {animal.tag_number}', 'Livestock')
        flash('Feeding record added!', 'success')
        return redirect(url_for('livestock_detail', livestock_id=livestock_id))
    
    return render_template('livestock/add_feeding.html', animal=animal)

# ============================================================================
# CROP MANAGEMENT ROUTES
# ============================================================================

@app.route('/crops')
@login_required
def crop_list():
    status_filter = request.args.get('status', 'Active')
    
    plantings = CropPlanting.query.filter_by(status=status_filter).order_by(
        CropPlanting.planting_date.desc()
    ).all()
    
    fields = FarmField.query.all()
    
    total_plantings = CropPlanting.query.filter_by(status='Active').count()
    total_harvested = CropPlanting.query.filter_by(status='Harvested').count()
    total_area = db.session.query(func.sum(CropPlanting.area_planted)).filter_by(status='Active').scalar() or 0
    
    return render_template('crops/list.html',
                         plantings=plantings,
                         fields=fields,
                         total_plantings=total_plantings,
                         total_harvested=total_harvested,
                         total_area=total_area)

@app.route('/crops/planting/add', methods=['GET', 'POST'])
@login_required
def add_planting():
    if request.method == 'POST':
        planting = CropPlanting(
            crop_id=request.form.get('crop_id'),
            field_id=request.form.get('field_id'),
            planting_date=datetime.strptime(request.form.get('planting_date'), '%Y-%m-%d'),
            planting_method=request.form.get('planting_method'),
            seed_rate_used=request.form.get('seed_rate_used'),
            area_planted=request.form.get('area_planted'),
            seed_cost=request.form.get('seed_cost'),
            expected_harvest_date=datetime.strptime(request.form.get('expected_harvest_date'), '%Y-%m-%d') if request.form.get('expected_harvest_date') else None,
            planted_by=current_user.id,
            notes=request.form.get('notes')
        )
        db.session.add(planting)
        db.session.commit()
        flash('Crop planting recorded!', 'success')
        return redirect(url_for('crop_list'))
    
    crops = Crop.query.order_by(Crop.name).all()
    fields = FarmField.query.filter(FarmField.current_status.in_(['Fallow', 'Under Preparation'])).all()
    return render_template('crops/add_planting.html', crops=crops, fields=fields)

@app.route('/crops/planting/<int:planting_id>')
@login_required
def planting_detail(planting_id):
    planting = CropPlanting.query.get_or_404(planting_id)
    activities = CropActivity.query.filter_by(planting_id=planting_id).order_by(CropActivity.activity_date.desc()).all()
    harvests = Harvest.query.filter_by(planting_id=planting_id).order_by(Harvest.harvest_date.desc()).all()
    
    total_cost = db.session.query(func.sum(CropActivity.cost_incurred)).filter_by(planting_id=planting_id).scalar() or 0
    total_harvest_value = db.session.query(func.sum(Harvest.quantity_kg)).filter_by(planting_id=planting_id).scalar() or 0
    
    return render_template('crops/planting_detail.html',
                         planting=planting,
                         activities=activities,
                         harvests=harvests,
                         total_cost=float(total_cost),
                         total_harvest_value=total_harvest_value)

@app.route('/crops/activity/add/<int:planting_id>', methods=['GET', 'POST'])
@login_required
def add_crop_activity(planting_id):
    planting = CropPlanting.query.get_or_404(planting_id)
    
    if request.method == 'POST':
        activity = CropActivity(
            planting_id=planting_id,
            activity_type=request.form.get('activity_type'),
            activity_name=request.form.get('activity_name'),
            activity_date=datetime.strptime(request.form.get('activity_date'), '%Y-%m-%dT%H:%M'),
            duration_hours=request.form.get('duration_hours'),
            workers_involved=request.form.get('workers_involved'),
            cost_incurred=request.form.get('cost_incurred'),
            tools_used=request.form.get('tools_used'),
            inputs_used=request.form.get('inputs_used'),
            performed_by=current_user.id,
            notes=request.form.get('notes'),
            weather_conditions=request.form.get('weather_conditions')
        )
        db.session.add(activity)
        db.session.commit()
        flash('Crop activity recorded!', 'success')
        return redirect(url_for('planting_detail', planting_id=planting_id))
    
    activity_types = [
        'Land Preparation', 'Ploughing', 'Harrowing', 'Ridging',
        'Planting', 'Transplanting', 'Gap Filling',
        'Weeding', 'Mulching', 'Herbicide Application',
        'Fertilizer Application', 'Top-dressing', 'Foliar Feeding',
        'Irrigation', 'Pruning', 'Training', 'Staking',
        'Pest Scouting', 'Disease Inspection',
        'Harvesting', 'Threshing', 'Winnowing',
        'Soil Conservation', 'Other'
    ]
    
    return render_template('crops/add_activity.html', 
                         planting=planting,
                         activity_types=activity_types)

@app.route('/crops/harvest/add/<int:planting_id>', methods=['GET', 'POST'])
@login_required
def add_harvest(planting_id):
    planting = CropPlanting.query.get_or_404(planting_id)
    
    if request.method == 'POST':
        harvest = Harvest(
            planting_id=planting_id,
            harvest_date=datetime.strptime(request.form.get('harvest_date'), '%Y-%m-%d'),
            quantity_kg=request.form.get('quantity_kg'),
            quality_grade=request.form.get('quality_grade'),
            moisture_content=request.form.get('moisture_content'),
            harvesting_method=request.form.get('harvesting_method'),
            workers_involved=request.form.get('workers_involved'),
            labor_cost=request.form.get('labor_cost'),
            harvested_by=current_user.id,
            storage_location=request.form.get('storage_location'),
            notes=request.form.get('notes')
        )
        db.session.add(harvest)
        planting.actual_harvest_date = datetime.strptime(request.form.get('harvest_date'), '%Y-%m-%d')
        planting.actual_yield_kg = request.form.get('quantity_kg')
        
        if request.form.get('final_harvest') == 'on':
            planting.status = 'Harvested'
            field = FarmField.query.get(planting.field_id)
            if field:
                field.current_status = 'Fallow'
        
        db.session.commit()
        flash('Harvest recorded!', 'success')
        return redirect(url_for('planting_detail', planting_id=planting_id))
    
    return render_template('crops/add_harvest.html', planting=planting)

@app.route('/crops/fields')
@login_required
def field_list():
    fields = FarmField.query.all()
    return render_template('crops/fields.html', fields=fields)

@app.route('/crops/fields/add', methods=['GET', 'POST'])
@login_required
def add_field():
    if request.method == 'POST':
        field = FarmField(
            field_name=request.form.get('field_name'),
            field_code=request.form.get('field_code'),
            size_acres=request.form.get('size_acres'),
            soil_type=request.form.get('soil_type'),
            ph_level=request.form.get('ph_level'),
            drainage=request.form.get('drainage'),
            location_description=request.form.get('location_description'),
            latitude=request.form.get('latitude'),
            longitude=request.form.get('longitude'),
            description=request.form.get('description')
        )
        db.session.add(field)
        db.session.commit()
        flash('Field added!', 'success')
        return redirect(url_for('field_list'))
    
    return render_template('crops/add_field.html')

# ============================================================================
# FINANCE MANAGEMENT ROUTES
# ============================================================================

@app.route('/finance')
@login_required
def finance_dashboard():
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    monthly_income = db.session.query(func.sum(FarmIncome.total_amount)).filter(
        extract('month', FarmIncome.income_date) == current_month,
        extract('year', FarmIncome.income_date) == current_year
    ).scalar() or 0
    
    monthly_expenses = db.session.query(func.sum(FarmExpense.total_amount)).filter(
        extract('month', FarmExpense.expense_date) == current_month,
        extract('year', FarmExpense.expense_date) == current_year
    ).scalar() or 0
    
    return render_template('finance/dashboard.html',
                         monthly_income=monthly_income,
                         monthly_expenses=monthly_expenses)

@app.route('/finance/income')
@login_required
def income_list():
    incomes = FarmIncome.query.order_by(FarmIncome.income_date.desc()).limit(50).all()
    total_income = db.session.query(func.sum(FarmIncome.total_amount)).scalar() or 0
    return render_template('finance/income.html', incomes=incomes, total_income=total_income)

@app.route('/finance/expenses')
@login_required
def expense_list():
    expenses = FarmExpense.query.order_by(FarmExpense.expense_date.desc()).limit(50).all()
    total_expenses = db.session.query(func.sum(FarmExpense.total_amount)).scalar() or 0
    return render_template('finance/expenses.html', expenses=expenses, total_expenses=total_expenses)

@app.route('/finance/payroll')
@login_required
def payroll_list():
    payrolls = Payroll.query.order_by(Payroll.created_at.desc()).limit(50).all()
    return render_template('finance/payroll.html', payrolls=payrolls)

@app.route('/finance/income/add', methods=['POST'])
@login_required
def add_income():
    if request.method == 'POST':
        income = FarmIncome(
            income_date=datetime.strptime(request.form.get('income_date'), '%Y-%m-%d'),
            income_source=request.form.get('income_source'),
            income_category=request.form.get('income_category'),
            description=request.form.get('description'),
            quantity=request.form.get('quantity'),
            unit_price=request.form.get('unit_price'),
            total_amount=request.form.get('total_amount'),
            buyer_name=request.form.get('buyer_name'),
            buyer_contact=request.form.get('buyer_contact'),
            payment_method=request.form.get('payment_method'),
            payment_status=request.form.get('payment_status', 'Pending'),
            receipt_number=generate_reference_number('INC'),
            recorded_by=current_user.id
        )
        db.session.add(income)
        db.session.commit()
        flash('Income recorded!', 'success')
        return redirect(url_for('income_list'))

@app.route('/finance/expenses/add', methods=['POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        expense = FarmExpense(
            expense_date=datetime.strptime(request.form.get('expense_date'), '%Y-%m-%d'),
            expense_category=request.form.get('expense_category'),
            description=request.form.get('description'),
            quantity=request.form.get('quantity'),
            unit_price=request.form.get('unit_price'),
            total_amount=request.form.get('total_amount'),
            supplier_name=request.form.get('supplier_name'),
            supplier_contact=request.form.get('supplier_contact'),
            payment_method=request.form.get('payment_method'),
            payment_status=request.form.get('payment_status', 'Pending'),
            recorded_by=current_user.id
        )
        db.session.add(expense)
        db.session.commit()
        flash('Expense recorded!', 'success')
        return redirect(url_for('expense_list'))

# ============================================================================
# TASK MANAGEMENT ROUTES
# ============================================================================

@app.route('/tasks')
@login_required
def task_list():
    status_filter = request.args.get('status')
    priority_filter = request.args.get('priority')
    category_filter = request.args.get('category')
    assigned_filter = request.args.get('assigned_to')
    
    query = Task.query
    
    if status_filter:
        query = query.filter(Task.status == status_filter)
    if priority_filter:
        query = query.filter(Task.priority == priority_filter)
    if category_filter:
        query = query.filter(Task.task_category == category_filter)
    if assigned_filter:
        query = query.filter(Task.assigned_to == assigned_filter)
    
    tasks = query.order_by(Task.due_date.asc()).all()
    users = User.query.filter_by(is_active=True).all()
    
    return render_template('tasks/list.html', tasks=tasks, users=users)

@app.route('/tasks/add', methods=['GET', 'POST'])
@login_required
def add_task():
    if request.method == 'POST':
        task = Task(
            task_title=request.form.get('task_title'),
            task_description=request.form.get('task_description'),
            task_category=request.form.get('task_category', 'General'),
            task_type=request.form.get('task_type', 'Routine'),
            priority=request.form.get('priority', 'Medium'),
            assigned_to=request.form.get('assigned_to') if request.form.get('assigned_to') else None,
            assigned_by=current_user.id,
            reported_by=current_user.id,
            due_date=datetime.strptime(request.form.get('due_date'), '%Y-%m-%d') if request.form.get('due_date') else None,
            estimated_hours=request.form.get('estimated_hours'),
            location=request.form.get('location'),
            tools_required=request.form.get('tools_required')
        )
        db.session.add(task)
        db.session.commit()
        flash('Task created!', 'success')
        return redirect(url_for('task_list'))
    
    users = User.query.filter_by(is_active=True).all()
    livestock = Livestock.query.filter_by(is_active=True).all()
    plantings = CropPlanting.query.filter_by(status='Active').all()
    fields = FarmField.query.all()
    
    return render_template('tasks/add.html',
                         users=users,
                         livestock=livestock,
                         plantings=plantings,
                         fields=fields)

@app.route('/tasks/<int:task_id>/complete', methods=['POST'])
@login_required
def complete_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = 'Completed'
    task.completed_by = current_user.id
    task.completion_date = datetime.utcnow()
    db.session.commit()
    flash('Task marked as complete!', 'success')
    return redirect(url_for('task_list'))

# ============================================================================
# INVENTORY MANAGEMENT ROUTES
# ============================================================================

@app.route('/inventory')
@login_required
def inventory_list():
    category = request.args.get('category')
    query = InventoryItem.query
    if category:
        query = query.filter_by(item_category=category)
    items = query.order_by(InventoryItem.item_name).all()
    low_stock = InventoryItem.query.filter(InventoryItem.quantity_in_stock <= InventoryItem.reorder_level).all()
    return render_template('inventory/list.html', items=items, low_stock=low_stock)

@app.route('/inventory/add', methods=['GET', 'POST'])
@login_required
def add_inventory_item():
    if request.method == 'POST':
        item = InventoryItem(
            item_code=request.form.get('item_code') or generate_reference_number('INV'),
            item_name=request.form.get('item_name'),
            item_category=request.form.get('item_category'),
            description=request.form.get('description'),
            unit_of_measure=request.form.get('unit_of_measure'),
            quantity_in_stock=request.form.get('quantity_in_stock', 0),
            reorder_level=request.form.get('reorder_level'),
            unit_price=request.form.get('unit_price'),
            supplier_name=request.form.get('supplier_name'),
            storage_location=request.form.get('storage_location'),
            expiry_date=datetime.strptime(request.form.get('expiry_date'), '%Y-%m-%d') if request.form.get('expiry_date') else None,
            notes=request.form.get('notes')
        )
        if item.quantity_in_stock and item.unit_price:
            item.total_value = float(item.quantity_in_stock) * float(item.unit_price)
        db.session.add(item)
        db.session.commit()
        flash('Inventory item added!', 'success')
        return redirect(url_for('inventory_list'))
    return render_template('inventory/add.html')

@app.route('/inventory/<int:item_id>/movement', methods=['POST'])
@login_required
def add_stock_movement(item_id):
    item = InventoryItem.query.get_or_404(item_id)
    movement_type = request.form.get('movement_type')
    quantity = float(request.form.get('quantity'))
    
    movement = StockMovement(
        item_id=item_id,
        movement_type=movement_type,
        quantity=quantity,
        reference_number=generate_reference_number('STK'),
        performed_by=current_user.id,
        reason=request.form.get('reason'),
        cost=request.form.get('cost')
    )
    
    if movement_type == 'In':
        item.quantity_in_stock = float(item.quantity_in_stock or 0) + quantity
    elif movement_type == 'Out':
        item.quantity_in_stock = float(item.quantity_in_stock or 0) - quantity
    elif movement_type == 'Adjustment':
        item.quantity_in_stock = quantity
    
    if item.unit_price:
        item.total_value = float(item.quantity_in_stock) * float(item.unit_price)
    
    db.session.add(movement)
    db.session.commit()
    flash('Stock movement recorded!', 'success')
    return redirect(url_for('inventory_list'))

# ============================================================================
# ATTENDANCE MANAGEMENT ROUTES
# ============================================================================

@app.route('/attendance')
@login_required
def attendance_list():
    user_id = request.args.get('user_id', current_user.id, type=int)
    month = request.args.get('month', datetime.now().month, type=int)
    year = request.args.get('year', datetime.now().year, type=int)
    
    attendances = Attendance.query.filter(
        Attendance.user_id == user_id,
        extract('month', Attendance.attendance_date) == month,
        extract('year', Attendance.attendance_date) == year
    ).order_by(Attendance.attendance_date.desc()).all()
    
    users = User.query.filter_by(is_active=True).all()
    
    return render_template('attendance/list.html',
                         attendances=attendances,
                         users=users,
                         selected_user=user_id,
                         month=month,
                         year=year)

@app.route('/attendance/check-in', methods=['POST'])
@login_required
def check_in():
    today = date.today()
    existing = Attendance.query.filter_by(user_id=current_user.id, attendance_date=today).first()
    
    if existing:
        flash('You have already checked in today.', 'warning')
        return redirect(url_for('attendance_list'))
    
    attendance = Attendance(
        user_id=current_user.id,
        attendance_date=today,
        time_in=datetime.utcnow(),
        status='Present',
        check_in_method='Manual',
        check_in_location=request.form.get('location', 'Farm'),
        notes=request.form.get('notes')
    )
    db.session.add(attendance)
    db.session.commit()
    flash('Check-in successful!', 'success')
    return redirect(url_for('attendance_list'))

@app.route('/attendance/check-out', methods=['POST'])
@login_required
def check_out():
    today = date.today()
    attendance = Attendance.query.filter_by(user_id=current_user.id, attendance_date=today).first()
    
    if not attendance:
        flash('You need to check in first.', 'danger')
        return redirect(url_for('attendance_list'))
    
    if attendance.time_out:
        flash('You have already checked out today.', 'warning')
        return redirect(url_for('attendance_list'))
    
    attendance.time_out = datetime.utcnow()
    if attendance.time_in:
        delta = attendance.time_out - attendance.time_in
        attendance.hours_worked = round(delta.total_seconds() / 3600, 2)
    
    db.session.commit()
    flash('Check-out successful!', 'success')
    return redirect(url_for('attendance_list'))

@app.route('/attendance/report')
@login_required
def attendance_report():
    month = request.args.get('month', datetime.now().month, type=int)
    year = request.args.get('year', datetime.now().year, type=int)
    
    report = db.session.query(
        User.id,
        User.first_name,
        User.last_name,
        User.department,
        func.count(case((Attendance.status == 'Present', 1), else_=None)).label('present_days'),
        func.count(case((Attendance.status == 'Absent', 1), else_=None)).label('absent_days'),
        func.count(case((Attendance.status == 'Late', 1), else_=None)).label('late_days'),
        func.sum(Attendance.hours_worked).label('total_hours')
    ).outerjoin(Attendance, and_(
        Attendance.user_id == User.id,
        extract('month', Attendance.attendance_date) == month,
        extract('year', Attendance.attendance_date) == year
    )).group_by(User.id).all()
    
    return render_template('attendance/report.html', 
                         report=report, 
                         month=month, 
                         year=year)

# ============================================================================
# LEAVE MANAGEMENT ROUTES
# ============================================================================

@app.route('/leaves')
@login_required
def leave_list():
    if current_user.role in ['admin', 'manager']:
        leaves = Leave.query.order_by(Leave.start_date.desc()).all()
    else:
        leaves = Leave.query.filter_by(user_id=current_user.id).order_by(Leave.start_date.desc()).all()
    return render_template('leaves/list.html', leaves=leaves)

@app.route('/leaves/apply', methods=['POST'])
@login_required
def apply_leave():
    if request.method == 'POST':
        start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d')
        days = (end_date - start_date).days + 1
        
        leave = Leave(
            user_id=current_user.id,
            leave_type=request.form.get('leave_type'),
            start_date=start_date,
            end_date=end_date,
            days_requested=days,
            reason=request.form.get('reason')
        )
        db.session.add(leave)
        db.session.commit()
        flash('Leave application submitted!', 'success')
        return redirect(url_for('leave_list'))

@app.route('/leaves/<int:leave_id>/approve', methods=['POST'])
@login_required
def approve_leave(leave_id):
    leave = Leave.query.get_or_404(leave_id)
    leave.status = 'Approved'
    leave.approved_by = current_user.id
    leave.approved_date = date.today()
    db.session.commit()
    flash('Leave approved!', 'success')
    return redirect(url_for('leave_list'))

@app.route('/leaves/<int:leave_id>/reject', methods=['POST'])
@login_required
def reject_leave(leave_id):
    leave = Leave.query.get_or_404(leave_id)
    leave.status = 'Rejected'
    leave.approved_by = current_user.id
    leave.rejection_reason = request.form.get('reason', 'Rejected by admin')
    db.session.commit()
    flash('Leave rejected!', 'warning')
    return redirect(url_for('leave_list'))

# ============================================================================
# FARM LOG ROUTES
# ============================================================================

@app.route('/farm-log')
@login_required
def farm_log():
    log_date = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    log_date = datetime.strptime(log_date, '%Y-%m-%d').date()
    daily_log = DailyFarmLog.query.filter_by(log_date=log_date).first()
    return render_template('farm_log.html', daily_log=daily_log, log_date=log_date)

@app.route('/farm-log/save', methods=['POST'])
@login_required
def save_farm_log():
    log_date = datetime.strptime(request.form.get('log_date'), '%Y-%m-%d').date()
    daily_log = DailyFarmLog.query.filter_by(log_date=log_date).first()
    
    if not daily_log:
        daily_log = DailyFarmLog(log_date=log_date)
    
    daily_log.weather_conditions = request.form.get('weather_conditions')
    daily_log.temperature_min = request.form.get('temperature_min')
    daily_log.temperature_max = request.form.get('temperature_max')
    daily_log.rainfall_mm = request.form.get('rainfall_mm')
    daily_log.activities_summary = request.form.get('activities_summary')
    daily_log.issues_identified = request.form.get('issues_identified')
    daily_log.recommendations = request.form.get('recommendations')
    daily_log.recorded_by = current_user.id
    
    if not daily_log.id:
        db.session.add(daily_log)
    
    db.session.commit()
    flash('Farm log saved!', 'success')
    return redirect(url_for('farm_log', date=log_date.strftime('%Y-%m-%d')))

# ============================================================================
# SYSTEM SETTINGS ROUTES
# ============================================================================

@app.route('/settings')
@login_required
def system_settings():
    settings = SystemSetting.query.all()
    return render_template('settings/index.html', settings=settings)

@app.route('/settings/save', methods=['POST'])
@login_required
def save_settings():
    for key, value in request.form.items():
        setting = SystemSetting.query.filter_by(setting_key=key).first()
        if setting:
            setting.setting_value = value
            setting.updated_at = datetime.utcnow()
    db.session.commit()
    flash('Settings saved successfully!', 'success')
    return redirect(url_for('system_settings'))

# ============================================================================
# REPORTS ROUTES
# ============================================================================

@app.route('/reports')
@login_required
def reports():
    return render_template('reports/index.html')

@app.route('/reports/livestock')
@login_required
def livestock_report():
    report_type = request.args.get('type', 'inventory')
    
    if report_type == 'inventory':
        livestock = Livestock.query.filter_by(is_active=True).order_by(Livestock.category_id, Livestock.tag_number).all()
        category_summary = db.session.query(
            LivestockCategory.name,
            func.count(Livestock.id),
            func.sum(Livestock.estimated_value)
        ).join(Livestock).filter(Livestock.is_active==True).group_by(LivestockCategory.name).all()
        return render_template('reports/livestock_inventory.html', livestock=livestock, category_summary=category_summary)
    elif report_type == 'health':
        health_summary = db.session.query(Livestock.health_status, func.count(Livestock.id)).filter(Livestock.is_active==True).group_by(Livestock.health_status).all()
        recent_treatments = LivestockHealth.query.order_by(LivestockHealth.performed_date.desc()).limit(50).all()
        return render_template('reports/livestock_health.html', health_summary=health_summary, recent_treatments=recent_treatments)
    elif report_type == 'production':
        milk_data = []
        for i in range(11, -1, -1):
            month_date = date.today().replace(day=1) - timedelta(days=i*30)
            total = db.session.query(func.sum(MilkRecord.quantity_litres)).filter(
                extract('month', MilkRecord.milking_date) == month_date.month,
                extract('year', MilkRecord.milking_date) == month_date.year
            ).scalar() or 0
            milk_data.append({'month': month_date.strftime('%b %Y'), 'production': float(total)})
        return render_template('reports/livestock_production.html', milk_data=milk_data)
    
    return render_template('reports/index.html')

@app.route('/reports/crops')
@login_required
def crop_report():
    report_type = request.args.get('type', 'planting')
    
    if report_type == 'planting':
        plantings = CropPlanting.query.order_by(CropPlanting.planting_date.desc()).all()
        return render_template('reports/crop_plantings.html', plantings=plantings)
    elif report_type == 'harvest':
        harvests = Harvest.query.order_by(Harvest.harvest_date.desc()).all()
        total_harvested = db.session.query(func.sum(Harvest.quantity_kg)).scalar() or 0
        harvest_by_crop = db.session.query(Crop.name, func.sum(Harvest.quantity_kg)).join(CropPlanting).join(Crop).group_by(Crop.name).all()
        return render_template('reports/crop_harvests.html', harvests=harvests, total_harvested=total_harvested, harvest_by_crop=harvest_by_crop)
    
    return render_template('reports/index.html')

@app.route('/reports/financial')
@login_required
def financial_report():
    report_type = request.args.get('type', 'monthly')
    year = request.args.get('year', datetime.now().year, type=int)
    
    if report_type == 'monthly':
        monthly_data = []
        for month in range(1, 13):
            income = db.session.query(func.sum(FarmIncome.total_amount)).filter(
                extract('month', FarmIncome.income_date) == month,
                extract('year', FarmIncome.income_date) == year
            ).scalar() or 0
            expenses = db.session.query(func.sum(FarmExpense.total_amount)).filter(
                extract('month', FarmExpense.expense_date) == month,
                extract('year', FarmExpense.expense_date) == year
            ).scalar() or 0
            monthly_data.append({
                'month': calendar.month_name[month],
                'income': float(income),
                'expenses': float(expenses),
                'profit': float(income) - float(expenses)
            })
        total_income = sum(m['income'] for m in monthly_data)
        total_expenses = sum(m['expenses'] for m in monthly_data)
        total_profit = total_income - total_expenses
        return render_template('reports/financial_monthly.html', monthly_data=monthly_data, year=year, total_income=total_income, total_expenses=total_expenses, total_profit=total_profit)
    elif report_type == 'expense_breakdown':
        expenses = db.session.query(FarmExpense.expense_category, func.sum(FarmExpense.total_amount).label('total')).filter(extract('year', FarmExpense.expense_date) == year).group_by(FarmExpense.expense_category).order_by(func.sum(FarmExpense.total_amount).desc()).all()
        return render_template('reports/expense_breakdown.html', expenses=expenses, year=year)
    
    return render_template('reports/index.html')

@app.route('/reports/generate-farm-report')
@login_required
def generate_farm_report():
    return render_template('reports/generate.html')

@app.route('/reports/generate-payslip/<int:payroll_id>')
@login_required
def generate_payslip(payroll_id):
    payroll = Payroll.query.get_or_404(payroll_id)
    return render_template('reports/payslip.html', payroll=payroll)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def calculate_attendance_rate(user_id):
    today = date.today()
    first_of_month = today.replace(day=1)
    
    total_working_days = 0
    current_date = first_of_month
    while current_date <= today:
        if current_date.weekday() < 6:
            total_working_days += 1
        current_date += timedelta(days=1)
    
    present_days = Attendance.query.filter(
        Attendance.user_id == user_id,
        Attendance.attendance_date >= first_of_month,
        Attendance.status.in_(['Present', 'Late'])
    ).count()
    
    if total_working_days > 0:
        return round((present_days / total_working_days) * 100, 1)
    return 0

def calculate_weight_gain(livestock_id):
    weights = WeightRecord.query.filter_by(animal_id=livestock_id).order_by(WeightRecord.weigh_date.asc()).all()
    if len(weights) < 2:
        return 0
    first_weight = float(weights[0].weight_kg)
    last_weight = float(weights[-1].weight_kg)
    days_between = (weights[-1].weigh_date - weights[0].weigh_date).days
    if days_between > 0:
        return round((last_weight - first_weight) / days_between, 3)
    return 0

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ============================================================================
# MAIN APPLICATION ENTRY POINT
# ============================================================================



@app.route('/livestock/<int:livestock_id>/production/add', methods=['GET', 'POST'])
@login_required
def add_production_record(livestock_id):
    animal = Livestock.query.get_or_404(livestock_id)
    
    if request.method == 'POST':
        image_file = request.files.get('image')
        image_url = None
        if image_file and allowed_image(image_file.filename):
            image_url = upload_to_cloudinary(image_file, 'production_photos')
        
        production = LivestockProduction(
            animal_id=livestock_id,
            product_type=request.form.get('product_type'),
            quantity=request.form.get('quantity'),
            unit=request.form.get('unit'),
            production_time=request.form.get('production_time'),
            production_date=datetime.strptime(request.form.get('production_date'), '%Y-%m-%d'),
            quality_grade=request.form.get('quality_grade'),
            value_ksh=request.form.get('value_ksh'),
            buyer=request.form.get('buyer'),
            recorded_by=current_user.id,
            notes=request.form.get('notes'),
            photo_url=image_url
        )
        db.session.add(production)
        db.session.commit()
        
        daily_summary = DailyProductionSummary(
            animal_id=livestock_id,
            production_date=datetime.strptime(request.form.get('production_date'), '%Y-%m-%d'),
            product_type=request.form.get('product_type'),
            quantity=request.form.get('quantity'),
            unit=request.form.get('unit'),
            value_ksh=request.form.get('value_ksh'),
            image_url=image_url,
            notes=request.form.get('notes'),
            recorded_by=current_user.id
        )
        db.session.add(daily_summary)
        db.session.commit()
        
        flash('Production record added!', 'success')
        return redirect(url_for('livestock_detail', livestock_id=livestock_id))
    
    return render_template('livestock/add_production.html', animal=animal)

@app.route('/livestock/<int:livestock_id>/production/chart')
@login_required
def production_chart(livestock_id):
    animal = Livestock.query.get_or_404(livestock_id)
    productions = LivestockProduction.query.filter_by(animal_id=livestock_id).order_by(LivestockProduction.production_date.desc()).all()
    return render_template('livestock/production_chart.html', animal=animal, productions=productions)




@app.route('/finance/payroll/<int:payroll_id>/mark-paid', methods=['POST'])
@login_required
def mark_payroll_paid(payroll_id):
    payroll = Payroll.query.get_or_404(payroll_id)
    
    if payroll.payment_status == 'Paid':
        flash('This payroll has already been marked as paid.', 'warning')
        return redirect(url_for('payroll_list'))
    
    payroll.payment_status = 'Paid'
    payroll.payment_date = date.today()
    payroll.payment_method = request.form.get('payment_method', 'Cash')
    payroll.payment_reference = request.form.get('payment_reference', '')
    payroll.paid_by = current_user.id
    
    db.session.commit()
    
    notification = Notification(
        user_id=payroll.user_id,
        title='Payroll Payment Received',
        message=f'Your payroll for {payroll.payroll_period} has been paid. Amount: Ksh {payroll.net_pay}',
        notification_type='success',
        link=url_for('payroll_list')
    )
    db.session.add(notification)
    db.session.commit()
    
    flash(f'Payroll for {payroll.employee.full_name} marked as paid!', 'success')
    return redirect(url_for('payroll_list'))

@app.route('/finance/payroll/employee/<int:user_id>')
@login_required
def employee_payroll_history(user_id):
    employee = User.query.get_or_404(user_id)
    payrolls = Payroll.query.filter_by(user_id=user_id).order_by(Payroll.payroll_period.desc()).all()
    
    total_earned = db.session.query(func.sum(Payroll.net_pay)).filter_by(user_id=user_id).scalar() or 0
    total_paid = db.session.query(func.sum(Payroll.net_pay)).filter_by(user_id=user_id, payment_status='Paid').scalar() or 0
    
    return render_template('finance/employee_payroll_history.html',
                         employee=employee,
                         payrolls=payrolls,
                         total_earned=total_earned,
                         total_paid=total_paid)




@app.route('/livestock/<int:livestock_id>/health/add', methods=['GET', 'POST'])
@login_required
def add_health_record(livestock_id):
    animal = Livestock.query.get_or_404(livestock_id)
    if request.method == 'POST':
        health = LivestockHealth(
            animal_id=livestock_id,
            record_type=request.form.get('record_type'),
            diagnosis=request.form.get('diagnosis'),
            treatment=request.form.get('treatment'),
            medication_used=request.form.get('medication_used'),
            dosage=request.form.get('dosage'),
            withdrawal_period_days=request.form.get('withdrawal_period_days'),
            veterinary_officer=request.form.get('veterinary_officer'),
            cost=request.form.get('cost'),
            next_action_date=datetime.strptime(request.form.get('next_action_date'), '%Y-%m-%d') if request.form.get('next_action_date') else None,
            next_action=request.form.get('next_action'),
            performed_by=current_user.id,
            notes=request.form.get('notes')
        )
        # Handle image upload if provided
        photo = request.files.get('photo')
        if photo and allowed_image(photo.filename):
            health.photo_url = upload_to_cloudinary(photo, 'health_photos')
        db.session.add(health)
        db.session.commit()
        flash('Health record added!', 'success')
        return redirect(url_for('livestock_detail', livestock_id=livestock_id))
    return render_template('livestock/add_health.html', animal=animal)

@app.route('/livestock/<int:livestock_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_livestock(livestock_id):
    animal = Livestock.query.get_or_404(livestock_id)
    if request.method == 'POST':
        animal.tag_number = request.form.get('tag_number')
        animal.name = request.form.get('name')
        animal.category_id = request.form.get('category_id')
        animal.breed = request.form.get('breed')
        animal.sex = request.form.get('sex')
        animal.date_of_birth = datetime.strptime(request.form.get('date_of_birth'), '%Y-%m-%d') if request.form.get('date_of_birth') else None
        animal.acquisition_date = datetime.strptime(request.form.get('acquisition_date'), '%Y-%m-%d') if request.form.get('acquisition_date') else None
        animal.acquisition_type = request.form.get('acquisition_type')
        animal.acquisition_cost = request.form.get('acquisition_cost')
        animal.current_weight = request.form.get('current_weight')
        animal.color = request.form.get('color')
        animal.markings = request.form.get('markings')
        animal.dam_tag = request.form.get('dam_tag')
        animal.sire_tag = request.form.get('sire_tag')
        animal.location = request.form.get('location')
        animal.shed_number = request.form.get('shed_number')
        animal.notes = request.form.get('notes')
        animal.estimated_value = request.form.get('estimated_value')
        # Handle image update
        image_file = request.files.get('image')
        if image_file and allowed_image(image_file.filename):
            animal.image_url = upload_to_cloudinary(image_file, 'livestock_photos')
        db.session.commit()
        flash('Livestock updated!', 'success')
        return redirect(url_for('livestock_detail', livestock_id=livestock_id))
    categories = LivestockCategory.query.all()
    return render_template('livestock/edit.html', animal=animal, categories=categories)

@app.route('/finance/payroll/generate', methods=['POST'])
@login_required
@admin_required
def generate_payroll():
    period = request.form.get('period')  # format YYYY-MM
    if not period:
        flash('Please select a period.', 'danger')
        return redirect(url_for('payroll_list'))
    # Split period into year and month
    year, month = map(int, period.split('-'))
    # Get all active employees with basic salary > 0
    employees = User.query.filter(User.is_active == True, User.basic_salary > 0).all()
    count = 0
    for emp in employees:
        # Check if payroll already exists for this period
        existing = Payroll.query.filter_by(user_id=emp.id, payroll_period=period).first()
        if existing:
            continue
        gross = emp.basic_salary or 0
        nssf = calculate_nssf(gross, 'I')
        nhif = calculate_nhif(gross)
        paye = calculate_kra_paye(gross)
        total_ded = nssf + nhif + paye
        net = gross - total_ded
        payroll = Payroll(
            payroll_period=period,
            user_id=emp.id,
            basic_salary=gross,
            gross_pay=gross,
            nssf_deduction=nssf,
            nhif_deduction=nhif,
            paye_tax=paye,
            total_deductions=total_ded,
            net_pay=net,
            generated_by=current_user.id,
            payment_status='Pending'
        )
        db.session.add(payroll)
        count += 1
    db.session.commit()
    flash(f'Generated {count} payroll records for {period}.', 'success')
    return redirect(url_for('payroll_list'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Create default admin user
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(
                employee_id='EMP20240001',
                username='admin',
                email='admin@farmmanager.com',
                first_name='Farm',
                last_name='Administrator',
                role='admin',
                department='Management',
                employment_type='Permanent',
                employment_date=date.today(),
                is_active=True,
                email_verified=True,
                basic_salary=50000
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            
            print("=" * 60)
            print("FARM MANAGEMENT SYSTEM INITIALIZED")
            print("=" * 60)
            print("Default admin login:")
            print("  Username: admin")
            print("  Password: admin123")
            print("=" * 60)
            print("IMPORTANT: Change the default password immediately!")
            print("=" * 60)
    
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
