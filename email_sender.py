"""
email_sender.py — Gmail API email sending utility
Separated from main.py to avoid circular imports with email_digest.py
"""

import logging
import base64
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

logger = logging.getLogger(__name__)


def send_email_report(
    subject: str,
    html_body: str,
    notify_email: str,
    credentials_file: str,
    token_file: str,
    scopes: list,
    attachments: list[str] | None = None,
) -> bool:
    """Send HTML email via Gmail API using stored OAuth2 token."""
    logger.info(f"Inside send_email_report for subject: {subject}")
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        logger.info("Checking credentials...")
        creds = None
        if Path(token_file).exists():
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)

        logger.info(f"Creds valid: {creds.valid if creds else 'None'}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing creds...")
                creds.refresh(Request())
            else:
                if not Path(credentials_file).exists():
                    logger.error("credentials.json not found — cannot send email")
                    return False
                logger.info("Running local server for auth...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(credentials_file), scopes
                )
                creds = flow.run_local_server(port=0)
            Path(token_file).write_text(creds.to_json())

        logger.info("Building gmail service...")
        service = build("gmail", "v1", credentials=creds)

        msg = MIMEMultipart("mixed" if attachments else "alternative")
        msg["Subject"] = subject
        msg["To"] = notify_email
        msg.attach(MIMEText(html_body, "html"))

        for path in attachments or []:
            p = Path(path)
            if not p.exists():
                logger.warning(f"Attachment not found, skipping: {p}")
                continue
            ctype, _ = mimetypes.guess_type(str(p))
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(p.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=p.name)
            msg.attach(part)

        logger.info("Sending message...")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info(f"Email report sent to {notify_email}")
        return True

    except ImportError:
        logger.error("google-api-python-client not installed.")
        return False
    except Exception as e:
        logger.exception(f"Email send failed: {e}")
        return False