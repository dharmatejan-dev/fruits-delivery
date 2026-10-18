from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    HomeLayoutView,
    DynamicSectionViewSet,
    BannerItemViewSet,
)

router = DefaultRouter()
router.register(r'sections', DynamicSectionViewSet, basename='dynamic-section')
router.register(r'banners', BannerItemViewSet, basename='banner-item')

urlpatterns = [
    # Customer Dynamic Home Page Layout Engine
    path('home-layout/', HomeLayoutView.as_view(), name='customer-home-layout'),

    # Admin CMS endpoints
    path('', include(router.urls)),
]
