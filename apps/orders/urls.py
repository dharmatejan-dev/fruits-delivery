from django.urls import path
from .views import (
    CartValidationView,
    OrderCreateView,
    CustomerOrderListView,
    CustomerOrderDetailView,
    CustomerOrderCancelView,
    AdminOrderListView,
    AdminOrderStatusUpdateView,
)

urlpatterns = [
    # Customer Cart & Checkout
    path('validate-cart/', CartValidationView.as_view(), name='cart-validate'),
    path('create/', OrderCreateView.as_view(), name='order-create'),
    path('my-orders/', CustomerOrderListView.as_view(), name='customer-orders-list'),
    path('details/<str:order_identifier>/', CustomerOrderDetailView.as_view(), name='customer-order-detail'),
    path('cancel/<str:order_identifier>/', CustomerOrderCancelView.as_view(), name='customer-order-cancel'),

    # Admin Order Desk
    path('admin/all/', AdminOrderListView.as_view(), name='admin-orders-list'),
    path('admin/<uuid:pk>/status/', AdminOrderStatusUpdateView.as_view(), name='admin-order-status-update'),
]
