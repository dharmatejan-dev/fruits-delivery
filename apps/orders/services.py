import uuid
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from ..authentication.models import UserAddress
from ..catalog.models import ProductVariant
from ..inventory.services import InventoryService
from .models import Order, OrderItem
from ..payments.models import PaymentRecord
from ..payments.services import RazorpayService
import logging

logger = logging.getLogger(__name__)


class OrderService:
    """
    Core order lifecycle manager handling server-side cart validation,
    atomic stock deduction, payment orchestration, and cancellation rules.
    """

    @classmethod
    def validate_cart(cls, items_payload: list) -> dict:
        """
        Server-side validation of cart items, recalculating live prices and stock availability.
        Prevents client-side price tampering.
        """
        if not items_payload or not isinstance(items_payload, list):
            raise ValidationError({"cart": ["Cart items must be a non-empty list."]})

        validated_items = []
        subtotal = Decimal('0.00')

        for entry in items_payload:
            variant_id = entry.get('variant_id')
            qty = entry.get('quantity', 1)

            try:
                quantity = int(qty)
                if quantity <= 0:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValidationError({"quantity": ["Item quantity must be a positive integer."]})

            try:
                variant = ProductVariant.objects.select_related('product', 'inventory').get(
                    id=variant_id,
                    is_active=True,
                    product__is_active=True
                )
            except ProductVariant.DoesNotExist:
                raise ValidationError({"variant_id": [f"Variant '{variant_id}' is unavailable or inactive."]})

            inventory = getattr(variant, 'inventory', None)
            available = inventory.available_stock if inventory else Decimal('0.00')

            if available < quantity and not getattr(inventory, 'allow_backorders', False):
                raise ValidationError({
                    "stock": [
                        f"Insufficient stock for '{variant.product.name} ({variant.display_unit})'. "
                        f"Requested: {quantity}, Available: {available}."
                    ]
                })

            unit_price = variant.selling_price_inr
            line_total = unit_price * quantity
            subtotal += line_total

            validated_items.append({
                "variant_id": str(variant.id),
                "product_name": variant.product.name,
                "display_unit": variant.display_unit,
                "unit_price_inr": str(unit_price),
                "quantity": quantity,
                "line_total_inr": str(line_total),
                "available_stock": str(available)
            })

        # Free delivery policy for Vizianagaram launch
        delivery_charge = Decimal('0.00')
        discount = Decimal('0.00')
        total_payable = subtotal + delivery_charge - discount

        return {
            "items": validated_items,
            "subtotal_inr": str(subtotal),
            "delivery_charge_inr": str(delivery_charge),
            "discount_inr": str(discount),
            "total_payable_inr": str(total_payable)
        }

    @classmethod
    def generate_order_number(cls) -> str:
        """
        Generates standard order identifier: OH-VZM-YYYYMMDD-XXXX
        """
        date_str = timezone.now().strftime("%Y%m%d")
        unique_suffix = uuid.uuid4().hex[:6].upper()
        return f"OH-VZM-{date_str}-{unique_suffix}"

    @classmethod
    def create_order(
        cls,
        customer,
        address_id: str,
        items_payload: list,
        payment_method: str = Order.PaymentMethod.RAZORPAY,
        customer_notes: str = "",
        idempotency_key=None,
    ) -> Order:
        """
        Atomically creates an order, locks and deducts stock, and initializes payment.
        """
        # Serialize checkout attempts per customer and return a prior result for retries.
        with transaction.atomic():
            type(customer).objects.select_for_update().get(pk=customer.pk)
            if idempotency_key:
                existing = Order.objects.filter(
                    customer=customer, idempotency_key=idempotency_key
                ).first()
                if existing:
                    return existing

        # 1. Validate shipping address
        try:
            address = customer.addresses.get(id=address_id, is_deleted=False)
        except UserAddress.DoesNotExist:
            raise ValidationError({"address_id": ["Invalid or deleted delivery address selected."]})

        # 2. Validate Cart
        cart_summary = cls.validate_cart(items_payload)
        subtotal = Decimal(cart_summary["subtotal_inr"])
        delivery_charge = Decimal(cart_summary["delivery_charge_inr"])
        discount = Decimal(cart_summary["discount_inr"])
        total_payable = Decimal(cart_summary["total_payable_inr"])

        # 3. Create frozen address snapshot
        address_snapshot = {
            "recipient_name": address.recipient_name,
            "recipient_phone": address.recipient_phone,
            "address_line1": address.address_line1,
            "address_line2": address.address_line2,
            "landmark": address.landmark,
            "locality": address.locality,
            "city": address.city,
            "state": address.state,
            "pincode": address.pincode,
            "latitude": str(address.latitude) if address.latitude else None,
            "longitude": str(address.longitude) if address.longitude else None,
        }

        order_number = cls.generate_order_number()

        # 4. Atomic Execution: stock deduction & order creation
        with transaction.atomic():
            type(customer).objects.select_for_update().get(pk=customer.pk)
            if idempotency_key:
                existing = Order.objects.filter(
                    customer=customer, idempotency_key=idempotency_key
                ).first()
                if existing:
                    return existing
            # Initial order state
            initial_status = (
                Order.OrderStatus.CONFIRMED
                if payment_method == Order.PaymentMethod.COD
                else Order.OrderStatus.PENDING_PAYMENT
            )
            initial_payment_status = Order.PaymentStatus.PENDING

            order = Order.objects.create(
                order_number=order_number,
                idempotency_key=idempotency_key,
                customer=customer,
                status=initial_status,
                payment_method=payment_method,
                payment_status=initial_payment_status,
                subtotal_inr=subtotal,
                delivery_charge_inr=delivery_charge,
                discount_inr=discount,
                total_payable_inr=total_payable,
                shipping_address_snapshot=address_snapshot,
                customer_notes=customer_notes.strip() if customer_notes else ""
            )

            # Deduct stock and build line items
            for item_data in cart_summary["items"]:
                variant_id = item_data["variant_id"]
                qty = item_data["quantity"]

                # Atomic deduction
                InventoryService.deduct_stock(variant_id=variant_id, quantity=Decimal(str(qty)))

                variant = ProductVariant.objects.get(id=variant_id)
                OrderItem.objects.create(
                    order=order,
                    variant=variant,
                    product_name_snapshot=item_data["product_name"],
                    unit_quantity_snapshot=item_data["display_unit"],
                    unit_price_inr=Decimal(item_data["unit_price_inr"]),
                    quantity=qty,
                    line_total_inr=Decimal(item_data["line_total_inr"])
                )

            # 5. Initialize Payment Record
            if payment_method == Order.PaymentMethod.RAZORPAY:
                razorpay_order_id = RazorpayService.create_razorpay_order(
                    amount_inr=total_payable,
                    receipt_order_number=order_number
                )
                PaymentRecord.objects.create(
                    order=order,
                    razorpay_order_id=razorpay_order_id,
                    amount_inr=total_payable,
                    status=PaymentRecord.PaymentStatus.CREATED
                )
            else:
                # COD
                PaymentRecord.objects.create(
                    order=order,
                    amount_inr=total_payable,
                    status=PaymentRecord.PaymentStatus.CREATED
                )

        logger.info(
            "Order %s created successfully for customer %s. Total: ₹%s [%s]",
            order.order_number, customer.phone_number, total_payable, payment_method
        )
        return order

    @classmethod
    def verify_razorpay_payment(
        cls,
        order: Order,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str
    ) -> Order:
        """
        Server-side cryptographic verification of Razorpay payment signature.
        Transitions order from PENDING_PAYMENT to CONFIRMED.
        """
        payment_record = getattr(order, 'payment_record', None)
        if not payment_record:
            raise ValidationError({"payment": ["No payment record found for this order."]})

        if payment_record.status == PaymentRecord.PaymentStatus.CAPTURED:
            # Already captured (idempotent)
            return order

        if (order.payment_method != Order.PaymentMethod.RAZORPAY
                or payment_record.razorpay_order_id != razorpay_order_id
                or order.status != Order.OrderStatus.PENDING_PAYMENT):
            raise ValidationError({"payment": ["Payment does not match a pending order."]})

        # Verify signature
        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature
        )

        if not is_valid:
            payment_record.status = PaymentRecord.PaymentStatus.FAILED
            payment_record.save(update_fields=['status', 'updated_at'])
            order.payment_status = Order.PaymentStatus.FAILED
            order.save(update_fields=['payment_status', 'updated_at'])
            raise ValidationError({"payment": ["Invalid payment signature. Verification failed."]})

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            payment_record = PaymentRecord.objects.select_for_update().get(order=order)
            if payment_record.status == PaymentRecord.PaymentStatus.CAPTURED:
                return order
            if payment_record.razorpay_order_id != razorpay_order_id or order.status != Order.OrderStatus.PENDING_PAYMENT:
                raise ValidationError({"payment": ["Payment does not match a pending order."]})
            payment_record.razorpay_payment_id = razorpay_payment_id
            payment_record.razorpay_signature = razorpay_signature
            payment_record.status = PaymentRecord.PaymentStatus.CAPTURED
            payment_record.verified_at = timezone.now()
            payment_record.save()

            order.status = Order.OrderStatus.CONFIRMED
            order.payment_status = Order.PaymentStatus.CAPTURED
            order.save(update_fields=['status', 'payment_status', 'updated_at'])

        logger.info("Payment captured for order %s. Payment ID: %s", order.order_number, razorpay_payment_id)
        return order

    @classmethod
    def cancel_order(cls, order: Order, reason: str, user) -> Order:
        """
        Cancels an eligible order and atomically restores inventory stock.
        """
        is_staff = getattr(user, 'is_admin_user', False)
        if not is_staff and not order.is_cancellable_by_customer:
            raise ValidationError({
                "cancellation": [
                    f"Order cannot be cancelled in '{order.get_status_display()}' status. "
                    "Customer self-cancellation is only permitted while the order is in Confirmed status."
                ]
            })

        if order.status in [Order.OrderStatus.CANCELLED, Order.OrderStatus.DELIVERED]:
            raise ValidationError({"cancellation": [f"Order is already {order.get_status_display()}."]})

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            if order.status == Order.OrderStatus.CANCELLED:
                return order
            if order.status != Order.OrderStatus.CONFIRMED:
                raise ValidationError({"cancellation": ["Only confirmed orders can be cancelled."]})
            if order.payment_status == Order.PaymentStatus.CAPTURED:
                raise ValidationError({"cancellation": ["A captured online payment requires an approved refund workflow before cancellation."]})
            # Atomically restore stock for all line items
            for item in order.items.select_related('variant').all():
                InventoryService.restore_stock(variant_id=item.variant.id, quantity=Decimal(str(item.quantity)))

            order.status = Order.OrderStatus.CANCELLED
            order.cancellation_reason = reason.strip() if reason else "Cancelled by customer."
            order.cancelled_at = timezone.now()
            order.save(update_fields=['status', 'cancellation_reason', 'cancelled_at', 'updated_at'])

        logger.info("Order %s cancelled by %s. Stock restored.", order.order_number, user)
        return order
