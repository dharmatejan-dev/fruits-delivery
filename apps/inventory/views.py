from rest_framework.views import APIView
from rest_framework import permissions, status
from drf_spectacular.utils import extend_schema, OpenApiParameter
from apps.common.responses import success_response
from apps.common.pagination import StandardPageNumberPagination
from apps.authentication.permissions import IsAdminUser
from apps.inventory.models import InventoryRecord, StockAdjustmentLog
from apps.inventory.serializers import (
    InventoryStatusSerializer,
    StockAdjustmentRequestSerializer,
    StockAdjustmentLogSerializer,
)
from apps.inventory.services import InventoryService


class InventoryStatusView(APIView):
    """
    Administrative inventory monitoring dashboard.
    Lists stock levels across all fruit variants with low-stock alerts.
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Inventory Stock Status",
        description="Returns stock levels, reserved quantities, and low stock indicators across all product variants.",
        parameters=[
            OpenApiParameter('low_stock_only', bool, description='Filter to items below low stock threshold'),
            OpenApiParameter('q', str, description='Search by fruit name'),
        ]
    )
    def get(self, request):
        qs = InventoryRecord.objects.select_related('variant__product').order_by('variant__product__name')

        low_stock_only = request.query_params.get('low_stock_only')
        if low_stock_only == 'true':
            qs = [record for record in qs if record.is_low_stock]
            serializer = InventoryStatusSerializer(qs, many=True)
            return success_response(
                data=serializer.data,
                message="Low stock inventory retrieved."
            )

        query = request.query_params.get('q')
        if query:
            qs = qs.filter(variant__product__name__icontains=query)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InventoryStatusSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class StockAdjustmentView(APIView):
    """
    Administrative stock adjustment with mandatory audit explanation.
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Adjust Variant Stock",
        description="Atomically updates variant available stock and writes an audit log entry.",
        request=StockAdjustmentRequestSerializer,
        responses={200: InventoryStatusSerializer}
    )
    def post(self, request):
        serializer = StockAdjustmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        variant_id = serializer.validated_data['variant_id']
        new_available_stock = serializer.validated_data['new_available_stock']
        adjustment_type = serializer.validated_data['adjustment_type']
        reason = serializer.validated_data['reason']

        record, log_entry = InventoryService.adjust_stock(
            variant_id=variant_id,
            new_available_stock=new_available_stock,
            user=request.user,
            reason=reason,
            adjustment_type=adjustment_type
        )

        return success_response(
            data={
                "inventory": InventoryStatusSerializer(record).data,
                "audit_log": StockAdjustmentLogSerializer(log_entry).data
            },
            message=f"Stock for {record.variant.product.name} ({record.variant.display_unit}) successfully updated to {new_available_stock}."
        )


class StockAdjustmentHistoryView(APIView):
    """
    Audit trail history for stock modifications.
    """
    permission_classes = [IsAdminUser]

    @extend_schema(
        summary="Stock Adjustment History",
        description="Returns audit trail of manual corrections, restocks, and spoilage disposals.",
        parameters=[
            OpenApiParameter('variant_id', str, description='Filter by variant UUID')
        ]
    )
    def get(self, request):
        qs = StockAdjustmentLog.objects.select_related('variant__product', 'adjusted_by').order_by('-created_at')
        variant_id = request.query_params.get('variant_id')
        if variant_id:
            qs = qs.filter(variant_id=variant_id)

        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = StockAdjustmentLogSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
