"""Backward-compatible imports for code that used the previous service path."""

from .otp import CustomerOTPService, normalize_indian_phone

MSG91OTPService = CustomerOTPService

__all__ = ['CustomerOTPService', 'MSG91OTPService', 'normalize_indian_phone']
