import json
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import is_admin, require_user
from app.database import get_engine, initialize_local_database

@asynccontextmanager
async def lifespan(_app):
    if os.getenv("DATABASE_URL", "").startswith("sqlite:"):
        initialize_local_database()
    yield


app = FastAPI(title="CodeAlpha E-Commerce", lifespan=lifespan)
origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:7860,http://127.0.0.1:7860").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
app.mount("/static", StaticFiles(directory="frontend"), name="frontend")


@app.get("/")
def index():
    return FileResponse("frontend/index.html")


@app.get("/api/public-config")
def public_config():
    return {"clerk_publishable_key": os.getenv("CLERK_PUBLISHABLE_KEY", "")}


@app.get("/api/health")
def health():
    return {"ok": True, "configured": {"clerk": bool(os.getenv("CLERK_SECRET_KEY")), "database": bool(os.getenv("DATABASE_URL"))}}


@app.get("/api/me")
def me(user_id: str = Depends(require_user)):
    return {"id": user_id, "name": "Shopper", "is_admin": is_admin(user_id)}


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


def row_dict(row):
    return dict(row) if row is not None else None


@app.get("/api/products")
def list_products(category: str = ""):
    try:
        query = "SELECT * FROM products WHERE category = :category ORDER BY id DESC" if category else "SELECT * FROM products ORDER BY id DESC"
        params = {"category": category} if category else {}
        with get_engine().connect() as conn:
            rows = conn.execute(text(query), params).mappings().all()
            return [dict(row) for row in rows]
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None


@app.get("/api/products/{pid}")
def get_product(pid: int):
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT * FROM products WHERE id = :id"), {"id": pid}).mappings().first()
    if not row:
        raise HTTPException(404, "Product not found")
    return dict(row)


@app.post("/api/products")
def create_product(product: ProductCreate, user_id: str = Depends(require_user)):
    if not is_admin(user_id):
        raise HTTPException(403, "Admin only")
    with get_engine().begin() as conn:
        result = conn.execute(text("""INSERT INTO products (name, description, price, image, category, stock)
            VALUES (:name, :description, :price, :image, :category, :stock) RETURNING id"""), product.model_dump())
        row = conn.execute(text("SELECT * FROM products WHERE id = :id"), {"id": result.scalar_one()}).mappings().one()
        return dict(row)


@app.delete("/api/products/{pid}")
def delete_product(pid: int, user_id: str = Depends(require_user)):
    if not is_admin(user_id):
        raise HTTPException(403, "Admin only")
    with get_engine().begin() as conn:
        result = conn.execute(text("DELETE FROM products WHERE id = :id"), {"id": pid})
        if result.rowcount == 0:
            raise HTTPException(404, "Product not found")
    return {"ok": True}


@app.post("/api/orders")
def create_order(order: OrderCreate, user_id: str = Depends(require_user)):
    quantities = {}
    for item in order.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    normalized = []
    total = 0.0
    with get_engine().begin() as conn:
        for product_id, quantity in quantities.items():
            lock = " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
            product = conn.execute(text(f"SELECT id, name, price, stock FROM products WHERE id = :id{lock}"), {"id": product_id}).mappings().first()
            if not product:
                raise HTTPException(404, "Product not found")
            if product["stock"] < quantity:
                raise HTTPException(409, "Insufficient stock")
            changed = conn.execute(text("UPDATE products SET stock = stock - :qty WHERE id = :id AND stock >= :qty"), {"qty": quantity, "id": product_id})
            if changed.rowcount != 1:
                raise HTTPException(409, "Insufficient stock")
            total += float(product["price"]) * quantity
            normalized.append({"product_id": product_id, "name": product["name"], "quantity": quantity, "unit_price": float(product["price"])})
        result = conn.execute(text("""INSERT INTO orders (user_id, items, total) VALUES (:user_id, :items, :total) RETURNING id"""),
            {"user_id": user_id, "items": json.dumps(normalized), "total": round(total, 2)})
        row = conn.execute(text("SELECT * FROM orders WHERE id = :id"), {"id": result.scalar_one()}).mappings().one()
    return dict(row)


@app.get("/api/orders")
def list_orders(user_id: str = Depends(require_user)):
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT * FROM orders WHERE user_id = :user_id ORDER BY id DESC"), {"user_id": user_id}).mappings().all()
        return [dict(row) for row in rows]


@app.get("/api/orders/all")
def all_orders(user_id: str = Depends(require_user)):
    if not is_admin(user_id):
        raise HTTPException(403, "Admin only")
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT * FROM orders ORDER BY id DESC")).mappings().all()
        return [dict(row) for row in rows]
