from django.urls import path
from apps.inventory.views import (
    InventoryStatusView,
    StockAdjustmentView,
    StockAdjustmentHistoryView,
)

urlpatterns = [
    path('status/', InventoryStatusView.as_view(), name='inventory-status'),
    path('adjust/', StockAdjustmentView.as_view(), name='inventory-adjust'),
    path('history/', StockAdjustmentHistoryView.as_view(), name='inventory-history'),
]
