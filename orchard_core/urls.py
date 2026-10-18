from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

handler404 = 'apps.common.views.custom_404_handler'
handler500 = 'apps.common.views.custom_500_handler'

api_v1_patterns = [
    path('', include('apps.common.urls')),
    path('auth/', include('apps.authentication.urls')),
    path('catalog/', include('apps.catalog.urls')),
    path('inventory/', include('apps.inventory.urls')),
    path('cms/', include('apps.cms.urls')),
    path('orders/', include('apps.orders.urls')),
    path('payments/', include('apps.payments.urls')),
    path('dispatch/', include('apps.dispatch.urls')),
    path('operations/', include('apps.operations.urls')),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # OpenAPI Documentation Endpoints
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # API Version 1
    path('api/v1/', include(api_v1_patterns)),
]

