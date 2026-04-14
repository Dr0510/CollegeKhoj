import pandas as pd
import numpy as np
import logging
import json
from sqlalchemy import or_

class MHCETRecommender:
    """MH-CET specific recommendation system using CAP cutoff data"""
    
    def __init__(self, db):
        self.db = db
        
    def get_admission_probability(self, student_percentile, cutoff_percentile, safety_margin=2.0):
        """
        Calculate realistic admission probability based on student percentile vs cutoff
        This mimics the real MSCET portal logic where cutoffs are strict
        
        Args:
            student_percentile: Student's MH-CET percentile
            cutoff_percentile: College cutoff percentile
            safety_margin: Safety margin for probability calculation
            
        Returns:
            Probability score (0-1)
        """
        percentile_diff = student_percentile - cutoff_percentile
        
        if percentile_diff >= 5.0:
            return 0.90  # Very high chance - well above cutoff
        elif percentile_diff >= 2.0:
            return 0.75  # High chance - above cutoff with margin
        elif percentile_diff >= 0.5:
            return 0.60  # Good chance - slightly above cutoff
        elif percentile_diff >= -0.5:
            return 0.35  # Moderate chance - around cutoff
        elif percentile_diff >= -2.0:
            return 0.15  # Low chance - slightly below cutoff
        elif percentile_diff >= -5.0:
            return 0.05  # Very low chance - below cutoff
        else:
            return 0.01  # Almost no chance - well below cutoff
    
    def categorize_recommendation(self, probability):
        """Categorize college recommendation based on admission probability"""
        if probability >= 0.70:
            return "Safe"
        elif probability >= 0.40:
            return "Moderate"
        elif probability >= 0.10:
            return "Ambitious"
        else:
            return "Dream"
    
    def get_mhcet_recommendations(self, percentile, category, gender, budget=None, 
                                 preferred_locations=None, preferred_branches=None, 
                                 top_n=10, include_years=[2022, 2023, 2024]):
        """
        Get college recommendations for MH-CET students based on cutoff analysis
        
        Args:
            percentile: Student's MH-CET percentile
            category: Student's category (Open, OBC, SC, ST, NT, EWS)
            gender: Student's gender (Male, Female, Other)
            budget: Maximum budget for fees
            preferred_locations: List of preferred locations
            preferred_branches: List of preferred branches
            top_n: Number of recommendations to return
            include_years: Years to consider for cutoff analysis
            
        Returns:
            List of tuples (College object, admission_data)
        """
        try:
            from models import College, CAPCutoff
            
            # Build base query for colleges
            college_query = College.query
            
            if budget:
                college_query = college_query.filter(College.fees <= budget)
            
            if preferred_locations:
                location_filters = []
                for location in preferred_locations:
                    location_filters.append(College.location.ilike(f'%{location}%'))
                if location_filters:
                    college_query = college_query.filter(or_(*location_filters))
            
            if preferred_branches:
                branch_filters = []
                for branch in preferred_branches:
                    branch_filters.append(College.branch.ilike(f'%{branch}%'))
                if branch_filters:
                    college_query = college_query.filter(or_(*branch_filters))
            
            colleges = college_query.all()
            
            if not colleges:
                logging.warning("No colleges found matching the criteria")
                return []
            
            recommendations = []
            
            for college in colleges:
                # Get cutoff data for this college
                cutoff_query = CAPCutoff.query.filter_by(
                    college_id=college.id,
                    category=category,
                    gender=gender
                ).filter(CAPCutoff.year.in_(include_years))
                
                cutoffs = cutoff_query.all()
                
                if not cutoffs:
                    # If no specific cutoff data, estimate based on NIRF rank and fees
                    estimated_cutoff = self._estimate_cutoff(college, category)
                    probability = self.get_admission_probability(percentile, estimated_cutoff)
                    
                    admission_data = {
                        'probability': probability,
                        'category_type': self.categorize_recommendation(probability),
                        'estimated_cutoff': estimated_cutoff,
                        'has_real_data': False,
                        'cutoff_trend': 'No historical data',
                        'years_analyzed': []
                    }
                else:
                    # Analyze real cutoff data
                    cutoff_percentiles = [c.cutoff_percentile for c in cutoffs]
                    avg_cutoff = np.mean(cutoff_percentiles)
                    latest_cutoff = max(cutoffs, key=lambda x: x.year).cutoff_percentile
                    
                    # Calculate trend
                    cutoff_trend = self._calculate_trend(cutoffs)
                    
                    # Calculate admission probability based on latest cutoff
                    probability = self.get_admission_probability(percentile, latest_cutoff)
                    
                    admission_data = {
                        'probability': probability,
                        'category_type': self.categorize_recommendation(probability),
                        'latest_cutoff': latest_cutoff,
                        'average_cutoff': avg_cutoff,
                        'min_cutoff': min(cutoff_percentiles),
                        'max_cutoff': max(cutoff_percentiles),
                        'has_real_data': True,
                        'cutoff_trend': cutoff_trend,
                        'years_analyzed': list(set([c.year for c in cutoffs])),
                        'cutoff_data': [c.to_dict() for c in cutoffs]
                    }
                
                recommendations.append((college, admission_data))
            
            # Sort by NIRF rank best first (like real MSCET portal: shows top colleges first with their chances)
            recommendations.sort(key=lambda x: x[0].nirf_rank)
            
            return recommendations[:top_n]
            
        except Exception as e:
            logging.error(f"Error in get_mhcet_recommendations: {e}")
            return []
    
    def _estimate_cutoff(self, college, category):
        """Estimate cutoff percentile based on college ranking and fees"""
        # More realistic base cutoff estimation based on NIRF rank
        if college.nirf_rank <= 5:  # Top IITs
            base_cutoff = 99.0
        elif college.nirf_rank <= 10:  # Other top IITs/NITs
            base_cutoff = 96.0
        elif college.nirf_rank <= 25:  # Good NITs/IIITs
            base_cutoff = 92.0
        elif college.nirf_rank <= 50:  # Decent engineering colleges
            base_cutoff = 85.0
        elif college.nirf_rank <= 100:  # Average engineering colleges
            base_cutoff = 78.0
        elif college.nirf_rank <= 200:  # Below average colleges
            base_cutoff = 65.0
        elif college.nirf_rank <= 300:  # Lower tier colleges
            base_cutoff = 55.0
        else:  # Bottom tier colleges
            base_cutoff = 45.0
        
        # Adjust based on category (realistic Maharashtra reservations)
        category_adjustments = {
            'Open': 0,
            'OBC': -8,
            'SC': -20,
            'ST': -25,
            'NT': -12,
            'EWS': -5
        }
        
        adjustment = category_adjustments.get(category, 0)
        estimated_cutoff = max(base_cutoff + adjustment, 25.0)  # Minimum 25th percentile
        
        return estimated_cutoff
    
    def _calculate_trend(self, cutoffs):
        """Calculate cutoff trend over years"""
        if len(cutoffs) < 2:
            return "Stable"
        
        # Sort by year
        sorted_cutoffs = sorted(cutoffs, key=lambda x: x.year)
        
        # Calculate trend
        first_year = sorted_cutoffs[0].cutoff_percentile
        last_year = sorted_cutoffs[-1].cutoff_percentile
        
        diff = last_year - first_year
        
        if diff > 2:
            return "Increasing"
        elif diff < -2:
            return "Decreasing"
        else:
            return "Stable"
    
    def analyze_student_profile(self, percentile, category, gender, budget=None):
        """
        Analyze student's profile and provide insights
        
        Returns:
            Dictionary with analysis and recommendations
        """
        analysis = {
            'percentile_category': self._get_percentile_category(percentile),
            'competitive_level': self._get_competitive_level(percentile, category),
            'budget_category': self._get_budget_category(budget) if budget else None,
            'recommendations': {
                'strategy': self._get_strategy_recommendation(percentile, category),
                'college_types': self._get_college_type_recommendation(percentile, budget),
                'backup_plan': self._get_backup_plan(percentile, category)
            }
        }
        
        return analysis
    
    def _get_percentile_category(self, percentile):
        """Categorize student based on percentile"""
        if percentile >= 99:
            return "Excellent"
        elif percentile >= 95:
            return "Very Good"
        elif percentile >= 90:
            return "Good"
        elif percentile >= 80:
            return "Above Average"
        elif percentile >= 60:
            return "Average"
        else:
            return "Below Average"
    
    def _get_competitive_level(self, percentile, category):
        """Determine competitive level for admission"""
        base_competitive_cutoff = {
            'Open': 90,
            'OBC': 85,
            'SC': 75,
            'ST': 70,
            'NT': 80,
            'EWS': 87
        }
        
        cutoff = base_competitive_cutoff.get(category, 85)
        
        if percentile >= cutoff + 5:
            return "Highly Competitive"
        elif percentile >= cutoff:
            return "Competitive"
        elif percentile >= cutoff - 10:
            return "Moderately Competitive"
        else:
            return "Less Competitive"
    
    def _get_budget_category(self, budget):
        """Categorize budget range"""
        if budget >= 1000000:  # 10 lakh+
            return "High Budget"
        elif budget >= 500000:  # 5-10 lakh
            return "Medium Budget"
        elif budget >= 200000:  # 2-5 lakh
            return "Moderate Budget"
        else:
            return "Low Budget"
    
    def _get_strategy_recommendation(self, percentile, category):
        """Get admission strategy recommendation"""
        if percentile >= 95:
            return "Target top-tier colleges and premium institutes. You have excellent chances."
        elif percentile >= 85:
            return "Apply to good government and private colleges. Consider location preferences."
        elif percentile >= 75:
            return "Focus on government colleges and established private institutions."
        elif percentile >= 65:
            return "Apply to multiple colleges including backup options. Consider less popular branches."
        else:
            return "Apply widely including backup colleges. Consider management quota in private colleges."
    
    def _get_college_type_recommendation(self, percentile, budget):
        """Recommend types of colleges to target"""
        recommendations = []
        
        if percentile >= 90:
            recommendations.append("Top government colleges (IITs, NITs, COEP)")
            
        if percentile >= 80:
            recommendations.append("Good government colleges")
            
        if budget and budget >= 300000:
            recommendations.append("Premium private colleges")
        elif budget and budget >= 150000:
            recommendations.append("Established private colleges")
            
        if percentile >= 70:
            recommendations.append("State government colleges")
            
        recommendations.append("Consider multiple locations for better options")
        
        return recommendations
    
    def _get_backup_plan(self, percentile, category):
        """Suggest backup plan based on profile"""
        if percentile >= 85:
            return "Apply to slightly lower-ranked colleges as backup"
        elif percentile >= 70:
            return "Include several backup colleges and consider management quota"
        else:
            return "Apply to maximum colleges, consider diploma-to-degree options, and management quota"