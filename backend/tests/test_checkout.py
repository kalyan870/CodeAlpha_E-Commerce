import os
import sys
import tempfile
from pathlib import Path

os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="codealpha-shop-tests-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app, get_db, tokens

client = TestClient(app)


def setup_function():
    tokens.clear()
    conn = get_db()
    conn.execute("DELETE FROM orders")
    conn.execute("DELETE FROM users")
    conn.execute("UPDATE products SET stock=10 WHERE id=1")
    conn.commit()
    conn.close()


def account():
    response = client.post("/api/register", json={"name": "Buyer", "email": "buyer@example.com", "password": "long-secure-pass"})
    assert response.status_code == 200
    result = response.json()
    return {"Authorization": f"Bearer {result['token']}"}


def test_auth_header_and_password_hash():
    headers = account()
    assert client.get("/api/me", headers=headers).status_code == 200
    assert client.get("/api/orders").status_code == 401
    conn = get_db()
    stored = conn.execute("SELECT password FROM users").fetchone()[0]
    conn.close()
    assert stored.startswith("pbkdf2_sha256$")


def test_checkout_ignores_client_total_and_updates_stock_atomically():
    headers = account()
    response = client.post("/api/orders", headers=headers, json={"items": [{"product_id": 1, "quantity": 2}], "total": 0.01})
    assert response.status_code == 200
    order = response.json()
    assert order["total"] == 159.98
    conn = get_db()
    assert conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0] == 8
    conn.close()


def test_invalid_quantities_and_insufficient_stock_do_not_change_stock():
    headers = account()
    assert client.post("/api/orders", headers=headers, json={"items": [{"product_id": 1, "quantity": -1}]}).status_code == 422
    response = client.post("/api/orders", headers=headers, json={"items": [{"product_id": 1, "quantity": 11}]})
    assert response.status_code == 409
    conn = get_db()
    assert conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0] == 10
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
    conn.close()


def test_default_admin_credential_is_not_seeded():
    conn = get_db()
    result = conn.execute("SELECT count(*) FROM users WHERE email='admin@codealpha.com' AND is_admin=1").fetchone()[0]
    conn.close()
    assert result == 0
