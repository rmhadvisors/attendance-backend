import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_reset_password_email(to_email: str, reset_link: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port_str = os.getenv("SMTP_PORT", "587").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_username).strip()

    # Determine fallback / test mode if SMTP credentials are not configured
    if not smtp_host or not smtp_username or not smtp_password:
        print("\n" + "="*80)
        print("⚠️  SMTP CONFIGURATION MISSING")
        print(f"To: {to_email}")
        print(f"Reset Link: {reset_link}")
        print("="*80 + "\n")
        return True

    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        smtp_port = 587

    # Create Message
    message = MIMEMultipart("alternative")
    message["Subject"] = "Reset your GLR Attendance Password"
    message["From"] = smtp_from
    message["To"] = to_email

    # Plain text version
    text_content = f"""
Hello,

You requested a password reset for your GLR Attendance account.
Please click the link below to reset your password (valid for 1 hour):

{reset_link}

If you did not request this, you can safely ignore this email.

Best regards,
GLR Attendance Team
"""

    # HTML version (styled beautifully)
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #f6f7f9;
      color: #1a1a1a;
      margin: 0;
      padding: 0;
    }}
    .container {{
      max-width: 500px;
      margin: 40px auto;
      background: #ffffff;
      padding: 32px;
      border-radius: 16px;
      border: 1px border #e5e7eb;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    .logo {{
      display: inline-block;
      background-color: #059669;
      color: #ffffff;
      font-size: 14px;
      font-weight: bold;
      padding: 8px 16px;
      border-radius: 8px;
      text-transform: uppercase;
      margin-bottom: 24px;
    }}
    h1 {{
      font-size: 22px;
      font-weight: 800;
      margin-top: 0;
      color: #0f172a;
    }}
    p {{
      font-size: 14px;
      line-height: 1.6;
      color: #4b5563;
    }}
    .button {{
      display: block;
      text-align: center;
      background-color: #059669;
      color: #ffffff !important;
      text-decoration: none;
      font-weight: 700;
      font-size: 14px;
      padding: 12px 24px;
      border-radius: 8px;
      margin: 28px 0;
    }}
    .footer {{
      font-size: 11px;
      color: #9ca3af;
      border-top: 1px solid #f3f4f6;
      padding-top: 16px;
      margin-top: 28px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">GLR</div>
    <h1>Reset your password</h1>
    <p>You requested a password reset for your GLR Attendance account. Click the button below to set a new password. This link is only valid for 1 hour.</p>
    
    <a href="{reset_link}" class="button">Reset Password</a>
    
    <p>If the button doesn't work, you can copy and paste this URL into your browser:</p>
    <p style="word-break: break-all; font-size: 12px; color: #6b7280; font-family: monospace;">{reset_link}</p>
    
    <p>If you did not request a password reset, you can safely ignore this email.</p>
    
    <div class="footer">
      This is an automated system email from GLR Attendance. Please do not reply directly to this message.
    </div>
  </div>
</body>
</html>
"""

    # Attach parts
    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    try:
        # Establish connection based on port
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(smtp_username, smtp_password)
        server.sendmail(smtp_from, to_email, message.as_string())
        server.close()
        return True
    except Exception as e:
        print(f"❌ Error sending password reset email: {e}")
        # Log to stdout so the link isn't lost if the SMTP attempt fails in test/development
        print("\n" + "="*80)
        print("⚠️  SMTP TRANSMISSION FAILED — FALLBACK URL:")
        print(f"To: {to_email}")
        print(f"Reset Link: {reset_link}")
        print("="*80 + "\n")
        return False
