from rest_framework_simplejwt.tokens import RefreshToken
from .models import CustomUser


class OrchardRefreshToken(RefreshToken):
    """
    Custom RefreshToken embedding Orchard Harvest role claims into the JWT payload.
    """
    @classmethod
    def for_user(cls, user: CustomUser):
        token = super().for_user(user)

        # Custom claims embedded directly in JWT payload
        token['user_id'] = str(user.id)
        token['username'] = user.username
        token['phone_number'] = user.phone_number or ''
        token['user_type'] = user.user_type
        token['is_temp_password'] = user.is_temp_password
        token['full_name'] = user.full_name or ''
        token['is_profile_complete'] = user.is_profile_complete

        return token


def get_tokens_for_user(user: CustomUser) -> dict:
    """
    Generates standard access and refresh JWT pair for a verified user.
    """
    refresh = OrchardRefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'token_type': 'Bearer'
    }
