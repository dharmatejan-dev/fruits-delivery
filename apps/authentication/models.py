import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from ..common.models import TimeStampedUUIDModel


class CustomUserManager(BaseUserManager):
    """
    Custom user manager for Orchard Harvest supporting phone-based customers
    and username-based staff / delivery partners.
    """
    def create_user(self, username=None, phone_number=None, password=None, user_type='CUSTOMER', **extra_fields):
        if not username and not phone_number:
            raise ValueError('Either a username or a phone number must be provided.')

        if not username and phone_number:
            username = phone_number

        email = extra_fields.pop('email', None)
        if email:
            email = self.normalize_email(email)

        user = self.model(
            username=username,
            phone_number=phone_number,
            email=email,
            user_type=user_type,
            **extra_fields
        )

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('user_type', 'SUPERADMIN')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(username=username, password=password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Unified User model for Orchard Harvest:
    - CUSTOMER: authenticated via phone OTP
    - DELIVERY_PARTNER: admin-provisioned with temporary password
    - ADMIN / SUPERADMIN: management access
    """
    class UserType(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer'
        DELIVERY_PARTNER = 'DELIVERY_PARTNER', 'Delivery Partner'
        ADMIN = 'ADMIN', 'Admin'
        SUPERADMIN = 'SUPERADMIN', 'Superadmin'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True, db_index=True)
    phone_number = models.CharField(max_length=15, unique=True, null=True, blank=True, db_index=True)
    email = models.EmailField(max_length=254, null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True)
    user_type = models.CharField(
        max_length=20,
        choices=UserType.choices,
        default=UserType.CUSTOMER,
        db_index=True
    )
    is_temp_password = models.BooleanField(
        default=False,
        help_text='Indicates delivery partner must change temporary password on first login.'
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']

    def __str__(self):
        return f"{self.username} ({self.get_user_type_display()})"

    @property
    def is_customer(self):
        return self.user_type == self.UserType.CUSTOMER

    @property
    def is_delivery_partner(self):
        return self.user_type == self.UserType.DELIVERY_PARTNER

    @property
    def is_admin_user(self):
        return self.user_type in [self.UserType.ADMIN, self.UserType.SUPERADMIN] or self.is_staff

    @property
    def is_profile_complete(self):
        if self.is_customer:
            return bool(self.full_name and self.full_name.strip())
        return bool(self.full_name)


class OTPRecord(models.Model):
    """
    Stores cryptographically hashed OTP records with rate-limiting & expiration metadata.
    Plaintext OTP is NEVER persisted.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(max_length=15, db_index=True)
    otp_hash = models.CharField(max_length=255)
    provider = models.CharField(
        max_length=20,
        choices=[
            ('development_mock', 'Development Mock'),
            ('otp_com_test', 'OTP.com Test'),
            ('otp_com_live', 'OTP.com Live'),
            ('msg91', 'MSG91'),
        ],
        default='msg91',
    )
    session_id = models.CharField(max_length=100, blank=True)
    attempts_count = models.SmallIntegerField(default=0)
    is_verified = models.BooleanField(default=False)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = 'OTP Record'
        verbose_name_plural = 'OTP Records'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone_number', '-created_at']),
        ]

    def __str__(self):
        return f"OTP for {self.phone_number} at {self.created_at}"


class UserAddress(TimeStampedUUIDModel):
    """
    Saved delivery addresses for customers in Vizianagaram and Andhra Pradesh.
    """
    class AddressType(models.TextChoices):
        HOME = 'HOME', 'Home'
        WORK = 'WORK', 'Work'
        OTHER = 'OTHER', 'Other'

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='addresses'
    )
    recipient_name = models.CharField(max_length=100)
    recipient_phone = models.CharField(max_length=15)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    landmark = models.CharField(max_length=150, blank=True)
    locality = models.CharField(max_length=100)
    city = models.CharField(max_length=100, default='Vizianagaram')
    state = models.CharField(max_length=100, default='Andhra Pradesh')
    pincode = models.CharField(max_length=6, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address_type = models.CharField(
        max_length=20,
        choices=AddressType.choices,
        default=AddressType.HOME
    )
    is_default = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False, help_text='Soft-deleted to preserve order history.')

    class Meta:
        verbose_name = 'User Address'
        verbose_name_plural = 'User Addresses'
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f"{self.recipient_name} - {self.address_line1}, {self.city} ({self.pincode})"
