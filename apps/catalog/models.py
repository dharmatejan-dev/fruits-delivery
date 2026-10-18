import uuid
from django.db import models
from django.utils.text import slugify
from ..common.models import TimeStampedUUIDModel


class Category(TimeStampedUUIDModel):
    """
    Fruit categories (e.g., Seasonal Fruits, Tropical & Exotic, Citrus & Melons, Berries).
    """
    title = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    image_url = models.CharField(max_length=500, null=True, blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['sort_order', 'title']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class Product(TimeStampedUUIDModel):
    """
    Fruit product catalog item.
    """
    category = models.ForeignKey(
        Category,
        on_delete=models.RESTRICT,
        related_name='products'
    )
    name = models.CharField(max_length=150, db_index=True)
    slug = models.SlugField(max_length=180, unique=True)
    short_description = models.CharField(max_length=300)
    detailed_description = models.TextField(blank=True)
    nutrition_facts = models.JSONField(default=dict, blank=True, help_text="e.g. {'calories': '52 kcal', 'vitamin_c': '14% DV'}")
    storage_tips = models.TextField(blank=True)
    is_seasonal = models.BooleanField(default=False, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = 'Product'
        verbose_name_plural = 'Products'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Product.objects.filter(slug=slug).exclude(id=self.id).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProductImage(models.Model):
    """
    Fruit product photography and gallery.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='images'
    )
    image_url = models.CharField(max_length=500)
    alt_text = models.CharField(max_length=150, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Product Image'
        verbose_name_plural = 'Product Images'
        ordering = ['-is_primary', 'sort_order', 'created_at']

    def __str__(self):
        return f"Image for {self.product.name} ({'Primary' if self.is_primary else 'Gallery'})"


class ProductVariant(TimeStampedUUIDModel):
    """
    Selling unit variant for fruit (e.g. 500g, 1kg, 1 Dozen, 1 Box).
    """
    class UnitType(models.TextChoices):
        KG = 'KG', 'Kilogram'
        GRAM = 'GRAM', 'Gram'
        PIECE = 'PIECE', 'Piece'
        BOX = 'BOX', 'Box'
        DOZEN = 'DOZEN', 'Dozen'

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants'
    )
    unit_quantity = models.DecimalField(max_digits=8, decimal_places=2, help_text="Quantity value e.g. 1.0 or 500")
    unit_type = models.CharField(max_length=20, choices=UnitType.choices, default=UnitType.KG)
    mrp_inr = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price_inr = models.DecimalField(max_digits=10, decimal_places=2)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = 'Product Variant'
        verbose_name_plural = 'Product Variants'
        ordering = ['selling_price_inr']

    @property
    def display_unit(self):
        qty = int(self.unit_quantity) if self.unit_quantity == int(self.unit_quantity) else self.unit_quantity
        return f"{qty} {self.get_unit_type_display()}"

    @property
    def discount_percentage(self):
        if self.mrp_inr > self.selling_price_inr:
            discount = ((self.mrp_inr - self.selling_price_inr) / self.mrp_inr) * 100
            return int(round(discount))
        return 0

    def __str__(self):
        return f"{self.product.name} - {self.display_unit} (₹{self.selling_price_inr})"
