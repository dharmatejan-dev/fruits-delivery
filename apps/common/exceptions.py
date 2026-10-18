from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
import logging

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Standardized DRF Exception Handler.
    Ensures every error adheres to the Orchard Harvest envelope:
    {
        "success": False,
        "code": "ERROR_CODE",
        "message": "Human readable summary",
        "errors": [{"field": "fieldName", "message": "Reason"}, ...]
    }
    """
    response = exception_handler(exc, context)

    if response is None:
        # Unhandled 500 server error
        logger.exception("Unhandled server exception encountered: %s", str(exc), exc_info=exc)
        return Response(
            {
                "success": False,
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please try again shortly.",
                "errors": [{"field": "non_field_errors", "message": str(exc)}]
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Determine standardized error code
    status_code = response.status_code
    error_code_mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_SERVER_ERROR",
    }
    
    if isinstance(exc, ValidationError):
        code = "VALIDATION_ERROR"
    else:
        code = getattr(exc, 'default_code', None)
        if not code:
            code = error_code_mapping.get(status_code, "API_ERROR")
        else:
            code = str(code).upper()

    # Normalize error detail into list of field errors
    formatted_errors = []
    message = "An error occurred during request processing."

    if isinstance(response.data, dict):
        if 'detail' in response.data:
            message = str(response.data['detail'])
            formatted_errors.append({"field": "non_field_errors", "message": message})
        else:
            message = "Validation error in submitted data."
            for field, value in response.data.items():
                if isinstance(value, list):
                    for msg in value:
                        formatted_errors.append({"field": field, "message": str(msg)})
                elif isinstance(value, dict):
                    formatted_errors.append({"field": field, "message": str(value)})
                else:
                    formatted_errors.append({"field": field, "message": str(value)})
    elif isinstance(response.data, list):
        for item in response.data:
            formatted_errors.append({"field": "non_field_errors", "message": str(item)})
    else:
        formatted_errors.append({"field": "non_field_errors", "message": str(response.data)})

    response.data = {
        "success": False,
        "code": code,
        "message": message,
        "errors": formatted_errors
    }

    return response
