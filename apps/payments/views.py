import json
import logging
from decimal import Decimal
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import permissions, status
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiResponse
from ..common.responses import success_response
from ..authentication.permissions import IsCustomer
from ..orders.models import Order
from ..orders.serializers import OrderDetailSerializer
from ..orders.services import OrderService
from .models import PaymentRecord
from .serializers import PaymentVerifyRequestSerializer
from .services import RazorpayService

logger = logging.getLogger(__name__)


class RazorpayPaymentVerifyView(APIView):
    """
    Submits Razorpay client telemetry for server-side cryptographic signature validation.
    Advances order from PENDING_PAYMENT to CONFIRMED.
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Verify Razorpay Payment Signature",
        request=PaymentVerifyRequestSerializer,
        responses={200: OrderDetailSerializer}
    )
    def post(self, request):
        serializer = PaymentVerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_id = serializer.validated_data['order_id']
        try:
            order = Order.objects.get(id=order_id, customer=request.user)
        except Order.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Order not found or access denied.")

        verified_order = OrderService.verify_razorpay_payment(
            order=order,
            razorpay_order_id=serializer.validated_data['razorpay_order_id'],
            razorpay_payment_id=serializer.validated_data['razorpay_payment_id'],
            razorpay_signature=serializer.validated_data['razorpay_signature']
        )

        return success_response(
            data=OrderDetailSerializer(verified_order).data,
            message="Payment verified successfully. Order confirmed."
        )


class RazorpayWebhookView(APIView):
    """
    Asynchronous server-to-server webhook endpoint from Razorpay.
    Validates webhook signature and idempotently confirms payments.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Razorpay Webhook Handler",
        responses={200: OpenApiResponse(description="Webhook acknowledged")}
    )
    def post(self, request):
        signature = request.META.get('HTTP_X_RAZORPAY_SIGNATURE', '')
        raw_body = request.body.decode('utf-8')

        is_valid = RazorpayService.verify_webhook_signature(raw_body=raw_body, signature=signature)
        if not is_valid:
            logger.warning("Rejected unverified Razorpay webhook with signature: %s", signature)
            return success_response(
                data={},
                message="Invalid webhook signature.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            payload = json.loads(raw_body)
            event = payload.get('event')
            logger.info("Processing Razorpay webhook event: %s", event)

            if event == 'payment.captured':
                payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
                razorpay_order_id = payment_entity.get('order_id')
                razorpay_payment_id = payment_entity.get('id')

                payment_record = PaymentRecord.objects.filter(razorpay_order_id=razorpay_order_id).first()
                if payment_record:
                    amount_paise = payment_entity.get('amount')
                    expected_paise = int(payment_record.amount_inr * 100)
                    if (payment_entity.get('currency') != payment_record.currency
                            or amount_paise != expected_paise
                            or payment_entity.get('status') != 'captured'):
                        logger.warning("Rejected webhook with mismatched amount, currency, or status")
                    else:
                        with transaction.atomic():
                            payment_record = PaymentRecord.objects.select_for_update().get(pk=payment_record.pk)
                            order = Order.objects.select_for_update().get(pk=payment_record.order_id)
                            if (payment_record.status != PaymentRecord.PaymentStatus.CAPTURED
                                    and order.status == Order.OrderStatus.PENDING_PAYMENT
                                    and order.payment_method == Order.PaymentMethod.RAZORPAY):
                                payment_record.razorpay_payment_id = razorpay_payment_id
                                payment_record.status = PaymentRecord.PaymentStatus.CAPTURED
                                payment_record.verified_at = timezone.now()
                                payment_record.raw_webhook_payload = payload
                                payment_record.save()
                                order.status = Order.OrderStatus.CONFIRMED
                                order.payment_status = Order.PaymentStatus.CAPTURED
                                order.save(update_fields=['status', 'payment_status', 'updated_at'])
                                logger.info("Webhook captured payment for order %s", order.order_number)

            elif event == 'payment.failed':
                payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
                razorpay_order_id = payment_entity.get('order_id')

                payment_record = PaymentRecord.objects.filter(razorpay_order_id=razorpay_order_id).first()
                if payment_record and payment_record.status != PaymentRecord.PaymentStatus.CAPTURED:
                    payment_record.status = PaymentRecord.PaymentStatus.FAILED
                    payment_record.raw_webhook_payload = payload
                    payment_record.save(update_fields=['status', 'raw_webhook_payload', 'updated_at'])

                    order = payment_record.order
                    order.payment_status = Order.PaymentStatus.FAILED
                    order.save(update_fields=['payment_status', 'updated_at'])
        except Exception as e:
            logger.exception("Error processing webhook payload: %s", str(e))

        return success_response(data={}, message="Webhook processed.")
