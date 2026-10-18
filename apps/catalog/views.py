import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Count, Q, Min
from rest_framework import viewsets, permissions, status, serializers
from rest_framework.exceptions import ServiceUnavailable
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiParameter
from apps.common.responses import success_response
from apps.common.pagination import StandardPageNumberPagination
from apps.authentication.permissions import IsAdminUser
from apps.catalog.models import Category, Product, ProductImage, ProductVariant
from apps.catalog.serializers import (
    CategorySerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    AdminProductCreateSerializer,
    ProductVariantSerializer,
    ProductImageSerializer,
)
from apps.inventory.models import InventoryRecord


class ProductImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()

    def validate_image(self, image):
        if image.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('Image must be 10 MB or smaller.')
        return image


class ProductImageUploadView(APIView):
    """Upload a selected product photo to Cloudinary and return its CDN URL."""
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = ProductImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        credentials = settings.CLOUDINARY_STORAGE
        if not all(credentials.get(key) for key in ('CLOUD_NAME', 'API_KEY', 'API_SECRET')):
            raise ServiceUnavailable(
                'Cloudinary is not configured. Add its cloud name, API key, and API secret to the backend environment.'
            )

        image = serializer.validated_data['image']
        safe_name = image.name.replace(chr(92), '/').rsplit('/', 1)[-1]
        storage_name = f'catalog/products/{uuid.uuid4().hex}/{safe_name}'
        try:
            saved_name = default_storage.save(storage_name, image)
            image_url = default_storage.url(saved_name)
        except Exception as exc:
            raise ServiceUnavailable('Cloudinary could not store this product image.') from exc

        return success_response(
            data={'image_url': image_url},
            message='Product image uploaded to Cloudinary.',
            status_code=status.HTTP_201_CREATED,
        )


class CategoryViewSet(viewsets.ModelViewSet):
    """
    Fruit Category ViewSet.
    Public read access for customers; Admin-restricted management.
    """
    serializer_class = CategorySerializer
    pagination_class = None

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [IsAdminUser()]

    def get_queryset(self):
        is_admin = bool(
            self.request.user
            and self.request.user.is_authenticated
            and self.request.user.is_admin_user
        )
        qs = Category.objects.annotate(
            products_count=Count('products', filter=Q(products__is_active=True))
        )
        if not is_admin:
            qs = qs.filter(is_active=True)
        return qs.order_by('sort_order', 'title')

    def get_object(self):
        lookup = self.kwargs[self.lookup_field]
        queryset = self.filter_queryset(self.get_queryset())
        # Support lookup by either UUID or slug
        filter_kwargs = {'id': lookup} if len(lookup) == 36 and '-' in lookup else {'slug': lookup}
        obj = queryset.filter(**filter_kwargs).first()
        if not obj:
            from rest_framework.exceptions import NotFound
            raise NotFound(f"Category '{lookup}' not found.")
        self.check_object_permissions(self.request, obj)
        return obj

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return success_response(
            data=serializer.data,
            message="Categories retrieved successfully."
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(
            data=serializer.data,
            message="Category details retrieved."
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=self.get_serializer(instance).data,
            message="Category created successfully.",
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
            message="Category updated successfully."
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return success_response(
            data={},
            message="Category deleted successfully."
        )


class ProductViewSet(viewsets.ModelViewSet):
    """
    Fruit Products ViewSet.
    Provides customer catalog browsing, full-text search, category filtering,
    and administrative fruit management.
    """
    pagination_class = StandardPageNumberPagination

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [IsAdminUser()]

    def get_serializer_class(self):
        if self.action == 'list':
            return ProductListSerializer
        elif self.action == 'create':
            return AdminProductCreateSerializer
        return ProductDetailSerializer

    def get_queryset(self):
        is_admin = bool(
            self.request.user
            and self.request.user.is_authenticated
            and self.request.user.is_admin_user
        )
        qs = Product.objects.select_related('category').prefetch_related('images', 'variants__inventory')

        if not is_admin:
            qs = qs.filter(is_active=True, category__is_active=True)

        # 1. Search Query
        query = self.request.query_params.get('q')
        if query:
            qs = qs.filter(
                Q(name__icontains=query) |
                Q(short_description__icontains=query) |
                Q(detailed_description__icontains=query)
            )

        # 2. Filter by Category
        category_param = self.request.query_params.get('category')
        if category_param:
            if len(category_param) == 36 and '-' in category_param:
                qs = qs.filter(category_id=category_param)
            else:
                qs = qs.filter(category__slug=category_param)

        # 3. Filter by Seasonal / Featured
        if self.request.query_params.get('is_seasonal') == 'true':
            qs = qs.filter(is_seasonal=True)
        if self.request.query_params.get('is_featured') == 'true':
            qs = qs.filter(is_featured=True)

        # 4. Sorting
        sort = self.request.query_params.get('sort')
        if sort == 'price_asc':
            qs = qs.annotate(min_price=Min('variants__selling_price_inr')).order_by('min_price')
        elif sort == 'price_desc':
            qs = qs.annotate(min_price=Min('variants__selling_price_inr')).order_by('-min_price')
        elif sort == 'newest':
            qs = qs.order_by('-created_at')
        else:
            qs = qs.order_by('-is_featured', '-created_at')

        return qs

    def get_object(self):
        lookup = self.kwargs[self.lookup_field]
        queryset = self.filter_queryset(self.get_queryset())
        filter_kwargs = {'id': lookup} if len(lookup) == 36 and '-' in lookup else {'slug': lookup}
        obj = queryset.filter(**filter_kwargs).first()
        if not obj:
            from rest_framework.exceptions import NotFound
            raise NotFound(f"Product '{lookup}' not found.")
        self.check_object_permissions(self.request, obj)
        return obj

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return success_response(
            data=serializer.data,
            message="Products retrieved successfully."
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(
            data=serializer.data,
            message="Product details retrieved."
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        return success_response(
            data=ProductDetailSerializer(product).data,
            message="Product created successfully with initial variant and inventory.",
            status_code=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = ProductDetailSerializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=ProductDetailSerializer(instance).data,
            message="Product updated successfully."
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        return success_response(
            data={},
            message="Product archived successfully."
        )


class ProductVariantViewSet(viewsets.ModelViewSet):
    """
    Administrative management of product selling unit variants.
    """
    serializer_class = ProductVariantSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        product_id = self.request.query_params.get('product_id')
        if product_id:
            return ProductVariant.objects.filter(product_id=product_id)
        return ProductVariant.objects.all()

    def perform_create(self, serializer):
        variant = serializer.save()
        # Ensure inventory record is created
        InventoryRecord.objects.get_or_create(variant=variant, defaults={'available_stock': 0})


class ProductImageViewSet(viewsets.ModelViewSet):
    """
    Administrative gallery photo management.
    """
    serializer_class = ProductImageSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        product_id = self.request.query_params.get('product_id')
        if product_id:
            return ProductImage.objects.filter(product_id=product_id)
        return ProductImage.objects.all()
