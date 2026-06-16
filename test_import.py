"""Quick end-to-end test of the PDF extraction + Neon import pipeline."""
import os
import sys
import logging
from datetime import datetime, timezone

sys.path.insert(0, '.')
logging.basicConfig(level=logging.WARNING)

os.environ['NEON_DATABASE_URL'] = 'postgresql://neondb_owner:npg_DIfNCaTu8AZ3@ep-damp-snow-aoyuwtab-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require'

# Flask app context
from flask import Flask
from database import init_database

app = Flask(__name__)
db = init_database(app)

with app.app_context():
    from admin.pdf_extractor import extract_pdf
    from sqlalchemy import text

    # Check current state
    total = db.session.execute(text("SELECT count(*) FROM college_cutoffs")).scalar()
    print(f"Current records in college_cutoffs: {total}")

    # Parse one of the PDFs
    pdf_dir = 'uploads/cutoffs'
    pdf_files = [f for f in os.listdir(pdf_dir) if f.endswith('.pdf')]
    if not pdf_files:
        print("No PDFs found in uploads/cutoffs/")
        sys.exit(0)

    pdf_path = os.path.join(pdf_dir, pdf_files[0])
    print(f"\nParsing: {pdf_files[0]}")

    extraction = extract_pdf(pdf_path, pdf_files[0])
    rows = extraction.get('rows', [])
    print(f"Parsed {len(rows)} rows")
    print(f"Year: {extraction['year']}, Round: {extraction['round']}")
    print(f"Method: {extraction['method']}, Confidence: {extraction['confidence']}%")

    if not rows:
        print("No rows extracted!")
        sys.exit(0)

    r = rows[0]
    print(f"\nSample row:")
    print(f"  College: {r['college_code']} - {r['college_name']}")
    print(f"  Course: {r['course_code']} - {r['course_name']}")
    print(f"  Category: {r['category']}, Rank: {r['rank']}, Percentile: {r['percentile']}")

    # Test bulk insert with ON CONFLICT
    inserted = 0
    duplicates = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        try:
            result = db.session.execute(
                text("""
                    INSERT INTO college_cutoffs 
                    (year, round, college_code, college_name, course_code, course_name,
                     category, rank, percentile, imported_at)
                    VALUES (:year, :round, :college_code, :college_name, :course_code,
                            :course_name, :category, :rank, :percentile, :imported_at)
                    ON CONFLICT (year, round, college_code, course_code, category)
                    DO NOTHING
                """),
                {
                    'year': row['year'],
                    'round': row['round'],
                    'college_code': row['college_code'],
                    'college_name': row['college_name'],
                    'course_code': row['course_code'],
                    'course_name': row['course_name'],
                    'category': row['category'],
                    'rank': row.get('rank'),
                    'percentile': row.get('percentile'),
                    'imported_at': now,
                }
            )
            if result.rowcount > 0:
                inserted += 1
            else:
                duplicates += 1
        except Exception as e:
            db.session.rollback()
            print(f"Error on row: {e}")
            raise

    db.session.commit()

    # Final stats
    total = db.session.execute(text("SELECT count(*) FROM college_cutoffs")).scalar()
    colleges = db.session.execute(text("SELECT count(DISTINCT college_code) FROM college_cutoffs")).scalar()
    courses = db.session.execute(text("SELECT count(DISTINCT course_code) FROM college_cutoffs")).scalar()

    print(f"\n=== Summary ===")
    print(f"Total Records Imported: {inserted}")
    print(f"Duplicate Records Skipped: {duplicates}")
    print(f"Total Colleges: {colleges}")
    print(f"Total Courses: {courses}")
    print(f"Total in DB: {total}")