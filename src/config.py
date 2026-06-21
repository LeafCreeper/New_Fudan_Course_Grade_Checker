"""Runtime configuration: constants + environment-derived settings.

Keeps every host/endpoint in one place so both WebVPN and direct
sessions read the same base URLs.
"""

import os

# ── Fudan host bases ──────────────────────────────────────────────────────
WEBVPN_BASE = "https://webvpn.fudan.edu.cn"
IDP_BASE = "https://id.fudan.edu.cn"
GRADE_BASE = "https://fdjwgl.fudan.edu.cn"

# Grade-system entry points (under GRADE_BASE)
GRADE_SHEET_PATH = "/student/for-std/grade/sheet"
GRADE_HOME_URL = f"{GRADE_BASE}{GRADE_SHEET_PATH}/"

# ── WebVPN AES key/IV (Fudan uses the same 16-byte value for both) ──────
# Reverse-engineered from the WebVPN JS bundle; identical to what the
# icourse_subscriber project uses.  Fudan has not rotated it in years.
WEBVPN_AES_KEY = b"wrdvpnisthebest!"
WEBVPN_AES_IV = b"wrdvpnisthebest!"

# ── Credentials (injected via env, never hard-coded) ────────────────────
STUDENT_ID = os.environ.get("StuId", "")
PASSWORD = os.environ.get("UISPsw", "")

# ── SMTP / email ─────────────────────────────────────────────────────────
SMTP_EMAIL = os.environ.get("QQ_EMAIL_SENDER", "")
SMTP_PASSWORD = os.environ.get("QQ_SMTP", "")
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465  # SSL

# Recipient defaults to the student's Fudan mailbox.
RECEIVER_EMAIL = (
    os.environ.get("RECEIVER_EMAIL")
    or (f"{STUDENT_ID}@m.fudan.edu.cn" if STUDENT_ID else "")
)

# ── Storage ──────────────────────────────────────────────────────────────
GRADES_FILE = os.environ.get("GRADES_FILE", "grades_encrypted.json")

# ── Access mode ──────────────────────────────────────────────────────────
# Default ON: GitHub Actions / off-campus hosts cannot reach fdjwgl directly,
# so WebVPN is the safe default.  Set USE_DIRECT=1 to bypass the VPN when
# running on the campus network (e.g. local testing).
USE_WEBVPN = os.environ.get("USE_DIRECT", "").strip().lower() not in ("1", "true", "yes")

# ── Ranking scope ────────────────────────────────────────────────────────
# Department ranking needs zero config — the server infers the student's
# department/grade from the grade-sheet ID alone.  Major ranking needs the
# internal majorAssoc id; leave blank to skip major ranking.
MAJOR_ASSOC = os.environ.get("MAJOR_ASSOC", "")

# ── Browser fingerprint ──────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
