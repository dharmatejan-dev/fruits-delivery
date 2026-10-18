import logging
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from ..common.responses import success_response
from ..authentication.permissions import IsAdminUser, IsCustomer
from ..authentication.models import CustomUser
from ..orders.models import Order
from ..common.pagination import StandardPageNumberPagination
from .models import AuditLog, DeliveryPartnerProfile, NotificationEvent, PushDevice, QualityClaim
from .serializers import (
    DeliveryPartnerCreateSerializer,
    DeliveryPartnerProfileSerializer,
    PartnerAvailabilityUpdateSerializer,
    PartnerActivationSerializer,
    QualityClaimSerializer,
    QualityClaimCreateSerializer,
    QualityClaimResolveSerializer,
    CustomerAdminSerializer,
    CustomerActivationSerializer,
    AuditLogSerializer,
    NotificationEventSerializer,
    PushDeviceRegistrationSerializer,
    PushDeviceDeleteSerializer,
)
from .services import PartnerProvisioningService, QualityClaimService

logger = logging.getLogger(__name__)


# ──────────────────── Partner Provisioning Views (Admin) ──────────────────────

class AdminPartnerListCreateView(APIView):
    """
    Admin: list all delivery partners or create a new partner account.
    GET/POST /api/v1/operations/partners/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="List Delivery Partners (Admin)",
        parameters=[
            OpenApiParameter('available_only', bool, description="Filter to only available partners."),
            OpenApiParameter('active_only', bool, description="Filter to only active accounts."),
        ],
        responses={200: DeliveryPartnerProfileSerializer(many=True)},
        tags=['Operations']
    )
    def get(self, request):
        qs = (
            DeliveryPartnerProfile.objects
            .select_related('user')
            .order_by('-created_at')
        )

        if request.query_params.get('available_only', '').lower() in ('true', '1', 'yes'):
            qs = qs.filter(is_available=True)

        if request.query_params.get('active_only', '').lower() in ('true', '1', 'yes'):
            qs = qs.filter(user__is_active=True)

        return success_response(data=DeliveryPartnerProfileSerializer(qs, many=True).data)

    @extend_schema(
        summary="Create Delivery Partner Account (Admin)",
        request=DeliveryPartnerCreateSerializer,
        responses={201: DeliveryPartnerProfileSerializer},
        tags=['Operations']
    )
    def post(self, request):
        serializer = DeliveryPartnerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = PartnerProvisioningService.create_partner(
            admin_user=request.user,
            data=serializer.validated_data
        )

        return success_response(
            data=DeliveryPartnerProfileSerializer(profile).data,
            message="Delivery partner account created successfully.",
            status_code=status.HTTP_201_CREATED
        )


class AdminPartnerDetailView(APIView):
    """
    Admin: retrieve or update a delivery partner profile.
    GET/PATCH /api/v1/operations/partners/<partner_id>/
    """
    permission_classes = [IsAdminUser]

    def _get_profile(self, partner_id):
        try:
            return DeliveryPartnerProfile.objects.select_related('user').get(id=partner_id)
        except DeliveryPartnerProfile.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Partner profile not found.")

    @extend_schema(
        summary="Get Partner Profile Detail (Admin)",
        responses={200: DeliveryPartnerProfileSerializer},
        tags=['Operations']
    )
    def get(self, request, partner_id):
        profile = self._get_profile(partner_id)
        return success_response(data=DeliveryPartnerProfileSerializer(profile).data)

    @extend_schema(
        summary="Update Partner Profile (Admin)",
        request=DeliveryPartnerProfileSerializer,
        responses={200: DeliveryPartnerProfileSerializer},
        tags=['Operations']
    )
    def patch(self, request, partner_id):
        profile = self._get_profile(partner_id)
        serializer = DeliveryPartnerProfileSerializer(
            profile, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        from .services import record_audit
        record_audit(request.user, 'PARTNER_PROFILE_UPDATED', profile,
                     'Administrator updated delivery partner profile.')
        return success_response(
            data=DeliveryPartnerProfileSerializer(profile).data,
            message="Partner profile updated."
        )


class AdminPartnerAvailabilityView(APIView):
    """
    Admin toggle partner availability flag.
    PATCH /api/v1/operations/partners/<partner_id>/availability/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Toggle Partner Availability (Admin)",
        request=PartnerAvailabilityUpdateSerializer,
        responses={200: DeliveryPartnerProfileSerializer},
        tags=['Operations']
    )
    def patch(self, request, partner_id):
        try:
            profile = DeliveryPartnerProfile.objects.select_related('user').get(id=partner_id)
        except DeliveryPartnerProfile.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Partner profile not found.")

        serializer = PartnerAvailabilityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = PartnerProvisioningService.set_partner_availability(
            profile=profile,
            is_available=serializer.validated_data['is_available'],
            admin_user=request.user,
        )

        return success_response(
            data=DeliveryPartnerProfileSerializer(profile).data,
            message=f"Partner availability set to {'Available' if profile.is_available else 'Unavailable'}."
        )


class AdminPartnerActivationView(APIView):
    """
    Admin activate or deactivate a partner account.
    PATCH /api/v1/operations/partners/<partner_id>/activation/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Activate/Deactivate Partner Account (Admin)",
        request=PartnerActivationSerializer,
        responses={200: DeliveryPartnerProfileSerializer},
        tags=['Operations']
    )
    def patch(self, request, partner_id):
        try:
            profile = DeliveryPartnerProfile.objects.select_related('user').get(id=partner_id)
        except DeliveryPartnerProfile.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Partner profile not found.")

        serializer = PartnerActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = PartnerProvisioningService.set_partner_active_status(
            profile=profile,
            is_active=serializer.validated_data['is_active'],
            admin_user=request.user,
        )

        return success_response(
            data=DeliveryPartnerProfileSerializer(profile).data,
            message=f"Partner account {'activated' if profile.user.is_active else 'deactivated'}."
        )


# ─────────────────────── Quality Claims (Customer) ────────────────────────────

class CustomerQualityClaimListCreateView(APIView):
    """
    Customer: list their own quality claims or file a new one.
    GET/POST /api/v1/operations/claims/

    POST requires multipart form data with photos (1–5 images).
    """
    permission_classes = [IsCustomer]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="List My Quality Claims (Customer)",
        responses={200: QualityClaimSerializer(many=True)},
        tags=['Operations']
    )
    def get(self, request):
        claims = (
            QualityClaim.objects
            .filter(customer=request.user)
            .prefetch_related('photos')
            .select_related('order')
            .order_by('-created_at')
        )
        return success_response(
            data=QualityClaimSerializer(claims, many=True, context={'request': request}).data
        )

    @extend_schema(
        summary="File Quality Claim with Photos (Customer)",
        request={'multipart/form-data': QualityClaimCreateSerializer},
        responses={201: QualityClaimSerializer},
        tags=['Operations']
    )
    def post(self, request):
        # Reconstruct data for serializer validation
        data = {
            'order_id': request.data.get('order_id'),
            'description': request.data.get('description'),
            'photos': request.FILES.getlist('photos'),
        }

        serializer = QualityClaimCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        claim = QualityClaimService.file_claim(
            customer=request.user,
            order_id=str(serializer.validated_data['order_id']),
            description=serializer.validated_data['description'],
            photos=serializer.validated_data['photos']
        )

        return success_response(
            data=QualityClaimSerializer(claim, context={'request': request}).data,
            message="Quality claim submitted for review.",
            status_code=status.HTTP_201_CREATED
        )


class CustomerQualityClaimDetailView(APIView):
    """
    Customer: view detail of a specific claim they own.
    GET /api/v1/operations/claims/<claim_id>/
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Quality Claim Detail (Customer)",
        responses={200: QualityClaimSerializer},
        tags=['Operations']
    )
    def get(self, request, claim_id):
        try:
            claim = (
                QualityClaim.objects
                .prefetch_related('photos')
                .select_related('order')
                .get(id=claim_id, customer=request.user)
            )
        except QualityClaim.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Quality claim not found or access denied.")

        return success_response(
            data=QualityClaimSerializer(claim, context={'request': request}).data
        )


# ─────────────────────── Quality Claims (Admin) ───────────────────────────────

class AdminQualityClaimListView(APIView):
    """
    Admin: list all quality claims with optional status filter.
    GET /api/v1/operations/admin/claims/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="List All Quality Claims (Admin)",
        parameters=[
            OpenApiParameter('status', str, description="Filter by claim status."),
        ],
        responses={200: QualityClaimSerializer(many=True)},
        tags=['Operations']
    )
    def get(self, request):
        qs = (
            QualityClaim.objects
            .prefetch_related('photos')
            .select_related('order', 'customer', 'reviewed_by')
            .order_by('-created_at')
        )

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status__iexact=status_filter)

        return success_response(
            data=QualityClaimSerializer(qs, many=True, context={'request': request}).data
        )


class AdminQualityClaimDetailView(APIView):
    """
    Admin: view and resolve a specific quality claim.
    GET /api/v1/operations/admin/claims/<claim_id>/
    POST /api/v1/operations/admin/claims/<claim_id>/resolve/
    """
    permission_classes = [IsAdminUser]

    def _get_claim(self, claim_id):
        try:
            return (
                QualityClaim.objects
                .prefetch_related('photos')
                .select_related('order', 'customer')
                .get(id=claim_id)
            )
        except QualityClaim.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Quality claim not found.")

    @extend_schema(
        summary="Get Claim Detail (Admin)",
        responses={200: QualityClaimSerializer},
        tags=['Operations']
    )
    def get(self, request, claim_id):
        claim = self._get_claim(claim_id)
        return success_response(
            data=QualityClaimSerializer(claim, context={'request': request}).data
        )


class AdminQualityClaimReviewView(APIView):
    """
    Admin marks a claim as Under Review.
    PATCH /api/v1/operations/admin/claims/<claim_id>/review/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Mark Claim Under Review (Admin)",
        responses={200: QualityClaimSerializer},
        tags=['Operations']
    )
    def patch(self, request, claim_id):
        try:
            claim = QualityClaim.objects.select_related('order', 'customer').get(id=claim_id)
        except QualityClaim.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Quality claim not found.")

        claim = QualityClaimService.mark_under_review(claim=claim, admin_user=request.user)
        return success_response(
            data=QualityClaimSerializer(claim, context={'request': request}).data,
            message="Claim marked as Under Review."
        )


class AdminQualityClaimResolveView(APIView):
    """
    Admin resolves or rejects a quality claim.
    POST /api/v1/operations/admin/claims/<claim_id>/resolve/
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Resolve or Reject Quality Claim (Admin)",
        request=QualityClaimResolveSerializer,
        responses={200: QualityClaimSerializer},
        tags=['Operations']
    )
    def post(self, request, claim_id):
        try:
            claim = QualityClaim.objects.select_related('order', 'customer').get(id=claim_id)
        except QualityClaim.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Quality claim not found.")

        serializer = QualityClaimResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        claim = QualityClaimService.resolve_claim(
            claim=claim,
            resolution_type=serializer.validated_data['resolution_type'],
            resolution_notes=serializer.validated_data['resolution_notes'],
            new_status=serializer.validated_data['new_status'],
            admin_user=request.user,
            refund_amount_inr=serializer.validated_data.get('refund_amount_inr'),
            credit_amount_inr=serializer.validated_data.get('credit_amount_inr'),
        )

        return success_response(
            data=QualityClaimSerializer(claim, context={'request': request}).data,
            message=f"Claim {claim.get_status_display()}."
        )


class AdminCustomerListView(APIView):
    """Admin customer directory with search and account status filters."""
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="List Customers (Admin)",
        parameters=[OpenApiParameter('q', str, description='Search by name or phone.'),
                    OpenApiParameter('active', bool, description='Filter by active status.')],
        responses={200: CustomerAdminSerializer(many=True)},
        tags=['Operations'],
    )
    def get(self, request):
        qs = CustomUser.objects.filter(user_type=CustomUser.UserType.CUSTOMER).order_by('-date_joined')
        query = request.query_params.get('q')
        if query:
            qs = qs.filter(Q(full_name__icontains=query) | Q(phone_number__icontains=query))
        active = request.query_params.get('active')
        if active is not None and active.lower() in ('true', 'false'):
            qs = qs.filter(is_active=active.lower() == 'true')
        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(CustomerAdminSerializer(page, many=True).data)


class AdminCustomerDetailView(APIView):
    """Admin can view a customer and activate/deactivate account access."""
    permission_classes = [IsAdminUser]

    def _get_customer(self, customer_id):
        try:
            return CustomUser.objects.get(id=customer_id, user_type=CustomUser.UserType.CUSTOMER)
        except CustomUser.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound('Customer not found.')

    @extend_schema(summary="Get Customer (Admin)", responses={200: CustomerAdminSerializer}, tags=['Operations'])
    def get(self, request, customer_id):
        return success_response(data=CustomerAdminSerializer(self._get_customer(customer_id)).data)

    @extend_schema(summary="Update Customer Access (Admin)", request=CustomerActivationSerializer,
                   responses={200: CustomerAdminSerializer}, tags=['Operations'])
    def patch(self, request, customer_id):
        customer = self._get_customer(customer_id)
        serializer = CustomerActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer.is_active = serializer.validated_data['is_active']
        customer.save(update_fields=['is_active'])
        from .services import record_audit
        record_audit(request.user, 'CUSTOMER_ACCESS_CHANGED', customer,
                     'Administrator changed customer account access.',
                     {'is_active': customer.is_active})
        return success_response(data=CustomerAdminSerializer(customer).data,
                                message='Customer access updated.')


class AdminAuditLogListView(APIView):
    """Read-only, paginated operational audit trail."""
    permission_classes = [IsAdminUser]

    @extend_schema(summary="Operational Audit History (Admin)",
                   parameters=[OpenApiParameter('action', str), OpenApiParameter('object_type', str)],
                   responses={200: AuditLogSerializer(many=True)}, tags=['Operations'])
    def get(self, request):
        qs = AuditLog.objects.select_related('actor').order_by('-created_at')
        if request.query_params.get('action'):
            qs = qs.filter(action=request.query_params['action'])
        if request.query_params.get('object_type'):
            qs = qs.filter(object_type=request.query_params['object_type'])
        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(AuditLogSerializer(page, many=True).data)


class AdminNotificationQueueView(APIView):
    """Read-only view of notification events waiting for provider integration."""
    permission_classes = [IsAdminUser]

    @extend_schema(summary="Notification Outbox (Admin)",
                   parameters=[OpenApiParameter('status', str), OpenApiParameter('event_type', str)],
                   responses={200: NotificationEventSerializer(many=True)}, tags=['Operations'])
    def get(self, request):
        qs = NotificationEvent.objects.select_related('recipient').order_by('created_at')
        if request.query_params.get('status'):
            qs = qs.filter(status=request.query_params['status'].upper())
        if request.query_params.get('event_type'):
            qs = qs.filter(event_type=request.query_params['event_type'])
        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(NotificationEventSerializer(page, many=True).data)


class PushDeviceRegistrationView(APIView):
    """Register or remove the authenticated user's current FCM device token."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Register Push Device", request=PushDeviceRegistrationSerializer,
                   responses={200: OpenApiResponse(description='Device registered')}, tags=['Operations'])
    def post(self, request):
        serializer = PushDeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        PushDevice.objects.update_or_create(
            registration_token=values['registration_token'],
            defaults={
                'user': request.user,
                'platform': values['platform'],
                'app_id': values['app_id'],
                'active': True,
            },
        )
        return success_response(data={'registered': True}, message='Push device registered.')

    @extend_schema(summary="Remove Push Device", request=PushDeviceDeleteSerializer,
                   responses={200: OpenApiResponse(description='Device removed')}, tags=['Operations'])
    def delete(self, request):
        serializer = PushDeviceDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        removed, _ = PushDevice.objects.filter(
            user=request.user,
            registration_token=serializer.validated_data['registration_token'],
        ).delete()
        return success_response(data={'removed': bool(removed)}, message='Push device removed.')


class AdminOperationsSummaryView(APIView):
    """Basic operational counts; excludes estimates and financial claims."""
    permission_classes = [IsAdminUser]

    @extend_schema(summary="Operational Summary (Admin)", tags=['Operations'])
    def get(self, request):
        orders = Order.objects.aggregate(
            total=Count('id'),
            pending_payment=Count('id', filter=Q(status=Order.OrderStatus.PENDING_PAYMENT)),
            confirmed=Count('id', filter=Q(status=Order.OrderStatus.CONFIRMED)),
            packed=Count('id', filter=Q(status=Order.OrderStatus.PACKED)),
            assigned=Count('id', filter=Q(status=Order.OrderStatus.ASSIGNED)),
            out_for_delivery=Count('id', filter=Q(status=Order.OrderStatus.OUT_FOR_DELIVERY)),
            delivered=Count('id', filter=Q(status=Order.OrderStatus.DELIVERED)),
            failed=Count('id', filter=Q(status=Order.OrderStatus.FAILED)),
            cancelled=Count('id', filter=Q(status=Order.OrderStatus.CANCELLED)),
        )
        return success_response(data={
            'orders': orders,
            'customers': {
                'total': CustomUser.objects.filter(user_type=CustomUser.UserType.CUSTOMER).count(),
                'active': CustomUser.objects.filter(user_type=CustomUser.UserType.CUSTOMER, is_active=True).count(),
            },
            'delivery_partners': {
                'total': DeliveryPartnerProfile.objects.count(),
                'active': DeliveryPartnerProfile.objects.filter(user__is_active=True).count(),
                'available': DeliveryPartnerProfile.objects.filter(user__is_active=True, is_available=True).count(),
            },
            'open_quality_claims': QualityClaim.objects.filter(
                status__in=[QualityClaim.ClaimStatus.SUBMITTED, QualityClaim.ClaimStatus.UNDER_REVIEW]
            ).count(),
            'generated_at': timezone.now(),
        })
