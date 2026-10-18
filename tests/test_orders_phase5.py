from decimal import Decimal
import pytest
from unittest.mock import patch
from rest_framework.exceptions import ValidationError
from django.test import override_settings
from rest_framework.test import APIClient
from apps.authentication.models import CustomUser, UserAddress
from apps.catalog.models import Category, Product, ProductVariant
from apps.inventory.models import InventoryRecord
from apps.orders.models import Order
from apps.orders.services import OrderService
from apps.payments.models import PaymentRecord


@pytest.fixture
def checkout_data(db):
    customer = CustomUser.objects.create_user(
        username='+919876543299', phone_number='+919876543299', full_name='Test Buyer'
    )
    address = UserAddress.objects.create(
        user=customer, recipient_name='Test Buyer', recipient_phone='+919876543299',
        address_line1='1 Orchard Road', locality='Vizianagaram', pincode='535001'
    )
    category = Category.objects.create(title='Fruit', slug='fruit')
    product = Product.objects.create(
        category=category, name='Test Apple', slug='test-apple', short_description='Sample'
    )
    variant = ProductVariant.objects.create(
        product=product, unit_quantity=Decimal('1'), unit_type=ProductVariant.UnitType.KG,
        mrp_inr=Decimal('100'), selling_price_inr=Decimal('80'), is_active=True
    )
    inventory = InventoryRecord.objects.create(
        variant=variant, available_stock=Decimal('10'), reserved_stock=Decimal('0')
    )
    return customer, address, variant, inventory


@pytest.mark.django_db
def test_checkout_idempotency_returns_original_order(checkout_data):
    customer, address, variant, inventory = checkout_data
    key = 'f85fbb1f-5cb8-4bc5-b862-9e4e178e7e80'
    payload = [{'variant_id': str(variant.id), 'quantity': 2}]
    first = OrderService.create_order(
        customer, str(address.id), payload, Order.PaymentMethod.COD, idempotency_key=key
    )
    retry = OrderService.create_order(
        customer, str(address.id), payload, Order.PaymentMethod.COD, idempotency_key=key
    )
    inventory.refresh_from_db()
    assert retry.pk == first.pk
    assert inventory.available_stock == Decimal('8')


@pytest.mark.django_db
def test_cancellation_restores_stock_only_once(checkout_data):
    customer, address, variant, inventory = checkout_data
    order = OrderService.create_order(
        customer, str(address.id), [{'variant_id': str(variant.id), 'quantity': 2}],
        Order.PaymentMethod.COD
    )
    OrderService.cancel_order(order, 'Customer requested', customer)
    OrderService.cancel_order(order, 'Retry', customer)
    inventory.refresh_from_db()
    assert inventory.available_stock == Decimal('10')


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_payment_verification_rejects_foreign_gateway_order(checkout_data):
    customer, address, variant, _inventory = checkout_data
    order = OrderService.create_order(
        customer, str(address.id), [{'variant_id': str(variant.id), 'quantity': 1}],
        Order.PaymentMethod.RAZORPAY
    )
    with pytest.raises(ValidationError):
        OrderService.verify_razorpay_payment(
            order, 'order_someone_elses', 'pay_123', 'mock_signature_valid'
        )


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_razorpay_webhook_rejects_invalid_signature(checkout_data):
    customer, address, variant, _inventory = checkout_data
    order = OrderService.create_order(
        customer, str(address.id), [{'variant_id': str(variant.id), 'quantity': 1}],
        Order.PaymentMethod.RAZORPAY,
    )
    client = APIClient()
    with patch('apps.payments.views.RazorpayService.verify_webhook_signature', return_value=False):
        response = client.post('/api/v1/payments/razorpay/webhook/', {'event': 'payment.captured'}, format='json')
    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == Order.OrderStatus.PENDING_PAYMENT


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_razorpay_captured_webhook_is_idempotent(checkout_data):
    customer, address, variant, _inventory = checkout_data
    order = OrderService.create_order(
        customer, str(address.id), [{'variant_id': str(variant.id), 'quantity': 1}],
        Order.PaymentMethod.RAZORPAY,
    )
    payment = PaymentRecord.objects.get(order=order)
    payload = {
        'event': 'payment.captured',
        'payload': {'payment': {'entity': {
            'order_id': payment.razorpay_order_id,
            'id': 'pay_phase8_capture',
            'amount': int(payment.amount_inr * 100),
            'currency': payment.currency,
            'status': 'captured',
        }}},
    }
    client = APIClient()
    with patch('apps.payments.views.RazorpayService.verify_webhook_signature', return_value=True):
        first = client.post('/api/v1/payments/razorpay/webhook/', payload, format='json')
        second = client.post('/api/v1/payments/razorpay/webhook/', payload, format='json')
    assert first.status_code == 200
    assert second.status_code == 200
    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == Order.OrderStatus.CONFIRMED
    assert order.payment_status == Order.PaymentStatus.CAPTURED
    assert payment.status == PaymentRecord.PaymentStatus.CAPTURED
    assert payment.razorpay_payment_id == 'pay_phase8_capture'
