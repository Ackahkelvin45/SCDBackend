"""One error shape for every response: {"detail", "code", "fields"} (requirements §0)."""
from rest_framework import exceptions, status
from rest_framework.views import exception_handler as drf_exception_handler


class ApiError(exceptions.APIException):
    """Raise with a machine code and optional extra keys, e.g. ApiError(409, "possible_duplicate", "...", matches=[...])."""

    def __init__(self, status_code, code, detail, fields=None, **extra):
        self.status_code = status_code
        self.code = code
        self.fields = fields or {}
        self.extra = extra
        super().__init__(detail, code)


def _first_message(value):
    while isinstance(value, (list, dict)) and value:
        value = value[0] if isinstance(value, list) else next(iter(value.values()))
    return str(value) if value else ""


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unhandled → Django's 500, which never includes the exception text

    if isinstance(exc, ApiError):
        response.data = {"detail": str(exc.detail), "code": exc.code, "fields": exc.fields, **exc.extra}
        return response

    if isinstance(exc, exceptions.ValidationError):
        data = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
        non_field = data.pop("non_field_errors", None)
        fields = {k: [str(m) for m in (v if isinstance(v, list) else [v])] for k, v in data.items()}
        detail = _first_message(non_field) or "Please check the highlighted fields."
        response.data = {"detail": detail, "code": "invalid", "fields": fields}
        return response

    codes = exc.get_codes() if hasattr(exc, "get_codes") else None
    code = codes if isinstance(codes, str) else getattr(exc, "default_code", "error")
    if response.status_code == status.HTTP_404_NOT_FOUND:
        code = "not_found"
    response.data = {"detail": _first_message(response.data.get("detail", response.data)), "code": code, "fields": {}}
    return response
