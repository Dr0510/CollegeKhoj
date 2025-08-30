import os
import logging
import pandas as pd
from flask import Flask, render_template, request, jsonify, flash, redirect, url_for
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
from models import College
from recommender import CollegeRecommender

# Initialize recommendation engine
recommender = CollegeRecommender(db)

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

# Initialize database and sample data
with app.app_context():
    db.create_all()
    init_sample_data()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
