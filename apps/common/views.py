from django.db import connection
from django.utils import timezone
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework import permissions, status
from drf_spectacular.utils import extend_schema, OpenApiResponse
from .responses import success_response
import logging

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """
    Health check endpoint verifying application runtime and database connectivity.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="API Health Check",
        description="Returns API status, database connectivity status, and current server time.",
        responses={
            200: OpenApiResponse(description="System is operational"),
            503: OpenApiResponse(description="Database or core service is degraded")
        }
    )
    def get(self, request):
        db_status = "connected"
        db_engine = connection.settings_dict.get('ENGINE', 'unknown')
        
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
        except Exception as e:
            logger.error("Health check database probe failed: %s", str(e))
            db_status = f"unhealthy: {str(e)}"
            return success_response(
                data={
                    "status": "degraded",
                    "database": db_status,
                    "engine": db_engine,
                    "timestamp": timezone.now().isoformat(),
                    "service": "Orchard Harvest API v1"
                },
                message="Service is experiencing degradation.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        return success_response(
            data={
                "status": "healthy",
                "database": db_status,
                "engine": db_engine,
                "timestamp": timezone.now().isoformat(),
                "service": "Orchard Harvest API v1"
            },
            message="Orchard Harvest API is fully operational."
        )


def custom_404_handler(request, exception=None):
    """Fallback 404 handler returning JSON envelope for API requests."""
    return JsonResponse(
        {
            "success": False,
            "code": "NOT_FOUND",
            "message": f"Endpoint not found: {request.path}",
            "errors": [{"field": "path", "message": "Resource or route does not exist."}]
        },
        status=404
    )


def custom_500_handler(request):
    """Fallback 500 handler returning JSON envelope."""
    return JsonResponse(
        {
            "success": False,
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An internal server error occurred.",
            "errors": [{"field": "server", "message": "Unexpected error."}]
        },
        status=500
    )
