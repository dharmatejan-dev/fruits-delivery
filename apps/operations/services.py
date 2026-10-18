import logging
from datetime import timedelta
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from ..authentication.models import CustomUser
from ..orders.models import Order
from .models import AuditLog, DeliveryPartnerProfile, NotificationEvent, QualityClaim, ClaimPhoto

logger = logging.getLogger(__name__)

MAX_CLAIM_PHOTOS = 5


def record_audit(actor, action, obj, summary, metadata=None):
    return AuditLog.objects.create(
        actor=actor, action=action, object_type=obj.__class__.__name__,
        object_id=str(obj.pk), summary=summary[:240], metadata=metadata or {},
    )


def enqueue_notification(recipient, event_type, obj, payload=None):
    """Store a deduplicated provider-neutral event in the transaction outbox."""
    event_key = f'{event_type}:{obj.__class__.__name__}:{obj.pk}:{recipient.pk}'
    event, _created = NotificationEvent.objects.get_or_create(
        event_key=event_key,
        defaults={
            'recipient': recipient,
            'event_type': event_type,
            'object_type': obj.__class__.__name__,
            'object_id': str(obj.pk),
            'payload': payload or {},
        },
    )
    return event


class PartnerProvisioningService:
    """
    Admin-side service for creating and managing delivery partner accounts.
    Partners are provisioned with a temporary password; they must change it
    before accessing any delivery features.
    """

    @classmethod
    def create_partner(cls, admin_user, data: dict) -> DeliveryPartnerProfile:
        """
        Creates a new delivery partner user account with a temporary password
        and initialises their partner profile.
        """
        with transaction.atomic():
            user = CustomUser.objects.create_user(
                username=data['username'],
                phone_number=data.get('phone_number'),
                full_name=data['full_name'],
                password=data['temp_password'],
                user_type=CustomUser.UserType.DELIVERY_PARTNER,
                is_temp_password=True,
                is_staff=False,
                is_active=True,
            )

            profile = DeliveryPartnerProfile.objects.create(
                user=user,
                vehicle_type=data.get('vehicle_type', ''),
                vehicle_number=data.get('vehicle_number', ''),
            )
            record_audit(admin_user, 'PARTNER_CREATED', profile,
                         'Delivery partner account created.', {'user_id': str(user.pk)})

        logger.info(
            "Delivery partner '%s' created by admin '%s'.",
            user.username, admin_user.username
        )
        return profile

    @classmethod
    def set_partner_availability(cls, profile: DeliveryPartnerProfile, is_available: bool, admin_user=None) -> DeliveryPartnerProfile:
        """Admin toggles partner availability for dispatch."""
        profile.is_available = is_available
        profile.save(update_fields=['is_available', 'updated_at'])
        record_audit(admin_user, 'PARTNER_AVAILABILITY_CHANGED', profile,
                     'Delivery partner availability changed.', {'is_available': is_available})
        logger.info(
            "Partner '%s' availability set to %s.",
            profile.user.username, is_available
        )
        return profile

    @classmethod
    def set_partner_active_status(cls, profile: DeliveryPartnerProfile, is_active: bool, admin_user=None) -> DeliveryPartnerProfile:
        """Admin activates or deactivates a partner account."""
        if not is_active and profile.user.assignments.filter(
            status__in=['ASSIGNED', 'ACCEPTED', 'PICKED_UP', 'OUT_FOR_DELIVERY']
        ).exists():
            raise ValidationError({
                "is_active": ["Reassign active deliveries before deactivating this partner account."]
            })
        profile.user.is_active = is_active
        profile.user.save(update_fields=['is_active'])
        record_audit(admin_user, 'PARTNER_ACTIVATION_CHANGED', profile,
                     'Delivery partner account status changed.', {'is_active': is_active})
        logger.info(
            "Partner '%s' account active status set to %s.",
            profile.user.username, is_active
        )
        return profile

    @classmethod
    def increment_delivery_count(cls, partner_user) -> None:
        """
        Called when an assignment reaches DELIVERED status.
        Atomically increments the total_deliveries_completed counter.
        """
        DeliveryPartnerProfile.objects.filter(user=partner_user).update(
            total_deliveries_completed=F('total_deliveries_completed') + 1
        )


class QualityClaimService:
    """
    Manages customer quality complaints filed post-delivery:
    - Customer files a claim after a delivered order.
    - Admin reviews and resolves with a typed resolution.
    """

    @classmethod
    @transaction.atomic
    def file_claim(cls, customer, order_id: str, description: str, photos: list) -> QualityClaim:
        """
        Customer files a quality claim against a delivered order.

        Claims must be filed within 24 hours of delivery with at least one photo.
        """
        try:
            order = Order.objects.select_for_update().get(id=order_id, customer=customer)
        except Order.DoesNotExist:
            raise ValidationError({"order_id": ["Order not found or access denied."]})

        if order.status != Order.OrderStatus.DELIVERED:
            raise ValidationError({
                "order_id": [
                    f"Quality claims can only be filed for delivered orders. "
                    f"Current status: {order.get_status_display()}."
                ]
            })

        delivery = order.assignments.filter(
            status='DELIVERED', delivered_at__isnull=False
        ).order_by('-delivered_at').first()
        if delivery is None or timezone.now() > delivery.delivered_at + timedelta(hours=24):
            raise ValidationError({
                "order_id": ["Quality claims must be filed within 24 hours of delivery."]
            })

        # Duplicate claim guard
        open_statuses = [QualityClaim.ClaimStatus.SUBMITTED, QualityClaim.ClaimStatus.UNDER_REVIEW]
        if QualityClaim.objects.filter(order=order, status__in=open_statuses).exists():
            raise ValidationError({
                "duplicate_claim": ["An open quality claim already exists for this order."]
            })

        photos = photos or []
        if not photos:
            raise ValidationError({"photos": ["At least one photo is required for a quality claim."]})
        if len(photos) > MAX_CLAIM_PHOTOS:
            raise ValidationError({
                "photos": [f"At most {MAX_CLAIM_PHOTOS} photographs may be uploaded."]
            })

        with transaction.atomic():
            claim = QualityClaim.objects.create(
                order=order,
                customer=customer,
                description=description,
                status=QualityClaim.ClaimStatus.SUBMITTED
            )
            for photo_file in photos:
                ClaimPhoto.objects.create(claim=claim, image=photo_file)
            record_audit(customer, 'QUALITY_CLAIM_SUBMITTED', claim,
                         'Customer submitted a quality claim.',
                         {'order_id': str(order.pk), 'photo_count': len(photos)})
            enqueue_notification(customer, 'quality_claim.submitted', claim,
                                 {'claim_id': str(claim.pk), 'order_number': order.order_number})

        logger.info(
            "Quality claim %s filed by customer %s for order %s.",
            claim.id, customer.phone_number, order.order_number
        )
        return claim

    @classmethod
    def resolve_claim(
        cls,
        claim: QualityClaim,
        resolution_type: str,
        resolution_notes: str,
        new_status: str,
        admin_user,
        refund_amount_inr=None,
        credit_amount_inr=None,
    ) -> QualityClaim:
        """Admin resolves or rejects a quality claim."""
        with transaction.atomic():
            claim = QualityClaim.objects.select_for_update().get(pk=claim.pk)
            if claim.status in [QualityClaim.ClaimStatus.RESOLVED, QualityClaim.ClaimStatus.REJECTED]:
                raise ValidationError({
                    "claim": [f"Claim is already {claim.get_status_display()} and cannot be modified."]
                })
            if new_status == QualityClaim.ClaimStatus.REJECTED and resolution_type != QualityClaim.ResolutionType.NO_ACTION:
                raise ValidationError({"resolution_type": ["Rejected claims must use the NO_ACTION outcome."]})
            if new_status == QualityClaim.ClaimStatus.RESOLVED and resolution_type == QualityClaim.ResolutionType.NO_ACTION:
                raise ValidationError({"resolution_type": ["Use REJECTED status for a NO_ACTION outcome."]})
            if resolution_type == QualityClaim.ResolutionType.REFUND and refund_amount_inr is None:
                raise ValidationError({"refund_amount_inr": ["Enter the amount approved for manual refund processing."]})
            if resolution_type == QualityClaim.ResolutionType.REFUND and refund_amount_inr > claim.order.total_payable_inr:
                raise ValidationError({"refund_amount_inr": ["Approved refund cannot exceed the order total."]})
            if resolution_type == QualityClaim.ResolutionType.CREDIT and credit_amount_inr is None:
                raise ValidationError({"credit_amount_inr": ["Enter the amount approved for manual credit entry."]})
            if resolution_type == QualityClaim.ResolutionType.CREDIT and credit_amount_inr > claim.order.total_payable_inr:
                raise ValidationError({"credit_amount_inr": ["Approved credit cannot exceed the order total."]})
            if resolution_type != QualityClaim.ResolutionType.REFUND and refund_amount_inr is not None:
                raise ValidationError({"refund_amount_inr": ["This amount is only used for refund decisions."]})
            if resolution_type != QualityClaim.ResolutionType.CREDIT and credit_amount_inr is not None:
                raise ValidationError({"credit_amount_inr": ["This amount is only used for credit decisions."]})

            claim.status = new_status
            claim.resolution_type = resolution_type
            claim.manual_refund_amount_inr = refund_amount_inr
            claim.manual_credit_amount_inr = credit_amount_inr
            claim.resolution_notes = resolution_notes.strip()
            claim.reviewed_by = admin_user
            claim.reviewed_at = timezone.now()
            claim.save(update_fields=[
                'status', 'resolution_type', 'resolution_notes',
                'manual_refund_amount_inr', 'manual_credit_amount_inr',
                'reviewed_by', 'reviewed_at', 'updated_at'
            ])
            record_audit(admin_user, 'QUALITY_CLAIM_RESOLVED', claim,
                         'Administrator recorded a quality claim outcome.',
                         {'status': new_status, 'resolution_type': resolution_type,
                          'manual_refund_amount_inr': str(refund_amount_inr) if refund_amount_inr is not None else None,
                          'manual_credit_amount_inr': str(credit_amount_inr) if credit_amount_inr is not None else None})
            enqueue_notification(claim.customer, f'quality_claim.{claim.status.lower()}', claim,
                                 {'claim_id': str(claim.pk), 'status': claim.status})

        logger.info(
            "Claim %s resolved by admin %s: %s — %s.",
            claim.id, admin_user.username, new_status, resolution_type
        )
        return claim

    @classmethod
    def mark_under_review(cls, claim: QualityClaim, admin_user) -> QualityClaim:
        """Admin marks a submitted claim as Under Review."""
        with transaction.atomic():
            claim = QualityClaim.objects.select_for_update().get(pk=claim.pk)
            if claim.status != QualityClaim.ClaimStatus.SUBMITTED:
                raise ValidationError({
                    "claim": [
                        f"Only SUBMITTED claims can be moved to Under Review. "
                        f"Current: {claim.get_status_display()}."
                    ]
                })
            claim.status = QualityClaim.ClaimStatus.UNDER_REVIEW
            claim.reviewed_by = admin_user
            claim.reviewed_at = timezone.now()
            claim.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
            record_audit(admin_user, 'QUALITY_CLAIM_REVIEW_STARTED', claim,
                         'Administrator started review of a quality claim.')
            enqueue_notification(claim.customer, f'quality_claim.{claim.status.lower()}', claim,
                                 {'claim_id': str(claim.pk), 'status': claim.status})
        return claim
