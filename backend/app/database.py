import os
from functools import lru_cache

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, text

metadata = MetaData()
products = Table(
    "products", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(160), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("price", Float, nullable=False),
    Column("image", String(1000), nullable=False, server_default=""),
    Column("category", String(80), nullable=False, server_default="general"),
    Column("stock", Integer, nullable=False, server_default="10"),
)
orders = Table(
    "orders", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(255), nullable=False),
    Column("items", Text, nullable=False),
    Column("total", Float, nullable=False),
    Column("status", String(40), nullable=False, server_default="pending"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

SEED_PRODUCTS = [
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


@lru_cache(maxsize=1)
def get_engine():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set on the server (use Neon for deployment).")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite:") else {}
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def initialize_local_database():
    """Create and seed a local SQLite demo DB; Neon is provisioned by the SQL migration."""
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Neon schema must be installed with the checked-in migration.")
    metadata.create_all(engine)
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM products")).scalar_one()
        if count == 0:
            conn.execute(products.insert(), [dict(name=n, description=d, price=p, image=i, category=c, stock=s) for n, d, p, i, c, s in SEED_PRODUCTS])
