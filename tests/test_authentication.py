import pytest
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import AccessToken
import jwt
from apps.authentication.models import CustomUser, OTPRecord, UserAddress
from apps.authentication.services.msg91 import MSG91OTPService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_customer(db):
    return CustomUser.objects.create_user(
        phone_number="+919876543210",
        username="+919876543210",
        user_type=CustomUser.UserType.CUSTOMER,
        full_name="Lakshmi Narayana"
    )


@pytest.fixture
def test_delivery_partner(db):
    user = CustomUser.objects.create_user(
        username="partner_vizag_1",
        phone_number="+919123456780",
        password="TempPassword@123",
        user_type=CustomUser.UserType.DELIVERY_PARTNER,
        full_name="Raju Delivery",
        is_temp_password=True
    )
    return user


@pytest.fixture
def test_admin(db):
    return CustomUser.objects.create_superuser(
        username="admin_vzm",
        password="AdminSecurePassword@123",
        email="admin@orchardharvest.in"
    )


# -----------------------------------------------------------------------------
# 1. Phone OTP Request & Rate Limiting Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_otp_request_valid_indian_number(api_client):
    url = reverse('customer-otp-request')
    response = api_client.post(url, {"phone_number": "9876543210"}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True
    assert data["data"]["phone_number"] == "+919876543210"

    # Check OTP record created
    record = OTPRecord.objects.filter(phone_number="+919876543210").first()
    assert record is not None
    assert record.is_verified is False
    assert record.otp_hash.startswith("pbkdf2_sha256$")  # Must be hashed


@pytest.mark.django_db
def test_otp_request_invalid_number_format(api_client):
    url = reverse('customer-otp-request')
    response = api_client.post(url, {"phone_number": "12345"}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["success"] is False
    assert data["code"] == "VALIDATION_ERROR"
    assert any("Indian mobile number" in err["message"] for err in data["errors"])


@pytest.mark.django_db
def test_otp_request_resend_cooldown(api_client):
    url = reverse('customer-otp-request')
    phone = "9876543211"

    # First request
    resp1 = api_client.post(url, {"phone_number": phone}, format='json')
    assert resp1.status_code == status.HTTP_200_OK

    # Immediate second request (violates 60-second cooldown)
    resp2 = api_client.post(url, {"phone_number": phone}, format='json')
    assert resp2.status_code == status.HTTP_400_BAD_REQUEST
    assert "wait" in resp2.json()["errors"][0]["message"].lower()


@pytest.mark.django_db
def test_otp_request_rate_limiting_max_three_in_ten_minutes(api_client):
    phone = "+919876543212"
    now = timezone.now()

    # Pre-create 3 records in the last 10 minutes
    for i in range(3):
        OTPRecord.objects.create(
            phone_number=phone,
            otp_hash="mock_hash",
            expires_at=now + timedelta(minutes=5),
            created_at=now - timedelta(minutes=i + 1)
        )

    url = reverse('customer-otp-request')
    response = api_client.post(url, {"phone_number": phone}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "too many otp requests" in response.json()["errors"][0]["message"].lower()


# -----------------------------------------------------------------------------
# 2. OTP Verification & JWT Issuance Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_otp_verify_new_customer(api_client):
    phone = "+919876543213"

    # Request OTP
    api_client.post(reverse('customer-otp-request'), {"phone_number": phone}, format='json')
    record = OTPRecord.objects.filter(phone_number=phone).latest('created_at')

    # In sandbox mode, session_id stores the plaintext OTP: "sandbox_session_<otp>"
    otp = record.session_id.replace("sandbox_session_", "")

    # Verify OTP
    verify_url = reverse('customer-otp-verify')
    response = api_client.post(verify_url, {"phone_number": phone, "otp": otp}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True
    assert data["data"]["is_new_customer"] is True
    assert data["data"]["profile_completed"] is False
    assert "tokens" in data["data"]
    assert "access" in data["data"]["tokens"]
    assert "refresh" in data["data"]["tokens"]

    # Verify user was created
    user = CustomUser.objects.filter(phone_number=phone).first()
    assert user is not None
    assert user.user_type == CustomUser.UserType.CUSTOMER


@pytest.mark.django_db
def test_otp_verify_existing_customer(api_client, test_customer):
    phone = test_customer.phone_number

    api_client.post(reverse('customer-otp-request'), {"phone_number": phone}, format='json')
    record = OTPRecord.objects.filter(phone_number=phone).latest('created_at')
    otp = record.session_id.replace("sandbox_session_", "")

    verify_url = reverse('customer-otp-verify')
    response = api_client.post(verify_url, {"phone_number": phone, "otp": otp}, format='json')

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"]["is_new_customer"] is False
    assert data["data"]["user"]["full_name"] == "Lakshmi Narayana"
    assert data["data"]["profile_completed"] is True


@pytest.mark.django_db
def test_otp_verify_wrong_code_and_attempt_limit(api_client):
    phone = "+919876543214"
    api_client.post(reverse('customer-otp-request'), {"phone_number": phone}, format='json')

    verify_url = reverse('customer-otp-verify')

    # Send wrong OTP 4 times
    for i in range(4):
        resp = api_client.post(verify_url, {"phone_number": phone, "otp": "000000"}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "remaining" in resp.json()["errors"][0]["message"].lower()

    # 5th attempt invalidates the OTP
    resp5 = api_client.post(verify_url, {"phone_number": phone, "otp": "000000"}, format='json')
    assert resp5.status_code == status.HTTP_400_BAD_REQUEST
    assert "exceeded" in resp5.json()["errors"][0]["message"].lower()


# -----------------------------------------------------------------------------
# 3. JWT Custom Claims Verification
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_jwt_claims_payload(test_customer):
    from apps.authentication.tokens import get_tokens_for_user
    tokens = get_tokens_for_user(test_customer)
    access_token = tokens["access"]

    # Decode without verifying signature for claim inspection
    decoded = jwt.decode(access_token, options={"verify_signature": False})
    assert decoded["user_id"] == str(test_customer.id)
    assert decoded["user_type"] == "CUSTOMER"
    assert decoded["phone_number"] == test_customer.phone_number
    assert decoded["is_temp_password"] is False
    assert decoded["full_name"] == test_customer.full_name


@pytest.mark.django_db
@pytest.mark.parametrize('token_value', ['not-a-jwt', 'expired'])
def test_invalid_or_expired_access_token_is_rejected(api_client, test_customer, token_value):
    if token_value == 'expired':
        token = AccessToken.for_user(test_customer)
        token['exp'] = int(timezone.now().timestamp()) - 1
        token_value = str(token)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token_value}')
    response = api_client.get(reverse('user-profile'))
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# -----------------------------------------------------------------------------
# 4. Staff & Delivery Partner Authentication
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_delivery_partner_login_and_password_change(api_client, test_delivery_partner):
    login_url = reverse('staff-login')
    login_resp = api_client.post(login_url, {
        "username": "partner_vizag_1",
        "password": "TempPassword@123"
    }, format='json')

    assert login_resp.status_code == status.HTTP_200_OK
    login_data = login_resp.json()["data"]
    assert login_data["is_temp_password"] is True
    access_token = login_data["tokens"]["access"]

    # Attempt to change temporary password
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    change_url = reverse('staff-change-password')
    change_resp = api_client.post(change_url, {
        "old_password": "TempPassword@123",
        "new_password": "NewPermanentPassword@2026",
        "confirm_password": "NewPermanentPassword@2026"
    }, format='json')

    assert change_resp.status_code == status.HTTP_200_OK
    test_delivery_partner.refresh_from_db()
    assert test_delivery_partner.is_temp_password is False
    assert test_delivery_partner.check_password("NewPermanentPassword@2026")


@pytest.mark.django_db
def test_partner_temp_password_permission_restriction(test_delivery_partner):
    from apps.authentication.permissions import IsDeliveryPartner
    from rest_framework.request import Request
    from django.test import RequestFactory
    from rest_framework.exceptions import PermissionDenied

    factory = RequestFactory()
    request = factory.get('/')
    request.user = test_delivery_partner

    perm = IsDeliveryPartner()

    # When is_temp_password is True -> raises PermissionDenied
    with pytest.raises(PermissionDenied):
        perm.has_permission(request, None)

    # Once cleared -> granted
    test_delivery_partner.is_temp_password = False
    assert perm.has_permission(request, None) is True


# -----------------------------------------------------------------------------
# 5. Customer Profile & Saved Addresses CRUD
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_customer_profile_update(api_client, test_customer):
    from apps.authentication.tokens import get_tokens_for_user
    tokens = get_tokens_for_user(test_customer)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    profile_url = reverse('user-profile')

    # GET profile
    get_resp = api_client.get(profile_url)
    assert get_resp.status_code == status.HTTP_200_OK
    assert get_resp.json()["data"]["username"] == test_customer.username

    # PATCH profile
    patch_resp = api_client.patch(profile_url, {
        "full_name": "Lakshmi Narayana V",
        "email": "lakshmi@example.com"
    }, format='json')

    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["data"]["full_name"] == "Lakshmi Narayana V"
    assert patch_resp.json()["data"]["email"] == "lakshmi@example.com"


@pytest.mark.django_db
def test_customer_saved_address_crud(api_client, test_customer):
    from apps.authentication.tokens import get_tokens_for_user
    tokens = get_tokens_for_user(test_customer)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    addresses_url = reverse('user-address-list')

    # 1. Create Address in Vizianagaram
    create_resp = api_client.post(addresses_url, {
        "recipient_name": "Lakshmi Narayana",
        "recipient_phone": "9876543210",
        "address_line1": "Flat 202, Royal Residency",
        "landmark": "Near Clock Tower",
        "locality": "Cantonment",
        "city": "Vizianagaram",
        "state": "Andhra Pradesh",
        "pincode": "535003",
        "address_type": "HOME",
        "is_default": True
    }, format='json')

    assert create_resp.status_code == status.HTTP_201_CREATED
    address_id = create_resp.json()["data"]["id"]
    assert create_resp.json()["data"]["pincode"] == "535003"
    assert create_resp.json()["data"]["is_default"] is True

    # 2. List addresses
    list_resp = api_client.get(addresses_url)
    assert list_resp.status_code == status.HTTP_200_OK
    assert len(list_resp.json()["data"]) == 1

    # 3. Update address
    detail_url = reverse('user-address-detail', kwargs={'pk': address_id})
    update_resp = api_client.patch(detail_url, {
        "landmark": "Opposite SBI Branch"
    }, format='json')
    assert update_resp.status_code == status.HTTP_200_OK
    assert update_resp.json()["data"]["landmark"] == "Opposite SBI Branch"

    # 4. Soft-delete address
    delete_resp = api_client.delete(detail_url)
    assert delete_resp.status_code == status.HTTP_200_OK

    # Verify address soft-deleted
    addr = UserAddress.objects.get(id=address_id)
    assert addr.is_deleted is True

    # Active list should now be empty
    list_after_delete = api_client.get(addresses_url)
    assert len(list_after_delete.json()["data"]) == 0
