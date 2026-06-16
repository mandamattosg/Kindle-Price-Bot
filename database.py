from dotenv import load_dotenv
import os

from httpx import main
import psycopg
from dataclasses import dataclass

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg.connect(DATABASE_URL)


def add_product(asin, name, color, display_order):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO products (
            asin,
            name,
            color,
            display_order
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (asin) DO NOTHING
    """, (asin, name, color, display_order))

    conn.commit()
    conn.close()

    print(f"✅ Produto '{name}' adicionado")

def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    asin TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    color TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    display_order INTEGER DEFAULT 0
)
""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS price_history (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    public_price NUMERIC(10,2),
    prime_price NUMERIC(10,2),
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id)
)
""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id TEXT NOT NULL UNIQUE,
    username TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS user_products (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    UNIQUE(user_id, product_id)
)
""")

    conn.commit()
    conn.close()


def get_user_by_telegram_id(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, telegram_id, username
        FROM users
        WHERE telegram_id = %s
    """, (str(telegram_id),))

    row = cursor.fetchone()

    conn.close()

    return row


def add_user_product(telegram_id, product_id):
    user = get_user_by_telegram_id(telegram_id)

    if not user:
        return False

    user_id = user[0]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO user_products (
            user_id,
            product_id
        )
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
    """, (user_id, product_id))

    conn.commit()

    was_added = cursor.rowcount > 0

    conn.close()

    return was_added


def get_users_by_product(product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT users.telegram_id
        FROM user_products

        JOIN users
            ON users.id = user_products.user_id

        WHERE user_products.product_id = %s
    """, (product_id,))

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_product_by_id(product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, asin, name, color
        FROM products
        WHERE id = %s
    """, (product_id,))

    row = cursor.fetchone()

    conn.close()

    return row


def get_active_products():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, asin, name, color
    FROM products
    WHERE active = 1
    ORDER BY display_order ASC
    """)

    products = cursor.fetchall()

    conn.close()

    return products


def get_user_products(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            products.name,
            products.color,
            user_products.created_at,
            products.id

        FROM user_products

        JOIN users
            ON users.id = user_products.user_id

        JOIN products
            ON products.id = user_products.product_id

        WHERE users.telegram_id = %s
    """, (str(telegram_id),))

    rows = cursor.fetchall()

    conn.close()

    return rows


def remove_user_product(telegram_id, product_id):
    user = get_user_by_telegram_id(telegram_id)

    if not user:
        return

    user_id = user[0]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM user_products
        WHERE user_id = %s
        AND product_id = %s
    """, (user_id, product_id))

    conn.commit()
    conn.close()


@dataclass
class LastPrices:
    public_price: float | None
    prime_price: float | None


def save_prices(product_id, public_price=None, prime_price=None):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO price_history (
            product_id,
            public_price,
            prime_price
        )
        VALUES (%s, %s, %s)
    """, (
        product_id,
        public_price,
        prime_price
    ))

    conn.commit()
    conn.close()


def save_price(product_id, price=None, public_price=None, prime_price=None):
    if price is not None:
        public_price = price

    return save_prices(product_id, public_price=public_price, prime_price=prime_price)


def save_user(telegram_id, username):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (
            telegram_id,
            username
        )
        VALUES (%s, %s)
        ON CONFLICT (telegram_id) DO NOTHING
    """, (str(telegram_id), username))

    conn.commit()
    conn.close()


def get_latest_price(product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT public_price, prime_price
        FROM price_history
        WHERE product_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (product_id,))

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    public_price, prime_price = row
    return prime_price if prime_price is not None else public_price


def get_last_prices(product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            public_price,
            prime_price
        FROM price_history
        WHERE product_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (product_id,))

    row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    return LastPrices(
        public_price=row[0],
        prime_price=row[1]
    )


def get_last_price(product_id):
    last_prices = get_last_prices(product_id)
    if last_prices is None:
        return None
    return last_prices.prime_price if last_prices.prime_price is not None else last_prices.public_price


def get_all_users():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM users
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows


def delete_old_prices():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM price_history
        WHERE captured_at < NOW() - INTERVAL '90 days'
    """)

    conn.commit()
    conn.close()


    