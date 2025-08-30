import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics.pairwise import cosine_similarity
from models import College
import logging

class CollegeRecommender:
    """College recommendation system using cosine similarity"""
    
    def __init__(self, db):
        self.db = db
        self.scaler = MinMaxScaler()
        
    def _prepare_data(self, colleges):
        """Prepare and normalize college data for similarity calculation"""
        if not colleges:
            return None, None
            
        # Convert to DataFrame
        data = []
        for college in colleges:
            data.append({
                'fees': college.fees,
                'placement_rate': college.placement_rate,
                'nirf_rank': college.nirf_rank,
                'rating': college.rating
            })
        
        df = pd.DataFrame(data)
        
        # For NIRF rank, lower is better, so we'll invert it
        # Convert to "rank score" where higher is better
        max_rank = df['nirf_rank'].max()
        df['rank_score'] = max_rank + 1 - df['nirf_rank']
        
        # For fees, lower is often better, so we'll invert it too
        # Convert to "affordability score" where higher is better
        max_fees = df['fees'].max()
        df['affordability_score'] = max_fees + 1 - df['fees']
        
        # Select features for similarity calculation
        features = ['affordability_score', 'placement_rate', 'rank_score', 'rating']
        feature_data = df[features]
        
        # Normalize features
        normalized_data = self.scaler.fit_transform(feature_data)
        
        return normalized_data, df
    
    def _create_user_profile(self, budget=None, preferences=None):
        """Create user preference profile for similarity matching"""
        # Default preferences if not provided
        if preferences is None:
            preferences = {
                'placement_rate': 85.0,  # Prefer high placement rate
                'rating': 4.0,           # Prefer good rating
                'fees_weight': 0.3       # Weight for affordability
            }
        
        # Create user profile based on preferences and budget
        user_profile = {
            'affordability_preference': 1.0 if budget else 0.5,
            'placement_preference': preferences.get('placement_rate', 85.0) / 100.0,
            'rank_preference': 0.8,  # Prefer good ranks
            'rating_preference': preferences.get('rating', 4.0) / 5.0
        }
        
        return np.array(list(user_profile.values())).reshape(1, -1)
    
    def get_recommendations(self, budget=None, location=None, branch=None, top_n=5, preferences=None):
        """
        Get college recommendations based on user preferences
        
        Args:
            budget: Maximum budget (fees)
            location: Preferred location
            branch: Preferred branch
            top_n: Number of recommendations to return
            preferences: Dictionary of user preferences
            
        Returns:
            List of tuples (College object, similarity_score)
        """
        try:
            # Build query with filters
            query = College.query
            
            if budget:
                query = query.filter(College.fees <= budget)
            
            if location:
                query = query.filter(College.location.ilike(f'%{location}%'))
            
            if branch:
                query = query.filter(College.branch.ilike(f'%{branch}%'))
            
            # Get filtered colleges
            colleges = query.all()
            
            if not colleges:
                logging.warning("No colleges found matching the criteria")
                return []
            
            if len(colleges) == 1:
                return [(colleges[0], 1.0)]
            
            # Prepare and normalize data
            normalized_data, df = self._prepare_data(colleges)
            
            if normalized_data is None:
                return []
            
            # Create user profile
            user_profile = self._create_user_profile(budget, preferences)
            
            # If user profile has different number of features, adjust it
            if user_profile.shape[1] != normalized_data.shape[1]:
                # Normalize user profile to match feature dimensions
                user_profile = self.scaler.transform(user_profile)
            
            # Calculate cosine similarity
            similarities = cosine_similarity(user_profile, normalized_data)[0]
            
            # Combine colleges with their similarity scores
            college_scores = list(zip(colleges, similarities))
            
            # Sort by similarity score (descending)
            college_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Return top N recommendations
            return college_scores[:top_n]
            
        except Exception as e:
            logging.error(f"Error in get_recommendations: {e}")
            # Fallback to simple filtering without ML
            query = College.query
            
            if budget:
                query = query.filter(College.fees <= budget)
            
            if location:
                query = query.filter(College.location.ilike(f'%{location}%'))
            
            if branch:
                query = query.filter(College.branch.ilike(f'%{branch}%'))
            
            # Sort by a combination of factors
            colleges = query.order_by(
                College.placement_rate.desc(),
                College.rating.desc(),
                College.nirf_rank.asc()
            ).limit(top_n).all()
            
            # Return with dummy scores
            return [(college, 1.0) for college in colleges]
    
    def get_similar_colleges(self, college_id, top_n=5):
        """
        Find colleges similar to a given college
        
        Args:
            college_id: ID of the reference college
            top_n: Number of similar colleges to return
            
        Returns:
            List of tuples (College object, similarity_score)
        """
        try:
            # Get reference college
            reference_college = College.query.get(college_id)
            if not reference_college:
                return []
            
            # Get all other colleges
            other_colleges = College.query.filter(College.id != college_id).all()
            
            if not other_colleges:
                return []
            
            # Combine reference and other colleges for normalization
            all_colleges = [reference_college] + other_colleges
            
            # Prepare and normalize data
            normalized_data, df = self._prepare_data(all_colleges)
            
            if normalized_data is None:
                return []
            
            # Reference college is the first one
            reference_vector = normalized_data[0:1]
            other_vectors = normalized_data[1:]
            
            # Calculate cosine similarity
            similarities = cosine_similarity(reference_vector, other_vectors)[0]
            
            # Combine colleges with their similarity scores
            college_scores = list(zip(other_colleges, similarities))
            
            # Sort by similarity score (descending)
            college_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Return top N similar colleges
            return college_scores[:top_n]
            
        except Exception as e:
            logging.error(f"Error in get_similar_colleges: {e}")
            return []
