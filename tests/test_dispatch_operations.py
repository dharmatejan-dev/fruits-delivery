"""
Phase 6 & 7 Integration Tests:
- Dispatch: assignment, reassignment, partner state machine, GPS ping ingestion, customer tracking
- Operations: partner provisioning, quality claim filing, admin review/resolve
"""
import pytest
from io import StringIO
from tempfile import TemporaryDirectory
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from unittest.mock import patch
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from apps.dispatch.models import DeliveryAssignment, GPSPing
from apps.dispatch.services import DispatchService
from apps.operations.models import DeliveryPartnerProfile, NotificationEvent, PushDevice, QualityClaim
from apps.operations.services import PartnerProvisioningService, QualityClaimService
from apps.authentication.models import CustomUser
from apps.orders.models import Order

User = get_user_model()


# ──────────────────────────── Fixtures ────────────────────────────────────────

def make_customer(phone='+919876543210', name='Test Customer'):
    return User.objects.create_user(
        username=phone,
        phone_number=phone,
        full_name=name,
        user_type=User.UserType.CUSTOMER,
    )


def make_partner(username='partner1', phone='+919000000001', temp=False):
    user = User.objects.create_user(
        username=username,
        phone_number=phone,
        full_name='Test Partner',
        password='securepass123',
        user_type=User.UserType.DELIVERY_PARTNER,
        is_temp_password=temp,
        is_active=True,
    )
    DeliveryPartnerProfile.objects.create(user=user)
    return user


def make_admin(username='admin1'):
    return User.objects.create_user(
        username=username,
        password='admin123',
        user_type=User.UserType.ADMIN,
        is_staff=True,
    )


def make_delivered_order(customer, order_number='OH-VZM-20261014-TEST'):
    return Order.objects.create(
        order_number=order_number,
        customer=customer,
        status=Order.OrderStatus.DELIVERED,
        payment_method=Order.PaymentMethod.COD,
        payment_status=Order.PaymentStatus.PENDING,
        subtotal_inr=Decimal('100.00'),
        delivery_charge_inr=Decimal('0.00'),
        discount_inr=Decimal('0.00'),
        total_payable_inr=Decimal('100.00'),
        shipping_address_snapshot={
            'recipient_name': 'Test', 'recipient_phone': '+919876543210',
            'address_line1': '1 Mango St', 'address_line2': '', 'landmark': '',
            'locality': 'Test', 'city': 'Vizianagaram', 'state': 'Andhra Pradesh',
            'pincode': '535001', 'latitude': None, 'longitude': None,
        }
    )


def make_packed_order(customer, order_number='OH-VZM-TEST-PACK01'):
    return Order.objects.create(
        order_number=order_number,
        customer=customer,
        status=Order.OrderStatus.PACKED,
        payment_method=Order.PaymentMethod.COD,
        payment_status=Order.PaymentStatus.PENDING,
        subtotal_inr=Decimal('200.00'),
        delivery_charge_inr=Decimal('0.00'),
        discount_inr=Decimal('0.00'),
        total_payable_inr=Decimal('200.00'),
        shipping_address_snapshot={
            'recipient_name': 'Test', 'recipient_phone': '+919876543210',
            'address_line1': '1 Mango St', 'address_line2': '', 'landmark': '',
            'locality': 'Test', 'city': 'Vizianagaram', 'state': 'Andhra Pradesh',
            'pincode': '535001', 'latitude': None, 'longitude': None,
        }
    )


# ──────────────────────────── Phase 6: Dispatch Tests ─────────────────────────

@pytest.mark.django_db
class TestDispatchAssignment:
    """Admin dispatch assignment business logic."""

    def test_assign_partner_to_packed_order(self):
        """Admin can assign a partner to a PACKED order."""
        admin = make_admin()
        customer = make_customer()
        partner = make_partner()
        order = make_packed_order(customer)

        assignment = DispatchService.assign_partner(
            order_id=str(order.id),
            partner_id=str(partner.id),
            admin_user=admin
        )

        assert assignment.status == DeliveryAssignment.AssignmentStatus.ASSIGNED
        order.refresh_from_db()
        assert order.status == Order.OrderStatus.ASSIGNED

    def test_cannot_assign_to_non_packed_order(self):
        """Assigning to a CONFIRMED order raises ValidationError."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        admin = make_admin()
        customer = make_customer()
        partner = make_partner()
        order = make_packed_order(customer)
        order.status = Order.OrderStatus.CONFIRMED
        order.save()

        with pytest.raises(DRFValidationError):
            DispatchService.assign_partner(str(order.id), str(partner.id), admin)

    def test_cannot_assign_partner_with_temp_password(self):
        """Partners who haven't changed their temp password cannot be assigned."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        admin = make_admin()
        customer = make_customer()
        partner = make_partner(username='temppartner', phone='+919000000099', temp=True)
        order = make_packed_order(customer, order_number='OH-VZM-TEMP-0001')

        with pytest.raises(DRFValidationError):
            DispatchService.assign_partner(str(order.id), str(partner.id), admin)

    def test_reassign_partner(self):
        """Admin can reassign an active order to a different partner."""
        admin = make_admin()
        customer = make_customer()
        partner1 = make_partner(username='p1', phone='+919000000002')
        partner2 = make_partner(username='p2', phone='+919000000003')
        order = make_packed_order(customer, order_number='OH-VZM-REASS-001')

        assignment1 = DispatchService.assign_partner(str(order.id), str(partner1.id), admin)
        assert assignment1.status == DeliveryAssignment.AssignmentStatus.ASSIGNED

        new_assignment = DispatchService.reassign_partner(
            order_id=str(order.id),
            new_partner_id=str(partner2.id),
            admin_user=admin
        )

        assignment1.refresh_from_db()
        assert assignment1.status == DeliveryAssignment.AssignmentStatus.REASSIGNED
        assert new_assignment.partner == partner2
        assert new_assignment.status == DeliveryAssignment.AssignmentStatus.ASSIGNED


@pytest.mark.django_db
class TestPartnerStateMachine:
    """Partner-driven assignment state machine transitions."""

    def setup_method(self):
        self.admin = make_admin(username='dispatch_admin')
        self.customer = make_customer(phone='+919111111111')
        self.partner = make_partner(username='statemachine_partner', phone='+919222222222')
        self.order = make_packed_order(self.customer, order_number='OH-VZM-STATE-001')
        self.assignment = DispatchService.assign_partner(
            str(self.order.id), str(self.partner.id), self.admin
        )

    def test_full_happy_path(self):
        """ASSIGNED → ACCEPT → PICKUP → START_DELIVERY → DELIVER."""
        a = DispatchService.apply_partner_transition(self.assignment, 'ACCEPT')
        assert a.status == DeliveryAssignment.AssignmentStatus.ACCEPTED

        a = DispatchService.apply_partner_transition(a, 'PICKUP')
        assert a.status == DeliveryAssignment.AssignmentStatus.PICKED_UP
        assert a.picked_up_at is not None

        a = DispatchService.apply_partner_transition(a, 'START_DELIVERY')
        assert a.status == DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY

        self.order.refresh_from_db()
        assert self.order.status == Order.OrderStatus.OUT_FOR_DELIVERY

        a = DispatchService.apply_partner_transition(a, 'DELIVER')
        assert a.status == DeliveryAssignment.AssignmentStatus.DELIVERED
        assert a.delivered_at is not None

        self.order.refresh_from_db()
        assert self.order.status == Order.OrderStatus.DELIVERED

    def test_failed_delivery_path(self):
        """OUT_FOR_DELIVERY → FAIL marks order as FAILED."""
        a = DispatchService.apply_partner_transition(self.assignment, 'ACCEPT')
        a = DispatchService.apply_partner_transition(a, 'PICKUP')
        a = DispatchService.apply_partner_transition(a, 'START_DELIVERY')
        a = DispatchService.apply_partner_transition(a, 'FAIL', notes='Customer not home.')

        assert a.status == DeliveryAssignment.AssignmentStatus.FAILED
        assert a.failure_reason == 'Customer not home.'
        self.order.refresh_from_db()
        assert self.order.status == Order.OrderStatus.FAILED

    def test_invalid_transition_raises_error(self):
        """Attempting invalid action from current state raises ValidationError."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        with pytest.raises(DRFValidationError):
            # Can't DELIVER from ASSIGNED status (must go through ACCEPT, PICKUP, START_DELIVERY first)
            DispatchService.apply_partner_transition(self.assignment, 'DELIVER')


@pytest.mark.django_db
class TestGPSPing:
    """GPS ping ingestion business rules."""

    def setup_method(self):
        self.admin = make_admin(username='gps_admin')
        self.customer = make_customer(phone='+919333333333')
        self.partner = make_partner(username='gps_partner', phone='+919444444444')
        self.order = make_packed_order(self.customer, order_number='OH-VZM-GPS-001')
        self.assignment = DispatchService.assign_partner(
            str(self.order.id), str(self.partner.id), self.admin
        )

    def _advance_to_out_for_delivery(self):
        a = DispatchService.apply_partner_transition(self.assignment, 'ACCEPT')
        a = DispatchService.apply_partner_transition(a, 'PICKUP')
        a = DispatchService.apply_partner_transition(a, 'START_DELIVERY')
        return a

    def test_gps_ping_accepted_when_out_for_delivery(self):
        """GPS pings are accepted when assignment is OUT_FOR_DELIVERY."""
        assignment = self._advance_to_out_for_delivery()
        ping_data = {
            'latitude': Decimal('18.1167'),
            'longitude': Decimal('83.4000'),
            'heading_degrees': 90,
            'accuracy_meters': 5.0,
            'recorded_at': timezone.now(),
        }
        ping = DispatchService.ingest_gps_ping(assignment, ping_data)
        assert ping.latitude == Decimal('18.1167')
        assert ping.longitude == Decimal('83.4000')

    def test_gps_ping_rejected_when_not_out_for_delivery(self):
        """GPS pings are rejected if assignment is not OUT_FOR_DELIVERY (e.g. ASSIGNED)."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        ping_data = {
            'latitude': Decimal('18.1167'),
            'longitude': Decimal('83.4000'),
            'recorded_at': timezone.now(),
        }
        with pytest.raises(DRFValidationError):
            DispatchService.ingest_gps_ping(self.assignment, ping_data)  # still ASSIGNED

    def test_customer_live_location_privacy(self):
        """Customer tracking returns location data without partner identity."""
        assignment = self._advance_to_out_for_delivery()
        ping_data = {
            'latitude': Decimal('18.1167'),
            'longitude': Decimal('83.4000'),
            'recorded_at': timezone.now(),
        }
        DispatchService.ingest_gps_ping(assignment, ping_data)

        location = DispatchService.get_live_location_for_customer(
            order_id=str(self.order.id),
            customer=self.customer
        )

        assert location['is_live'] is True
        assert location['latitude'] is not None
        assert 'partner_id' not in location
        assert 'partner_name' not in location

    def test_stale_gps_detection(self):
        """If last ping > 120s ago, is_live=False and stale_message is set."""
        assignment = self._advance_to_out_for_delivery()

        # Create a ping that's 200 seconds old (beyond the 120s threshold)
        old_time = timezone.now() - timedelta(seconds=200)
        ping = GPSPing.objects.create(
            assignment=assignment,
            latitude=Decimal('18.1167'),
            longitude=Decimal('83.4000'),
            recorded_at=old_time,
            received_at=old_time,
        )
        # Override auto_now_add by direct update
        GPSPing.objects.filter(pk=ping.pk).update(received_at=old_time)

        location = DispatchService.get_live_location_for_customer(
            order_id=str(self.order.id),
            customer=self.customer
        )

        assert location['is_live'] is False
        assert location['stale_message'] is not None
        assert 'unavailable' in location['stale_message'].lower()

    def test_customer_cannot_track_other_customers_order(self):
        """A customer cannot track another customer's order."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        other_customer = make_customer(phone='+919555555555', name='Other Customer')

        with pytest.raises(DRFValidationError):
            DispatchService.get_live_location_for_customer(
                order_id=str(self.order.id),
                customer=other_customer
            )


# ──────────────────────────── Phase 7: Operations Tests ───────────────────────

@pytest.mark.django_db
class TestPartnerProvisioning:
    """Delivery partner account provisioning and management."""

    def test_create_partner_sets_temp_password_flag(self):
        """Newly created partners have is_temp_password=True."""
        admin = make_admin(username='provisioning_admin')
        data = {
            'full_name': 'Ravi Kumar',
            'username': 'ravi_kumar',
            'phone_number': '+919700000001',
            'temp_password': 'TempPass@123',
            'vehicle_type': 'Motorcycle',
            'vehicle_number': 'AP39AB1234',
        }
        profile = PartnerProvisioningService.create_partner(admin_user=admin, data=data)

        assert profile.user.is_temp_password is True
        assert profile.user.user_type == User.UserType.DELIVERY_PARTNER
        assert profile.vehicle_type == 'Motorcycle'

    def test_toggle_partner_availability(self):
        """Admin can toggle partner availability."""
        partner = make_partner(username='avail_partner', phone='+919700000002')
        profile = DeliveryPartnerProfile.objects.get(user=partner)

        result = PartnerProvisioningService.set_partner_availability(profile, False)
        assert result.is_available is False

        result = PartnerProvisioningService.set_partner_availability(profile, True)
        assert result.is_available is True

    def test_deactivate_partner(self):
        """Admin can deactivate a partner account."""
        partner = make_partner(username='deact_partner', phone='+919700000003')
        profile = DeliveryPartnerProfile.objects.get(user=partner)

        result = PartnerProvisioningService.set_partner_active_status(profile, False)
        result.user.refresh_from_db()
        assert result.user.is_active is False


@pytest.mark.django_db
class TestQualityClaims:
    """Quality claim lifecycle: filing, review, resolution."""

    def test_customer_can_file_claim_against_delivered_order(self):
        """Customer files a valid claim for a delivered order."""
        customer = make_customer(phone='+919800000001')
        order = make_delivered_order(customer)
        admin = make_admin(username='claim_admin')
        partner = make_partner(username='claim_partner', phone='+919800000002')

        # Set up a delivered assignment to give a delivered_at timestamp
        assignment = DeliveryAssignment.objects.create(
            order=order,
            partner=partner,
            assigned_by=admin,
            status=DeliveryAssignment.AssignmentStatus.DELIVERED,
            delivered_at=timezone.now() - timedelta(hours=2)
        )

        with TemporaryDirectory() as media_dir, override_settings(MEDIA_ROOT=media_dir):
            photo = SimpleUploadedFile('evidence.jpg', b'test-image', content_type='image/jpeg')
            claim = QualityClaimService.file_claim(
                customer=customer,
                order_id=str(order.id),
                description='The mangoes were rotten. Very disappointed with the quality.',
                photos=[photo],
            )
            assert claim.photos.count() == 1

        assert claim.status == QualityClaim.ClaimStatus.SUBMITTED
        assert claim.customer == customer
        assert claim.order == order

    def test_cannot_file_claim_for_non_delivered_order(self):
        """Filing a claim against a non-delivered order raises ValidationError."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        customer = make_customer(phone='+919800000010')
        order = make_packed_order(customer, order_number='OH-VZM-NODELIV-001')
        # Order is PACKED not DELIVERED

        with pytest.raises(DRFValidationError):
            QualityClaimService.file_claim(
                customer=customer,
                order_id=str(order.id),
                description='Some quality issue description here for test.',
                photos=[]
            )

    def test_cannot_file_duplicate_claim(self):
        """A second open claim for the same order is rejected."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        customer = make_customer(phone='+919800000020')
        order = make_delivered_order(customer, order_number='OH-VZM-DUP-001')
        admin = make_admin(username='dup_admin')
        partner = make_partner(username='dup_partner', phone='+919800000021')

        DeliveryAssignment.objects.create(
            order=order, partner=partner, assigned_by=admin,
            status=DeliveryAssignment.AssignmentStatus.DELIVERED,
            delivered_at=timezone.now() - timedelta(hours=1)
        )

        # First claim
        QualityClaim.objects.create(
            order=order, customer=customer,
            description='First claim already submitted.',
            status=QualityClaim.ClaimStatus.SUBMITTED
        )

        # Second claim should fail
        with pytest.raises(DRFValidationError):
            QualityClaimService.file_claim(
                customer=customer,
                order_id=str(order.id),
                description='Trying to file a second claim for the same order.',
                photos=[]
            )

    def test_admin_resolve_claim(self):
        """Admin can resolve a quality claim with a resolution type."""
        admin = make_admin(username='resolve_admin')
        customer = make_customer(phone='+919800000030')
        order = make_delivered_order(customer, order_number='OH-VZM-RESOLVE-001')

        claim = QualityClaim.objects.create(
            order=order,
            customer=customer,
            description='Mangoes were damaged in transit.',
            status=QualityClaim.ClaimStatus.SUBMITTED
        )

        resolved = QualityClaimService.resolve_claim(
            claim=claim,
            resolution_type=QualityClaim.ResolutionType.REFUND,
            resolution_notes='Full refund approved as photos confirmed damage.',
            new_status=QualityClaim.ClaimStatus.RESOLVED,
            admin_user=admin,
            refund_amount_inr=Decimal('100.00'),
        )

        assert resolved.status == QualityClaim.ClaimStatus.RESOLVED
        assert resolved.resolution_type == QualityClaim.ResolutionType.REFUND
        assert resolved.reviewed_by == admin
        assert resolved.manual_refund_amount_inr == Decimal('100.00')

    def test_claim_cutoff_and_photo_are_enforced(self):
        from rest_framework.exceptions import ValidationError as DRFValidationError
        customer = make_customer(phone='+919800000035')
        partner = make_partner(username='cutoff_partner', phone='+919800000036')
        admin = make_admin(username='cutoff_admin')

        expired_order = make_delivered_order(customer, order_number='OH-VZM-EXPIRED-001')
        DeliveryAssignment.objects.create(
            order=expired_order, partner=partner, assigned_by=admin,
            status=DeliveryAssignment.AssignmentStatus.DELIVERED,
            delivered_at=timezone.now() - timedelta(hours=24, seconds=1),
        )
        with pytest.raises(DRFValidationError, match='within 24 hours'):
            QualityClaimService.file_claim(customer, str(expired_order.id), 'A sufficiently detailed quality claim.', [])

        recent_order = make_delivered_order(customer, order_number='OH-VZM-NOPHOTO-001')
        DeliveryAssignment.objects.create(
            order=recent_order, partner=partner, assigned_by=admin,
            status=DeliveryAssignment.AssignmentStatus.DELIVERED,
            delivered_at=timezone.now() - timedelta(hours=1),
        )
        with pytest.raises(DRFValidationError, match='At least one photo'):
            QualityClaimService.file_claim(customer, str(recent_order.id), 'A sufficiently detailed quality claim.', [])

    @pytest.mark.parametrize('resolution_type,amount_field,amount', [
        (QualityClaim.ResolutionType.REPLACEMENT, None, None),
        (QualityClaim.ResolutionType.CREDIT, 'credit_amount_inr', Decimal('40.00')),
    ])
    def test_manual_replacement_and_credit_approval(self, resolution_type, amount_field, amount):
        admin = make_admin(username=f'manual_{resolution_type.lower()}')
        customer = make_customer(phone='+919800000037')
        order = make_delivered_order(customer, order_number=f'OH-VZM-{resolution_type}-001')
        claim = QualityClaim.objects.create(
            order=order, customer=customer, description='Product quality issue requires review.',
            status=QualityClaim.ClaimStatus.SUBMITTED,
        )
        kwargs = {amount_field: amount} if amount_field else {}
        resolved = QualityClaimService.resolve_claim(
            claim, resolution_type, 'Approved for manual handling.',
            QualityClaim.ClaimStatus.RESOLVED, admin, **kwargs,
        )
        assert resolved.resolution_type == resolution_type
        if amount_field:
            resolved.refresh_from_db()
            assert resolved.manual_credit_amount_inr == amount

    @pytest.mark.parametrize('resolution_type,amount_field', [
        (QualityClaim.ResolutionType.REFUND, 'refund_amount_inr'),
        (QualityClaim.ResolutionType.CREDIT, 'credit_amount_inr'),
    ])
    def test_manual_amount_cannot_exceed_order_total(self, resolution_type, amount_field):
        from rest_framework.exceptions import ValidationError as DRFValidationError
        admin = make_admin(username=f'overcap_{resolution_type.lower()}')
        customer = make_customer(phone='+919800000038')
        order = make_delivered_order(customer, order_number=f'OH-VZM-OVERCAP-{resolution_type}')
        claim = QualityClaim.objects.create(
            order=order, customer=customer, description='Product quality issue requires review.',
            status=QualityClaim.ClaimStatus.SUBMITTED,
        )
        with pytest.raises(DRFValidationError, match='cannot exceed the order total'):
            QualityClaimService.resolve_claim(
                claim, resolution_type, 'Over cap amount.', QualityClaim.ClaimStatus.RESOLVED,
                admin, **{amount_field: Decimal('100.01')},
            )

    def test_cannot_resolve_already_resolved_claim(self):
        """Attempting to re-resolve an already resolved claim raises ValidationError."""
        from rest_framework.exceptions import ValidationError as DRFValidationError
        admin = make_admin(username='double_resolve_admin')
        customer = make_customer(phone='+919800000040')
        order = make_delivered_order(customer, order_number='OH-VZM-DBLRESOLVE-001')

        claim = QualityClaim.objects.create(
            order=order, customer=customer,
            description='Already resolved claim scenario.',
            status=QualityClaim.ClaimStatus.RESOLVED,
            resolution_type=QualityClaim.ResolutionType.REFUND,
            resolution_notes='Already resolved.',
            reviewed_by=admin
        )

        with pytest.raises(DRFValidationError):
            QualityClaimService.resolve_claim(
                claim=claim,
                resolution_type=QualityClaim.ResolutionType.NO_ACTION,
                resolution_notes='Trying to resolve again.',
                new_status=QualityClaim.ClaimStatus.REJECTED,
                admin_user=admin
            )


# ────────────────────── API Endpoint Smoke Tests (Phase 6 & 7) ──────────────────

@pytest.mark.django_db
class TestDispatchAPIEndpoints:
    """Basic API endpoint access control and response shape checks."""

    def setup_method(self):
        self.client = APIClient()
        self.admin = make_admin(username='api_admin')
        self.customer = make_customer(phone='+919900000001')
        self.partner = make_partner(username='api_partner', phone='+919900000002')
        self.order = make_packed_order(self.customer, order_number='OH-VZM-API-001')

    def _admin_auth(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token.access_token)}')

    def _customer_auth(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.customer)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token.access_token)}')

    def _partner_auth(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.partner)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token.access_token)}')

    def test_unauthenticated_assign_returns_401(self):
        resp = self.client.post('/api/v1/dispatch/assign/', {
            'order_id': str(self.order.id), 'partner_id': str(self.partner.id)
        }, format='json')
        assert resp.status_code == 401

    def test_customer_cannot_access_assign_endpoint(self):
        self._customer_auth()
        resp = self.client.post('/api/v1/dispatch/assign/', {
            'order_id': str(self.order.id), 'partner_id': str(self.partner.id)
        }, format='json')
        assert resp.status_code == 403

    def test_admin_can_assign_partner(self):
        self._admin_auth()
        resp = self.client.post('/api/v1/dispatch/assign/', {
            'order_id': str(self.order.id), 'partner_id': str(self.partner.id)
        }, format='json')
        assert resp.status_code == 201

    def test_partner_can_list_their_jobs(self):
        self._partner_auth()
        resp = self.client.get('/api/v1/dispatch/partner/jobs/')
        assert resp.status_code == 200

    def test_admin_live_map_accessible(self):
        self._admin_auth()
        resp = self.client.get('/api/v1/dispatch/live-map/')
        assert resp.status_code == 200

    def test_admin_assignment_list(self):
        self._admin_auth()
        resp = self.client.get('/api/v1/dispatch/assignments/')
        assert resp.status_code == 200

    def test_pack_order_endpoint(self):
        """Admin can mark a confirmed order as packed."""
        self.order.status = Order.OrderStatus.CONFIRMED
        self.order.save()
        self._admin_auth()
        resp = self.client.patch(f'/api/v1/dispatch/orders/{self.order.id}/pack/')
        assert resp.status_code == 200
        self.order.refresh_from_db()
        assert self.order.status == Order.OrderStatus.PACKED


@pytest.mark.django_db
class TestOperationsAPIEndpoints:
    """Basic API endpoint access control for operations."""

    def setup_method(self):
        self.client = APIClient()
        self.admin = make_admin(username='ops_admin')
        self.customer = make_customer(phone='+919900000010')

    def _admin_auth(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token.access_token)}')

    def _customer_auth(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.customer)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token.access_token)}')

    def test_admin_can_list_partners(self):
        self._admin_auth()
        resp = self.client.get('/api/v1/operations/partners/')
        assert resp.status_code == 200

    def test_customer_cannot_list_partners(self):
        self._customer_auth()
        resp = self.client.get('/api/v1/operations/partners/')
        assert resp.status_code == 403

    def test_admin_create_partner(self):
        self._admin_auth()
        resp = self.client.post('/api/v1/operations/partners/', {
            'full_name': 'New Partner',
            'username': 'newpartner99',
            'phone_number': '+919700099001',
            'temp_password': 'TempPass@123',
            'vehicle_type': 'Bicycle',
            'vehicle_number': 'N/A',
        }, format='json')
        assert resp.status_code == 201

    def test_customer_can_list_their_claims(self):
        self._customer_auth()
        resp = self.client.get('/api/v1/operations/claims/')
        assert resp.status_code == 200

    def test_admin_can_list_all_claims(self):
        self._admin_auth()
        resp = self.client.get('/api/v1/operations/admin/claims/')
        assert resp.status_code == 200

    def test_unauthenticated_claims_returns_401(self):
        resp = self.client.get('/api/v1/operations/claims/')
        assert resp.status_code == 401

    def test_customer_can_register_and_remove_own_push_device(self):
        self._customer_auth()
        payload = {
            'registration_token': 'fcm-token-customer-1',
            'platform': 'ANDROID',
            'app_id': 'com.orchard.customer',
        }
        registered = self.client.post('/api/v1/operations/push-devices/', payload, format='json')
        assert registered.status_code == 200
        device = PushDevice.objects.get(registration_token=payload['registration_token'])
        assert device.user == self.customer
        removed = self.client.delete('/api/v1/operations/push-devices/', {
            'registration_token': payload['registration_token'],
        }, format='json')
        assert removed.status_code == 200
        assert not PushDevice.objects.filter(pk=device.pk).exists()


@pytest.mark.django_db
def test_fcm_outbox_command_marks_successful_event_sent():
    customer = make_customer(phone='+919900000020')
    event = NotificationEvent.objects.create(
        recipient=customer, event_key='test:Order:1:user', event_type='order.status.packed',
        object_type='Order', object_id='1', payload={'order_number': 'OH-TEST-1'},
    )
    PushDevice.objects.create(
        user=customer, registration_token='fcm-token-worker', platform=PushDevice.Platform.ANDROID,
    )
    with patch('apps.operations.management.commands.send_notifications.get_firebase_app', return_value=object()), \
         patch('apps.operations.management.commands.send_notifications.send_event_to_device', return_value='fcm-id'):
        call_command('send_notifications', stdout=StringIO())
    event.refresh_from_db()
    assert event.status == NotificationEvent.Status.SENT
    assert event.attempts == 1
    assert event.sent_at is not None
