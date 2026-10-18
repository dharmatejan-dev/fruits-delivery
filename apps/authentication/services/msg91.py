import re
import secrets
import logging
import requests
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
from rest_framework.exceptions import ValidationError
from ..models import OTPRecord

logger = logging.getLogger(__name__)

INDIAN_PHONE_REGEX = re.compile(r'^\+91[6-9]\d{9}$')


def normalize_indian_phone(phone_input: str) -> str:
    """
    Cleans and normalizes an Indian phone number to E.164 standard: +91XXXXXXXXXX.
    Rejects non-Indian or improperly formatted numbers.
    """
    if not phone_input:
        raise ValidationError({"phone_number": ["Phone number is required."]})

    # Strip whitespaces, dashes, dots, brackets
    cleaned = re.sub(r'[\s\-\(\)\.]', '', str(phone_input).strip())

    if cleaned.startswith('0'):
        cleaned = cleaned[1:]
    if cleaned.startswith('91') and len(cleaned) == 12:
        cleaned = '+' + cleaned
    elif not cleaned.startswith('+91') and len(cleaned) == 10:
        cleaned = '+91' + cleaned

    if not INDIAN_PHONE_REGEX.match(cleaned):
        raise ValidationError({
            "phone_number": ["Enter a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9."]
        })

    return cleaned


class MSG91OTPService:
    """
    Production-grade MSG91 OTP Gateway service with rate limiting,
    cryptographic hashing, and sandbox/mock fallback.
    """
    OTP_EXPIRY_MINUTES = 5
    MAX_ATTEMPTS = 5
    RATE_LIMIT_WINDOW_MINUTES = 10
    MAX_REQUESTS_IN_WINDOW = 3
    RESEND_COOLDOWN_SECONDS = 60

    @classmethod
    def generate_otp(cls) -> str:
        """Generates a secure 6-digit numeric OTP."""
        return str(secrets.randbelow(900000) + 100000)

    @classmethod
    def request_otp(cls, phone_number: str, ip_address: str = None) -> dict:
        """
        Validates rate limits, creates a hashed OTP record, and dispatches via MSG91.
        """
        normalized_phone = normalize_indian_phone(phone_number)
        now = timezone.now()

        # 1. Rate limiting: Max 3 requests in 10 minutes
        window_start = now - timedelta(minutes=cls.RATE_LIMIT_WINDOW_MINUTES)
        recent_requests_count = OTPRecord.objects.filter(
            phone_number=normalized_phone,
            created_at__gte=window_start
        ).count()

        if recent_requests_count >= cls.MAX_REQUESTS_IN_WINDOW:
            raise ValidationError({
                "phone_number": [
                    f"Too many OTP requests. Please wait {cls.RATE_LIMIT_WINDOW_MINUTES} minutes before trying again."
                ]
            })

        # 2. Resend cooldown: Minimum 60 seconds between requests
        latest_record = OTPRecord.objects.filter(phone_number=normalized_phone).order_by('-created_at').first()
        if latest_record:
            seconds_since_last = (now - latest_record.created_at).total_seconds()
            if seconds_since_last < cls.RESEND_COOLDOWN_SECONDS:
                wait_time = int(cls.RESEND_COOLDOWN_SECONDS - seconds_since_last)
                raise ValidationError({
                    "phone_number": [f"Please wait {wait_time} seconds before requesting a new OTP."]
                })

        # 3. Generate and hash OTP
        # Local development uses a predictable code so a physical-device debug
        # build can authenticate without MSG91 credentials. Production always
        # generates a cryptographically random OTP.
        plain_otp = '1234' if settings.DEBUG else cls.generate_otp()
        otp_hash = make_password(plain_otp)
        expires_at = now + timedelta(minutes=cls.OTP_EXPIRY_MINUTES)

        otp_record = OTPRecord.objects.create(
            phone_number=normalized_phone,
            otp_hash=otp_hash,
            expires_at=expires_at,
            ip_address=ip_address
        )

        # 4. Dispatch via MSG91
        cls._send_via_msg91(normalized_phone, plain_otp, otp_record)

        return {
            "phone_number": normalized_phone,
            "expires_in_seconds": cls.OTP_EXPIRY_MINUTES * 60,
            "resend_cooldown_seconds": cls.RESEND_COOLDOWN_SECONDS
        }

    @classmethod
    def verify_otp(cls, phone_number: str, otp_input: str) -> bool:
        """
        Verifies client-provided OTP against the latest unverified record.
        """
        normalized_phone = normalize_indian_phone(phone_number)
        if not otp_input or not str(otp_input).strip():
            raise ValidationError({"otp": ["OTP is required."]})

        otp_input = str(otp_input).strip()
        now = timezone.now()

        latest_record = OTPRecord.objects.filter(
            phone_number=normalized_phone,
            is_verified=False
        ).order_by('-created_at').first()

        if not latest_record:
            raise ValidationError({"otp": ["No active OTP found. Please request an OTP first."]})

        # Check expiration
        if latest_record.expires_at < now:
            raise ValidationError({"otp": ["OTP has expired. Please request a new OTP."]})

        # Check maximum attempts limit
        if latest_record.attempts_count >= cls.MAX_ATTEMPTS:
            raise ValidationError({
                "otp": ["Maximum verification attempts exceeded. Please request a new OTP."]
            })

        # Verify hash
        is_valid = check_password(otp_input, latest_record.otp_hash)
        if not is_valid:
            latest_record.attempts_count += 1
            latest_record.save(update_fields=['attempts_count'])
            remaining = cls.MAX_ATTEMPTS - latest_record.attempts_count
            if remaining <= 0:
                raise ValidationError({
                    "otp": ["Maximum verification attempts exceeded. Please request a new OTP."]
                })
            raise ValidationError({
                "otp": [f"Invalid OTP entered. {remaining} attempt(s) remaining."]
            })

        # Mark as verified
        latest_record.is_verified = True
        latest_record.save(update_fields=['is_verified'])
        return True

    @classmethod
    def _send_via_msg91(cls, phone_number: str, plain_otp: str, record: OTPRecord):
        """
        Makes HTTPS request to MSG91 Send OTP endpoint.
        In sandbox/mock mode, logs the OTP safely for local development and tests.
        """
        auth_key = getattr(settings, 'MSG91_AUTH_KEY', '')
        template_id = getattr(settings, 'MSG91_OTP_TEMPLATE_ID', '')

        # Check for Sandbox / Development mode
        is_sandbox = (
            not auth_key
            or auth_key.startswith('mock_')
            or getattr(settings, 'DEBUG', False)
        )

        if is_sandbox:
            logger.info(
                "[MSG91 SANDBOX OTP] Phone: %s | OTP: %s (Simulated Delivery)",
                phone_number,
                plain_otp
            )
            # Tag record for verification in testing environment
            record.session_id = f"sandbox_session_{plain_otp}"
            record.save(update_fields=['session_id'])
            return

        # Live MSG91 API invocation
        url = "https://control.msg91.com/api/v5/otp"
        headers = {
            "authkey": auth_key,
            "Content-Type": "application/json"
        }
        # Strip '+' for MSG91 payload
        mobile_digits = phone_number.replace('+', '')
        payload = {
            "template_id": template_id,
            "mobile": mobile_digits,
            "otp": plain_otp,
            "sender": getattr(settings, 'MSG91_SENDER_ID', 'ORCHRD')
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            response_data = response.json()
            if response.status_code == 200 and response_data.get('type') == 'success':
                record.session_id = str(response_data.get('request_id', ''))
                record.save(update_fields=['session_id'])
            else:
                logger.error("MSG91 API error response: %s", response.text)
        except Exception as e:
            logger.exception("Failed to connect to MSG91 API: %s", str(e))
