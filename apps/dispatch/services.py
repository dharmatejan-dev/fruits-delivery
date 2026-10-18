import logging
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from ..authentication.models import CustomUser
from ..orders.models import Order
from ..operations.models import DeliveryPartnerProfile
from ..operations.services import enqueue_notification, record_audit
from .models import DeliveryAssignment, GPSPing

logger = logging.getLogger(__name__)

# Stale GPS threshold: if no ping received in 120 seconds, mark as not live
GPS_STALE_SECONDS = 120


class DispatchService:
    """
    Core dispatch service implementing:
    - Manual admin assignment of delivery partners to packed orders
    - Partner-driven status state machine transitions
    - GPS ping ingestion with status guards
    - Customer live location query with staleness detection
    - Admin live map aggregation
    """

    # ─────────────────────────── Assignment ────────────────────────────

    @classmethod
    @transaction.atomic
    def assign_partner(cls, order_id: str, partner_id: str, admin_user) -> DeliveryAssignment:
        """
        Admin action: assign a delivery partner to a packed order.
        Creates a new DeliveryAssignment and advances order to ASSIGNED status.
        """
        try:
            order = Order.objects.select_for_update().get(id=order_id)
        except Order.DoesNotExist:
            raise ValidationError({"order_id": ["Order not found."]})

        if order.status != Order.OrderStatus.PACKED:
            raise ValidationError({
                "order_id": [
                    f"Order must be in PACKED status to assign a partner. "
                    f"Current status: {order.get_status_display()}."
                ]
            })

        # Check order not already assigned (guard against duplicate calls)
        active_assignment = DeliveryAssignment.objects.filter(order=order).exclude(
            status=DeliveryAssignment.AssignmentStatus.REASSIGNED
        ).first()
        if active_assignment:
            raise ValidationError({
                "order_id": [
                    f"This order already has an active assignment "
                    f"(status: {active_assignment.get_status_display()}). "
                    "Reassign using the reassign endpoint."
                ]
            })

        try:
            partner = CustomUser.objects.get(
                id=partner_id,
                user_type=CustomUser.UserType.DELIVERY_PARTNER,
                is_active=True
            )
        except CustomUser.DoesNotExist:
            raise ValidationError({"partner_id": ["Active delivery partner not found."]})

        if partner.is_temp_password:
            raise ValidationError({
                "partner_id": [
                    f"Partner '{partner.full_name or partner.username}' has not yet changed their "
                    "temporary password and cannot be assigned deliveries."
                ]
            })
        try:
            profile = partner.partner_profile
        except DeliveryPartnerProfile.DoesNotExist:
            raise ValidationError({"partner_id": ["Delivery partner profile is not configured."]})
        if not profile.is_available:
            raise ValidationError({"partner_id": ["Delivery partner is currently unavailable for assignments."]})

        with transaction.atomic():
            assignment = DeliveryAssignment.objects.create(
                order=order,
                partner=partner,
                assigned_by=admin_user,
                status=DeliveryAssignment.AssignmentStatus.ASSIGNED
            )

            order.status = Order.OrderStatus.ASSIGNED
            order.save(update_fields=['status', 'updated_at'])
            record_audit(admin_user, 'ORDER_PARTNER_ASSIGNED', assignment,
                         'Administrator assigned a delivery partner.',
                         {'order_id': str(order.pk), 'partner_id': str(partner.pk)})
            enqueue_notification(partner, 'delivery.assignment.created', assignment,
                                 {'assignment_id': str(assignment.pk), 'order_number': order.order_number})
            enqueue_notification(order.customer, 'order.status.assigned', order,
                                 {'order_id': str(order.pk), 'order_number': order.order_number})

        logger.info(
            "Order %s assigned to partner %s by admin %s",
            order.order_number, partner.username, admin_user.username
        )
        return assignment

    @classmethod
    @transaction.atomic
    def reassign_partner(cls, order_id: str, new_partner_id: str, admin_user) -> DeliveryAssignment:
        """
        Admin action: reassign an order to a different delivery partner.
        Marks the previous assignment as REASSIGNED and creates a fresh one.
        """
        try:
            order = Order.objects.select_for_update().get(id=order_id)
        except Order.DoesNotExist:
            raise ValidationError({"order_id": ["Order not found."]})

        # Order must still be in a reassignable state
        reassignable_statuses = [
            Order.OrderStatus.ASSIGNED,
        ]
        if order.status not in reassignable_statuses:
            raise ValidationError({
                "order_id": [
                    f"Order in '{order.get_status_display()}' cannot be reassigned."
                ]
            })

        try:
            current_assignment = DeliveryAssignment.objects.select_for_update().get(
                order=order,
                status__in=[
                    DeliveryAssignment.AssignmentStatus.ASSIGNED,
                    DeliveryAssignment.AssignmentStatus.ACCEPTED,
                ]
            )
        except DeliveryAssignment.DoesNotExist:
            raise ValidationError({"order_id": ["No active assignment found for this order."]})

        try:
            new_partner = CustomUser.objects.get(
                id=new_partner_id,
                user_type=CustomUser.UserType.DELIVERY_PARTNER,
                is_active=True
            )
        except CustomUser.DoesNotExist:
            raise ValidationError({"partner_id": ["Active delivery partner not found."]})

        if new_partner.is_temp_password:
            raise ValidationError({
                "partner_id": ["New partner has not changed their temporary password."]
            })
        try:
            new_partner_available = new_partner.partner_profile.is_available
        except DeliveryPartnerProfile.DoesNotExist:
            raise ValidationError({"partner_id": ["Delivery partner profile is not configured."]})
        if not new_partner_available:
            raise ValidationError({"partner_id": ["Delivery partner is currently unavailable for assignments."]})

        if current_assignment.partner_id == new_partner.id:
            raise ValidationError({"partner_id": ["Select a different delivery partner."]})

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            current_assignment = DeliveryAssignment.objects.select_for_update().get(pk=current_assignment.pk)
            if current_assignment.status == DeliveryAssignment.AssignmentStatus.REASSIGNED:
                raise ValidationError({"order_id": ["Assignment has already been reassigned."]})
            current_assignment.status = DeliveryAssignment.AssignmentStatus.REASSIGNED
            current_assignment.save(update_fields=['status', 'updated_at'])

            new_assignment = DeliveryAssignment.objects.create(
                order=order,
                partner=new_partner,
                assigned_by=admin_user,
                status=DeliveryAssignment.AssignmentStatus.ASSIGNED
            )

            order.status = Order.OrderStatus.ASSIGNED
            order.save(update_fields=['status', 'updated_at'])
            record_audit(admin_user, 'ORDER_PARTNER_REASSIGNED', new_assignment,
                         'Administrator reassigned a delivery partner.',
                         {'order_id': str(order.pk), 'partner_id': str(new_partner.pk),
                          'previous_assignment_id': str(current_assignment.pk)})
            enqueue_notification(current_assignment.partner, 'delivery.assignment.reassigned',
                                 current_assignment, {'order_number': order.order_number})
            enqueue_notification(new_partner, 'delivery.assignment.created', new_assignment,
                                 {'assignment_id': str(new_assignment.pk), 'order_number': order.order_number})
            enqueue_notification(order.customer, 'order.partner_reassigned', order,
                                 {'order_id': str(order.pk), 'order_number': order.order_number})

        logger.info(
            "Order %s reassigned from %s to %s by admin %s",
            order.order_number, current_assignment.partner.username,
            new_partner.username, admin_user.username
        )
        return new_assignment

    # ─────────────────────── Status Transitions ─────────────────────────

    # Maps (current_assignment_status, action) → (new_assignment_status, new_order_status)
    TRANSITION_MAP = {
        (DeliveryAssignment.AssignmentStatus.ASSIGNED, 'ACCEPT'): (
            DeliveryAssignment.AssignmentStatus.ACCEPTED, Order.OrderStatus.ASSIGNED
        ),
        (DeliveryAssignment.AssignmentStatus.ACCEPTED, 'PICKUP'): (
            DeliveryAssignment.AssignmentStatus.PICKED_UP, Order.OrderStatus.ASSIGNED
        ),
        (DeliveryAssignment.AssignmentStatus.PICKED_UP, 'START_DELIVERY'): (
            DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY, Order.OrderStatus.OUT_FOR_DELIVERY
        ),
        (DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY, 'DELIVER'): (
            DeliveryAssignment.AssignmentStatus.DELIVERED, Order.OrderStatus.DELIVERED
        ),
        (DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY, 'FAIL'): (
            DeliveryAssignment.AssignmentStatus.FAILED, Order.OrderStatus.FAILED
        ),
    }

    @classmethod
    @transaction.atomic
    def apply_partner_transition(cls, assignment: DeliveryAssignment, action: str, notes: str = '', actor=None) -> DeliveryAssignment:
        """
        Partner-driven state machine: advance an assignment through its lifecycle.
        """
        if action == 'FAIL' and not notes.strip():
            raise ValidationError({"notes": ["A failure reason is required."]})

        now = timezone.now()
        assignment = DeliveryAssignment.objects.select_for_update().get(pk=assignment.pk)
        key = (assignment.status, action)
        transition = cls.TRANSITION_MAP.get(key)
        if transition is None:
            raise ValidationError({
                "action": [
                    f"Action '{action}' is not valid from current status "
                    f"'{assignment.get_status_display()}'. "
                    f"Valid actions: {[k[1] for k in cls.TRANSITION_MAP if k[0] == assignment.status]}"
                ]
            })

        new_assignment_status, new_order_status = transition
        order = Order.objects.select_for_update().get(pk=assignment.order_id)
        expected_order_status = (
            Order.OrderStatus.OUT_FOR_DELIVERY
            if assignment.status == DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY
            else Order.OrderStatus.ASSIGNED
        )
        if order.status != expected_order_status:
            raise ValidationError({"order": ["Order status changed; reload the assignment before continuing."]})

        if notes:
            if action == 'FAIL':
                assignment.failure_reason = notes
            else:
                assignment.partner_notes = notes
        assignment.status = new_assignment_status
        if action == 'PICKUP':
            assignment.picked_up_at = now
        elif action == 'DELIVER':
            assignment.delivered_at = now
        assignment.save()

        order.status = new_order_status
        order.save(update_fields=['status', 'updated_at'])
        record_audit(actor, 'DELIVERY_STATUS_CHANGED', assignment,
                     'Delivery partner changed assignment status.',
                     {'order_id': str(order.pk), 'action': action,
                      'assignment_status': assignment.status, 'order_status': order.status})
        enqueue_notification(assignment.partner,
                             f'delivery.assignment.{assignment.status.lower()}', assignment,
                             {'assignment_id': str(assignment.pk), 'order_number': order.order_number})
        if new_order_status != expected_order_status:
            enqueue_notification(order.customer, f'order.status.{new_order_status.lower()}', order,
                                 {'order_id': str(order.pk), 'order_number': order.order_number})

        logger.info(
            "Assignment %s transitioned via '%s': %s → %s | Order %s → %s",
            assignment.id, action,
            key[0], new_assignment_status,
            assignment.order.order_number, new_order_status
        )
        return assignment

    # ──────────────────────────── GPS ────────────────────────────────────

    @classmethod
    @transaction.atomic
    def ingest_gps_ping(cls, assignment: DeliveryAssignment, ping_data: dict) -> GPSPing:
        """
        Ingest a GPS location ping from a delivery partner's device.
        Only accepted when assignment is OUT_FOR_DELIVERY.

        Acceptance criteria:
        - Returns HTTP 400 if order not in OUT_FOR_DELIVERY.
        - Returns HTTP 403 if caller is not the assigned partner (enforced in view).
        """
        assignment = DeliveryAssignment.objects.select_for_update().get(pk=assignment.pk)
        order = Order.objects.select_for_update().get(pk=assignment.order_id)
        if (assignment.status != DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY
                or order.status != Order.OrderStatus.OUT_FOR_DELIVERY):
            raise ValidationError({
                "gps": [
                    f"GPS pings are only accepted when the assignment is Out for Delivery. "
                    f"Current assignment status: {assignment.get_status_display()}."
                ]
            })

        if ping_data['recorded_at'] > timezone.now() + timedelta(minutes=2):
            raise ValidationError({"recorded_at": ["Location timestamp cannot be more than two minutes in the future."]})

        ping = GPSPing.objects.create(
            assignment=assignment,
            latitude=ping_data['latitude'],
            longitude=ping_data['longitude'],
            heading_degrees=ping_data.get('heading_degrees'),
            accuracy_meters=ping_data.get('accuracy_meters'),
            recorded_at=ping_data['recorded_at'],
        )
        return ping

    @classmethod
    def get_live_location_for_customer(cls, order_id: str, customer) -> dict:
        """
        Returns the live location payload for a customer tracking their order.
        Privacy: only lat/lng/heading are returned; partner identity is NOT exposed.
        Staleness: if last ping > 120s ago, is_live=False and stale_message is set.
        """
        try:
            order = Order.objects.get(id=order_id, customer=customer)
        except Order.DoesNotExist:
            raise ValidationError({"order_id": ["Order not found or access denied."]})

        if order.status != Order.OrderStatus.OUT_FOR_DELIVERY:
            raise ValidationError({
                "tracking": [
                    f"Live tracking is only available when order is Out for Delivery. "
                    f"Current status: {order.get_status_display()}."
                ]
            })

        assignment = order.assignments.exclude(
            status=DeliveryAssignment.AssignmentStatus.REASSIGNED
        ).order_by('-assigned_at').first()
        if assignment is None:
            raise ValidationError({"tracking": ["No active delivery assignment found."]})

        latest_ping = (
            GPSPing.objects
            .filter(assignment=assignment)
            .order_by('-recorded_at', '-received_at')
            .first()
        )

        now = timezone.now()
        stale_threshold = now - timedelta(seconds=GPS_STALE_SECONDS)

        if latest_ping and latest_ping.recorded_at >= stale_threshold:
            is_live = True
            stale_message = None
        elif latest_ping:
            is_live = False
            minutes_ago = int((now - latest_ping.recorded_at).total_seconds() / 60)
            stale_message = f"Driver signal temporarily unavailable. Last seen {minutes_ago} min ago."
        else:
            is_live = False
            stale_message = "Awaiting live location from delivery partner."

        return {
            "order_id": str(order.id),
            "order_number": order.order_number,
            "assignment_status": assignment.get_status_display(),
            "is_live": is_live,
            "latitude": latest_ping.latitude if latest_ping else None,
            "longitude": latest_ping.longitude if latest_ping else None,
            "heading_degrees": latest_ping.heading_degrees if latest_ping else None,
            "last_ping_at": latest_ping.recorded_at if latest_ping else None,
            "stale_message": stale_message,
        }

    @classmethod
    def get_admin_live_map(cls) -> list:
        """
        Returns all delivery partners currently out for delivery with their
        latest GPS coordinates. Used by the admin live map screen.
        """
        active_assignments = (
            DeliveryAssignment.objects
            .filter(status=DeliveryAssignment.AssignmentStatus.OUT_FOR_DELIVERY)
            .select_related('partner', 'order')
        )

        now = timezone.now()
        stale_threshold = now - timedelta(seconds=GPS_STALE_SECONDS)
        result = []

        for assignment in active_assignments:
            latest_ping = (
                GPSPing.objects
                .filter(assignment=assignment)
                .order_by('-recorded_at', '-received_at')
                .first()
            )

            is_live = bool(latest_ping and latest_ping.recorded_at >= stale_threshold)

            result.append({
                "partner_id": str(assignment.partner.id),
                "partner_name": assignment.partner.full_name or assignment.partner.username,
                "partner_phone": assignment.partner.phone_number or '',
                "order_id": str(assignment.order.id),
                "order_number": assignment.order.order_number,
                "latitude": latest_ping.latitude if latest_ping else None,
                "longitude": latest_ping.longitude if latest_ping else None,
                "heading_degrees": latest_ping.heading_degrees if latest_ping else None,
                "last_ping_at": latest_ping.recorded_at if latest_ping else None,
                "is_live": is_live,
            })

        return result
