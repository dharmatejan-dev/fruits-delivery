import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework.exceptions import ValidationError
from apps.common.exceptions import custom_exception_handler
from apps.common.pagination import StandardPageNumberPagination
import psycopg


@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
def test_health_check_endpoint(client):
    """Verify health check endpoint returns 200 with standard envelope."""
    url = reverse('api-health-check')
    response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True
    assert "data" in data
    assert data["data"]["status"] == "healthy"
    assert data["data"]["database"] == "connected"
    assert "timestamp" in data["data"]


@pytest.mark.django_db
def test_standardized_error_envelope_on_404(client):
    """Verify 404 routes return standardized Orchard Harvest error envelope."""
    response = client.get('/api/v1/non-existent-endpoint/')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert data["success"] is False
    assert data["code"] == "NOT_FOUND"
    assert "errors" in data
    assert len(data["errors"]) > 0


@pytest.mark.django_db
def test_openapi_schema_endpoint(client):
    """Verify OpenAPI schema can be retrieved."""
    url = reverse('schema')
    response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert "openapi" in response.content.decode()


@pytest.mark.django_db
def test_swagger_and_redoc_ui_endpoints(client):
    """Verify Swagger UI and Redoc documentation pages load."""
    swagger_url = reverse('swagger-ui')
    redoc_url = reverse('redoc')

    swagger_resp = client.get(swagger_url)
    redoc_resp = client.get(redoc_url)

    assert swagger_resp.status_code == status.HTTP_200_OK
    assert redoc_resp.status_code == status.HTTP_200_OK


def test_custom_exception_handler_validation_error():
    """Verify custom exception handler formats DRF validation errors accurately."""
    exc = ValidationError({"phone_number": ["Enter a valid 10-digit mobile number."]})
    response = custom_exception_handler(exc, {})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["success"] is False
    assert response.data["code"] == "VALIDATION_ERROR"
    assert response.data["errors"] == [
        {"field": "phone_number", "message": "Enter a valid 10-digit mobile number."}
    ]


def test_postgresql_driver_availability():
    """Verify psycopg driver is importable and available for PostgreSQL connections."""
    assert psycopg.__version__ is not None
