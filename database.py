import sqlite3


DB_NAME = "kindle_bot.db"

def add_product(asin, name, color):
    conn = get_connection()
    cursor = conn.cursor()


    cursor.execute("""
    INSERT INTO products (asin, name, color)
    VALUES (?, ?, ?)
    """, (asin, name, color))

    conn.commit()
    conn.close()

    print(f"✅ Produto '{name}' adicionado")

def get_connection():
    return sqlite3.connect(DB_NAME)


def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    # tabela de produtos
    # cursor.execute("""
    # CREATE TABLE IF NOT EXISTS products (
    #     id INTEGER PRIMARY KEY AUTOINCREMENT,
    #     asin TEXT UNIQUE NOT NULL,
    #     name TEXT NOT NULL,
    #     color TEXT,
    #     active INTEGER DEFAULT 1,
    #     created_at TEXT DEFAULT CURRENT_TIMESTAMP
    # )
    # """)

    # histórico de preços
    # cursor.execute("""
    # CREATE TABLE IF NOT EXISTS price_history (
    #     id INTEGER PRIMARY KEY AUTOINCREMENT,
    #     product_id INTEGER NOT NULL,
    #     price REAL NOT NULL,
    #     captured_at TEXT DEFAULT CURRENT_TIMESTAMP,

    #     FOREIGN KEY(product_id) REFERENCES products(id)
    # )
    # """)

    # tabela de usuarios 
    # cursor.execute("""CREATE TABLE users (
    # id INTEGER PRIMARY KEY AUTOINCREMENT,
    # telegram_id TEXT NOT NULL UNIQUE,
    # username TEXT,
    # created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    # );""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS user_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

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
        WHERE telegram_id = ?
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
        INSERT OR IGNORE INTO user_products (
            user_id,
            product_id
        )
        VALUES (?, ?)
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

        WHERE user_products.product_id = ?
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
        WHERE id = ?
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

        WHERE users.telegram_id = ?
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
        WHERE user_id = ?
        AND product_id = ?
    """, (user_id, product_id))

    conn.commit()
    conn.close()

def save_price(product_id, price):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO price_history (product_id, price)
    VALUES (?, ?)
    """, (product_id, price))

    conn.commit()
    conn.close()


def save_user(telegram_id, username):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO users (
            telegram_id,
            username
        )
        VALUES (?, ?)
    """, (str(telegram_id), username))

    conn.commit()
    conn.close()


def get_latest_price(product_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT price
        FROM price_history
        WHERE product_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (product_id,))

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None

def get_last_price(product_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT price
        FROM price_history
        WHERE product_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (product_id,))

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None

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
        WHERE captured_at < datetime('now', '-90 day')
    """)

    conn.commit()
    conn.close()

# if __name__ == "__main__":
#     create_tables()
#     #print("✅ Banco e tabelas criados com sucesso")
#     #add_product(
#     #    "B0FBSC6CPR",
#     #    "Kindle Scribe 32GB Sem Luz Frontal",
#     #    "Grafite"
#     #)
#     get_active_products()