from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from .models import CustomUser


class IsCustomer(permissions.BasePermission):
    """
    Grants access only to authenticated and active customers.
    """
    message = "Access restricted to authenticated customers only."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.user_type == CustomUser.UserType.CUSTOMER
        )


class IsDeliveryPartner(permissions.BasePermission):
    """
    Grants access only to active delivery partners who have completed
    their mandatory first-time password change.
    """
    message = "Access restricted to active delivery partners."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_active):
            return False

        if request.user.user_type != CustomUser.UserType.DELIVERY_PARTNER:
            return False

        if request.user.is_temp_password:
            raise PermissionDenied(
                "You must change your temporary password before accessing partner features."
            )

        return True


class IsAdminUser(permissions.BasePermission):
    """
    Grants access only to active administrators or superadmins.
    """
    message = "Access restricted to authorized administrators only."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and (
                request.user.user_type in [CustomUser.UserType.ADMIN, CustomUser.UserType.SUPERADMIN]
                or request.user.is_staff
            )
        )


class IsAddressOwner(permissions.BasePermission):
    """
    Object-level permission ensuring users only access their own addresses.
    """
    message = "You do not have permission to view or modify this address."

    def has_object_permission(self, request, view, obj):
        return obj.user == request.user
