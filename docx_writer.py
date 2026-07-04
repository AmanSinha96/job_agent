"""
docx_writer.py

Production Resume Writer

Responsibilities
----------------
1. Save customized resume as DOCX
2. Convert DOCX -> PDF (cross-platform)
3. Generate safe filenames
4. Manage output directory
5. Never fail resume generation because
   PDF conversion is unavailable

Phase 5.3 (Rebuilt)
"""

from pathlib import Path
import shutil
import subprocess
import platform

from docx import Document


# =====================================================
# Output Directory
# =====================================================

OUTPUT_DIR = Path("generated_resumes")

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =====================================================
# Resume Writer
# =====================================================

class ResumeWriter:

    """
    Handles exporting customized resumes.

    Output:

        generated_resumes/

            JohnDoe_DataEng_Microsoft.docx

            JohnDoe_DataEng_Microsoft.pdf
    """

    # -------------------------------------------------

    def __init__(

        self,

        document: Document,

    ):

        self.document = document

    # =================================================
    # Filename Helpers
    # =================================================

    @staticmethod
    def clean(

        text,

    ):
        """
        Remove invalid filename characters.
        """

        if not text:

            return ""

        invalid = [

            "\\",
            "/",
            ":",
            "*",
            "?",
            "\"",
            "<",
            ">",
            "|",

        ]

        for ch in invalid:

            text = text.replace(

                ch,

                "",

            )

        return (

            text
            .replace(" ", "")
            .strip()

        )

    # -------------------------------------------------

    @staticmethod
    def abbreviate(

        text,

        max_length=18,

    ):
        """
        Produce compact names.

        Example

        Senior Data Engineer

        →

        SeniDataEngi
        """

        if not text:

            return ""

        text = text.replace(

            "&",

            "",

        )

        # Strip filesystem/artifact-unsafe characters (title text like
        # "Consultant | Business Analyst | Mumbai" otherwise leaves a bare
        # "|" surviving as its own whitespace-separated token below, which
        # breaks GitHub Actions artifact upload and is invalid on Windows).
        for ch in ("\\", "/", ":", "*", "?", "\"", "<", ">", "|"):
            text = text.replace(ch, "")

        words = text.split()

        if len(words) == 1:

            return words[0][:max_length]

        short = ""

        for word in words:

            short += word[:4]

        return short[:max_length]

    # -------------------------------------------------

    def build_filename(

        self,

        candidate,

        role,

        company,

    ):
        """
        Build deterministic filename.

        Example

        JohnDoe_DataEng_Microsoft
        """

        candidate = self.clean(

            candidate,

        )

        role = self.abbreviate(

            role,

        )

        company = self.abbreviate(

            company,

        )

        return (

            f"{candidate}_{role}_{company}"

        )

    # =================================================
    # DOCX
    # =================================================

    def save_docx(

        self,

        candidate,

        role,

        company,

    ):
        """
        Save resume as DOCX.
        """

        filename = self.build_filename(

            candidate,

            role,

            company,

        )

        path = (

            OUTPUT_DIR

            /

            f"{filename}.docx"

        )

        self.document.save(

            str(path)

        )

        print(

            f"[ResumeWriter] DOCX saved -> {path}"

        )

        return path
    
        # =================================================
    # PDF Conversion
    # =================================================

    def _find_libreoffice(self):
        """
        Locate LibreOffice executable.

        Works for

        • Ubuntu (GitHub Actions)
        • Windows
        • macOS
        """

        candidates = [

            shutil.which("libreoffice"),
            shutil.which("soffice"),

        ]

        if platform.system() == "Windows":

            candidates.extend([

                r"C:\Program Files\LibreOffice\program\soffice.exe",

                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",

            ])

        elif platform.system() == "Darwin":

            candidates.append(

                "/Applications/LibreOffice.app/Contents/MacOS/soffice"

            )

        for candidate in candidates:

            if candidate and Path(candidate).exists():

                return candidate

        return None

    # -------------------------------------------------

    def convert_pdf(

        self,

        docx_path,

    ):
        """
        Convert DOCX to PDF.

        Strategy

        Windows
            -> docx2pdf (if installed)

        Linux/macOS
            -> LibreOffice

        If conversion cannot be performed,
        return None without failing the
        pipeline.
        """

        pdf_path = docx_path.with_suffix(

            ".pdf"

        )

        # --------------------------------------------
        # Windows : docx2pdf
        # --------------------------------------------

        if platform.system() == "Windows":

            try:

                from docx2pdf import convert

                convert(

                    str(docx_path),

                    str(pdf_path),

                )

                if pdf_path.exists():

                    print(

                        "[ResumeWriter] PDF created using docx2pdf"

                    )

                    return pdf_path

            except Exception as e:

                print(

                    f"[ResumeWriter] docx2pdf unavailable: {e}"

                )

        # --------------------------------------------
        # LibreOffice
        # --------------------------------------------

        libreoffice = self._find_libreoffice()

        if libreoffice is None:

            print(

                "[ResumeWriter] LibreOffice not found."

            )

            print(

                "[ResumeWriter] Skipping PDF conversion."

            )

            return None

        try:

            command = [

                libreoffice,

                "--headless",

                "--convert-to",

                "pdf",

                "--outdir",

                str(docx_path.parent),

                str(docx_path),

            ]

            subprocess.run(

                command,

                check=True,

                stdout=subprocess.PIPE,

                stderr=subprocess.PIPE,

                text=True,

            )

        except subprocess.CalledProcessError as e:

            print(

                "[ResumeWriter] LibreOffice conversion failed."

            )

            print(

                e.stderr

            )

            return None

        except Exception as e:

            print(

                f"[ResumeWriter] PDF conversion error: {e}"

            )

            return None

        if pdf_path.exists():

            print(

                f"[ResumeWriter] PDF saved -> {pdf_path}"

            )

            return pdf_path

        print(

            "[ResumeWriter] PDF was not created."

        )

        return None
    
        # =================================================
    # Export
    # =================================================

    def save(

        self,

        candidate,

        role,

        company,

    ):
        """
        Export resume.

        Always generates a DOCX.

        Attempts PDF generation if supported
        on the current platform.

        Returns
        -------
        {
            "docx_path": "...",
            "pdf_path": "... or None",
            "filename": "...",
            "output_dir": "...",
            "pdf_generated": bool
        }
        """

        filename = self.build_filename(

            candidate,

            role,

            company,

        )

        # --------------------------------------------
        # Save DOCX
        # --------------------------------------------

        docx_path = self.save_docx(

            candidate,

            role,

            company,

        )

        # --------------------------------------------
        # Optional PDF
        # --------------------------------------------

        pdf_path = self.convert_pdf(

            docx_path,

        )

        result = {

            "filename": filename,

            "docx_path": str(

                docx_path

            ),

            "pdf_path": (

                str(pdf_path)

                if pdf_path

                else None

            ),

            "output_dir": str(

                OUTPUT_DIR

            ),

            "pdf_generated": (

                pdf_path is not None

            ),

        }

        print()

        print(

            "=" * 60

        )

        print(

            "Resume Export Completed"

        )

        print(

            "=" * 60

        )

        print(

            f"DOCX : {result['docx_path']}"

        )

        if result["pdf_generated"]:

            print(

                f"PDF  : {result['pdf_path']}"

            )

        else:

            print(

                "PDF  : Not Generated"

            )

        print(

            "=" * 60

        )

        print()

        return result