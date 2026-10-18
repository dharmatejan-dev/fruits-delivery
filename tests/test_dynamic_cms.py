import pytest
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from rest_framework.test import APIClient
from rest_framework import status
from apps.authentication.models import CustomUser
from apps.authentication.tokens import get_tokens_for_user
from apps.catalog.models import Category, Product, ProductVariant, ProductImage
from apps.inventory.models import InventoryRecord
from apps.cms.models import DynamicSection, BannerItem, SectionCollectionItem


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_admin(db):
    return CustomUser.objects.create_superuser(
        username="admin_cms_vzm",
        password="AdminSecurePassword@123",
        email="admin_cms@orchardharvest.in"
    )


@pytest.fixture
def test_customer(db):
    return CustomUser.objects.create_user(
        phone_number="+919876543230",
        username="+919876543230",
        user_type=CustomUser.UserType.CUSTOMER,
        full_name="Ramesh Babu"
    )


@pytest.fixture
def sample_catalog_item(db):
    category = Category.objects.create(
        title="Royal Mangoes",
        slug="royal-mangoes",
        description="Fresh Vizianagaram orchard mangoes.",
        sort_order=1
    )
    product = Product.objects.create(
        category=category,
        name="Suvarnarekha Mango",
        slug="suvarnarekha-mango",
        short_description="Locally grown juicy sweet mangoes.",
        is_seasonal=True,
        is_featured=True
    )
    variant = ProductVariant.objects.create(
        product=product,
        unit_quantity=Decimal('1.00'),
        unit_type=ProductVariant.UnitType.KG,
        mrp_inr=Decimal('150.00'),
        selling_price_inr=Decimal('120.00'),
        is_default=True
    )
    InventoryRecord.objects.create(
        variant=variant,
        available_stock=Decimal('40.00'),
        reserved_stock=Decimal('0.00')
    )
    ProductImage.objects.create(
        product=product,
        image_url="https://images.unsplash.com/mango-sample.jpg",
        is_primary=True
    )
    return product, category


# -----------------------------------------------------------------------------
# 1. Admin Section Creation & Reordering
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_create_dynamic_sections(api_client, test_admin):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    url = reverse('dynamic-section-list')

    # 1. Hero Carousel
    resp1 = api_client.post(url, {
        "section_key": "HERO_CAROUSEL",
        "title": "Fresh Daily Harvest",
        "subtitle": "Directly from orchards to your doorstep",
        "section_type": "BANNER_CAROUSEL",
        "sort_order": 1,
        "is_published": True
    }, format='json')
    assert resp1.status_code == status.HTTP_201_CREATED

    # 2. Curated Product Horizontal Scroll
    resp2 = api_client.post(url, {
        "section_key": "SEASONAL_PICKS",
        "title": "Season's Best Picks",
        "subtitle": "Limited harvest seasonal selections",
        "section_type": "PRODUCT_HORIZONTAL_SCROLL",
        "sort_order": 2,
        "is_published": True
    }, format='json')
    assert resp2.status_code == status.HTTP_201_CREATED

    # Check list endpoint
    list_resp = api_client.get(url)
    assert list_resp.status_code == status.HTTP_200_OK
    assert len(list_resp.json()["data"]) == 2


@pytest.mark.django_db
def test_bulk_reorder_sections(api_client, test_admin):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    s1 = DynamicSection.objects.create(
        section_key="SEC_A",
        title="Section A",
        section_type="CATEGORY_PILLS",
        sort_order=10
    )
    s2 = DynamicSection.objects.create(
        section_key="SEC_B",
        title="Section B",
        section_type="CATEGORY_PILLS",
        sort_order=20
    )

    reorder_url = reverse('dynamic-section-bulk-reorder')
    payload = [
        {"id": str(s1.id), "sort_order": 50},
        {"id": str(s2.id), "sort_order": 5}
    ]
    response = api_client.post(reorder_url, payload, format='json')
    assert response.status_code == status.HTTP_200_OK

    s1.refresh_from_db()
    s2.refresh_from_db()
    assert s1.sort_order == 50
    assert s2.sort_order == 5


# -----------------------------------------------------------------------------
# 2. Banners & Collection Product Management
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_banner_management(api_client, test_admin):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    section = DynamicSection.objects.create(
        section_key="MAIN_HERO",
        title="Main Hero",
        section_type="BANNER_CAROUSEL"
    )

    url = reverse('banner-item-list')
    resp = api_client.post(url, {
        "section": str(section.id),
        "title": "Summer Mango Festival 2026",
        "image_url": "https://images.unsplash.com/summer-mango-banner.jpg",
        "deeplink_type": "CATEGORY",
        "target_id": "royal-mangoes",
        "sort_order": 1,
        "is_active": True
    }, format='json')

    assert resp.status_code == status.HTTP_201_CREATED
    assert BannerItem.objects.filter(section=section).count() == 1


@pytest.mark.django_db
def test_collection_curation_add_and_remove_products(api_client, test_admin, sample_catalog_item):
    product, category = sample_catalog_item
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    section = DynamicSection.objects.create(
        section_key="CURATED_GRID",
        title="Curated Mangoes",
        section_type="PRODUCT_GRID"
    )

    manage_url = reverse('dynamic-section-manage-product', kwargs={'pk': str(section.id)})

    # Add product
    add_resp = api_client.post(manage_url, {
        "product_id": str(product.id),
        "action": "ADD",
        "sort_order": 1
    }, format='json')
    assert add_resp.status_code == status.HTTP_200_OK
    assert SectionCollectionItem.objects.filter(section=section, product=product).exists()

    # Remove product
    rem_resp = api_client.post(manage_url, {
        "product_id": str(product.id),
        "action": "REMOVE"
    }, format='json')
    assert rem_resp.status_code == status.HTTP_200_OK
    assert not SectionCollectionItem.objects.filter(section=section, product=product).exists()


# -----------------------------------------------------------------------------
# 3. Dynamic Customer Home Layout Resolution Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_customer_home_layout_resolution(api_client, sample_catalog_item):
    product, category = sample_catalog_item

    # 1. Carousel with banner
    sec_carousel = DynamicSection.objects.create(
        section_key="HOME_CAROUSEL",
        title="Special Offers",
        section_type="BANNER_CAROUSEL",
        sort_order=1,
        is_published=True
    )
    BannerItem.objects.create(
        section=sec_carousel,
        title="Daily Harvest Promo",
        image_url="https://images.unsplash.com/banner.jpg",
        deeplink_type="CATEGORY",
        target_id="royal-mangoes",
        is_active=True
    )

    # 2. Category Pills
    DynamicSection.objects.create(
        section_key="HOME_CATEGORIES",
        title="Explore Categories",
        section_type="CATEGORY_PILLS",
        sort_order=2,
        is_published=True
    )

    # 3. Product Horizontal Scroll
    sec_scroll = DynamicSection.objects.create(
        section_key="HOME_SCROLL",
        title="Featured Freshness",
        section_type="PRODUCT_HORIZONTAL_SCROLL",
        sort_order=3,
        is_published=True
    )
    SectionCollectionItem.objects.create(
        section=sec_scroll,
        product=product,
        sort_order=1
    )

    # Call customer endpoint
    url = reverse('customer-home-layout')
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["total_sections"] == 3

    # Validate Carousel content
    carousel = next(s for s in data["sections"] if s["section_key"] == "HOME_CAROUSEL")
    assert len(carousel["content"]) == 1
    assert carousel["content"][0]["title"] == "Daily Harvest Promo"

    # Validate Category Pills content
    categories_sec = next(s for s in data["sections"] if s["section_key"] == "HOME_CATEGORIES")
    assert len(categories_sec["content"]) >= 1
    assert categories_sec["content"][0]["title"] == "Royal Mangoes"

    # Validate Product Scroll content
    products_sec = next(s for s in data["sections"] if s["section_key"] == "HOME_SCROLL")
    assert len(products_sec["content"]) == 1
    assert products_sec["content"][0]["name"] == "Suvarnarekha Mango"
    assert products_sec["content"][0]["default_variant"]["selling_price_inr"] == "120.00"


@pytest.mark.django_db
def test_scheduling_and_active_window(api_client, sample_catalog_item):
    product, _ = sample_catalog_item
    now = timezone.now()

    # Section scheduled for future (should NOT appear)
    sec_future = DynamicSection.objects.create(
        section_key="FUTURE_SEC",
        title="Next Week Festival",
        section_type="PRODUCT_GRID",
        sort_order=1,
        is_published=True,
        active_from=now + timedelta(days=2)
    )
    SectionCollectionItem.objects.create(section=sec_future, product=product)

    # Unpublished section (should NOT appear)
    sec_unpub = DynamicSection.objects.create(
        section_key="UNPUBLISHED_SEC",
        title="Draft Section",
        section_type="PRODUCT_GRID",
        sort_order=2,
        is_published=False
    )
    SectionCollectionItem.objects.create(section=sec_unpub, product=product)

    url = reverse('customer-home-layout')
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    keys = [s["section_key"] for s in data["sections"]]
    assert "FUTURE_SEC" not in keys
    assert "UNPUBLISHED_SEC" not in keys


@pytest.mark.django_db
def test_empty_collection_graceful_filtering(api_client):
    # Empty product grid section without products
    DynamicSection.objects.create(
        section_key="EMPTY_GRID",
        title="Empty Curated Collection",
        section_type="PRODUCT_GRID",
        sort_order=1,
        is_published=True
    )

    url = reverse('customer-home-layout')
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    # Empty section must be gracefully omitted
    assert data["total_sections"] == 0


@pytest.mark.django_db
def test_customer_forbidden_from_cms_admin(api_client, test_customer):
    tokens = get_tokens_for_user(test_customer)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    url = reverse('dynamic-section-list')
    response = api_client.post(url, {
        "section_key": "HACK_SEC",
        "title": "Unauthorized Section",
        "section_type": "BANNER_CAROUSEL"
    }, format='json')

    assert response.status_code == status.HTTP_403_FORBIDDEN
