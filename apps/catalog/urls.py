from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryViewSet,
    ProductViewSet,
    ProductVariantViewSet,
    ProductImageViewSet,
    ProductImageUploadView,
)

router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'variants', ProductVariantViewSet, basename='product-variant')
router.register(r'images', ProductImageViewSet, basename='product-image')

urlpatterns = [
    path('images/upload/', ProductImageUploadView.as_view(), name='product-image-upload'),
    path('', include(router.urls)),
]
