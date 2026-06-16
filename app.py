import os
import logging
from dotenv import load_dotenv

# Load .env file into environment variables
load_dotenv()

import bcrypt
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, session, g
from werkzeug.middleware.proxy_fix import ProxyFix
from database import db, init_database
import io
import re

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max file size
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"

# Initialize database
init_database(app)

# Import models and recommender after db initialization
from models import College, CAPCutoff, MHCETStudent, User, UploadedFile, BackupHistory, AuditLog, ImportJob, CollegeTrend
from recommender import CollegeRecommender
from mhcet_recommender import MHCETRecommender
from auth_decorators import login_required, api_login_required
from email_service import send_verification_email as resend_verify, send_password_reset_email
from admin import admin_bp

# Register admin blueprint
app.register_blueprint(admin_bp)

# ── Auth helpers ───────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password: str, password_hash: str) -> bool:
    """Check a password against its hash."""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

def is_valid_email(email: str) -> bool:
    """Basic email validation."""
    return bool(re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email))

def send_verification_email(user: User):
    """Send a verification code email via Resend."""
    code = user.generate_verification_code()
    db.session.commit()
    success = resend_verify(user.email, code)
    if success:
        logging.info(f"✅ Verification email sent via Resend to {user.email}")
    else:
        logging.warning(f"⚠️  Failed to send verification email to {user.email}, code still available: {code}")
    return code


# ── Auth middleware ────────────────────────────────────────────────────────────
@app.before_request
def load_current_user():
    """Load user from session into g.user."""
    g.user = None
    user_id = session.get('user_id')
    if user_id:
        try:
            user = db.session.get(User, user_id)
            g.user = user
        except Exception:
            session.pop('user_id', None)


@app.context_processor
def inject_user():
    """Make current user available in every template."""
    return {
        'current_user': g.get('user'),
    }

# Initialize recommendation engines
recommender = CollegeRecommender(db)
mhcet_recommender = MHCETRecommender(db)

def init_sample_cutoff_data():
    """Initialize sample CAP cutoff data for testing"""
    if CAPCutoff.query.count() == 0:
        # Get Maharashtra colleges for cutoff data across different tiers
        maharashtra_colleges = College.query.filter(
            College.location.in_(['Mumbai', 'Pune', 'Nagpur'])
        ).limit(30).all()
        
        if maharashtra_colleges:
            categories = ['Open', 'OBC', 'SC', 'ST', 'EWS']
            genders = ['Male', 'Female']
            years = [2022, 2023, 2024]
            
            for college in maharashtra_colleges:
                # More realistic and diverse cutoff based on college ranking
                if 'IIT' in college.college and college.nirf_rank <= 5:
                    base_cutoff = 99.0
                elif 'IIT' in college.college or college.nirf_rank <= 10:
                    base_cutoff = 96.0
                elif 'NIT' in college.college or college.nirf_rank <= 25:
                    base_cutoff = 92.0
                elif college.nirf_rank <= 50:
                    base_cutoff = 85.0
                elif college.nirf_rank <= 100:
                    base_cutoff = 78.0
                elif college.nirf_rank <= 200:
                    base_cutoff = 68.0
                elif college.nirf_rank <= 300:
                    base_cutoff = 58.0
                else:
                    base_cutoff = 45.0
                
                for year in years:
                    for category in categories:
                        # Category adjustments
                        if category == 'Open':
                            cat_adj = 0
                        elif category == 'OBC':
                            cat_adj = -8
                        elif category == 'SC':
                            cat_adj = -20
                        elif category == 'ST':
                            cat_adj = -25
                        else:  # EWS
                            cat_adj = -5
                        
                        for gender in genders:
                            cutoff = max(base_cutoff + cat_adj, 30.0)
                            
                            cutoff_data = CAPCutoff(
                                college_id=college.id,
                                year=year,
                                round_number=1,
                                category=category,
                                gender=gender,
                                cutoff_percentile=round(cutoff, 2),
                                opening_rank=1000,
                                closing_rank=2000,
                                seats_available=60
                            )
                            db.session.add(cutoff_data)
            
            try:
                db.session.commit()
                logging.info("Sample cutoff data initialized successfully")
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error initializing cutoff data: {e}")

def init_sample_data():
    """Initialize database with sample college data"""
    if College.query.count() == 0:
        sample_colleges = [
            College(
                college="Indian Institute of Technology Delhi",
                location="Delhi",
                branch="Computer Science",
                fees=200000,
                placement_rate=95.5,
                nirf_rank=2,
                rating=4.8
            ),
            College(
                college="Indian Institute of Technology Bombay",
                location="Mumbai",
                branch="Computer Science",
                fees=220000,
                placement_rate=97.2,
                nirf_rank=1,
                rating=4.9
            ),
            College(
                college="Indian Institute of Science",
                location="Bangalore",
                branch="Research",
                fees=50000,
                placement_rate=98.0,
                nirf_rank=3,
                rating=4.7
            ),
            College(
                college="Delhi Technological University",
                location="Delhi",
                branch="Electronics",
                fees=150000,
                placement_rate=85.3,
                nirf_rank=45,
                rating=4.2
            ),
            College(
                college="National Institute of Technology Trichy",
                location="Trichy",
                branch="Mechanical",
                fees=180000,
                placement_rate=90.1,
                nirf_rank=15,
                rating=4.5
            ),
            College(
                college="Birla Institute of Technology and Science",
                location="Pilani",
                branch="Computer Science",
                fees=400000,
                placement_rate=92.8,
                nirf_rank=25,
                rating=4.6
            ),
            College(
                college="Vellore Institute of Technology",
                location="Vellore",
                branch="Information Technology",
                fees=180000,
                placement_rate=88.5,
                nirf_rank=35,
                rating=4.3
            ),
            College(
                college="Manipal Institute of Technology",
                location="Manipal",
                branch="Bioengineering",
                fees=250000,
                placement_rate=82.7,
                nirf_rank=55,
                rating=4.1
            ),
            College(
                college="Jadavpur University",
                location="Kolkata",
                branch="Civil Engineering",
                fees=120000,
                placement_rate=87.9,
                nirf_rank=20,
                rating=4.4
            ),
            College(
                college="Anna University",
                location="Chennai",
                branch="Electrical Engineering",
                fees=100000,
                placement_rate=79.3,
                nirf_rank=40,
                rating=4.0
            ),
            College(
                college="Pune Institute of Computer Technology",
                location="Pune",
                branch="Computer Science",
                fees=160000,
                placement_rate=86.2,
                nirf_rank=50,
                rating=4.2
            ),
            College(
                college="SRM Institute of Science and Technology",
                location="Chennai",
                branch="Aerospace Engineering",
                fees=200000,
                placement_rate=84.1,
                nirf_rank=42,
                rating=4.1
            )
        ]
        
        for college in sample_colleges:
            db.session.add(college)
        
        try:
            db.session.commit()
            logging.info("Sample data initialized successfully")
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error initializing sample data: {e}")

@app.route('/')
def index():
    """Home page with recommendation form"""
    # Get unique locations and branches for form options
    locations = db.session.query(College.location).distinct().all()
    branches = db.session.query(College.branch).distinct().all()
    
    locations = [loc[0] for loc in locations]
    branches = [branch[0] for branch in branches]
    
    return render_template('index.html', locations=locations, branches=branches)

@app.route('/recommend', methods=['GET', 'POST'])
def recommend():
    """Get college recommendations"""
    if request.method == 'GET':
        # Handle API request
        budget = request.args.get('budget', type=float)
        location = request.args.get('location', '')
        branch = request.args.get('branch', '')
        top_n = request.args.get('top_n', 5, type=int)
        
        try:
            recommendations = recommender.get_recommendations(
                budget=budget,
                location=location if location else None,
                branch=branch if branch else None,
                top_n=top_n
            )
            
            # Convert to JSON format
            result = []
            for college, score in recommendations:
                result.append({
                    'college': college.college,
                    'location': college.location,
                    'branch': college.branch,
                    'fees': college.fees,
                    'placement_rate': college.placement_rate,
                    'nirf_rank': college.nirf_rank,
                    'rating': college.rating,
                    'similarity_score': round(score * 100, 2)
                })
            
            return jsonify({'recommendations': result})
            
        except Exception as e:
            logging.error(f"Error getting recommendations: {e}")
            return jsonify({'error': str(e)}), 500
    
    else:
        # Handle form submission
        budget = request.form.get('budget', type=float)
        location = request.form.get('location', '')
        branch = request.form.get('branch', '')
        top_n = request.form.get('top_n', 5, type=int)
        
        try:
            recommendations = recommender.get_recommendations(
                budget=budget,
                location=location if location else None,
                branch=branch if branch else None,
                top_n=top_n
            )
            
            # Get form options again
            locations = db.session.query(College.location).distinct().all()
            branches = db.session.query(College.branch).distinct().all()
            
            locations = [loc[0] for loc in locations]
            branches = [branch_opt[0] for branch_opt in branches]
            
            return render_template('index.html', 
                                 recommendations=recommendations,
                                 locations=locations,
                                 branches=branches,
                                 form_data={
                                     'budget': budget,
                                     'location': location,
                                     'branch': branch,
                                     'top_n': top_n
                                 })
            
        except Exception as e:
            logging.error(f"Error getting recommendations: {e}")
            flash(f'Error getting recommendations: {str(e)}', 'error')
            return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload_csv():
    """Upload CSV file to update database"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    if file and file.filename.lower().endswith('.csv'):
        try:
            # Read CSV file
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            df = pd.read_csv(stream)
            
            # Validate required columns
            required_columns = ['College', 'Location', 'Branch', 'Fees', 'PlacementRate', 'NIRFRank', 'Rating']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                flash(f'Missing required columns: {", ".join(missing_columns)}', 'error')
                return redirect(url_for('index'))
            
            # Add colleges to database
            added_count = 0
            for _, row in df.iterrows():
                # Check if college already exists
                existing = College.query.filter_by(
                    college=row['College'],
                    location=row['Location'],
                    branch=row['Branch']
                ).first()
                
                if not existing:
                    college = College(
                        college=row['College'],
                        location=row['Location'],
                        branch=row['Branch'],
                        fees=float(row['Fees']),
                        placement_rate=float(row['PlacementRate']),
                        nirf_rank=int(row['NIRFRank']),
                        rating=float(row['Rating'])
                    )
                    db.session.add(college)
                    added_count += 1
            
            db.session.commit()
            flash(f'Successfully added {added_count} new colleges from CSV', 'success')
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error uploading CSV: {e}")
            flash(f'Error processing CSV file: {str(e)}', 'error')
    
    else:
        flash('Please upload a valid CSV file', 'error')
    
    return redirect(url_for('index'))

@app.route('/colleges')
def list_colleges():
    """API endpoint to list all colleges"""
    try:
        colleges = College.query.all()
        result = []
        for college in colleges:
            result.append({
                'id': college.id,
                'college': college.college,
                'location': college.location,
                'branch': college.branch,
                'fees': college.fees,
                'placement_rate': college.placement_rate,
                'nirf_rank': college.nirf_rank,
                'rating': college.rating
            })
        return jsonify({'colleges': result})
    except Exception as e:
        logging.error(f"Error listing colleges: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/mhcet')
def mhcet_page():
    """MH-CET recommendation page"""
    locations = db.session.query(College.location).distinct().all()
    branches = db.session.query(College.branch).distinct().all()
    
    locations = [loc[0] for loc in locations]
    branches = [branch[0] for branch in branches]
    
    categories = ['Open', 'OBC', 'SC', 'ST', 'NT', 'EWS']
    genders = ['Male', 'Female', 'Other']
    
    return render_template('mhcet.html', 
                         locations=locations, 
                         branches=branches,
                         categories=categories,
                         genders=genders)

@app.route('/mhcet/recommend', methods=['POST'])
def mhcet_recommend():
    """Get MH-CET based college recommendations"""
    try:
        # Get form data
        percentile = request.form.get('percentile', type=float)
        category = request.form.get('category', '')
        gender = request.form.get('gender', '')
        budget = request.form.get('budget', type=float)
        locations = request.form.getlist('locations')
        branches = request.form.getlist('branches')
        top_n = request.form.get('top_n', 10, type=int)
        
        # Validate input
        if not percentile or percentile < 0 or percentile > 100:
            flash('Please enter a valid percentile (0-100)', 'error')
            return redirect(url_for('mhcet_page'))
        
        if not category or not gender:
            flash('Please select category and gender', 'error')
            return redirect(url_for('mhcet_page'))
        
        # Get recommendations
        recommendations = mhcet_recommender.get_mhcet_recommendations(
            percentile=percentile,
            category=category,
            gender=gender,
            budget=budget,
            preferred_locations=locations if locations else None,
            preferred_branches=branches if branches else None,
            top_n=top_n
        )
        
        # Get student analysis
        student_analysis = mhcet_recommender.analyze_student_profile(
            percentile=percentile,
            category=category,
            gender=gender,
            budget=budget
        )
        
        # Get form options again for redisplay
        all_locations = db.session.query(College.location).distinct().all()
        all_branches = db.session.query(College.branch).distinct().all()
        
        all_locations = [loc[0] for loc in all_locations]
        all_branches = [branch[0] for branch in all_branches]
        
        categories = ['Open', 'OBC', 'SC', 'ST', 'NT', 'EWS']
        genders = ['Male', 'Female', 'Other']
        
        return render_template('mhcet.html',
                             recommendations=recommendations,
                             student_analysis=student_analysis,
                             locations=all_locations,
                             branches=all_branches,
                             categories=categories,
                             genders=genders,
                             form_data={
                                 'percentile': percentile,
                                 'category': category,
                                 'gender': gender,
                                 'budget': budget,
                                 'locations': locations,
                                 'branches': branches,
                                 'top_n': top_n
                             })
        
    except Exception as e:
        logging.error(f"Error getting MH-CET recommendations: {e}")
        flash(f'Error getting recommendations: {str(e)}', 'error')
        return redirect(url_for('mhcet_page'))

@app.route('/college/<int:college_id>')
def college_profile(college_id):
    """Detailed college profile page"""
    college = College.query.get_or_404(college_id)
    cutoffs = CAPCutoff.query.filter_by(college_id=college_id).order_by(
        CAPCutoff.year.desc(), CAPCutoff.category, CAPCutoff.gender
    ).all()
    years = sorted(set(c.year for c in cutoffs), reverse=True)
    categories = sorted(set(c.category for c in cutoffs))
    return render_template('college_profile.html',
                           college=college, cutoffs=cutoffs,
                           years=years, categories=categories)


@app.route('/compare')
def compare_page():
    """Side-by-side college comparison page"""
    ids = request.args.getlist('ids', type=int)
    colleges = College.query.filter(College.id.in_(ids)).all() if ids else []
    return render_template('compare.html', colleges=colleges)


@app.route('/bookmarks')
def bookmarks_page():
    """Bookmarked colleges page"""
    ids = request.args.getlist('ids', type=int)
    colleges = College.query.filter(College.id.in_(ids)).all() if ids else []
    return render_template('bookmarks.html', colleges=colleges)


@app.route('/mhcet/recommend', methods=['GET'])
def mhcet_recommend_get():
    """GET handler for shareable MH-CET results links"""
    try:
        percentile = request.args.get('percentile', type=float)
        category   = request.args.get('category', '')
        gender     = request.args.get('gender', '')
        budget     = request.args.get('budget', type=float)
        locations  = request.args.getlist('locations')
        branches   = request.args.getlist('branches')
        top_n      = request.args.get('top_n', 10, type=int)

        recommendations = student_analysis = None
        if percentile and category and gender:
            recommendations = mhcet_recommender.get_mhcet_recommendations(
                percentile=percentile, category=category, gender=gender,
                budget=budget,
                preferred_locations=locations if locations else None,
                preferred_branches=branches if branches else None,
                top_n=top_n
            )
            student_analysis = mhcet_recommender.analyze_student_profile(
                percentile=percentile, category=category, gender=gender, budget=budget
            )

        all_locations = [l[0] for l in db.session.query(College.location).distinct().all()]
        all_branches  = [b[0] for b in db.session.query(College.branch).distinct().all()]

        return render_template('mhcet.html',
                               recommendations=recommendations,
                               student_analysis=student_analysis,
                               locations=all_locations, branches=all_branches,
                               categories=['Open','OBC','SC','ST','NT','EWS'],
                               genders=['Male','Female','Other'],
                               form_data={
                                   'percentile': percentile, 'category': category,
                                   'gender': gender, 'budget': budget,
                                   'locations': locations, 'branches': branches,
                                   'top_n': top_n
                               })
    except Exception as e:
        logging.error(f"Error in MH-CET GET recommend: {e}")
        return redirect(url_for('mhcet_page'))


@app.route('/mhcet/api', methods=['GET'])
def mhcet_api():
    """API endpoint for MH-CET recommendations"""
    try:
        percentile = request.args.get('percentile', type=float)
        category = request.args.get('category', '')
        gender = request.args.get('gender', '')
        budget = request.args.get('budget', type=float)
        top_n = request.args.get('top_n', 10, type=int)
        
        if not percentile or not category or not gender:
            return jsonify({'error': 'Missing required parameters: percentile, category, gender'}), 400
        
        recommendations = mhcet_recommender.get_mhcet_recommendations(
            percentile=percentile,
            category=category,
            gender=gender,
            budget=budget,
            top_n=top_n
        )
        
        # Convert to JSON format
        result = []
        for college, admission_data in recommendations:
            result.append({
                'college': college.college,
                'location': college.location,
                'branch': college.branch,
                'fees': college.fees,
                'placement_rate': college.placement_rate,
                'nirf_rank': college.nirf_rank,
                'rating': college.rating,
                'admission_probability': admission_data['probability'],
                'category_type': admission_data['category_type'],
                'cutoff_info': admission_data
            })
        
        return jsonify({'recommendations': result})
        
    except Exception as e:
        logging.error(f"Error in MH-CET API: {e}")
        return jsonify({'error': str(e)}), 500


# ── Auth Routes (Custom Password-based Auth) ──────────────────────────────────


@app.route('/login')
def login_page():
    """Render the login page."""
    next_url = request.args.get('next', url_for('mhcet_page'))
    if g.user:
        return redirect(next_url)
    return render_template('login.html', next_url=next_url)


@app.route('/signup')
def signup_page():
    """Render the signup page."""
    next_url = request.args.get('next', url_for('mhcet_page'))
    if g.user:
        return redirect(next_url)
    return render_template('signup.html', next_url=next_url)


@app.route('/auth/signup', methods=['POST'])
def auth_signup():
    """Handle signup form submission."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'Invalid request'}), 400

        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()

        # Validation
        if not email or not is_valid_email(email):
            return jsonify({'ok': False, 'error': 'Please enter a valid email address'}), 400
        if not password or len(password) < 8:
            return jsonify({'ok': False, 'error': 'Password must be at least 8 characters'}), 400
        if not first_name:
            return jsonify({'ok': False, 'error': 'Name is required'}), 400

        # Check if user already exists
        existing = User.query.filter_by(email=email).first()
        if existing:
            return jsonify({'ok': False, 'error': 'An account with this email already exists. Please sign in instead.'}), 409

        # Create user
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name or None,
            password_hash=hash_password(password),
            is_verified=False,
            created_at=datetime.utcnow(),
        )
        db.session.add(user)
        db.session.flush()

        # Send verification code
        send_verification_email(user)
        db.session.commit()

        return jsonify({
            'ok': True,
            'user': user.to_dict(),
            'message': 'Account created! Please check your email for the verification code.'
        }), 201

    except Exception as e:
        db.session.rollback()
        logging.error(f"Signup error: {e}")
        return jsonify({'ok': False, 'error': 'Something went wrong. Please try again.'}), 500


@app.route('/auth/verify', methods=['POST'])
def auth_verify():
    """Handle email verification code submission."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'Invalid request'}), 400

        email = data.get('email', '').strip().lower()
        code = data.get('code', '').strip()

        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found. Please sign up again.'}), 404

        if user.is_verified:
            return jsonify({'ok': True, 'message': 'Email already verified.'})

        if not user.verify_code(code):
            return jsonify({'ok': False, 'error': 'Invalid or expired verification code. Please try again.'}), 400

        user.is_verified = True
        user.verification_code = None
        user.verification_code_expiry = None
        user.last_login = datetime.utcnow()
        db.session.commit()

        # Log the user in
        session['user_id'] = user.id
        session.permanent = False

        return jsonify({
            'ok': True,
            'user': user.to_dict(),
            'message': 'Email verified successfully! Welcome to CollegeKhoj.'
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"Verification error: {e}")
        return jsonify({'ok': False, 'error': 'Something went wrong. Please try again.'}), 500


@app.route('/auth/resend-code', methods=['POST'])
def auth_resend_code():
    """Resend verification code."""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower() if data else ''

        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found.'}), 404

        send_verification_email(user)
        db.session.commit()

        return jsonify({'ok': True, 'message': f'New verification code sent to {email}'})

    except Exception as e:
        db.session.rollback()
        logging.error(f"Resend code error: {e}")
        return jsonify({'ok': False, 'error': 'Failed to resend code. Please try again.'}), 500


@app.route('/auth/login', methods=['POST'])
def auth_login():
    """Handle login form submission."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'Invalid request'}), 400

        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({'ok': False, 'error': 'Email and password are required'}), 400

        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({'ok': False, 'error': 'No account found with this email address. Please check your email or create a new account.'}), 401

        if not user.password_hash or not check_password(password, user.password_hash):
            return jsonify({'ok': False, 'error': 'Incorrect password. Please try again or reset your password.'}), 401

        if not user.is_verified:
            # Resend verification code
            send_verification_email(user)
            db.session.commit()
            return jsonify({
                'ok': False,
                'error': 'Please verify your email first.',
                'needs_verification': True,
                'email': user.email,
            }), 403

        # Login successful — store both user_id and role in session
        user.last_login = datetime.utcnow()
        db.session.commit()

        session['user_id'] = user.id
        session['role'] = user.role
        session.permanent = False

        # Role-based redirect + message
        is_admin = user.role == 'admin'
        redirect_url = url_for('admin_bp.admin_dashboard') if is_admin else url_for('mhcet_page')

        return jsonify({
            'ok': True,
            'user': user.to_dict(),
            'redirect_url': redirect_url,
            'message': 'Welcome Admin' if is_admin else 'Welcome Back',
        })

    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({'ok': False, 'error': 'Something went wrong. Please try again.'}), 500


@app.route('/auth/reset-password', methods=['POST'])
def auth_reset_password():
    """Send password reset email."""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower() if data else ''

        user = User.query.filter_by(email=email).first()
        if not user:
            # Don't reveal whether the email exists
            return jsonify({'ok': True, 'message': 'If an account exists with this email, you will receive a password reset link.'})

        token = user.generate_reset_token()
        db.session.commit()

        reset_url = url_for('auth_reset_password_confirm_page', token=token, _external=True)
        success = send_password_reset_email(user.email, reset_url)
        if success:
            logging.info(f"✅ Password reset email sent via Resend to {user.email}")
        else:
            logging.warning(f"⚠️  Failed to send password reset email to {user.email}, link: {reset_url}")

        return jsonify({'ok': True, 'message': 'If an account exists with this email, you will receive a password reset link.'})

    except Exception as e:
        db.session.rollback()
        logging.error(f"Reset password error: {e}")
        return jsonify({'ok': False, 'error': 'Something went wrong. Please try again.'}), 500


@app.route('/auth/reset-password/<token>', methods=['GET'])
def auth_reset_password_confirm_page(token):
    """Show password reset form (validates token)."""
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_token_expiry or datetime.utcnow() > user.reset_token_expiry:
        flash('This reset link has expired or is invalid. Please request a new one.', 'error')
        return redirect(url_for('login_page'))
    return render_template('reset_password.html', token=token, email=user.email)


@app.route('/auth/reset-password/<token>', methods=['POST'])
def auth_reset_password_confirm(token):
    """Handle password reset submission."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'Invalid request'}), 400

        new_password = data.get('password', '')

        if len(new_password) < 8:
            return jsonify({'ok': False, 'error': 'Password must be at least 8 characters'}), 400

        user = User.query.filter_by(reset_token=token).first()
        if not user or not user.reset_token_expiry or datetime.utcnow() > user.reset_token_expiry:
            return jsonify({'ok': False, 'error': 'This reset link has expired. Please request a new one.'}), 400

        user.password_hash = hash_password(new_password)
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()

        # Log the user in
        session['user_id'] = user.id

        return jsonify({'ok': True, 'message': 'Password reset successfully! You are now signed in.'})

    except Exception as e:
        db.session.rollback()
        logging.error(f"Reset password confirm error: {e}")
        return jsonify({'ok': False, 'error': 'Something went wrong. Please try again.'}), 500


@app.route('/logout')
def logout():
    """Log the user out."""
    session.clear()
    flash('You have been signed out.', 'success')
    return redirect(url_for('mhcet_page'))


@app.route('/profile')
@login_required
def profile_page():
    """User profile page."""
    return render_template('profile.html', user=g.user)


@app.route('/settings')
@login_required
def settings_page():
    """Account settings page."""
    return render_template('settings.html', user=g.user)


# ── Initialize database and sample data ───────────────────────────────────────

# Initialize database and sample data
# All production data is stored in Neon PostgreSQL via SQLAlchemy.
# SQLite is used only for local development and testing.
with app.app_context():
    # Create all tables from SQLAlchemy models
    # This handles both PostgreSQL (Neon) and SQLite (dev) automatically.
    db.create_all()
    logging.info("✅ Database tables created/verified on Neon PostgreSQL")

    # ── Add new columns / FKs to existing tables if they don't exist ──
    try:
        inspector = db.inspect(db.engine)
        if 'cap_cutoffs' in inspector.get_table_names():
            cap_columns = [c['name'] for c in inspector.get_columns('cap_cutoffs')]
            if 'college_name' not in cap_columns:
                db.session.execute(db.text('ALTER TABLE cap_cutoffs ADD COLUMN college_name VARCHAR(200)'))
                logging.info("✅ Added college_name column to cap_cutoffs")
            if 'branch' not in cap_columns:
                db.session.execute(db.text('ALTER TABLE cap_cutoffs ADD COLUMN branch VARCHAR(100)'))
                logging.info("✅ Added branch column to cap_cutoffs")
            db.session.commit()
        # ── Ensure college_cutoffs.source_file_id has FK constraint ──
        if 'college_cutoffs' in inspector.get_table_names():
            cc_columns = [c['name'] for c in inspector.get_columns('college_cutoffs')]
            if 'source_file_id' in cc_columns:
                # Check if FK already exists
                fks = inspector.get_foreign_keys('college_cutoffs')
                has_fk = any(
                    fk['constrained_columns'] == ['source_file_id']
                    for fk in fks
                )
                if not has_fk:
                    db.session.execute(db.text(
                        "DO $$ BEGIN "
                        "  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_college_cutoffs_source_file') THEN "
                        "    ALTER TABLE college_cutoffs "
                        "      ADD CONSTRAINT fk_college_cutoffs_source_file "
                        "      FOREIGN KEY (source_file_id) REFERENCES uploaded_files(id); "
                        "  END IF; "
                        "END $$;"
                    ))
                    logging.info("✅ Added FK constraint to college_cutoffs.source_file_id")
                    db.session.commit()
    except Exception as e:
        db.session.rollback()
        logging.warning(f"Migration note (may be OK): {e}")

    init_sample_data()
    init_sample_cutoff_data()

    # Seed admin user from environment variables (if configured)
    admin_email = os.environ.get('ADMIN_EMAIL', '').strip().lower()
    admin_password = os.environ.get('ADMIN_PASSWORD', '').strip()
    if admin_email and admin_password and len(admin_password) >= 8:
        existing_admin = User.query.filter_by(email=admin_email).first()
        if not existing_admin:
            admin_user = User(
                email=admin_email,
                first_name='Admin',
                last_name='User',
                password_hash=hash_password(admin_password),
                role='admin',
                is_verified=True,
                created_at=datetime.utcnow(),
            )
            db.session.add(admin_user)
            db.session.commit()
            logging.info(f"✅ Admin user seeded: {admin_email}")
        elif not existing_admin.is_admin():
            existing_admin.role = 'admin'
            existing_admin.is_verified = True
            if not existing_admin.password_hash:
                existing_admin.password_hash = hash_password(admin_password)
            db.session.commit()
            logging.info(f"✅ Existing user {admin_email} promoted to admin")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)