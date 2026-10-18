import uuid
from django.db import models
from django.utils import timezone
from ..common.models import TimeStampedUUIDModel
from ..catalog.models import Product


class DynamicSection(TimeStampedUUIDModel):
    """
    Dynamic Home Page Section configurable from the Admin App.
    Controls home page hierarchy, presentation layout, and publication scheduling.
    """
    class SectionType(models.TextChoices):
        BANNER_CAROUSEL = 'BANNER_CAROUSEL', 'Banner Carousel'
        PRODUCT_HORIZONTAL_SCROLL = 'PRODUCT_HORIZONTAL_SCROLL', 'Product Horizontal Scroll'
        PRODUCT_GRID = 'PRODUCT_GRID', 'Product Grid'
        CATEGORY_PILLS = 'CATEGORY_PILLS', 'Category Pills'

    section_key = models.CharField(
        max_length=50,
        unique=True,
        help_text="Stable programmatic identifier (e.g., HERO_CAROUSEL, SEASONAL_HIGHLIGHTS, CITRUS_FESTIVAL)"
    )
    title = models.CharField(max_length=150)
    subtitle = models.CharField(max_length=255, blank=True)
    section_type = models.CharField(
        max_length=30,
        choices=SectionType.choices,
        default=SectionType.PRODUCT_HORIZONTAL_SCROLL
    )
    sort_order = models.IntegerField(default=0, help_text="Ascending display order on the customer home page.")
    is_published = models.BooleanField(default=True, db_index=True)
    active_from = models.DateTimeField(null=True, blank=True, help_text="Optional scheduled release start time.")
    active_to = models.DateTimeField(null=True, blank=True, help_text="Optional scheduled expiration time.")
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary UI tokens (e.g. {'badge': 'Farm Fresh', 'accent_color': '#588157'})"
    )

    class Meta:
        verbose_name = 'Dynamic Section'
        verbose_name_plural = 'Dynamic Sections'
        ordering = ['sort_order', 'created_at']

    @property
    def is_currently_active(self):
        """Checks if section is published and within its scheduled active window."""
        if not self.is_published:
            return False
        now = timezone.now()
        if self.active_from and self.active_from > now:
            return False
        if self.active_to and self.active_to < now:
            return False
        return True

    def __str__(self):
        return f"{self.title} ({self.get_section_type_display()}) [Order: {self.sort_order}]"


class BannerItem(models.Model):
    """
    Promotional banners within a BANNER_CAROUSEL section.
    """
    class DeeplinkType(models.TextChoices):
        CATEGORY = 'CATEGORY', 'Category'
        PRODUCT = 'PRODUCT', 'Product'
        EXTERNAL = 'EXTERNAL', 'External URL'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(
        DynamicSection,
        on_delete=models.CASCADE,
        related_name='banners'
    )
    title = models.CharField(max_length=150)
    image_url = models.CharField(max_length=500)
    deeplink_type = models.CharField(
        max_length=30,
        choices=DeeplinkType.choices,
        default=DeeplinkType.CATEGORY
    )
    target_id = models.CharField(
        max_length=200,
        blank=True,
        help_text="Target category slug, product slug, or external web URL"
    )
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Banner Item'
        verbose_name_plural = 'Banner Items'
        ordering = ['sort_order', 'created_at']

    def __str__(self):
        return f"Banner: {self.title} ({self.get_deeplink_type_display()} -> {self.target_id})"


class SectionCollectionItem(models.Model):
    """
    Mapping between a dynamic section and fruit products in its curated collection.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(
        DynamicSection,
        on_delete=models.CASCADE,
        related_name='collection_items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='section_memberships'
    )
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Section Collection Item'
        verbose_name_plural = 'Section Collection Items'
        ordering = ['sort_order', 'created_at']
        unique_together = ('section', 'product')

    def __str__(self):
        return f"{self.section.title} -> {self.product.name} (Position: {self.sort_order})"
