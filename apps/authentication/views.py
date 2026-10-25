from rest_framework.views import APIView
from rest_framework import viewsets, permissions, status
from drf_spectacular.utils import extend_schema, OpenApiResponse
from ..common.responses import success_response
from .models import CustomUser, UserAddress
from .serializers import (
    OTPRequestSerializer,
    OTPVerifySerializer,
    StaffLoginSerializer,
    ChangePasswordSerializer,
    UserProfileSerializer,
    UserAddressSerializer,
)
from .services.otp import CustomerOTPService
from .tokens import get_tokens_for_user
from .permissions import IsCustomer, IsAddressOwner


def get_client_ip(request):
    """Utility to retrieve client IP address for audit and rate limiting."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class CustomerOTPRequestView(APIView):
    """
    Step 1 of Customer Authentication: Request OTP from the configured backend provider.
    Rate limited to 3 requests per 10 minutes with a 60-second resend cooldown.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Request Phone OTP",
        description="Requests a customer OTP from the configured backend provider for a +91 mobile number.",
        request=OTPRequestSerializer,
        responses={200: OpenApiResponse(description="OTP sent successfully")}
    )
    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data['phone_number']
        ip_address = get_client_ip(request)

        result = CustomerOTPService.request_otp(phone_number=phone_number, ip_address=ip_address)
        return success_response(
            data=result,
            message="OTP dispatched successfully to your mobile number."
        )


class CustomerOTPVerifyView(APIView):
    """
    Step 2 of Customer Authentication: Verify OTP, issue JWT tokens, and look up or create customer profile.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Verify OTP & Issue JWT",
        description="Verifies the configured provider's OTP and authenticates the customer, returning access and refresh JWT tokens.",
        request=OTPVerifySerializer,
        responses={200: OpenApiResponse(description="Authentication successful")}
    )
    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data['phone_number']
        otp = serializer.validated_data['otp']

        # Verify through the provider recorded for this OTP request.
        CustomerOTPService.verify_otp(phone_number=phone_number, otp_input=otp)

        # Lookup or create customer account
        user = CustomUser.objects.filter(phone_number=phone_number).first()
        is_new_customer = False

        if not user:
            user = CustomUser.objects.create_user(
                phone_number=phone_number,
                username=phone_number,
                user_type=CustomUser.UserType.CUSTOMER
            )
            is_new_customer = True

        tokens = get_tokens_for_user(user)
        user_data = UserProfileSerializer(user).data

        return success_response(
            data={
                "user": user_data,
                "tokens": tokens,
                "is_new_customer": is_new_customer,
                "profile_completed": user.is_profile_complete
            },
            message="Authentication successful."
        )


class StaffLoginView(APIView):
    """
    Login endpoint for Delivery Partners and Admins using username and password.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Staff / Partner Credentials Login",
        description="Authenticates delivery partners and administrators via username and password.",
        request=StaffLoginSerializer,
        responses={200: OpenApiResponse(description="Login successful")}
    )
    def post(self, request):
        serializer = StaffLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        tokens = get_tokens_for_user(user)
        user_data = UserProfileSerializer(user).data

        return success_response(
            data={
                "user": user_data,
                "tokens": tokens,
                "is_temp_password": user.is_temp_password
            },
            message="Login successful."
        )


class StaffChangePasswordView(APIView):
    """
    Mandatory password change for Delivery Partners and Admins.
    Clears the `is_temp_password` flag upon success.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Change Password",
        description="Allows staff and delivery partners to change their temporary password.",
        request=ChangePasswordSerializer,
        responses={200: OpenApiResponse(description="Password changed successfully")}
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data['old_password']):
            return success_response(
                data={},
                message="The current password provided is incorrect.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(serializer.validated_data['new_password'])
        user.is_temp_password = False
        user.save()

        # Generate fresh tokens
        tokens = get_tokens_for_user(user)

        return success_response(
            data={
                "tokens": tokens,
                "is_temp_password": False
            },
            message="Password updated successfully."
        )


class UserProfileView(APIView):
    """
    Retrieve or update the authenticated user's profile details.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get User Profile",
        responses={200: UserProfileSerializer}
    )
    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return success_response(
            data=serializer.data,
            message="Profile retrieved successfully."
        )

    @extend_schema(
        summary="Update User Profile",
        request=UserProfileSerializer,
        responses={200: UserProfileSerializer}
    )
    def patch(self, request):
        serializer = UserProfileSerializer(
            request.user,
            data=request.data,
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return success_response(
            data=serializer.data,
            message="Profile updated successfully."
        )


class UserAddressViewSet(viewsets.ModelViewSet):
    """
    Customer Delivery Addresses ViewSet.
    Allows listing, creating, updating, and soft-deleting saved delivery addresses in Vizianagaram.
    """
    serializer_class = UserAddressSerializer
    permission_classes = [IsCustomer, IsAddressOwner]

    def get_queryset(self):
        return UserAddress.objects.filter(
            user=self.request.user,
            is_deleted=False
        ).order_by('-is_default', '-created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return success_response(
            data=serializer.data,
            message="Addresses retrieved successfully."
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=self.get_serializer(instance).data,
            message="Address created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return success_response(
            data=serializer.data,
            message="Address details retrieved."
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return success_response(
            data=self.get_serializer(instance).data,
            message="Address updated successfully."
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_deleted = True
        instance.save(update_fields=['is_deleted'])
        return success_response(
            data={},
            message="Address deleted successfully."
        )
