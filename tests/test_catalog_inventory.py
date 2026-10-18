import pytest
from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework.exceptions import ValidationError
from apps.authentication.models import CustomUser
from apps.authentication.tokens import get_tokens_for_user
from apps.catalog.models import Category, Product, ProductVariant, ProductImage
from apps.inventory.models import InventoryRecord, StockAdjustmentLog
from apps.inventory.services import InventoryService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_admin(db):
    return CustomUser.objects.create_superuser(
        username="admin_catalog_vzm",
        password="AdminSecurePassword@123",
        email="admin@orchardharvest.in"
    )


@pytest.fixture
def test_customer(db):
    return CustomUser.objects.create_user(
        phone_number="+919876543220",
        username="+919876543220",
        user_type=CustomUser.UserType.CUSTOMER,
        full_name="Kavitha Reddy"
    )


@pytest.fixture
def sample_category(db):
    return Category.objects.create(
        title="Seasonal Harvest",
        slug="seasonal-harvest",
        description="Freshly plucked seasonal fruits from local orchards.",
        sort_order=1
    )


@pytest.fixture
def sample_product(db, sample_category):
    product = Product.objects.create(
        category=sample_category,
        name="Banganapalli Mango",
        slug="banganapalli-mango",
        short_description="Sweet and juicy golden Andhra mangoes.",
        detailed_description="Plucked at peak ripeness from organic Vizianagaram farms.",
        nutrition_facts={"calories": "60 kcal", "vitamin_c": "60%"},
        is_seasonal=True,
        is_featured=True
    )
    variant = ProductVariant.objects.create(
        product=product,
        unit_quantity=Decimal('1.00'),
        unit_type=ProductVariant.UnitType.KG,
        mrp_inr=Decimal('200.00'),
        selling_price_inr=Decimal('160.00'),
        is_default=True,
        is_active=True
    )
    InventoryRecord.objects.create(
        variant=variant,
        available_stock=Decimal('30.00'),
        reserved_stock=Decimal('0.00'),
        low_stock_threshold=Decimal('5.00')
    )
    ProductImage.objects.create(
        product=product,
        image_url="https://images.unsplash.com/photo-mango-sample",
        is_primary=True
    )
    return product


# -----------------------------------------------------------------------------
# 1. Category CRUD & Permission Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_customer_list_categories(api_client, sample_category):
    url = reverse('category-list')
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True
    assert len(data["data"]) >= 1
    assert data["data"][0]["title"] == "Seasonal Harvest"


@pytest.mark.django_db
def test_customer_cannot_create_category(api_client, test_customer):
    tokens = get_tokens_for_user(test_customer)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    url = reverse('category-list')
    response = api_client.post(url, {"title": "Exotic Fruits"}, format='json')

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_admin_create_and_update_category(api_client, test_admin):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    url = reverse('category-list')
    # Create
    create_resp = api_client.post(url, {
        "title": "Melons & Citrus",
        "description": "Hydrating farm fruits.",
        "sort_order": 2
    }, format='json')

    assert create_resp.status_code == status.HTTP_201_CREATED
    cat_id = create_resp.json()["data"]["id"]
    assert create_resp.json()["data"]["slug"] == "melons-citrus"

    # Update
    detail_url = reverse('category-detail', kwargs={'pk': cat_id})
    update_resp = api_client.patch(detail_url, {"description": "Updated hydrating fruits."}, format='json')
    assert update_resp.status_code == status.HTTP_200_OK
    assert update_resp.json()["data"]["description"] == "Updated hydrating fruits."


# -----------------------------------------------------------------------------
# 2. Product Creation with Initial Variant and Inventory
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_product_creation(api_client, test_admin, sample_category):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    url = reverse('product-list')
    payload = {
        "name": "Custard Apple (Sitaphal)",
        "category": sample_category.id,
        "short_description": "Creamy rich organic custard apples.",
        "detailed_description": "Harvested fresh from Vizianagaram hills.",
        "unit_quantity": "1.00",
        "unit_type": "KG",
        "mrp_inr": "180.00",
        "selling_price_inr": "140.00",
        "initial_stock": "25.00",
        "image_url": "https://images.unsplash.com/sitaphal-sample",
        "is_seasonal": True
    }
    response = api_client.post(url, payload, format='json')

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["name"] == "Custard Apple (Sitaphal)"
    assert data["slug"] == "custard-apple-sitaphal"
    assert len(data["variants"]) == 1
    assert data["variants"][0]["available_stock"] == 25.0
    assert data["variants"][0]["is_in_stock"] is True


# -----------------------------------------------------------------------------
# 3. Catalog Browsing, Search, and Filtering Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_catalog_search_and_category_filter(api_client, sample_product):
    # 1. Search Query
    search_url = reverse('product-list') + "?q=banganapalli"
    resp = api_client.get(search_url)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "Banganapalli Mango"

    # 2. Category Slug Filter
    cat_url = reverse('product-list') + "?category=seasonal-harvest"
    cat_resp = api_client.get(cat_url)
    assert cat_resp.status_code == status.HTTP_200_OK
    assert len(cat_resp.json()["data"]) == 1

    # 3. Product Detail by Slug
    detail_url = reverse('product-detail', kwargs={'pk': 'banganapalli-mango'})
    detail_resp = api_client.get(detail_url)

    assert detail_resp.status_code == status.HTTP_200_OK
    detail_data = detail_resp.json()["data"]
    assert detail_data["slug"] == "banganapalli-mango"
    assert detail_data["nutrition_facts"]["calories"] == "60 kcal"
    assert len(detail_data["variants"]) == 1
    assert detail_data["variants"][0]["discount_percentage"] == 20  # (200 - 160) / 200 = 20%


# -----------------------------------------------------------------------------
# 4. Inventory Concurrency & Transactional Service Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_inventory_deduct_and_restore_concurrency(sample_product):
    variant = sample_product.variants.first()
    variant_id = variant.id

    # Initial stock: 30 available, 0 reserved
    record = InventoryService.deduct_stock(variant_id, Decimal('10.00'))
    assert record.available_stock == Decimal('20.00')
    assert record.reserved_stock == Decimal('10.00')

    # Restore 4 units (e.g. order cancelled)
    record_restored = InventoryService.restore_stock(variant_id, Decimal('4.00'))
    assert record_restored.available_stock == Decimal('24.00')
    assert record_restored.reserved_stock == Decimal('6.00')


@pytest.mark.django_db
def test_inventory_insufficient_stock_error(sample_product):
    variant = sample_product.variants.first()

    # Attempt to deduct 50 when only 30 is available
    with pytest.raises(ValidationError) as exc_info:
        InventoryService.deduct_stock(variant.id, Decimal('50.00'))

    assert "insufficient stock" in str(exc_info.value).lower()

    # Verify database state was NOT corrupted
    record = InventoryRecord.objects.get(variant_id=variant.id)
    assert record.available_stock == Decimal('30.00')


# -----------------------------------------------------------------------------
# 5. Administrative Stock Adjustment & Audit Trail Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_stock_adjustment_api(api_client, test_admin, sample_product):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    variant = sample_product.variants.first()
    url = reverse('inventory-adjust')

    payload = {
        "variant_id": str(variant.id),
        "new_available_stock": "55.00",
        "adjustment_type": "RESTOCK",
        "reason": "Fresh morning harvest delivery from Vizianagaram orchard."
    }
    response = api_client.post(url, payload, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["inventory"]["available_stock"] == "55.00"
    assert data["audit_log"]["previous_stock"] == "30.00"
    assert data["audit_log"]["new_stock"] == "55.00"
    assert data["audit_log"]["adjustment_type"] == "RESTOCK"

    # Verify audit log in DB
    log = StockAdjustmentLog.objects.filter(variant_id=variant.id).first()
    assert log is not None
    assert log.adjusted_by == test_admin
    assert log.reason == "Fresh morning harvest delivery from Vizianagaram orchard."


@pytest.mark.django_db
def test_admin_inventory_status_and_low_stock_filter(api_client, test_admin, sample_product):
    tokens = get_tokens_for_user(test_admin)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    variant = sample_product.variants.first()
    # Adjust stock down to 3 (below threshold of 5)
    InventoryService.adjust_stock(variant.id, Decimal('3.00'), user=test_admin, reason="Test low stock")

    url = reverse('inventory-status') + "?low_stock_only=true"
    response = api_client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert len(data) >= 1
    assert data[0]["is_low_stock"] is True
    assert data[0]["available_stock"] == "3.00"


@pytest.mark.django_db
def test_customer_forbidden_from_inventory_api(api_client, test_customer):
    tokens = get_tokens_for_user(test_customer)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    url = reverse('inventory-status')
    response = api_client.get(url)

    assert response.status_code == status.HTTP_403_FORBIDDEN
