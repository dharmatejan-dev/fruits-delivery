from rest_framework.views import APIView
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from drf_spectacular.utils import extend_schema, OpenApiResponse
from ..common.responses import success_response
from ..authentication.permissions import IsAdminUser
from .models import DynamicSection, BannerItem, SectionCollectionItem
from ..catalog.models import Product
from .serializers import (
    DynamicSectionAdminSerializer,
    HomeLayoutSectionSerializer,
    BannerItemSerializer,
    BulkReorderSerializer,
    CollectionProductActionSerializer,
)


class HomeLayoutView(APIView):
    """
    Public Customer Home Page Layout Engine.
    Aggregates active published banners, category chips, and curated fruit collections
    in a single optimized, mobile-ready response payload.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Customer Dynamic Home Page Layout",
        description="Returns all active, scheduled, and published sections for rendering the dynamic customer home screen.",
        responses={200: OpenApiResponse(description="Dynamic home layout payload")}
    )
    def get(self, request):
        sections_qs = DynamicSection.objects.prefetch_related(
            'banners',
            'collection_items__product__category',
            'collection_items__product__images',
            'collection_items__product__variants__inventory'
        ).filter(is_published=True).order_by('sort_order')

        # Filter active sections and omit empty ones
        active_sections = []
        for section in sections_qs:
            if not section.is_currently_active:
                continue

            # Check if section has renderable content
            if section.section_type == DynamicSection.SectionType.BANNER_CAROUSEL:
                if not section.banners.filter(is_active=True).exists():
                    continue
            elif section.section_type in [
                DynamicSection.SectionType.PRODUCT_HORIZONTAL_SCROLL,
                DynamicSection.SectionType.PRODUCT_GRID
            ]:
                if not section.collection_items.filter(product__is_active=True).exists():
                    continue

            active_sections.append(section)

        serializer = HomeLayoutSectionSerializer(active_sections, many=True)
        return success_response(
            data={
                "sections": serializer.data,
                "total_sections": len(serializer.data)
            },
            message="Dynamic home layout retrieved successfully."
        )


class DynamicSectionViewSet(viewsets.ModelViewSet):
    """
    Administrative management of dynamic home layout sections,
    scheduling, visibility toggles, and collection curation.
    """
    serializer_class = DynamicSectionAdminSerializer
    permission_classes = [IsAdminUser]
    pagination_class = None

    def get_queryset(self):
        return DynamicSection.objects.prefetch_related('banners', 'collection_items').order_by('sort_order', 'created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return success_response(
            data=serializer.data,
            message="All dynamic sections retrieved."
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(
            data=serializer.data,
            message="Section details retrieved."
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=self.get_serializer(instance).data,
            message="Dynamic section created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=self.get_serializer(instance).data,
            message="Dynamic section updated successfully."
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return success_response(
            data={},
            message="Dynamic section deleted successfully."
        )

    @action(detail=False, methods=['post'], url_path='bulk-reorder')
    def bulk_reorder(self, request):
        """
        Reorders section display sequence in bulk.
        Accepts: [{"id": "<uuid>", "sort_order": <int>}, ...]
        """
        items = request.data
        if not isinstance(items, list):
            return success_response(
                data={},
                message="Expected a list of section reordering objects.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        serializer = BulkReorderSerializer(data=items, many=True)
        serializer.is_valid(raise_exception=True)

        for item in serializer.validated_data:
            DynamicSection.objects.filter(id=item['id']).update(sort_order=item['sort_order'])

        return success_response(
            data={},
            message="Dynamic sections reordered successfully."
        )

    @action(detail=True, methods=['post'], url_path='manage-product')
    def manage_product(self, request, pk=None):
        """
        Adds or removes a fruit product from this section's collection.
        Accepts: {"product_id": "<uuid>", "action": "ADD"|"REMOVE", "sort_order": 0}
        """
        section = self.get_object()
        serializer = CollectionProductActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data['product_id']
        action_type = serializer.validated_data['action']
        sort_order = serializer.validated_data.get('sort_order', 0)

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return success_response(
                data={},
                message="Product not found.",
                status_code=status.HTTP_404_NOT_FOUND
            )

        if action_type == 'ADD':
            membership, created = SectionCollectionItem.objects.update_or_create(
                section=section,
                product=product,
                defaults={'sort_order': sort_order}
            )
            msg = f"Added '{product.name}' to collection '{section.title}'."
        else:
            SectionCollectionItem.objects.filter(section=section, product=product).delete()
            msg = f"Removed '{product.name}' from collection '{section.title}'."

        return success_response(data={}, message=msg)


class BannerItemViewSet(viewsets.ModelViewSet):
    """
    Administrative promotional banner manager.
    """
    serializer_class = BannerItemSerializer
    permission_classes = [IsAdminUser]
    pagination_class = None

    def get_queryset(self):
        section_id = self.request.query_params.get('section_id')
        if section_id:
            return BannerItem.objects.filter(section_id=section_id).order_by('sort_order')
        return BannerItem.objects.all().order_by('sort_order')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return success_response(data=serializer.data, message="Banners retrieved successfully.")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=self.get_serializer(instance).data,
            message="Banner created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(data=self.get_serializer(instance).data, message="Banner updated successfully.")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return success_response(data={}, message="Banner deleted successfully.")
