import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape


class EmailConfigurationError(ValueError):
    """Raised when required email-provider configuration is missing or invalid."""


class EmailDeliveryError(RuntimeError):
    """Provider failure with a safe diagnostic that never includes secrets."""

    def __init__(
        self,
        stage,
        diagnostic,
        exception_type,
        provider_status=None
    ):
        super().__init__(diagnostic)
        self.stage = stage
        self.diagnostic = diagnostic
        self.exception_type = exception_type
        self.provider_status = provider_status


def is_valid_email(value):
    if not isinstance(value, str):
        return False
    address = value.strip()
    parsed_address = parseaddr(address)[1]
    if parsed_address != address or address.count('@') != 1:
        return False
    local_part, domain = address.rsplit('@', 1)
    return bool(
        local_part
        and domain
        and '.' in domain
        and not domain.startswith('.')
        and not domain.endswith('.')
        and not any(character.isspace() for character in address)
    )


def _format_time(class_start):
    return datetime.strptime(class_start, '%H:%M').strftime('%I:%M %p').lstrip('0')


def build_class_reminder_message(
    student_name,
    class_row,
    class_start,
    reminder_at,
    class_date=None,
    *,
    reminder_minutes
):
    formatted_start = _format_time(class_start)
    subject_name = str(class_row['subject'])
    room = str(class_row.get('room') or 'Not specified')
    student_name = student_name or 'Student'
    if isinstance(class_date, datetime):
        class_date = class_date.date()
    if class_date is None:
        class_date = date.today()
    formatted_date = class_date.strftime('%B %d, %Y')
    safe_name = escape(str(student_name))
    safe_subject = escape(subject_name)
    safe_date = escape(formatted_date)
    safe_room = escape(room)
    message = EmailMessage()
    message['Subject'] = 'ClassBuddy – Upcoming Class Reminder'
    message.set_content(
        f"""Hello {student_name},

This is a reminder about your upcoming class.

Subject: {subject_name}
Date: {formatted_date}
Time: {formatted_start}
Room: {room}

Your class will start in {reminder_minutes} minutes.

Please be ready for your class.

Regards,
ClassBuddy – Smart Class Attendance & Reminder System
"""
    )
    message.add_alternative(
        f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        @media only screen and (max-width: 600px) {{
            .email-card {{ padding: 24px 18px !important; }}
            .email-title {{ font-size: 22px !important; }}
        }}
    </style>
</head>
<body style="margin:0;padding:24px 12px;background:#f3f6fb;
             font-family:Arial,Helvetica,sans-serif;color:#172033;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="max-width:600px;margin:0 auto;">
        <tr><td class="email-card"
            style="padding:36px 40px;background:#ffffff;border:1px solid #e5eaf2;
                   border-radius:16px;box-shadow:0 12px 32px rgba(23,32,51,.08);">
            <p style="margin:0 0 8px;color:#315ed0;font-size:12px;
                      font-weight:bold;letter-spacing:1.5px;">CLASSBUDDY</p>
            <h1 class="email-title"
                style="margin:0 0 18px;font-size:26px;line-height:1.3;">
                Your class starts soon
            </h1>
            <p style="margin:0 0 22px;line-height:1.6;">Hello {safe_name},<br>
                This is a reminder about your upcoming class.</p>
            <table role="presentation" width="100%" cellspacing="0"
                   cellpadding="0"
                   style="border-collapse:collapse;background:#f7f9fe;
                          border-radius:10px;">
                <tr><td style="padding:11px 14px;color:#64748b;">Subject</td>
                    <td style="padding:11px 14px;text-align:right;
                               font-weight:bold;">{safe_subject}</td></tr>
                <tr><td style="padding:11px 14px;color:#64748b;">Date</td>
                    <td style="padding:11px 14px;text-align:right;
                               font-weight:bold;">{safe_date}</td></tr>
                <tr><td style="padding:11px 14px;color:#64748b;">Time</td>
                    <td style="padding:11px 14px;text-align:right;
                               font-weight:bold;">{escape(formatted_start)}</td></tr>
                <tr><td style="padding:11px 14px;color:#64748b;">Room</td>
                    <td style="padding:11px 14px;text-align:right;
                               font-weight:bold;">{safe_room}</td></tr>
            </table>
            <p style="margin:22px 0 0;line-height:1.6;">
                Your class will start in {reminder_minutes} minutes.</p>
            <p style="margin:22px 0;line-height:1.6;">
                Please be ready for your class.</p>
            <p style="margin:0;color:#64748b;line-height:1.6;">
                Regards,<br><strong style="color:#172033;">ClassBuddy – Smart
                Class Attendance &amp; Reminder System</strong>
            </p>
        </td></tr>
    </table>
</body>
</html>
""",
        subtype='html'
    )
    return message


def send_email(config, recipient, message):
    server = config.get('MAIL_SERVER')
    port = config.get('MAIL_PORT', 587)
    use_tls = config.get('MAIL_USE_TLS', True)
    username = config.get('MAIL_USERNAME')
    password = config.get('MAIL_PASSWORD')
    sender = config.get('MAIL_DEFAULT_SENDER')
    sender_name = config.get('MAIL_SENDER_NAME', 'ClassBuddy')

    missing_settings = [
        key
        for key, value in (
            ('MAIL_SERVER', server),
            ('MAIL_USERNAME', username),
            ('MAIL_PASSWORD', password),
            ('MAIL_DEFAULT_SENDER', sender)
        )
        if not value
    ]
    if missing_settings:
        raise EmailConfigurationError(
            'Missing SMTP email configuration: '
            + ', '.join(missing_settings)
            + '.'
        )
    if not is_valid_email(sender):
        raise EmailConfigurationError(
            'MAIL_DEFAULT_SENDER must be a valid email address.'
        )
    if not is_valid_email(recipient):
        raise EmailConfigurationError(
            'The recipient address must be a valid email address.'
        )

    try:
        message['From'] = formataddr((sender_name, sender))
        message['To'] = recipient
        with smtplib.SMTP(
            server,
            port,
            timeout=config.get('MAIL_TIMEOUT', 15)
        ) as smtp:
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(username, password)
            refused_recipients = smtp.send_message(
                message,
                from_addr=sender,
                to_addrs=[recipient]
            )
            if recipient in refused_recipients:
                status = refused_recipients[recipient][0]
                raise EmailDeliveryError(
                    'SMTP delivery',
                    f'The SMTP server refused the recipient '
                    f'(SMTP status {status}).',
                    'SMTPRecipientsRefused',
                    status
                )
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError(
            'SMTP authentication',
            'The SMTP server rejected the configured credentials.',
            type(exc).__name__,
            exc.smtp_code
        ) from exc
    except EmailDeliveryError:
        raise
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(
            'SMTP delivery',
            'The SMTP server could not deliver the email.',
            type(exc).__name__,
            getattr(exc, 'smtp_code', None)
        ) from exc
    except OSError as exc:
        raise EmailDeliveryError(
            'SMTP connection',
            'Could not connect to the configured SMTP server.',
            type(exc).__name__
        ) from exc


def send_class_reminder(
    config,
    student_name,
    recipient,
    class_row,
    reminder_at,
    class_start
):
    reminder_minutes = max(
        0,
        int((class_start - reminder_at).total_seconds() // 60)
    )
    message = build_class_reminder_message(
        student_name,
        class_row,
        class_row['start_time'],
        reminder_at.strftime('%H:%M'),
        class_start,
        reminder_minutes=reminder_minutes
    )
    send_email(config, recipient, message)


def send_test_email(config, recipient, student_name):
    message = EmailMessage()
    message['Subject'] = 'ClassBuddy email configuration test'
    message.set_content(
        f"""Hello {student_name or 'Student'},

This test email confirms that ClassBuddy can send email notifications to
your registered address.

Thank you,
ClassBuddy – Smart Class Attendance and Reminder System
"""
    )
    send_email(config, recipient, message)
