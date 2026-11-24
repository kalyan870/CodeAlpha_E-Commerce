import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.mkdtemp(prefix="codealpha-shop-tests-")) / "shop.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["ADMIN_CLERK_USER_IDS"] = "clerk_admin_test"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from app.auth import is_admin, require_user
from app.database import get_engine, initialize_local_database
from app.main import app

initialize_local_database()
client = TestClient(app)


def auth_override(request: Request):
    token = request.headers.get("authorization", "")
    if token == "Bearer buyer-test":
        return "clerk_buyer_test"
    if token == "Bearer admin-test":
        return "clerk_admin_test"
    raise HTTPException(401, "Authentication required")


app.dependency_overrides[require_user] = auth_override


def setup_function():
    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM orders")
        conn.exec_driver_sql("UPDATE products SET stock=10 WHERE id=1")


def buyer_headers():
    return {"Authorization": "Bearer buyer-test"}


def test_auth_dependency_uses_only_verified_clerk_subject(monkeypatch):
    import app.auth as auth

    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "http://localhost:7860,https://shop.example")
    seen = {}

    class State:
        is_signed_in = True
        payload = {"sub": "clerk_user_from_verified_token"}

    def fake_authenticate(request, options):
        seen["options"] = options
        return State()

    monkeypatch.setattr(auth, "authenticate_request", fake_authenticate)
    scope = {"type": "http", "method": "GET", "path": "/api/me", "headers": [], "query_string": b"", "server": ("test", 80), "client": ("test", 1), "scheme": "http"}
    assert require_user(Request(scope)) == "clerk_user_from_verified_token"
    assert seen["options"].accepts_token == ["session_token"]


def test_unauthenticated_or_missing_provider_configuration_is_rejected(monkeypatch):
    import app.auth as auth

    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    scope = {"type": "http", "method": "GET", "path": "/api/me", "headers": [], "query_string": b"", "server": ("test", 80), "client": ("test", 1), "scheme": "http"}
    try:
        require_user(Request(scope))
        assert False, "missing Clerk configuration should fail closed"
    except HTTPException as exc:
        assert exc.status_code == 503


def test_customer_orders_are_owned_and_checkout_uses_server_prices():
    response = client.post("/api/orders", headers=buyer_headers(), json={"items": [{"product_id": 1, "quantity": 2}], "total": 0.01})
    assert response.status_code == 200
    order = response.json()
    assert order["total"] == 159.98
    assert order["user_id"] == "clerk_buyer_test"
    with get_engine().connect() as conn:
        assert conn.exec_driver_sql("SELECT stock FROM products WHERE id=1").scalar_one() == 8
    assert client.get("/api/orders", headers={"Authorization": "Bearer admin-test"}).json() == []


def test_invalid_quantities_and_stock_conflicts_leave_inventory_unchanged():
    assert client.post("/api/orders", headers=buyer_headers(), json={"items": [{"product_id": 1, "quantity": -1}]}).status_code == 422
    response = client.post("/api/orders", headers=buyer_headers(), json={"items": [{"product_id": 1, "quantity": 11}]})
    assert response.status_code == 409
    with get_engine().connect() as conn:
        assert conn.exec_driver_sql("SELECT stock FROM products WHERE id=1").scalar_one() == 10
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM orders").scalar_one() == 0


def test_admin_access_uses_explicit_clerk_id_allowlist():
    assert is_admin("clerk_admin_test")
    assert not is_admin("clerk_buyer_test")
    assert client.post("/api/products", headers=buyer_headers(), json={"name": "Nope", "price": 1}).status_code == 403


def test_storefront_inline_javascript_parses():
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(encoding="utf-8")
    scripts = [script for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL) if script.strip()]
    assert scripts
    result = subprocess.run(["node", "--check"], input=scripts[-1], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
