from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]

PROXY_CONFIGS = (
    ("nginx.conf", 8443),
    ("nginx.phase6.conf", 18443),
    ("nginx.phase9.conf", 19443),
)


def read_proxy_config(filename: str) -> str:
    return (
        REPO_ROOT
        / "deployment"
        / "nginx"
        / filename
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("filename", "https_port"),
    PROXY_CONFIGS,
)
def test_redirect_rejects_untrusted_hosts_and_uses_fixed_destination(
    filename,
    https_port,
):
    config = read_proxy_config(filename)

    assert config.count("server_name localhost;") == 2
    assert config.count("if ($host != localhost)") == 2
    assert (
        f"return 308 https://localhost:{https_port}$request_uri;"
        in config
    )
    assert "return 301 https://$host:" not in config


@pytest.mark.parametrize(
    ("filename", "https_port"),
    PROXY_CONFIGS,
)
def test_tls_transport_policy_is_explicit(
    filename,
    https_port,
):
    config = read_proxy_config(filename)

    assert "listen 8443 ssl default_server;" in config
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "ssl_ciphers " in config
    assert "ssl_session_cache shared:SSL:10m;" in config
    assert "ssl_session_timeout 10m;" in config
    assert "server_tokens off;" in config

    # These are localhost-certificate configs, not public-origin configs.
    assert "add_header Strict-Transport-Security" not in config
    assert "proxy_hide_header Strict-Transport-Security;" in config


@pytest.mark.parametrize(
    ("filename", "https_port"),
    PROXY_CONFIGS,
)
def test_request_limits_and_socketio_forwarding_are_configured(
    filename,
    https_port,
):
    config = read_proxy_config(filename)

    assert "client_max_body_size 16m;" in config
    assert "client_header_timeout 15s;" in config
    assert "client_body_timeout 60s;" in config
    assert "send_timeout 60s;" in config

    assert "proxy_connect_timeout 5s;" in config
    assert "proxy_read_timeout 75s;" in config
    assert "proxy_send_timeout 75s;" in config

    assert "map $http_upgrade $connection_upgrade {" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert "proxy_set_header Connection $connection_upgrade;" in config
    assert 'proxy_set_header Connection "upgrade";' not in config

    # Replace untrusted incoming X-Forwarded-For rather than appending to it.
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert (
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"
        not in config
    )

    assert "upstream clinic_api_backend {" in config

    if filename == "nginx.phase9.conf":
        assert "zone clinic_api_backend 64k;" in config
        assert "include /etc/nginx/upstreams/backend.conf;" in config
    else:
        assert "server backend:5000 resolve max_fails=2 fail_timeout=10s;" in config

    assert "proxy_pass http://clinic_api_backend;" in config
    assert (
        "proxy_next_upstream error timeout http_502 http_503 http_504;"
        in config
    )
    assert "proxy_next_upstream_tries 2;" in config
