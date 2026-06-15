import os
import logging
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, session, g
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from database import db, init_database
import io

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max file size

# Initialize database
init_database(app)

# Import models and recommender after db initialization
from models import College, CAPCutoff, MHCETStudent, User
from recommender import CollegeRecommender
from mhcet_recommender import MHCETRecommender
from clerk_auth import verify_token, get_clerk_user_data, extract_primary_email, CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY

# ── Clerk frontend API (derived from publishable key) ─────────────────────────
def _clerk_frontend_api():
    key = CLERK_PUBLISHABLE_KEY
    if not key:
        return ''
    # pk_test_abc123... → abc123.clerk.accounts.dev
    # pk_live_abc123... → abc123.clerk.accounts.dev
    try:
        b64 = key.split('_')[2]        # third segment after pk_test_ or pk_live_
        import base64
        decoded = base64.b64decode(b64 + '==').decode('utf-8').rstrip('$')
        return decoded
    except Exception:
        return 'accounts.clerk.dev'

CLERK_FRONTEND_API = _clerk_frontend_api()

# ── Auth middleware ────────────────────────────────────────────────────────────
@app.before_request
def load_current_user():
    """Verify Clerk session token from cookie and load user into g.user."""
    g.user = None
    # Try Authorization header first (for AJAX calls), then cookie
    auth_header = request.headers.get('Authorization', '')
    token = None
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
    else:
        token = request.cookies.get('__session') or request.cookies.get('__client_uat')

    if token and CLERK_SECRET_KEY:
        claims = verify_token(token)
        if claims:
            clerk_id = claims.get('sub', '')
            if clerk_id:
                user = User.query.filter_by(clerk_id=clerk_id).first()
                g.user = user

@app.context_processor
def inject_user():
    """Make current user and Clerk keys available in every template."""
    return {
        'current_user': g.get('user'),
        'clerk_publishable_key': CLERK_PUBLISHABLE_KEY,
        'clerk_frontend_api': CLERK_FRONTEND_API,
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
    # Get unique locations and branches for form options
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

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/login')
def login_page():
    next_url = request.args.get('next', url_for('mhcet_page'))
    if not CLERK_PUBLISHABLE_KEY:
        flash('Clerk keys not configured. Please set CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY.', 'error')
        return redirect(url_for('mhcet_page'))
    return render_template('login.html', next_url=next_url)


@app.route('/signup')
def signup_page():
    """Sign-up page with Clerk's SignUp component."""
    next_url = request.args.get('next', url_for('mhcet_page'))
    if not CLERK_PUBLISHABLE_KEY:
        flash('Clerk keys not configured. Please set CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY.', 'error')
        return redirect(url_for('mhcet_page'))
    return render_template('signup.html', next_url=next_url)


@app.route('/auth/sync', methods=['GET', 'POST'])
def auth_sync():
    """Called by Clerk JS after sign-in to sync user into Neon DB."""
    next_url = request.args.get('next', url_for('mhcet_page'))

    auth_header = request.headers.get('Authorization', '')
    token = auth_header[7:] if auth_header.startswith('Bearer ') else None

    if not token:
        return redirect(url_for('login_page'))

    claims = verify_token(token)
    if not claims:
        logging.warning("Auth sync: invalid token")
        return redirect(url_for('login_page'))

    clerk_id = claims.get('sub', '')
    if not clerk_id:
        return redirect(url_for('login_page'))

    try:
        user = User.query.filter_by(clerk_id=clerk_id).first()

        # Fetch fresh profile data from Clerk API
        clerk_data = get_clerk_user_data(clerk_id) or {}
        email      = extract_primary_email(clerk_data) if clerk_data else ''
        first_name = clerk_data.get('first_name') or ''
        last_name  = clerk_data.get('last_name') or ''
        image_url  = clerk_data.get('image_url') or ''

        if user:
            # Update existing user
            user.email             = email or user.email
            user.first_name        = first_name or user.first_name
            user.last_name         = last_name or user.last_name
            user.profile_image_url = image_url or user.profile_image_url
            user.last_login        = datetime.utcnow()
        else:
            user = User(
                clerk_id=clerk_id,
                email=email,
                first_name=first_name,
                last_name=last_name,
                profile_image_url=image_url,
                created_at=datetime.utcnow(),
                last_login=datetime.utcnow(),
            )
            db.session.add(user)

        db.session.commit()
        logging.info(f"User synced: {clerk_id} ({email})")

        if request.method == 'POST':
            return jsonify({'ok': True, 'user': user.to_dict()})

    except Exception as e:
        db.session.rollback()
        logging.error(f"Auth sync error: {e}")
        if request.method == 'POST':
            return jsonify({'ok': False, 'error': str(e)}), 500

    return redirect(next_url)


@app.route('/logout')
def logout():
    """Clear server-side session and redirect to Clerk-signed-out page."""
    session.clear()
    return redirect(url_for('mhcet_page'))


@app.route('/profile')
def profile_page():
    """User profile page — shows saved data from Neon DB."""
    user = g.get('user')
    if not user:
        return redirect(url_for('login_page', next=url_for('profile_page')))
    return render_template('profile.html', user=user)


# ── Initialize database and sample data ───────────────────────────────────────

# Initialize database and sample data
with app.app_context():
    db.create_all()
    init_sample_data()
    init_sample_cutoff_data()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
