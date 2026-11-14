import os
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
import sqlite3, hashlib, secrets, json, hmac
from datetime import datetime

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="CodeAlpha E-Commerce")
origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:7860,http://127.0.0.1:7860").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
app.mount("/static", StaticFiles(directory="frontend"), name="frontend")

@app.get("/")
def index():
    return FileResponse("frontend/index.html")

DATA_DIR = os.getenv("APP_DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, "ecommerce.db")

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            image TEXT,
            category TEXT DEFAULT 'general',
            stock INTEGER DEFAULT 10
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            items TEXT NOT NULL,
            total REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

# Models
class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)

class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default="", max_length=3000)
    price: float = Field(gt=0, le=1000000)
    image: Optional[str] = Field(default="", max_length=1000)
    category: Optional[str] = Field(default="general", max_length=80)
    stock: Optional[int] = Field(default=10, ge=0, le=1000000)

class CartItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1000)

class OrderCreate(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=100)

def hash_pw(pw):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310_000)
    return "pbkdf2_sha256$310000$%s$%s" % (salt.hex(), digest.hex())

def verify_pw(pw, stored):
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, expected = stored.split("$")
            actual = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds)).hex()
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(hashlib.sha256(pw.encode()).hexdigest(), stored)
def gen_token(): return secrets.token_hex(32)

# In-memory token store (simple)
tokens = {}

def authenticated_user(authorization: str):
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
    return uid

@app.post("/api/register")
def register(u: UserCreate):
    conn = get_db()
    email = u.email.strip().lower()
    if conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        conn.close()
        raise HTTPException(400, "Email already registered")
    conn.execute("INSERT INTO users (name, email, password) VALUES (?,?,?)", (u.name.strip(), email, hash_pw(u.password)))
    conn.commit()
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    token = gen_token()
    tokens[token] = user_id
    user = conn.execute("SELECT id, name, email, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return {"token": token, "user": dict(user)}

@app.post("/api/login")
def login(u: UserLogin):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email=?", (u.email.strip().lower(),)).fetchone()
    if not row or not verify_pw(u.password, row["password"]):
        conn.close()
        raise HTTPException(401, "Invalid email or password")
    if not row["password"].startswith("pbkdf2_sha256$"):
        conn.execute("UPDATE users SET password=? WHERE id=?", (hash_pw(u.password), row["id"]))
        conn.commit()
    conn.close()
    user = dict(row)
    token = gen_token()
    tokens[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"], "is_admin": user["is_admin"]}}

@app.get("/api/me")
def me(authorization: str = Header(default="")):
    uid = authenticated_user(authorization)
    conn = get_db()
    row = conn.execute("SELECT id, name, email, is_admin FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row)

# Products
@app.get("/api/products")
def list_products(category: str = ""):
    conn = get_db()
    if category:
        rows = conn.execute("SELECT * FROM products WHERE category=? ORDER BY id DESC", (category,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/products/{pid}")
def get_product(pid: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Product not found")
    return dict(row)

@app.post("/api/products")
def create_product(p: ProductCreate, authorization: str = Header(default="")):
    uid = authenticated_user(authorization)
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not user or not user["is_admin"]:
        conn.close()
        raise HTTPException(403, "Admin only")
    conn.execute("INSERT INTO products (name, description, price, image, category, stock) VALUES (?,?,?,?,?,?)",
                 (p.name, p.description, p.price, p.image, p.category, p.stock))
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row)

@app.delete("/api/products/{pid}")
def delete_product(pid: int, authorization: str = Header(default="")):
    uid = authenticated_user(authorization)
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not user or not user["is_admin"]:
        conn.close()
        raise HTTPException(403, "Admin only")
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# Orders
@app.post("/api/orders")
def create_order(o: OrderCreate, authorization: str = Header(default="")):
    uid = authenticated_user(authorization)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        quantities = {}
        for item in o.items:
            quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
        normalized, total = [], 0.0
        for product_id, quantity in quantities.items():
            product = conn.execute("SELECT id, name, price, stock FROM products WHERE id=?", (product_id,)).fetchone()
            if not product:
                raise HTTPException(404, "Product not found")
            if product["stock"] < quantity:
                raise HTTPException(409, "Insufficient stock")
            total += product["price"] * quantity
            normalized.append({"product_id": product_id, "name": product["name"], "quantity": quantity, "unit_price": product["price"]})
        for product_id, quantity in quantities.items():
            conn.execute("UPDATE products SET stock=stock-? WHERE id=?", (quantity, product_id))
        cursor = conn.execute("INSERT INTO orders (user_id, items, total) VALUES (?,?,?)", (uid, json.dumps(normalized), round(total, 2)))
        row = conn.execute("SELECT * FROM orders WHERE id=?", (cursor.lastrowid,)).fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return dict(row)

@app.get("/api/orders")
def list_orders(authorization: str = Header(default="")):
    uid = authenticated_user(authorization)
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/orders/all")
def all_orders(authorization: str = Header(default="")):
    uid = authenticated_user(authorization)
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not user or not user["is_admin"]:
        conn.close()
        raise HTTPException(403, "Admin only")
    rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Seed some products
conn = get_db()
if not conn.execute("SELECT id FROM products LIMIT 1").fetchone():
    products = [
        ("Wireless Headphones", "Premium noise-cancelling Bluetooth headphones", 79.99, "https://picsum.photos/seed/headphones/400/400", "electronics", 25),
        ("Organic Cotton T-Shirt", "Soft, breathable organic cotton tee", 24.99, "https://picsum.photos/seed/tshirt/400/400", "clothing", 50),
        ("Stainless Steel Water Bottle", "Double-wall insulated, 750ml", 19.99, "https://picsum.photos/seed/bottle/400/400", "accessories", 30),
        ("Mechanical Keyboard", "RGB backlit mechanical keyboard", 89.99, "https://picsum.photos/seed/keyboard/400/400", "electronics", 15),
        ("Leather Wallet", "Genuine leather bifold wallet", 34.99, "https://picsum.photos/seed/wallet/400/400", "accessories", 40),
        ("Running Shoes", "Lightweight mesh running shoes", 59.99, "https://picsum.photos/seed/shoes/400/400", "clothing", 20),
        ("USB-C Hub", "7-in-1 USB-C hub with HDMI", 29.99, "https://picsum.photos/seed/usbhub/400/400", "electronics", 35),
        ("Canvas Backpack", "Vintage style canvas backpack", 44.99, "https://picsum.photos/seed/backpack/400/400", "accessories", 18),
        ("Smart Watch", "Fitness tracker with heart rate monitor", 129.99, "https://picsum.photos/seed/watch/400/400", "electronics", 12),
        ("Denim Jacket", "Classic blue denim jacket", 64.99, "https://picsum.photos/seed/jacket/400/400", "clothing", 22),
    ]
    for p in products:
        conn.execute("INSERT INTO products (name, description, price, image, category, stock) VALUES (?,?,?,?,?,?)", p)
    conn.commit()
conn.execute("UPDATE users SET is_admin=0 WHERE email=? AND password=?", ("admin@codealpha.com", hashlib.sha256(b"admin123").hexdigest()))
conn.commit()
conn.close()

# Optional first-admin bootstrap; credentials come only from deployment environment.
admin_email, admin_password = os.getenv("ADMIN_EMAIL", "").strip().lower(), os.getenv("ADMIN_PASSWORD", "")
if admin_email and admin_password:
    if len(admin_password) < 16:
        raise RuntimeError("ADMIN_PASSWORD must contain at least 16 characters")
    conn = get_db()
    if not conn.execute("SELECT id FROM users WHERE is_admin=1 LIMIT 1").fetchone():
        conn.execute("INSERT OR IGNORE INTO users (name,email,password,is_admin) VALUES (?,?,?,1)", ("Admin", admin_email, hash_pw(admin_password)))
        conn.commit()
    conn.close()
