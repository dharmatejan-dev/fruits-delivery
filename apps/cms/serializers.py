from rest_framework import serializers
from .models import DynamicSection, BannerItem, SectionCollectionItem
from ..catalog.models import Product, Category
from ..catalog.serializers import ProductListSerializer, CategorySerializer


class BannerItemSerializer(serializers.ModelSerializer):
    """Promotional banner serializer."""
    class Meta:
        model = BannerItem
        fields = [
            'id',
            'section',
            'title',
            'image_url',
            'deeplink_type',
            'target_id',
            'sort_order',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class SectionCollectionItemSerializer(serializers.ModelSerializer):
    """Collection mapping item."""
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)

    class Meta:
        model = SectionCollectionItem
        fields = [
            'id',
            'section',
            'product',
            'product_name',
            'product_slug',
            'sort_order',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class DynamicSectionAdminSerializer(serializers.ModelSerializer):
    """Administrative serializer for dynamic home layout sections."""
    is_currently_active = serializers.BooleanField(read_only=True)
    banners = BannerItemSerializer(many=True, read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = DynamicSection
        fields = [
            'id',
            'section_key',
            'title',
            'subtitle',
            'section_type',
            'sort_order',
            'is_published',
            'is_currently_active',
            'active_from',
            'active_to',
            'metadata',
            'items_count',
            'banners',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'is_currently_active', 'created_at', 'updated_at']

    def get_items_count(self, obj):
        if obj.section_type == DynamicSection.SectionType.BANNER_CAROUSEL:
            return obj.banners.filter(is_active=True).count()
        return obj.collection_items.count()


class BulkReorderSerializer(serializers.Serializer):
    """Bulk sequence reordering item."""
    id = serializers.UUIDField(required=True)
    sort_order = serializers.IntegerField(required=True, min_value=0)


class CollectionProductActionSerializer(serializers.Serializer):
    """Payload to add, remove, or reposition a product in a section collection."""
    product_id = serializers.UUIDField(required=True)
    action = serializers.ChoiceField(choices=['ADD', 'REMOVE'], default='ADD')
    sort_order = serializers.IntegerField(required=False, default=0)


class HomeLayoutSectionSerializer(serializers.ModelSerializer):
    """
    Consumer-facing dynamic section serializer.
    Dynamically loads and bundles banners, category pills, or product cards.
    """
    content = serializers.SerializerMethodField()

    class Meta:
        model = DynamicSection
        fields = [
            'id',
            'section_key',
            'title',
            'subtitle',
            'section_type',
            'sort_order',
            'metadata',
            'content',
        ]

    def get_content(self, obj):
        if obj.section_type == DynamicSection.SectionType.BANNER_CAROUSEL:
            banners = obj.banners.filter(is_active=True).order_by('sort_order')
            return BannerItemSerializer(banners, many=True).data

        elif obj.section_type == DynamicSection.SectionType.CATEGORY_PILLS:
            categories = Category.objects.filter(is_active=True).order_by('sort_order', 'title')
            return CategorySerializer(categories, many=True).data

        elif obj.section_type in [
            DynamicSection.SectionType.PRODUCT_HORIZONTAL_SCROLL,
            DynamicSection.SectionType.PRODUCT_GRID
        ]:
            collection_memberships = obj.collection_items.select_related('product__category').prefetch_related(
                'product__images', 'product__variants__inventory'
            ).filter(product__is_active=True, product__category__is_active=True).order_by('sort_order')

            products = [item.product for item in collection_memberships]
            return ProductListSerializer(products, many=True).data

        return []
