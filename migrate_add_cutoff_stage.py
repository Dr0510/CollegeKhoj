"""Migrate: Add stage column to cutoffs table."""
from app import app
from database import db
import logging

logger = logging.getLogger(__name__)

def migrate():
    with app.app_context():
        with db.engine.connect() as conn:
            try:
                conn.execute(db.text('ALTER TABLE public.cutoffs ADD COLUMN IF NOT EXISTS stage VARCHAR(20)'))
                conn.commit()
                logger.info("Added stage column to cutoffs")
            except Exception as e:
                logger.error(f"Migration failed: {e}")

if __name__ == '__main__':
    migrate()