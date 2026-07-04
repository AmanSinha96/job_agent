"""
role_classifier.py
"""


def classify_role(job_text):

    text = job_text.lower()

    ai_words = [
        "llm",
        "rag",
        "machine learning",
        "genai",
        "langchain",
        "vector database",
        "ai engineer",
    ]

    analytics_words = [
        "power bi",
        "tableau",
        "dashboard",
        "analytics",
        "reporting",
        "business intelligence",
    ]

    engineer_words = [
        "spark",
        "databricks",
        "snowflake",
        "airflow",
        "etl",
        "data pipeline",
    ]

    if any(k in text for k in ai_words):
        return "genai"

    if any(k in text for k in analytics_words):
        return "analytics"

    return "data_engineer"