from flask import Flask

from app.core.api import register_api_blueprint
from app.core.auth.user.routes import auth_routes
from app.extensions import limiter


def make_google_rate_limited_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="google-rate-limit-test-secret",
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="memory://",
        RATELIMIT_HEADERS_ENABLED=False,
        AUTH_GOOGLE_RATE_LIMIT="1 per minute",
    )

    limiter.init_app(app)
    register_api_blueprint(
        app,
        auth_routes.auth_bp,
    )

    return app


def test_google_authorization_start_enforces_rate_limit(monkeypatch):
    monkeypatch.setattr(
        auth_routes,
        "get_google_authorization_url",
        lambda: (
            "https://accounts.google.com/o/oauth2/v2/auth?client_id=test",
            "test-state",
        ),
    )

    client = make_google_rate_limited_app().test_client()

    first = client.get("/api/v1/auth/google")
    second = client.get("/api/v1/auth/google")

    assert first.status_code == 200
    assert second.status_code == 429


def test_google_callback_enforces_rate_limit():
    client = make_google_rate_limited_app().test_client()
    callback = "/api/v1/auth/google/callback?error=access_denied"

    first = client.get(callback)
    second = client.get(callback)

    assert first.status_code == 400
    assert second.status_code == 429