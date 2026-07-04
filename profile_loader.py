"""
profile_loader.py
"""

from config import DEFAULT_ANSWERS


def load_profile():
    return {
        "full_name":
            DEFAULT_ANSWERS["full_name"],

        "email":
            DEFAULT_ANSWERS["email"],

        "phone":
            DEFAULT_ANSWERS["phone"],

        "education":
            """
Bachelor's Degree
Computer Science
            """,

        "experience":
            """
Senior Data Consultant

• Built ETL pipelines
• Developed cloud analytics platforms
• Delivered AI-powered products
• Worked with SQL, Python,
  AWS, Databricks and Snowflake
            """,
    }