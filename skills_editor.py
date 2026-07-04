"""
skills_editor.py

Phase 5.3

Intelligently updates the Skills section
of the master resume.

Part 1
"""

import re

from collections import OrderedDict


class SkillsEditor:

    def __init__(

        self,

        parser,

        jd_keywords,

    ):

        self.parser = parser

        self.jd_keywords = [

            k.strip()

            for k in jd_keywords

            if k.strip()

        ]

        self.skill_categories = OrderedDict()

    # --------------------------------------------------

    @staticmethod
    def normalize(text):

        return (

            text

            .lower()

            .strip()

        )

    # --------------------------------------------------

    def parse_existing_skills(self):

        """
        Convert:

        AI & LLMs:
        Claude, OpenAI

        Backend:
        Python, FastAPI

        into dictionary
        """

        skills_text = self.parser.get_skills()

        current = None

        categories = OrderedDict()

        for line in skills_text.split("\n"):

            line = line.strip()

            if not line:

                continue

            if ":" in line:

                category, values = line.split(

                    ":",

                    1

                )

                current = category.strip()

                categories[current] = [

                    s.strip()

                    for s in values.split(",")

                    if s.strip()

                ]

            elif current:

                values = [

                    s.strip()

                    for s in line.split(",")

                    if s.strip()

                ]

                categories[current].extend(

                    values

                )

        self.skill_categories = categories

        return categories

    # --------------------------------------------------

    def category_mapping(self):

        """
        JD keyword classifier.
        """

        return {

            "AI & LLMs": [

                "llm",

                "gpt",

                "openai",

                "claude",

                "gemini",

                "langchain",

                "rag",

                "vector",

                "prompt",

                "bedrock",

            ],

            "Backend": [

                "python",

                "fastapi",

                "flask",

                "django",

                "rest",

                "api",

                "prisma",

            ],

            "Data Engineering": [

                "sql",

                "spark",

                "pyspark",

                "airflow",

                "dbt",

                "etl",

                "elt",

                "snowflake",

                "redshift",

                "databricks",

                "hadoop",

                "kafka",

                "delta",

            ],

            "Cloud & Deployment": [

                "aws",

                "azure",

                "gcp",

                "docker",

                "kubernetes",

                "terraform",

                "ec2",

                "lambda",

                "s3",

                "rds",

                "glue",

            ],

            "BI & Visualization": [

                "tableau",

                "power bi",

                "excel",

                "looker",

                "dashboard",

            ],

            "ML (Foundational)": [

                "tensorflow",

                "pytorch",

                "xgboost",

                "lightgbm",

                "sklearn",

                "scikit-learn",

                "machine learning",

                "regression",

                "classification",

                "a/b testing",

            ],

            "Integrations": [

                "graph api",

                "slack",

                "jira",

                "salesforce",

                "hubspot",

                "resend",

            ],

        }

    # --------------------------------------------------

    def detect_category(

        self,

        keyword,

    ):

        keyword_lower = self.normalize(

            keyword

        )

        mapping = self.category_mapping()

        for category, words in mapping.items():

            for word in words:

                if word in keyword_lower:

                    return category

        return "Data Engineering"

    # --------------------------------------------------

    # Too generic to ever appear as a standalone skill line item — useful
    # for JD-relevance matching (job_filters.MATCH_KEYWORDS) but not as a
    # literal resume entry.
    GENERIC_SKIP = {"data", "ai", "pipeline", "analytics"}

    def add_keyword(

        self,

        category,

        keyword,

    ):

        norm_keyword = self.normalize(keyword)

        if norm_keyword in self.GENERIC_SKIP:
            return

        if category not in self.skill_categories:

            self.skill_categories[

                category

            ] = []

        existing = [

            self.normalize(x)

            for x in self.skill_categories[

                category

            ]

        ]

        # Skip exact duplicates and near-duplicates already implied by an
        # existing entry (e.g. "redshift" when "Amazon Redshift" is there).
        if norm_keyword in existing:
            return

        if any(norm_keyword in e for e in existing):
            return

        self.skill_categories[

            category

        ].append(keyword)

    # --------------------------------------------------

    def optimize_skills(

        self,

        max_new=12,

    ):

        """
        Add only missing JD keywords.
        """

        self.parse_existing_skills()

        added = 0

        for keyword in self.jd_keywords:

            if added >= max_new:

                break

            category = self.detect_category(

                keyword

            )

            before = len(

                self.skill_categories.get(

                    category,

                    []

                )

            )

            self.add_keyword(

                category,

                keyword

            )

            after = len(

                self.skill_categories[

                    category

                ]

            )

            if after > before:

                added += 1

        return self.skill_categories

    # --------------------------------------------------

    def find_skill_heading(self):

        for i, paragraph in enumerate(

            self.parser.document.paragraphs

        ):

            text = paragraph.text.strip().lower()

            if text in [

                "technical skills",

                "skills",

                "core skills",

            ]:

                return i

        return None

    # --------------------------------------------------

    def find_next_heading(

        self,

        start,

    ):
        """
        This resume's section titles ("WORK EXPERIENCE", "CERTIFICATIONS",
        etc.) are plain "Normal"-styled paragraphs, not real Word Heading
        styles — they're only visually headings via short all-caps text.
        Checking style name alone misses them entirely, which previously
        caused this to run off the end of the document and treat the whole
        rest of the resume as skills-section overflow to be blanked.
        """

        paragraphs = self.parser.document.paragraphs

        for i in range(

            start + 1,

            len(paragraphs)

        ):

            text = paragraphs[i].text.strip()

            style = paragraphs[i].style.name.lower()

            if "heading" in style:

                return i

            if text and text == text.upper() and len(text) < 60:

                return i

        return len(paragraphs)

    # --------------------------------------------------

    @staticmethod
    def clear_paragraph(

        paragraph,

    ):
        """
        Remove only run elements. Paragraph-level formatting (<w:pPr> —
        alignment, spacing, style) is left untouched, unlike naively
        clearing every XML child of the paragraph.
        """

        for run in list(paragraph.runs):

            run._element.getparent().remove(run._element)

    # --------------------------------------------------

    @staticmethod
    def _capture_run_style(run):

        return {

            "bold": run.bold,
            "italic": run.italic,
            "underline": run.underline,
            "font_name": run.font.name,
            "font_size": run.font.size,
            "color": run.font.color.rgb if run.font.color and run.font.color.type else None,

        }

    @staticmethod
    def _apply_run_style(run, style):

        if not style:
            return

        run.bold = style["bold"]
        run.italic = style["italic"]
        run.underline = style["underline"]

        if style["font_name"]:
            run.font.name = style["font_name"]
        if style["font_size"]:
            run.font.size = style["font_size"]
        if style["color"]:
            run.font.color.rgb = style["color"]

    # --------------------------------------------------

    def replace_skill_section(self):

        """
        Replace only the body of the Skills section — one paragraph per
        category line, written as a bold "Category: " label run plus a
        normal-weight values run (matching the original two-run layout),
        with font/size/color copied from the existing lines so tailored
        output looks identical in style to the untouched resume.
        """

        heading = self.find_skill_heading()

        if heading is None:

            print("Skills section not found.")

            return False

        end = self.find_next_heading(heading)

        paragraphs = self.parser.document.paragraphs

        body_start = heading + 1
        if body_start < end and not paragraphs[body_start].text.strip():
            body_start += 1

        body_paragraphs = list(paragraphs[body_start:end])

        # Template styles from the first existing "Category: values" line —
        # reused for every rewritten/inserted line so styling stays consistent.
        label_style = value_style = None
        for para in body_paragraphs:
            if len(para.runs) >= 2:
                label_style = self._capture_run_style(para.runs[0])
                value_style = self._capture_run_style(para.runs[1])
                break

        entries = []
        for category, skills in self.skill_categories.items():
            unique, seen = [], set()
            for skill in skills:
                s = skill.strip()
                if not s or self.normalize(s) in seen:
                    continue
                seen.add(self.normalize(s))
                unique.append(s)
            if unique:
                entries.append((category, ", ".join(unique)))

        def write_line(para, category, values):
            self.clear_paragraph(para)
            label_run = para.add_run(f"{category}: ")
            self._apply_run_style(label_run, label_style)
            value_run = para.add_run(values)
            self._apply_run_style(value_run, value_style)

        # Reuse existing paragraphs one-per-line — preserves original formatting.
        for para, (category, values) in zip(body_paragraphs, entries):
            write_line(para, category, values)

        # More category lines than existing paragraph slots — insert new
        # paragraphs (cloned style from an existing skill line) right before
        # the next section heading.
        if len(entries) > len(body_paragraphs):
            style = body_paragraphs[-1].style if body_paragraphs else None
            anchor = paragraphs[end] if end < len(paragraphs) else None
            for category, values in entries[len(body_paragraphs):]:
                new_para = (
                    anchor.insert_paragraph_before("", style=style)
                    if anchor is not None
                    else self.parser.document.add_paragraph("", style=style)
                )
                write_line(new_para, category, values)

        # Fewer category lines than existing paragraph slots — blank the rest.
        if len(entries) < len(body_paragraphs):
            for para in body_paragraphs[len(entries):]:
                self.clear_paragraph(para)

        return True

    # --------------------------------------------------

    def update(

        self,

        max_new=12,

    ):

        """
        Main entry point.
        """

        self.optimize_skills(

            max_new=max_new

        )

        self.replace_skill_section()

        return self.parser.document