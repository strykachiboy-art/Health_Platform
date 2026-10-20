from flask import Flask

from app.core.api import register_api_blueprint
from app.extensions import limiter
from app.modules.billing.routes import billing_route


def make_rate_limited_webhook_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="payment-webhook-rate-limit-test-secret",
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="memory://",
        RATELIMIT_HEADERS_ENABLED=False,
        PAYMENT_WEBHOOK_RATE_LIMIT="1 per minute",
    )

    limiter.init_app(app)
    register_api_blueprint(
        app,
        billing_route.billing_bp,
    )

    return app


def test_payment_webhook_rate_limit_blocks_repeated_request(
    monkeypatch,
):
    calls = []

    def fake_process_payment_webhook(**kwargs):
        calls.append(kwargs)
        return {
            "status": "processed_successful",
            "event_id": "evt-rate-limit-1",
            "payment_id": 10,
            "payment_status": "successful",
        }

    monkeypatch.setattr(
        billing_route,
        "process_payment_webhook",
        fake_process_payment_webhook,
    )

    client = make_rate_limited_webhook_app().test_client()
    url = "/api/v1/billing/webhooks/123/paystack"
    payload = b'{"event":"charge.success","data":{"id":"TX-1"}}'

    first = client.post(url, data=payload)
    second = client.post(url, data=payload)

    assert first.status_code == 200
    assert second.status_code == 429

    # Rate-limited requests must not enter payment processing.
    assert len(calls) == 1
    assert calls[0]["clinic_id"] == 123
    assert calls[0]["gateway"] == "paystack"
    assert calls[0]["payload"] == payload