from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestUvicornStartup:
    def test_get_application(self):
        import main

        app = main.get_application()
        assert isinstance(app, FastAPI)
        route_paths = [r.path for r in app.routes]
        assert "/health" in route_paths
        assert "/v1/status" in route_paths
        assert "/v1/login" in route_paths

    def test_main_app_asgi_exported(self):
        import main

        assert hasattr(main, "app")
        assert isinstance(main.app, FastAPI)

    def test_start_uvicorn_custom_args(self):
        import main

        mock_app = MagicMock()
        with patch("uvicorn.run") as mock_run:
            main.start_uvicorn(
                host="127.0.0.1",
                port=9000,
                reload=True,
                workers=4,
                log_level="debug",
                app_target=mock_app,
            )
            mock_run.assert_called_once_with(
                mock_app,
                host="127.0.0.1",
                port=9000,
                reload=True,
                workers=4,
                log_level="debug",
            )

    def test_run_server_delegation(self):
        import main

        with patch("main.start_uvicorn") as mock_start:
            main.run_server(host="0.0.0.0", port=8000, reload=False, workers=2, log_level="warning")
            mock_start.assert_called_once_with(
                host="0.0.0.0",
                port=8000,
                reload=False,
                workers=2,
                log_level="warning",
            )

    def test_cli_bot_start_uvicorn_function(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cli_bot_login_test", PROJECT_ROOT / "cli-bot" / "login.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert hasattr(mod, "start_uvicorn")
        with patch("uvicorn.run") as mock_run:
            mod.start_uvicorn(host="127.0.0.1", port=8005, reload=False, workers=1, log_level="info")
            mock_run.assert_called_once_with(
                mod.app,
                host="127.0.0.1",
                port=8005,
                reload=False,
                workers=1,
                log_level="info",
            )


class TestNginxConfiguration:
    @pytest.fixture
    def nginx_conf(self):
        conf_path = PROJECT_ROOT / "nginx" / "nginx.conf"
        assert conf_path.exists(), f"nginx.conf not found at {conf_path}"
        return conf_path.read_text(encoding="utf-8")

    def test_nginx_listens_on_port_80(self, nginx_conf):
        assert "listen 80;" in nginx_conf

    def test_nginx_upstream_points_to_app_8000(self, nginx_conf):
        assert "upstream vtop_backend" in nginx_conf
        assert "server app:8000;" in nginx_conf

    def test_nginx_proxy_headers(self, nginx_conf):
        assert "proxy_pass http://vtop_backend;" in nginx_conf
        assert "proxy_set_header Host $host;" in nginx_conf
        assert "proxy_set_header X-Real-IP $remote_addr;" in nginx_conf
        assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in nginx_conf
        assert "proxy_set_header X-Forwarded-Proto $scheme;" in nginx_conf

    def test_nginx_health_endpoint_routed(self, nginx_conf):
        assert "location /health" in nginx_conf
        assert "proxy_pass http://vtop_backend/health;" in nginx_conf


class TestDockerComposeNginxService:
    @pytest.fixture
    def compose_content(self):
        compose_file = PROJECT_ROOT / "docker-compose.yml"
        assert compose_file.exists()
        return compose_file.read_text(encoding="utf-8")

    def test_docker_compose_has_nginx_service(self, compose_content):
        assert "nginx:" in compose_content
        assert "${NGINX_PORT:-80}:80" in compose_content
        assert "./nginx/nginx.conf:/etc/nginx/nginx.conf:ro" in compose_content

    def test_docker_compose_app_uvicorn_command(self, compose_content):
        assert "command: [\"python\", \"main.py\", \"server\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]" in compose_content
