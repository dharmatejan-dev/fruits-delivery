from rest_framework import serializers
from .models import Category, Product, ProductImage, ProductVariant
from ..inventory.models import InventoryRecord


class CategorySerializer(serializers.ModelSerializer):
    """Category serializer with product count."""
    products_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = [
            'id',
            'title',
            'slug',
            'description',
            'image_url',
            'sort_order',
            'is_active',
            'products_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class ProductImageSerializer(serializers.ModelSerializer):
    """Product gallery photography."""
    class Meta:
        model = ProductImage
        fields = [
            'id',
            'image_url',
            'alt_text',
            'is_primary',
            'sort_order',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ProductVariantSerializer(serializers.ModelSerializer):
    """Variant serializer including live stock availability."""
    display_unit = serializers.CharField(read_only=True)
    discount_percentage = serializers.IntegerField(read_only=True)
    available_stock = serializers.SerializerMethodField()
    is_in_stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            'id',
            'unit_quantity',
            'unit_type',
            'display_unit',
            'mrp_inr',
            'selling_price_inr',
            'discount_percentage',
            'is_default',
            'is_active',
            'available_stock',
            'is_in_stock',
        ]
        read_only_fields = ['id', 'display_unit', 'discount_percentage']

    def get_available_stock(self, obj):
        inventory = getattr(obj, 'inventory', None)
        return float(inventory.available_stock) if inventory else 0.0

    def get_is_in_stock(self, obj):
        inventory = getattr(obj, 'inventory', None)
        return bool(inventory and inventory.available_stock > 0)


class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight product card serializer for catalog browsing and search."""
    category_title = serializers.CharField(source='category.title', read_only=True)
    primary_image = serializers.SerializerMethodField()
    default_variant = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'slug',
            'category',
            'category_title',
            'short_description',
            'is_seasonal',
            'is_featured',
            'is_active',
            'primary_image',
            'default_variant',
            'created_at',
        ]

    def get_primary_image(self, obj):
        image = obj.images.filter(is_primary=True).first() or obj.images.first()
        return image.image_url if image else None

    def get_default_variant(self, obj):
        variant = obj.variants.filter(is_default=True, is_active=True).first() or obj.variants.filter(is_active=True).first()
        if variant:
            return ProductVariantSerializer(variant).data
        return None


class ProductDetailSerializer(serializers.ModelSerializer):
    """Comprehensive product detail view with variants and gallery."""
    category = CategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        source='category',
        write_only=True
    )
    images = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'slug',
            'category',
            'category_id',
            'short_description',
            'detailed_description',
            'nutrition_facts',
            'storage_tips',
            'is_seasonal',
            'is_featured',
            'is_active',
            'images',
            'variants',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class AdminProductCreateSerializer(serializers.ModelSerializer):
    """
    Administrative product creation serializer.
    Atomically creates the product, initial variant, and initializes inventory record.
    """
    unit_quantity = serializers.DecimalField(max_digits=8, decimal_places=2, write_only=True)
    unit_type = serializers.ChoiceField(choices=ProductVariant.UnitType.choices, write_only=True)
    mrp_inr = serializers.DecimalField(max_digits=10, decimal_places=2, write_only=True)
    selling_price_inr = serializers.DecimalField(max_digits=10, decimal_places=2, write_only=True)
    initial_stock = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, write_only=True)
    image_url = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'slug',
            'category',
            'short_description',
            'detailed_description',
            'nutrition_facts',
            'storage_tips',
            'is_seasonal',
            'is_featured',
            'is_active',
            'unit_quantity',
            'unit_type',
            'mrp_inr',
            'selling_price_inr',
            'initial_stock',
            'image_url',
        ]
        read_only_fields = ['id', 'slug']

    def create(self, validated_data):
        unit_quantity = validated_data.pop('unit_quantity')
        unit_type = validated_data.pop('unit_type')
        mrp_inr = validated_data.pop('mrp_inr')
        selling_price_inr = validated_data.pop('selling_price_inr')
        initial_stock = validated_data.pop('initial_stock', 0)
        image_url = validated_data.pop('image_url', None)

        product = Product.objects.create(**validated_data)

        variant = ProductVariant.objects.create(
            product=product,
            unit_quantity=unit_quantity,
            unit_type=unit_type,
            mrp_inr=mrp_inr,
            selling_price_inr=selling_price_inr,
            is_default=True,
            is_active=True
        )

        InventoryRecord.objects.create(
            variant=variant,
            available_stock=initial_stock,
            reserved_stock=0
        )

        if image_url:
            ProductImage.objects.create(
                product=product,
                image_url=image_url,
                is_primary=True
            )

        return product
