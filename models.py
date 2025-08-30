from app import db
from sqlalchemy import Column, Integer, String, Float

class College(db.Model):
    """College model for storing college information"""
    
    __tablename__ = 'colleges'
    
    id = Column(Integer, primary_key=True)
    college = Column(String(200), nullable=False)
    location = Column(String(100), nullable=False)
    branch = Column(String(100), nullable=False)
    fees = Column(Float, nullable=False)
    placement_rate = Column(Float, nullable=False)  # Percentage
    nirf_rank = Column(Integer, nullable=False)
    rating = Column(Float, nullable=False)  # Out of 5
    
    def __repr__(self):
        return f'<College {self.college} - {self.branch} ({self.location})>'
    
    def to_dict(self):
        """Convert college object to dictionary"""
        return {
            'id': self.id,
            'college': self.college,
            'location': self.location,
            'branch': self.branch,
            'fees': self.fees,
            'placement_rate': self.placement_rate,
            'nirf_rank': self.nirf_rank,
            'rating': self.rating
        }
