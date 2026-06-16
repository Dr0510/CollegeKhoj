"""
Email service using Resend for sending verification codes and password reset links.

Requires the RESEND_API_KEY environment variable to be set.
Uses Resend's Python SDK — see https://resend.com/docs/send-with-python
"""

import os
import logging
import resend

logger = logging.getLogger(__name__)

# Resend sender address.
# For testing with the free tier, Resend requires you send *to* your own email only,
# and the verified "from" address on your Resend account.
#
# Production: set RESEND_FROM in .env, e.g.
#   RESEND_FROM="CollegeKhoj <noreply@yourverifieddomain.com>"
# and add your domain at https://resend.com/domains
#
# Fallback: onboarding@resend.dev (works for testing to your account email).
RESEND_FROM = os.environ.get("RESEND_FROM", "CollegeKhoj <onboarding@resend.dev>")

_app_name = "CollegeKhoj"


def _init_resend() -> bool:
    """Initialise the Resend API key from environment.
    Returns True if the key is present, False otherwise.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.warning("RESEND_API_KEY is not set — emails will NOT be sent.")
        return False
    resend.api_key = api_key
    return True


# ── Public helpers ────────────────────────────────────────────────────────────


def send_verification_email(to_email: str, code: str) -> bool:
    """Send a 6-digit verification code to the given email address.

    Returns True if Resend accepted the send request, False otherwise.
    """
    if not _init_resend():
        return False

    subject = f"Your {_app_name} verification code"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f4f6f9; margin: 0; padding: 0; }}
            .container {{ max-width: 480px; margin: 32px auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
            .header {{ background: linear-gradient(135deg, #4f8ef7 0%, #6c5ce7 100%); padding: 32px 24px; text-align: center; }}
            .header h1 {{ color: #fff; font-size: 22px; margin: 0; font-weight: 700; }}
            .header .icon {{ font-size: 36px; margin-bottom: 8px; }}
            .body {{ padding: 32px 24px; }}
            .body p {{ color: #4a5568; font-size: 15px; line-height: 1.6; margin: 0 0 16px; }}
            .code {{ display: block; margin: 24px auto; padding: 16px 32px; background: #f0f4ff; border-radius: 12px; font-size: 36px; font-weight: 800; letter-spacing: 8px; text-align: center; color: #4f8ef7; font-family: 'Courier New', monospace; }}
            .footer {{ padding: 20px 24px; background: #f8fafc; text-align: center; }}
            .footer p {{ color: #94a3b8; font-size: 12px; margin: 0; }}
            .footer a {{ color: #4f8ef7; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="icon">🎓</div>
                <h1>{_app_name}</h1>
            </div>
            <div class="body">
                <p>Hi there,</p>
                <p>Thanks for signing up! Use the verification code below to complete your registration. This code is valid for <strong>10 minutes</strong>.</p>
                <div class="code">{code}</div>
                <p>If you didn't create an account with {_app_name}, you can safely ignore this email.</p>
            </div>
            <div class="footer">
                <p>&copy; {_app_name} &middot; Your AI-powered college counsellor</p>
                <p><a href="mailto:support@collegekhoj.com">Contact support</a></p>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        params = {
            "from": RESEND_FROM,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        response = resend.Emails.send(params)
        logger.info(f"Verification email sent to {to_email} — Resend id: {response.get('id')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email to {to_email}: {e}")
        return False


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Send a password reset link to the given email address.

    Returns True if Resend accepted the send request, False otherwise.
    """
    if not _init_resend():
        return False

    subject = f"Reset your {_app_name} password"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f4f6f9; margin: 0; padding: 0; }}
            .container {{ max-width: 480px; margin: 32px auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
            .header {{ background: linear-gradient(135deg, #6c5ce7 0%, #e17055 100%); padding: 32px 24px; text-align: center; }}
            .header h1 {{ color: #fff; font-size: 22px; margin: 0; font-weight: 700; }}
            .header .icon {{ font-size: 36px; margin-bottom: 8px; }}
            .body {{ padding: 32px 24px; }}
            .body p {{ color: #4a5568; font-size: 15px; line-height: 1.6; margin: 0 0 16px; }}
            .btn {{ display: inline-block; margin: 16px 0; padding: 14px 36px; background: linear-gradient(135deg, #4f8ef7 0%, #6c5ce7 100%); color: #fff !important; text-decoration: none; border-radius: 10px; font-weight: 600; font-size: 15px; }}
            .btn:hover {{ opacity: 0.9; }}
            .fallback-link {{ display: block; margin-top: 12px; font-size: 12px; color: #94a3b8; word-break: break-all; }}
            .footer {{ padding: 20px 24px; background: #f8fafc; text-align: center; }}
            .footer p {{ color: #94a3b8; font-size: 12px; margin: 0; }}
            .footer a {{ color: #4f8ef7; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="icon">🔐</div>
                <h1>{_app_name}</h1>
            </div>
            <div class="body">
                <p>Hi there,</p>
                <p>We received a request to reset the password for your {_app_name} account. Click the button below to set a new password. This link is valid for <strong>1 hour</strong>.</p>
                <p style="text-align: center;">
                    <a class="btn" href="{reset_link}">Reset password</a>
                </p>
                <p>If you didn't request a password reset, you can safely ignore this email — your account is secure.</p>
                <p>If the button doesn't work, copy and paste this link into your browser:</p>
                <span class="fallback-link">{reset_link}</span>
            </div>
            <div class="footer">
                <p>&copy; {_app_name} &middot; Your AI-powered college counsellor</p>
                <p><a href="mailto:support@collegekhoj.com">Contact support</a></p>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        params = {
            "from": RESEND_FROM,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        response = resend.Emails.send(params)
        logger.info(f"Password reset email sent to {to_email} — Resend id: {response.get('id')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send password reset email to {to_email}: {e}")
        return False