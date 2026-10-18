from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    CustomerOTPRequestView,
    CustomerOTPVerifyView,
    StaffLoginView,
    StaffChangePasswordView,
    UserProfileView,
    UserAddressViewSet,
)

router = DefaultRouter()
router.register(r'addresses', UserAddressViewSet, basename='user-address')

urlpatterns = [
    # Customer Phone OTP Flow
    path('customer/otp/request/', CustomerOTPRequestView.as_view(), name='customer-otp-request'),
    path('customer/otp/verify/', CustomerOTPVerifyView.as_view(), name='customer-otp-verify'),

    # Token Refresh
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),

    # Staff & Delivery Partner Flow
    path('staff/login/', StaffLoginView.as_view(), name='staff-login'),
    path('staff/change-password/', StaffChangePasswordView.as_view(), name='staff-change-password'),

    # Profile Management
    path('profile/', UserProfileView.as_view(), name='user-profile'),

    # Saved Addresses CRUD
    path('', include(router.urls)),
]
