import smtplib

from django.core.mail.backends.locmem import EmailBackend


class FlakyBackend(EmailBackend):
    """An SMTP relay that can be switched off."""

    down = False

    def send_messages(self, email_messages):
        if FlakyBackend.down:
            msg = "Connection refused"
            raise smtplib.SMTPConnectError(421, msg)
        return super().send_messages(email_messages)
