import hmac
import hashlib
import uuid
import logging
from django.conf import settings
from decimal import Decimal
from django.conf import settings
from rest_framework.exceptions import ValidationError
import razorpay

logger = logging.getLogger(__name__)


class RazorpayService:
    """
    Razorpay integration manager handling order creation, HMAC-SHA256
    signature verification, and webhook authenticity validation.
    """

    @classmethod
    def get_client(cls):
        key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
        key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
        if not key_id or not key_secret or key_id.startswith('rzp_test_placeholder'):
            return None
        return razorpay.Client(auth=(key_id, key_secret))

    @classmethod
    def create_razorpay_order(cls, amount_inr: Decimal, receipt_order_number: str) -> str:
        """
        Creates an online order with Razorpay in paise (amount * 100).
        In sandbox/mock mode without live keys, returns a deterministic mock order ID.
        """
        client = cls.get_client()
        amount_paise = int(Decimal(str(amount_inr)) * 100)

        if not client:
            if not settings.DEBUG:
                raise ValidationError({"payment": ["Razorpay is not configured."]})
            mock_order_id = f"order_mock_{uuid.uuid4().hex[:14]}"
            logger.info(
                "[RAZORPAY SANDBOX] Generated simulated Razorpay Order ID: %s for %s (₹%s)",
                mock_order_id, receipt_order_number, amount_inr
            )
            return mock_order_id

        try:
            order_data = {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt_order_number,
                "payment_capture": 1
            }
            rp_order = client.order.create(data=order_data)
            return rp_order['id']
        except Exception as e:
            logger.exception("Failed to create Razorpay order: %s", str(e))
            raise ValidationError({"payment": [f"Payment gateway error: {str(e)}"]})

    @classmethod
    def verify_payment_signature(cls, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Validates HMAC-SHA256 signature against RAZORPAY_KEY_SECRET.
        """
        client = cls.get_client()

        # In Sandbox / Mock mode, accept testing signature
        if not client:
            if not settings.DEBUG:
                return False
            if razorpay_signature in ["simulated_valid_signature", "mock_signature_valid"]:
                return True
            # Also calculate HMAC if mock secret is present
            key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', 'mock_secret')
            payload = f"{razorpay_order_id}|{razorpay_payment_id}".encode('utf-8')
            expected_sig = hmac.new(key_secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected_sig, razorpay_signature)

        try:
            params_dict = {
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            }
            client.utility.verify_payment_signature(params_dict)
            return True
        except razorpay.errors.SignatureVerificationError:
            logger.warning("Razorpay signature verification failed for payment %s", razorpay_payment_id)
            return False
        except Exception as e:
            logger.exception("Unexpected error verifying Razorpay signature: %s", str(e))
            return False

    @classmethod
    def verify_webhook_signature(cls, raw_body: str, signature: str) -> bool:
        """
        Validates Razorpay webhook payload signature.
        """
        webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '')
        if webhook_secret == 'mock_webhook_secret' and not settings.DEBUG:
            return False
        if not webhook_secret or webhook_secret == 'mock_webhook_secret':
            # Sandbox test support
            if settings.DEBUG and signature == "valid_mock_webhook_signature":
                return True

        if not webhook_secret:
            return False

        try:
            client = cls.get_client()
            if client:
                client.utility.verify_webhook_signature(raw_body, signature, webhook_secret)
                return True
            else:
                expected_sig = hmac.new(
                    webhook_secret.encode('utf-8'),
                    raw_body.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()
                return hmac.compare_digest(expected_sig, signature)
        except Exception:
            return False
