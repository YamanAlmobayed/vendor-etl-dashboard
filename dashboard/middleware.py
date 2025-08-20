import logging

app_logger = logging.getLogger("my_app_logger")


class DRFAuthLoggingMiddleware:
    """
    Middleware that logs requests with DRF-authenticated users (Token/JWT).
    Must be added AFTER AuthenticationMiddleware and SessionMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Process request and get response (so DRF auth runs first)
        response = self.get_response(request)

        # After DRF authentication, request.user is set
        if hasattr(request, "user") and request.user.is_authenticated:
            user = getattr(request.user, "username", str(request.user))
        else:
            user = "Anonymous"

        app_logger.info(
            f"[Request] {request.method} {request.path} "
            f"-> Status {response.status_code} | User: {user}"
        )

        return response
