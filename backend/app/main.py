import os
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import sqlite3, hashlib, secrets, json
from datetime import datetime

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="CodeAlpha E-Commerce")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="frontend"), name="frontend")

@app.get("/")
def index():
    return FileResponse("frontend/index.html")

DB = "/data/ecommerce.db"
os.makedirs("/data", exist_ok=True)

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
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    price: float
    image: Optional[str] = ""
    category: Optional[str] = "general"
    stock: Optional[int] = 10

class CartItem(BaseModel):
    product_id: int
    quantity: int

class OrderCreate(BaseModel):
    items: list[CartItem]
    total: float

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
def gen_token(): return secrets.token_hex(32)

def get_user_by_token(auth: str):
    if not auth or not auth.startswith("Bearer "):
        return None
    token = auth.split(" ")[1]
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE password=? AND email LIKE '%@%'", (token,)).fetchone()
    conn.close()
    if row:
        u = dict(row)
        # verify the token matches a stored session token (for simplicity, use password field as token for session)
        # Actually let's use a simple approach: generate a session token
        return u
    return None

# In-memory token store (simple)
tokens = {}

@app.post("/api/register")
def register(u: UserCreate):
    conn = get_db()
    if conn.execute("SELECT id FROM users WHERE email=?", (u.email,)).fetchone():
        conn.close()
        raise HTTPException(400, "Email already registered")
    conn.execute("INSERT INTO users (name, email, password) VALUES (?,?,?)", (u.name, u.email, hash_pw(u.password)))
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
    row = conn.execute("SELECT * FROM users WHERE email=? AND password=?", (u.email, hash_pw(u.password))).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "Invalid email or password")
    user = dict(row)
    token = gen_token()
    tokens[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"], "is_admin": user["is_admin"]}}

@app.get("/api/me")
def me(authorization: str = ""):
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
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
def create_product(p: ProductCreate, authorization: str = ""):
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
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
def delete_product(pid: int, authorization: str = ""):
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
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
def create_order(o: OrderCreate, authorization: str = ""):
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
    items_json = json.dumps([i.model_dump() for i in o.items])
    conn = get_db()
    conn.execute("INSERT INTO orders (user_id, items, total) VALUES (?,?,?)", (uid, items_json, o.total))
    conn.commit()
    oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # reduce stock
    for item in o.items:
        conn.execute("UPDATE products SET stock = stock - ? WHERE id=?", (item.quantity, item.product_id))
    conn.commit()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    conn.close()
    return dict(row)

@app.get("/api/orders")
def list_orders(authorization: str = ""):
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/orders/all")
def all_orders(authorization: str = ""):
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else ""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Not authenticated")
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
    # Create admin: admin@codealpha.com / admin123
    conn.execute("INSERT OR IGNORE INTO users (name, email, password, is_admin) VALUES (?,?,?,?)",
                 ("Admin", "admin@codealpha.com", hash_pw("admin123"), 1))
    conn.commit()
conn.close()
