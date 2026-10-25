import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..models import OTPRecord
from .otp_providers import get_otp_provider

logger = logging.getLogger(__name__)

INDIAN_PHONE_REGEX = re.compile(r'^\+91[6-9]\d{9}$')


def normalize_indian_phone(phone_input: str) -> str:
    """Normalize a valid Indian mobile number to E.164 (+91XXXXXXXXXX)."""
    if not phone_input:
        raise ValidationError({'phone_number': ['Phone number is required.']})

    cleaned = re.sub(r'[\s\-\(\)\.]', '', str(phone_input).strip())
    if cleaned.startswith('0'):
        cleaned = cleaned[1:]
    if cleaned.startswith('91') and len(cleaned) == 12:
        cleaned = '+' + cleaned
    elif not cleaned.startswith('+91') and len(cleaned) == 10:
        cleaned = '+91' + cleaned

    if not INDIAN_PHONE_REGEX.match(cleaned):
        raise ValidationError({
            'phone_number': [
                'Enter a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.'
            ]
        })
    return cleaned


class CustomerOTPService:
    """Rate-limited customer OTP workflow backed by a server-selected provider."""

    OTP_EXPIRY_MINUTES = 5
    MAX_ATTEMPTS = 5
    RATE_LIMIT_WINDOW_MINUTES = 10
    MAX_REQUESTS_IN_WINDOW = 3
    RESEND_COOLDOWN_SECONDS = 60

    @classmethod
    def generate_otp(cls) -> str:
        """Generate a cryptographically secure six-digit code."""
        import secrets

        return str(secrets.randbelow(900000) + 100000)

    @classmethod
    def request_otp(cls, phone_number: str, ip_address: str = None) -> dict:
        normalized_phone = normalize_indian_phone(phone_number)
        now = timezone.now()

        recent_requests = OTPRecord.objects.filter(
            phone_number=normalized_phone,
            created_at__gte=now - timedelta(minutes=cls.RATE_LIMIT_WINDOW_MINUTES),
        ).count()
        if recent_requests >= cls.MAX_REQUESTS_IN_WINDOW:
            raise ValidationError({
                'phone_number': [
                    f'Too many OTP requests. Please wait '
                    f'{cls.RATE_LIMIT_WINDOW_MINUTES} minutes before trying again.'
                ]
            })

        latest_record = OTPRecord.objects.filter(
            phone_number=normalized_phone
        ).order_by('-created_at').first()
        if latest_record:
            elapsed = (now - latest_record.created_at).total_seconds()
            if elapsed < cls.RESEND_COOLDOWN_SECONDS:
                wait_seconds = int(cls.RESEND_COOLDOWN_SECONDS - elapsed)
                raise ValidationError({
                    'phone_number': [
                        f'Please wait {wait_seconds} seconds before requesting a new OTP.'
                    ]
                })

        provider = get_otp_provider()
        plain_otp = provider.generate_code()
        otp_record = OTPRecord.objects.create(
            phone_number=normalized_phone,
            otp_hash=make_password(plain_otp) if plain_otp else '',
            provider=provider.name,
            expires_at=now + timedelta(minutes=cls.OTP_EXPIRY_MINUTES),
            ip_address=ip_address,
        )

        try:
            provider_result = provider.send(
                normalized_phone,
                client_ip=ip_address,
                code=plain_otp,
            )
        except Exception:
            # Keep the request in the rate-limit window, but ensure it can never
            # be verified after the provider rejected or failed the send.
            otp_record.expires_at = now
            otp_record.save(update_fields=['expires_at'])
            raise

        otp_record.session_id = provider_result.get('session_id', '')[:100]
        otp_record.save(update_fields=['session_id'])

        result = {
            'phone_number': normalized_phone,
            'expires_in_seconds': cls.OTP_EXPIRY_MINUTES * 60,
            'resend_cooldown_seconds': cls.RESEND_COOLDOWN_SECONDS,
            'otp_length': provider.code_length,
        }
        debug_otp = provider_result.get('debug_otp')
        if settings.DEBUG and debug_otp:
            result['debug_otp'] = debug_otp
        return result

    @classmethod
    def verify_otp(cls, phone_number: str, otp_input: str) -> bool:
        normalized_phone = normalize_indian_phone(phone_number)
        submitted_code = str(otp_input or '').strip()
        if not submitted_code:
            raise ValidationError({'otp': ['OTP is required.']})

        otp_record = OTPRecord.objects.filter(
            phone_number=normalized_phone,
            is_verified=False,
        ).order_by('-created_at').first()
        if not otp_record:
            raise ValidationError({'otp': ['No active OTP found. Please request an OTP first.']})

        now = timezone.now()
        if otp_record.expires_at < now:
            raise ValidationError({'otp': ['OTP has expired. Please request a new OTP.']})
        if otp_record.attempts_count >= cls.MAX_ATTEMPTS:
            raise ValidationError({
                'otp': ['Maximum verification attempts exceeded. Please request a new OTP.']
            })

        provider = get_otp_provider(otp_record.provider)
        code_has_expected_shape = (
            submitted_code.isdigit() and len(submitted_code) == provider.code_length
        )
        if not code_has_expected_shape:
            is_valid = False
        elif provider.uses_remote_verification:
            is_valid = provider.verify(
                otp_record.session_id,
                submitted_code,
                otp_record.otp_hash,
            )
        else:
            is_valid = check_password(submitted_code, otp_record.otp_hash)

        if not is_valid:
            otp_record.attempts_count += 1
            otp_record.save(update_fields=['attempts_count'])
            remaining = cls.MAX_ATTEMPTS - otp_record.attempts_count
            if remaining <= 0:
                raise ValidationError({
                    'otp': ['Maximum verification attempts exceeded. Please request a new OTP.']
                })
            raise ValidationError({
                'otp': [f'Invalid verification code. {remaining} attempt(s) remaining.']
            })

        otp_record.is_verified = True
        otp_record.save(update_fields=['is_verified'])
        return True
