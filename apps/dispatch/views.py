import logging
from rest_framework.views import APIView
from rest_framework import permissions, status
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from ..common.responses import success_response
from ..authentication.permissions import IsAdminUser, IsDeliveryPartner, IsCustomer
from .models import DeliveryAssignment
from .serializers import (
    AssignPartnerRequestSerializer,
    PartnerStatusTransitionSerializer,
    GPSPingCreateSerializer,
    GPSPingResponseSerializer,
    DeliveryAssignmentSerializer,
    LiveLocationResponseSerializer,
    AdminLiveMapPartnerSerializer,
)
from .services import DispatchService

logger = logging.getLogger(__name__)


# ─────────────────────────── Admin Dispatch Views ──────────────────────────────

class AdminAssignPartnerView(APIView):
    """
    Admin assigns a delivery partner to a packed order.
    POST /api/v1/dispatch/assign/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Assign Delivery Partner to Order",
        request=AssignPartnerRequestSerializer,
        responses={201: DeliveryAssignmentSerializer},
        tags=['Dispatch']
    )
    def post(self, request):
        serializer = AssignPartnerRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        assignment = DispatchService.assign_partner(
            order_id=str(serializer.validated_data['order_id']),
            partner_id=str(serializer.validated_data['partner_id']),
            admin_user=request.user
        )

        return success_response(
            data=DeliveryAssignmentSerializer(assignment).data,
            message="Delivery partner assigned successfully.",
            status_code=status.HTTP_201_CREATED
        )


class AdminReassignPartnerView(APIView):
    """
    Admin reassigns an active order to a different delivery partner.
    POST /api/v1/dispatch/reassign/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Reassign Order to Different Delivery Partner",
        request=AssignPartnerRequestSerializer,
        responses={200: DeliveryAssignmentSerializer},
        tags=['Dispatch']
    )
    def post(self, request):
        serializer = AssignPartnerRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        assignment = DispatchService.reassign_partner(
            order_id=str(serializer.validated_data['order_id']),
            new_partner_id=str(serializer.validated_data['partner_id']),
            admin_user=request.user
        )

        return success_response(
            data=DeliveryAssignmentSerializer(assignment).data,
            message="Order successfully reassigned to new delivery partner."
        )


class AdminAssignmentListView(APIView):
    """
    Admin: list all delivery assignments with optional status filter.
    GET /api/v1/dispatch/assignments/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="List All Delivery Assignments (Admin)",
        parameters=[
            OpenApiParameter('status', str, description="Filter by assignment status."),
            OpenApiParameter('partner_id', str, description="Filter by partner UUID."),
        ],
        responses={200: DeliveryAssignmentSerializer(many=True)},
        tags=['Dispatch']
    )
    def get(self, request):
        qs = (
            DeliveryAssignment.objects
            .select_related('order', 'partner', 'assigned_by')
            .prefetch_related('order__items')
            .order_by('-assigned_at')
        )

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status__iexact=status_filter)

        partner_id = request.query_params.get('partner_id')
        if partner_id:
            qs = qs.filter(partner_id=partner_id)

        return success_response(data=DeliveryAssignmentSerializer(qs, many=True).data)


class AdminLiveMapView(APIView):
    """
    Admin live map: all partners currently out for delivery with GPS coordinates.
    GET /api/v1/dispatch/live-map/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Admin Live Delivery Map",
        responses={200: AdminLiveMapPartnerSerializer(many=True)},
        tags=['Dispatch']
    )
    def get(self, request):
        partners_data = DispatchService.get_admin_live_map()
        serializer = AdminLiveMapPartnerSerializer(data=partners_data, many=True)
        serializer.is_valid(raise_exception=True)
        return success_response(data=serializer.data)


class AdminUpdateOrderStatusView(APIView):
    """
    Admin can advance an order to PACKED status (after physical packing).
    PATCH /api/v1/dispatch/orders/<order_id>/pack/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Mark Order as Packed (Admin)",
        responses={200: OpenApiResponse(description="Order marked as packed.")},
        tags=['Dispatch']
    )
    def patch(self, request, order_id):
        from ..orders.models import Order
        try:
            order = Order.objects.get(id=order_id)
        except Order.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Order not found.")

        if order.status != Order.OrderStatus.CONFIRMED:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({
                "status": [
                    f"Only CONFIRMED orders can be packed. "
                    f"Current status: {order.get_status_display()}."
                ]
            })

        order.status = Order.OrderStatus.PACKED
        order.save(update_fields=['status', 'updated_at'])
        from ..operations.services import enqueue_notification, record_audit
        record_audit(request.user, 'ORDER_PACKED', order,
                     'Administrator marked the order packed.')
        enqueue_notification(order.customer, 'order.status.packed', order,
                             {'order_id': str(order.pk), 'order_number': order.order_number})

        return success_response(
            data={'order_id': str(order.id), 'order_number': order.order_number, 'status': order.status},
            message="Order successfully marked as Packed."
        )


# ─────────────────────────── Partner Views ──────────────────────────────────

class PartnerJobListView(APIView):
    """
    Delivery partner: list their current and recent assignments.
    GET /api/v1/dispatch/partner/jobs/
    """
    permission_classes = [IsDeliveryPartner]

    @extend_schema(
        summary="Partner Job List",
        parameters=[
            OpenApiParameter('active_only', bool, description="If true, return only active (non-terminal) jobs."),
        ],
        responses={200: DeliveryAssignmentSerializer(many=True)},
        tags=['Dispatch']
    )
    def get(self, request):
        qs = (
            DeliveryAssignment.objects
            .filter(partner=request.user)
            .exclude(status=DeliveryAssignment.AssignmentStatus.REASSIGNED)
            .select_related('order')
            .prefetch_related('order__items')
            .order_by('-assigned_at')
        )

        if request.query_params.get('active_only', '').lower() in ('true', '1', 'yes'):
            terminal_statuses = [
                DeliveryAssignment.AssignmentStatus.DELIVERED,
                DeliveryAssignment.AssignmentStatus.FAILED,
                DeliveryAssignment.AssignmentStatus.REASSIGNED,
            ]
            qs = qs.exclude(status__in=terminal_statuses)

        return success_response(data=DeliveryAssignmentSerializer(qs, many=True).data)


class PartnerJobDetailView(APIView):
    """
    Delivery partner: retrieve a specific assignment detail.
    GET /api/v1/dispatch/partner/jobs/<assignment_id>/
    """
    permission_classes = [IsDeliveryPartner]

    @extend_schema(
        summary="Partner Job Detail",
        responses={200: DeliveryAssignmentSerializer},
        tags=['Dispatch']
    )
    def get(self, request, assignment_id):
        try:
            assignment = DeliveryAssignment.objects.select_related('order').prefetch_related('order__items').exclude(
                status=DeliveryAssignment.AssignmentStatus.REASSIGNED
            ).get(
                id=assignment_id,
                partner=request.user
            )
        except DeliveryAssignment.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Assignment not found or access denied.")

        return success_response(data=DeliveryAssignmentSerializer(assignment).data)


class PartnerStatusTransitionView(APIView):
    """
    Delivery partner: advance assignment through the status state machine.
    POST /api/v1/dispatch/partner/jobs/<assignment_id>/transition/

    Valid transitions:
    ASSIGNED → ACCEPT → ACCEPTED
    ACCEPTED → PICKUP → PICKED_UP
    PICKED_UP → START_DELIVERY → OUT_FOR_DELIVERY
    OUT_FOR_DELIVERY → DELIVER → DELIVERED
    OUT_FOR_DELIVERY → FAIL → FAILED
    """
    permission_classes = [IsDeliveryPartner]

    @extend_schema(
        summary="Partner Status Transition",
        request=PartnerStatusTransitionSerializer,
        responses={200: DeliveryAssignmentSerializer},
        tags=['Dispatch']
    )
    def post(self, request, assignment_id):
        try:
            assignment = DeliveryAssignment.objects.select_related('order').get(
                id=assignment_id,
                partner=request.user
            )
        except DeliveryAssignment.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Assignment not found or access denied.")

        serializer = PartnerStatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated_assignment = DispatchService.apply_partner_transition(
            assignment=assignment,
            action=serializer.validated_data['action'],
            notes=serializer.validated_data.get('notes', ''),
            actor=request.user,
        )

        return success_response(
            data=DeliveryAssignmentSerializer(updated_assignment).data,
            message=f"Delivery status updated to '{updated_assignment.get_status_display()}'."
        )


class PartnerGPSPingView(APIView):
    """
    Delivery partner: submit a live GPS location ping.
    POST /api/v1/dispatch/partner/jobs/<assignment_id>/gps/

    Only accepted when assignment is OUT_FOR_DELIVERY.
    Partner identity enforced: only the assigned partner can submit pings for an assignment.
    """
    permission_classes = [IsDeliveryPartner]

    @extend_schema(
        summary="Submit GPS Ping (Partner)",
        request=GPSPingCreateSerializer,
        responses={201: GPSPingResponseSerializer},
        tags=['Dispatch']
    )
    def post(self, request, assignment_id):
        try:
            assignment = DeliveryAssignment.objects.select_related('order').get(
                id=assignment_id,
                partner=request.user  # Partner identity enforced here
            )
        except DeliveryAssignment.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Assignment not found or access denied.")

        serializer = GPSPingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ping = DispatchService.ingest_gps_ping(
            assignment=assignment,
            ping_data=serializer.validated_data
        )

        return success_response(
            data=GPSPingResponseSerializer(ping).data,
            message="Location recorded.",
            status_code=status.HTTP_201_CREATED
        )


# ─────────────────────────── Customer Tracking ──────────────────────────────

class CustomerLiveTrackingView(APIView):
    """
    Customer live order tracking endpoint.
    Returns privacy-scrubbed GPS coordinates (no partner identity).
    Includes staleness indicator if last ping > 120 seconds ago.
    GET /api/v1/dispatch/track/<order_id>/
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Customer Live Order Tracking",
        responses={200: LiveLocationResponseSerializer},
        tags=['Dispatch']
    )
    def get(self, request, order_id):
        location_data = DispatchService.get_live_location_for_customer(
            order_id=str(order_id),
            customer=request.user
        )

        serializer = LiveLocationResponseSerializer(data=location_data)
        serializer.is_valid()
        return success_response(data=serializer.data)
