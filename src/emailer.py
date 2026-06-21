"""Email notifications via QQ SMTP.

A thin wrapper kept in its own module so the crawl logic doesn't carry
smtplib boilerplate.  All sender / recipient / auth defaults come from
:mod:`src.config`, matching the GitHub Actions secret names
(``QQ_EMAIL_SENDER``, ``QQ_SMTP``).
"""

import smtplib
from email.header import Header
from email.mime.text import MIMEText

from src import config


def send_email(
    subject: str,
    body: str,
    recipient_email: str = None,
    sender_email: str = None,
    smtp_auth_code: str = None,
) -> bool:
    """Send a plain-text email.  Returns True on success.

    Missing config is a soft-fail (prints + returns False) rather than a
    raise, so a misconfigured SMTP secret never aborts the whole crawl.
    """
    sender_email = sender_email or config.SMTP_EMAIL
    smtp_auth_code = smtp_auth_code or config.SMTP_PASSWORD
    recipient_email = recipient_email or config.RECEIVER_EMAIL

    if not sender_email or not smtp_auth_code:
        print("[-] Email skipped: QQ_EMAIL_SENDER / QQ_SMTP not configured.")
        return False
    if not recipient_email:
        print("[-] Email skipped: no recipient (need StuId or RECEIVER_EMAIL).")
        return False

    message = MIMEText(body, "plain", "utf-8")
    message["From"] = Header(f"Fudan Grade Monitor <{sender_email}>")
    message["To"] = Header(recipient_email)
    message["Subject"] = Header(subject)

    try:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT) as smtp:
            smtp.login(sender_email, smtp_auth_code)
            smtp.sendmail(sender_email, recipient_email, message.as_string())
        print(f"[+] Email sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"[-] Email send failed: {e}")
        return False
