from django.urls import path
from .views import (
    RazorpayPaymentVerifyView,
    RazorpayWebhookView,
)

urlpatterns = [
    path('razorpay/verify/', RazorpayPaymentVerifyView.as_view(), name='razorpay-verify'),
    path('razorpay/webhook/', RazorpayWebhookView.as_view(), name='razorpay-webhook'),
]
