from rest_framework.views import APIView
from rest_framework import permissions, status
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from ..common.responses import success_response
from ..common.pagination import StandardPageNumberPagination
from ..authentication.permissions import IsCustomer, IsAdminUser
from .models import Order
from .serializers import (
    CartValidationRequestSerializer,
    OrderCreateRequestSerializer,
    OrderDetailSerializer,
    OrderCancelRequestSerializer,
    AdminOrderStatusUpdateSerializer,
)
from .services import OrderService


class CartValidationView(APIView):
    """
    Validates cart contents, recalculates real-time prices and availability,
    and returns authoritative total payable.
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Validate Cart & Calculate Totals",
        request=CartValidationRequestSerializer,
        responses={200: OpenApiResponse(description="Authoritative cart totals and item availability")}
    )
    def post(self, request):
        serializer = CartValidationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = OrderService.validate_cart(serializer.validated_data['items'])
        return success_response(
            data=result,
            message="Cart verified successfully."
        )


class OrderCreateView(APIView):
    """
    Customer checkout endpoint.
    Atomically creates the order, locks and deducts stock, and initiates payment.
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Create Order & Initiate Checkout",
        request=OrderCreateRequestSerializer,
        responses={201: OrderDetailSerializer}
    )
    def post(self, request):
        serializer = OrderCreateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = OrderService.create_order(
            customer=request.user,
            address_id=str(serializer.validated_data['address_id']),
            items_payload=serializer.validated_data['items'],
            payment_method=serializer.validated_data['payment_method'],
            customer_notes=serializer.validated_data.get('customer_notes', ''),
            idempotency_key=serializer.validated_data.get('idempotency_key'),
        )

        return success_response(
            data=OrderDetailSerializer(order).data,
            message=f"Order {order.order_number} placed successfully.",
            status_code=status.HTTP_201_CREATED
        )


class CustomerOrderListView(APIView):
    """
    List of orders placed by the authenticated customer.
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Customer Order History",
        parameters=[
            OpenApiParameter('status', str, description='Filter by order status')
        ]
    )
    def get(self, request):
        qs = Order.objects.filter(customer=request.user).prefetch_related('items', 'payment_record').order_by('-placed_at')
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrderDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class CustomerOrderDetailView(APIView):
    """
    Retrieve specific order details. Strictly scoped to customer ownership.
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Customer Order Details",
        responses={200: OrderDetailSerializer}
    )
    def get(self, request, order_identifier):
        # Support either order_number or UUID
        filter_kwargs = (
            {'id': order_identifier}
            if len(order_identifier) == 36 and '-' in order_identifier
            else {'order_number': order_identifier}
        )
        try:
            order = Order.objects.prefetch_related('items', 'payment_record').get(
                customer=request.user,
                **filter_kwargs
            )
        except Order.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Order not found or access denied.")

        return success_response(
            data=OrderDetailSerializer(order).data,
            message="Order details retrieved."
        )


class CustomerOrderCancelView(APIView):
    """
    Customer self-service cancellation for confirmed orders before packing.
    Atomically restores inventory stock.
    """
    permission_classes = [IsCustomer]

    @extend_schema(
        summary="Cancel Order",
        request=OrderCancelRequestSerializer,
        responses={200: OrderDetailSerializer}
    )
    def post(self, request, order_identifier):
        filter_kwargs = (
            {'id': order_identifier}
            if len(order_identifier) == 36 and '-' in order_identifier
            else {'order_number': order_identifier}
        )
        try:
            order = Order.objects.get(customer=request.user, **filter_kwargs)
        except Order.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Order not found or access denied.")

        serializer = OrderCancelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cancelled_order = OrderService.cancel_order(
            order=order,
            reason=serializer.validated_data['reason'],
            user=request.user
        )

        return success_response(
            data=OrderDetailSerializer(cancelled_order).data,
            message="Order cancelled successfully. Stock has been restored."
        )


class AdminOrderListView(APIView):
    """
    Administrative Order Control Desk.
    Provides complete visibility into orders, fulfillment statuses, and payment tracking.
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Admin Order Control Desk",
        parameters=[
            OpenApiParameter('status', str, description='Filter by order status'),
            OpenApiParameter('payment_status', str, description='Filter by payment status'),
            OpenApiParameter('q', str, description='Search by order number or customer phone'),
        ]
    )
    def get(self, request):
        qs = Order.objects.select_related('customer').prefetch_related('items', 'payment_record').order_by('-placed_at')

        status_param = request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        payment_status_param = request.query_params.get('payment_status')
        if payment_status_param:
            qs = qs.filter(payment_status=payment_status_param)

        query = request.query_params.get('q')
        if query:
            qs = qs.filter(order_number__icontains=query)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrderDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminOrderStatusUpdateView(APIView):
    """
    Administrative status transition endpoint (e.g., advancing to PACKED).
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Admin Update Order Status",
        request=AdminOrderStatusUpdateSerializer,
        responses={200: OrderDetailSerializer}
    )
    def patch(self, request, pk):
        try:
            order = Order.objects.get(id=pk)
        except Order.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound("Order not found.")

        serializer = AdminOrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data['status']
        if order.status != Order.OrderStatus.CONFIRMED or new_status != Order.OrderStatus.PACKED:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"status": ["Only confirmed orders may be marked packed here; payment and dispatch workflows own other transitions."]})
        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])

        return success_response(
            data=OrderDetailSerializer(order).data,
            message=f"Order {order.order_number} status updated to {order.get_status_display()}."
        )
