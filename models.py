from database import db
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship

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
    
    # Relationship to cutoff data
    cutoff_data = relationship("CAPCutoff", back_populates="college")
    
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
    
    def get_cutoff_data(self, year=None, category=None, gender=None):
        """Get cutoff data for specific criteria"""
        query = CAPCutoff.query.filter_by(college_id=self.id)
        
        if year:
            query = query.filter_by(year=year)
        if category:
            query = query.filter_by(category=category)
        if gender:
            query = query.filter_by(gender=gender)
            
        return query.all()


class CAPCutoff(db.Model):
    """Model for storing CAP (Centralized Admission Process) cutoff data"""
    
    __tablename__ = 'cap_cutoffs'
    
    id = Column(Integer, primary_key=True)
    college_id = Column(Integer, ForeignKey('colleges.id'), nullable=False)
    year = Column(Integer, nullable=False)  # Year of admission (2022, 2023, 2024)
    round_number = Column(Integer, nullable=False)  # CAP Round (1, 2, 3)
    category = Column(String(20), nullable=False)  # Open, OBC, SC, ST, NT, EWS
    gender = Column(String(10), nullable=False)  # Male, Female, Other
    cutoff_percentile = Column(Float, nullable=False)  # MH-CET percentile cutoff
    opening_rank = Column(Integer, nullable=True)  # Opening rank
    closing_rank = Column(Integer, nullable=True)  # Closing rank
    seats_available = Column(Integer, nullable=True)  # Total seats available
    
    # Relationship
    college = relationship("College", back_populates="cutoff_data")
    
    def __repr__(self):
        return f'<CAPCutoff {self.college.college} - {self.year} {self.category} {self.gender}>'
    
    def to_dict(self):
        """Convert cutoff object to dictionary"""
        return {
            'id': self.id,
            'college_id': self.college_id,
            'year': self.year,
            'round_number': self.round_number,
            'category': self.category,
            'gender': self.gender,
            'cutoff_percentile': self.cutoff_percentile,
            'opening_rank': self.opening_rank,
            'closing_rank': self.closing_rank,
            'seats_available': self.seats_available
        }


class MHCETStudent(db.Model):
    """Model for storing MH-CET student information and preferences"""
    
    __tablename__ = 'mhcet_students'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    mhcet_score = Column(Float, nullable=False)  # MH-CET score out of 200
    percentile = Column(Float, nullable=False)  # MH-CET percentile
    category = Column(String(20), nullable=False)  # Open, OBC, SC, ST, NT, EWS
    gender = Column(String(10), nullable=False)  # Male, Female, Other
    domicile = Column(String(50), nullable=False)  # Maharashtra, Outside Maharashtra
    budget_max = Column(Float, nullable=True)  # Maximum budget for fees
    preferred_locations = Column(Text, nullable=True)  # JSON string of preferred locations
    preferred_branches = Column(Text, nullable=True)  # JSON string of preferred branches
    
    def __repr__(self):
        return f'<MHCETStudent {self.name} - {self.percentile}%ile>'
    
    def to_dict(self):
        """Convert student object to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'mhcet_score': self.mhcet_score,
            'percentile': self.percentile,
            'category': self.category,
            'gender': self.gender,
            'domicile': self.domicile,
            'budget_max': self.budget_max,
            'preferred_locations': self.preferred_locations,
            'preferred_branches': self.preferred_branches
        }
