import uuid
from decimal import Decimal
from django.db import models
from django.db.models import Q
from django.conf import settings
from ..common.models import TimeStampedUUIDModel
from ..orders.models import Order


class DeliveryAssignment(TimeStampedUUIDModel):
    """
    Tracks which delivery partner is assigned to fulfil a specific order.
    Created by admin dispatch when an order is in PACKED status.
    """

    class AssignmentStatus(models.TextChoices):
        ASSIGNED = 'ASSIGNED', 'Assigned'
        ACCEPTED = 'ACCEPTED', 'Accepted by Partner'
        PICKED_UP = 'PICKED_UP', 'Picked Up from Facility'
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
        DELIVERED = 'DELIVERED', 'Delivered'
        FAILED = 'FAILED', 'Failed Delivery'
        REASSIGNED = 'REASSIGNED', 'Reassigned to Another Partner'

    order = models.ForeignKey(
        Order,
        on_delete=models.RESTRICT,
        related_name='assignments'
    )
    partner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='assignments',
        limit_choices_to={'user_type': 'DELIVERY_PARTNER'}
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='dispatch_assignments'
    )
    status = models.CharField(
        max_length=25,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.ASSIGNED,
        db_index=True
    )
    partner_notes = models.TextField(
        blank=True,
        help_text='Notes added by the delivery partner on assignment or delivery.'
    )
    failure_reason = models.TextField(
        blank=True,
        help_text='Reason recorded when a delivery is marked as failed.'
    )
    assigned_at = models.DateTimeField(auto_now_add=True, db_index=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Delivery Assignment'
        verbose_name_plural = 'Delivery Assignments'
        ordering = ['-assigned_at']
        indexes = [
            models.Index(fields=['partner', 'status']),
            models.Index(fields=['status', '-assigned_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['order'],
                condition=~Q(status='REASSIGNED'),
                name='one_active_assignment_per_order',
            ),
        ]

    def __str__(self):
        return (
            f"Assignment #{self.order.order_number} → "
            f"{self.partner.full_name or self.partner.username} [{self.get_status_display()}]"
        )


class GPSPing(models.Model):
    """
    Time-series GPS location ping from a delivery partner's device.
    Stored only when an assignment is in OUT_FOR_DELIVERY status.
    Latitude and longitude are the only data exposed to customers;
    all partner-identifying data is scrubbed from the customer tracking API.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(
        DeliveryAssignment,
        on_delete=models.CASCADE,
        related_name='gps_pings'
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        help_text='Latitude in decimal degrees (WGS84).'
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        help_text='Longitude in decimal degrees (WGS84).'
    )
    heading_degrees = models.SmallIntegerField(
        null=True,
        blank=True,
        help_text='Device compass heading 0–359°. Optional.'
    )
    accuracy_meters = models.FloatField(
        null=True,
        blank=True,
        help_text='GPS fix accuracy radius in metres reported by the device.'
    )
    recorded_at = models.DateTimeField(
        db_index=True,
        help_text='Device-local timestamp when the location was captured.'
    )
    received_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Server timestamp when the ping was ingested.'
    )

    class Meta:
        verbose_name = 'GPS Ping'
        verbose_name_plural = 'GPS Pings'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['assignment', '-received_at']),
        ]

    def __str__(self):
        return (
            f"Ping [{self.assignment.order.order_number}] "
            f"({self.latitude}, {self.longitude}) @ {self.received_at}"
        )
