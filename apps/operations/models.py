import uuid
from django.db import models
from django.db.models import Q
from django.conf import settings
from ..common.models import TimeStampedUUIDModel
from ..orders.models import Order


class DeliveryPartnerProfile(TimeStampedUUIDModel):
    """
    Extended profile for delivery partners, storing operational metadata
    managed by administrators. Links 1:1 to the CustomUser account.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='partner_profile',
        limit_choices_to={'user_type': 'DELIVERY_PARTNER'}
    )
    vehicle_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g., Bicycle, Motorcycle, Auto"
    )
    vehicle_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Vehicle registration number."
    )
    id_proof_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="Type of identity document presented: Aadhaar, Driving Licence, etc."
    )
    id_proof_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="Masked/stored identity document number for verification."
    )
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=15, blank=True)
    is_available = models.BooleanField(
        default=True,
        help_text='Admin-toggled availability flag. Unavailable partners are hidden from dispatch.'
    )
    total_deliveries_completed = models.PositiveIntegerField(default=0)
    notes = models.TextField(
        blank=True,
        help_text='Internal admin notes about this partner.'
    )

    class Meta:
        verbose_name = 'Delivery Partner Profile'
        verbose_name_plural = 'Delivery Partner Profiles'

    def __str__(self):
        return f"Profile: {self.user.full_name or self.user.username}"


class QualityClaim(TimeStampedUUIDModel):
    """
    Customer quality complaint filed within the 24-hour claim window with at least
    one photo of evidence.
    """

    class ClaimStatus(models.TextChoices):
        SUBMITTED = 'SUBMITTED', 'Submitted'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        RESOLVED = 'RESOLVED', 'Resolved'
        REJECTED = 'REJECTED', 'Rejected'

    class ResolutionType(models.TextChoices):
        REFUND = 'REFUND', 'Full or Partial Refund'
        REPLACEMENT = 'REPLACEMENT', 'Replacement Delivery'
        CREDIT = 'CREDIT', 'Store Credit'
        NO_ACTION = 'NO_ACTION', 'No Action (Rejected)'

    order = models.ForeignKey(
        Order,
        on_delete=models.RESTRICT,
        related_name='quality_claims'
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='quality_claims'
    )
    description = models.TextField(
        help_text='Customer description of the quality issue.'
    )
    status = models.CharField(
        max_length=20,
        choices=ClaimStatus.choices,
        default=ClaimStatus.SUBMITTED,
        db_index=True
    )
    resolution_type = models.CharField(
        max_length=20,
        choices=ResolutionType.choices,
        null=True,
        blank=True
    )
    manual_refund_amount_inr = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Admin-approved amount for manual refund processing. No payment is issued automatically.'
    )
    manual_credit_amount_inr = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Admin-approved store-credit amount for manual credit entry. No wallet balance is changed automatically.'
    )
    resolution_notes = models.TextField(
        blank=True,
        help_text='Admin resolution explanation visible to the customer.'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_claims'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Quality Claim'
        verbose_name_plural = 'Quality Claims'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer', 'status']),
            models.Index(fields=['status', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['order'],
                condition=Q(status__in=['SUBMITTED', 'UNDER_REVIEW']),
                name='one_open_quality_claim_per_order',
            ),
        ]

    def __str__(self):
        return f"Claim {self.id} for Order {self.order.order_number} [{self.get_status_display()}]"


class ClaimPhoto(models.Model):
    """
    Photographic evidence attached to a quality claim. At least one is required;
    the submission API accepts up to five.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim = models.ForeignKey(
        QualityClaim,
        on_delete=models.CASCADE,
        related_name='photos'
    )
    image = models.ImageField(
        upload_to='claims/photos/%Y/%m/%d/',
        help_text='JPEG or PNG photograph of the quality issue.'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Claim Photo'
        verbose_name_plural = 'Claim Photos'
        ordering = ['uploaded_at']

    def __str__(self):
        return f"Photo for Claim {self.claim_id}"


class AuditLog(models.Model):
    """Append-only record of privileged operational actions."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='operational_audit_events',
    )
    action = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=80, db_index=True)
    object_id = models.CharField(max_length=80, db_index=True)
    summary = models.CharField(max_length=240)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['object_type', 'object_id', '-created_at'])]

    def __str__(self):
        return f"{self.action} {self.object_type} {self.object_id} by {self.actor_id}"


class NotificationEvent(models.Model):
    """Provider-neutral notification outbox; queued events await provider setup."""
    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notification_events',
    )
    event_key = models.CharField(max_length=200, unique=True)
    event_type = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f"{self.event_type} to {self.recipient_id} [{self.status}]"


class PushDevice(models.Model):
    """An authenticated app installation eligible to receive FCM messages."""
    class Platform(models.TextChoices):
        ANDROID = 'ANDROID', 'Android'
        IOS = 'IOS', 'iOS'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_devices'
    )
    # Unique indexes on utf8mb4 columns must fit MySQL's 3072-byte key limit.
    # 512 characters keeps the full token indexed while allowing ample room
    # for current FCM registration tokens.
    registration_token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    app_id = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_seen_at']

    def __str__(self):
        return f"{self.platform} push device for {self.user_id}"
