from rest_framework.response import Response
from rest_framework import status


def success_response(data=None, message="Operation completed successfully.", status_code=status.HTTP_200_OK, extra_meta=None):
    """
    Standardized Success Response Envelope.
    {
        "success": True,
        "message": "...",
        "data": { ... },
        "meta": { ... } (optional)
    }
    """
    payload = {
        "success": True,
        "message": message,
        "data": data if data is not None else {}
    }
    if extra_meta:
        payload["meta"] = extra_meta
    return Response(payload, status=status_code)
