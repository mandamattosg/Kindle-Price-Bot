from dotenv import load_dotenv
import os

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

    cursor.execute("""
CREATE TABLE IF NOT EXISTS user_product_state (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    last_seen_price NUMERIC(10,2),
    last_notified_price NUMERIC(10,2),
    pending_alert_old_price NUMERIC(10,2),
    pending_alert_new_price NUMERIC(10,2),
    pending_alert_prime BOOLEAN,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    UNIQUE(user_id, product_id)
)
""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS product_promotion_state (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    alert_old_price NUMERIC(10,2),
    alert_new_price NUMERIC(10,2),
    alert_prime BOOLEAN,
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id)
)
""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS alert_approvals (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    old_price NUMERIC(10,2) NOT NULL,
    new_price NUMERIC(10,2) NOT NULL,
    is_prime BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP,
    decided_by TEXT,
    admin_chat_id TEXT,
    admin_message_id BIGINT,
    source TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id),
    CHECK (status IN ('pending', 'approved', 'rejected'))
)
""")

    cursor.execute("""
CREATE TABLE IF NOT EXISTS notification_queue (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    product_id INTEGER,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    retry_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    button_url TEXT,
    old_price NUMERIC(10,2),
    new_price NUMERIC(10,2),
    is_prime BOOLEAN,
    error_message TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    CHECK (status IN ('pending', 'sending', 'sent', 'failed'))
)
""")

    # Backward-compatible migration for existing databases.
    cursor.execute("""
        ALTER TABLE user_product_state
        ADD COLUMN IF NOT EXISTS pending_alert_old_price NUMERIC(10,2)
    """)
    cursor.execute("""
        ALTER TABLE user_product_state
        ADD COLUMN IF NOT EXISTS pending_alert_new_price NUMERIC(10,2)
    """)
    cursor.execute("""
        ALTER TABLE user_product_state
        ADD COLUMN IF NOT EXISTS pending_alert_prime BOOLEAN
    """)

    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS button_url TEXT
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS old_price NUMERIC(10,2)
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS new_price NUMERIC(10,2)
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS is_prime BOOLEAN
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS error_message TEXT
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ALTER COLUMN product_id DROP NOT NULL
    """)

    cursor.execute("""
        ALTER TABLE alert_approvals
        ADD COLUMN IF NOT EXISTS admin_chat_id TEXT
    """)
    cursor.execute("""
        ALTER TABLE alert_approvals
        ADD COLUMN IF NOT EXISTS admin_message_id BIGINT
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


def ensure_alert_approval_table():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
CREATE TABLE IF NOT EXISTS alert_approvals (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    old_price NUMERIC(10,2) NOT NULL,
    new_price NUMERIC(10,2) NOT NULL,
    is_prime BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP,
    decided_by TEXT,
    admin_chat_id TEXT,
    admin_message_id BIGINT,
    source TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id),
    CHECK (status IN ('pending', 'approved', 'rejected'))
)
""")

    cursor.execute("""
        ALTER TABLE alert_approvals
        ADD COLUMN IF NOT EXISTS admin_chat_id TEXT
    """)
    cursor.execute("""
        ALTER TABLE alert_approvals
        ADD COLUMN IF NOT EXISTS admin_message_id BIGINT
    """)

    conn.commit()
    conn.close()


def ensure_notification_queue_table():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
CREATE TABLE IF NOT EXISTS notification_queue (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    product_id INTEGER,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    retry_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    button_url TEXT,
    old_price NUMERIC(10,2),
    new_price NUMERIC(10,2),
    is_prime BOOLEAN,
    error_message TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    CHECK (status IN ('pending', 'sending', 'sent', 'failed'))
)
""")

    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS button_url TEXT
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS old_price NUMERIC(10,2)
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS new_price NUMERIC(10,2)
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS is_prime BOOLEAN
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS error_message TEXT
    """)
    cursor.execute("""
        ALTER TABLE notification_queue
        ALTER COLUMN product_id DROP NOT NULL
    """)

    conn.commit()
    conn.close()


@dataclass
class NotificationQueueItem:
    id: int
    user_id: int
    telegram_id: str
    product_id: int | None
    message: str
    retry_count: int
    button_url: str | None
    old_price: float | None
    new_price: float | None
    is_prime: bool | None


def enqueue_notification(
    user_id,
    product_id,
    message,
    button_url=None,
    old_price=None,
    new_price=None,
    is_prime=None
):
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO notification_queue (
            user_id,
            product_id,
            message,
            status,
            available_at,
            button_url,
            old_price,
            new_price,
            is_prime,
            updated_at
        )
        SELECT %s, %s, %s, 'pending', CURRENT_TIMESTAMP, %s, %s, %s, %s, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1
            FROM notification_queue
            WHERE user_id = %s
              AND product_id = %s
              AND status IN ('pending', 'sending')
              AND COALESCE(new_price, -1) = COALESCE(%s, -1)
              AND COALESCE(is_prime, FALSE) = COALESCE(%s, FALSE)
        )
        RETURNING id
    """, (
        user_id,
        product_id,
        message,
        button_url,
        old_price,
        new_price,
        is_prime,
        user_id,
        product_id,
        new_price,
        is_prime
    ))

    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return row[0] if row else None


def enqueue_coupon_notifications_bulk(user_ids, message):
    ensure_notification_queue_table()

    clean_user_ids = []
    for user_id in user_ids:
        try:
            clean_user_ids.append(int(user_id))
        except (TypeError, ValueError):
            continue

    if not clean_user_ids:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        WITH input_users AS (
            SELECT DISTINCT UNNEST(%s::INTEGER[]) AS user_id
        ),
        inserted AS (
            INSERT INTO notification_queue (
                user_id,
                product_id,
                message,
                status,
                available_at,
                updated_at
            )
            SELECT
                input_users.user_id,
                NULL,
                %s,
                'pending',
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM input_users
            WHERE NOT EXISTS (
                SELECT 1
                FROM notification_queue queue
                WHERE queue.user_id = input_users.user_id
                  AND queue.product_id IS NULL
                  AND queue.status IN ('pending', 'sending')
                  AND queue.new_price IS NULL
                  AND COALESCE(queue.is_prime, FALSE) = FALSE
            )
            RETURNING id
        )
        SELECT COUNT(*)
        FROM inserted
        """,
        (clean_user_ids, message),
    )

    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return int(row[0]) if row else 0


def count_pending_notifications():
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM notification_queue
        WHERE status = 'pending'
          AND available_at <= CURRENT_TIMESTAMP
    """)

    row = cursor.fetchone()
    conn.close()

    return int(row[0]) if row else 0


def claim_pending_notifications(batch_size=50):
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        WITH picked AS (
            SELECT id
            FROM notification_queue
            WHERE status = 'pending'
              AND available_at <= CURRENT_TIMESTAMP
            ORDER BY created_at ASC, id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE notification_queue queue
        SET
            status = 'sending',
            updated_at = CURRENT_TIMESTAMP
        FROM picked, users
        WHERE queue.id = picked.id
          AND users.id = queue.user_id
        RETURNING
            queue.id,
            queue.user_id,
            users.telegram_id,
            queue.product_id,
            queue.message,
            queue.retry_count,
            queue.button_url,
            queue.old_price,
            queue.new_price,
            queue.is_prime
    """, (batch_size,))

    rows = cursor.fetchall()
    conn.commit()
    conn.close()

    items = []
    for row in rows:
        items.append(
            NotificationQueueItem(
                id=row[0],
                user_id=row[1],
                telegram_id=row[2],
                product_id=row[3],
                message=row[4],
                retry_count=row[5],
                button_url=row[6],
                old_price=row[7],
                new_price=row[8],
                is_prime=row[9]
            )
        )

    return items


def mark_notification_sent(notification_id):
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE notification_queue
        SET
            status = 'sent',
            sent_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP,
            error_message = NULL
        WHERE id = %s
    """, (notification_id,))

    conn.commit()
    conn.close()


def requeue_notification(notification_id, delay_seconds, error_message=None):
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE notification_queue
        SET
            status = 'pending',
            retry_count = retry_count + 1,
            available_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
            updated_at = CURRENT_TIMESTAMP,
            error_message = %s
        WHERE id = %s
    """, (
        max(0, int(delay_seconds)),
        error_message,
        notification_id
    ))

    conn.commit()
    conn.close()


def mark_notification_failed(notification_id, error_message=None):
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE notification_queue
        SET
            status = 'failed',
            retry_count = retry_count + 1,
            updated_at = CURRENT_TIMESTAMP,
            error_message = %s
        WHERE id = %s
    """, (
        error_message,
        notification_id
    ))

    conn.commit()
    conn.close()


def reset_stuck_sending_notifications(max_minutes=10):
    ensure_notification_queue_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE notification_queue
        SET
            status = 'pending',
            retry_count = retry_count + 1,
            available_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP,
            error_message = 'worker_restart'
        WHERE status = 'sending'
          AND updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 minute')
    """, (max(1, int(max_minutes)),))

    recovered = cursor.rowcount
    conn.commit()
    conn.close()

    return recovered


def acquire_notification_worker_lock(lock_key=982451653):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pg_try_advisory_lock(%s)", (int(lock_key),))
    row = cursor.fetchone()

    locked = bool(row[0]) if row else False
    if not locked:
        conn.close()
        return None

    return conn


def release_notification_worker_lock(lock_conn, lock_key=982451653):
    if lock_conn is None:
        return

    try:
        cursor = lock_conn.cursor()
        cursor.execute("SELECT pg_advisory_unlock(%s)", (int(lock_key),))
        lock_conn.commit()
    except Exception:
        pass
    finally:
        lock_conn.close()


def create_alert_approval_request(product_id, product_name, old_price, new_price, is_prime=False):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO alert_approvals (
            product_id,
            product_name,
            old_price,
            new_price,
            is_prime,
            status
        )
        VALUES (%s, %s, %s, %s, %s, 'pending')
        RETURNING id
    """, (
        product_id,
        product_name,
        old_price,
        new_price,
        bool(is_prime)
    ))

    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return row[0]


def get_or_create_alert_approval_request(
    product_id,
    product_name,
    old_price,
    new_price,
    is_prime=False
):
    """Atomically find the latest approval or create a pending one."""
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"alert-approval:{product_id}:{new_price}:{bool(is_prime)}",)
        )
        cursor.execute("""
            SELECT id, status
            FROM alert_approvals
            WHERE product_id = %s
              AND new_price = %s
              AND is_prime = %s
            ORDER BY id DESC
            LIMIT 1
        """, (product_id, new_price, bool(is_prime)))

        row = cursor.fetchone()
        if row:
            conn.commit()
            return row

        cursor.execute("""
            INSERT INTO alert_approvals (
                product_id,
                product_name,
                old_price,
                new_price,
                is_prime,
                status
            )
            VALUES (%s, %s, %s, %s, %s, 'pending')
            RETURNING id, status
        """, (
            product_id,
            product_name,
            old_price,
            new_price,
            bool(is_prime)
        ))

        row = cursor.fetchone()
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_alert_approval_status(approval_id):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT status
        FROM alert_approvals
        WHERE id = %s
    """, (approval_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return row[0]


def get_latest_alert_approval_status_by_signature(product_id, old_price, new_price, is_prime=False):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT status
        FROM alert_approvals
        WHERE product_id = %s
          AND new_price = %s
          AND is_prime = %s
        ORDER BY id DESC
        LIMIT 1
    """, (
        product_id,
        new_price,
        bool(is_prime)
    ))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return row[0]


def refresh_pending_alert_approval_request_by_signature(
    product_id,
    product_name,
    old_price,
    new_price,
    is_prime=False
):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE alert_approvals
        SET
            product_name = %s,
            old_price = %s,
            requested_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT id
            FROM alert_approvals
            WHERE product_id = %s
              AND new_price = %s
              AND is_prime = %s
              AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
        )
        AND old_price IS DISTINCT FROM %s
        RETURNING id, admin_chat_id, admin_message_id
    """, (
        product_name,
        old_price,
        product_id,
        new_price,
        bool(is_prime),
        old_price
    ))

    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return row if row else None


def claim_alert_approval_prompt_slot(approval_id):
    """Atomically reserve the right to send the admin prompt (admin_message_id=0 means in-flight)."""
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE alert_approvals
        SET admin_message_id = 0
        WHERE id = %s
          AND status = 'pending'
          AND admin_message_id IS NULL
        RETURNING id
    """, (approval_id,))

    claimed = cursor.fetchone() is not None
    conn.commit()
    conn.close()

    return claimed


def release_alert_approval_prompt_slot(approval_id):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE alert_approvals
        SET admin_message_id = NULL
        WHERE id = %s
          AND admin_message_id = 0
    """, (approval_id,))

    conn.commit()
    conn.close()


def set_alert_approval_admin_message_reference(approval_id, admin_chat_id, admin_message_id):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE alert_approvals
        SET
            admin_chat_id = %s,
            admin_message_id = %s
        WHERE id = %s
    """, (
        str(admin_chat_id) if admin_chat_id is not None else None,
        int(admin_message_id) if admin_message_id is not None else None,
        approval_id,
    ))

    conn.commit()
    conn.close()


def get_alert_approval_request(approval_id):
    ensure_alert_approval_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            product_id,
            product_name,
            old_price,
            new_price,
            is_prime,
            status,
            requested_at,
            decided_at,
            decided_by,
            source
        FROM alert_approvals
        WHERE id = %s
    """, (approval_id,))

    row = cursor.fetchone()
    conn.close()

    return row


def resolve_alert_approval_request(approval_id, approved, decided_by=None, source="telegram"):
    ensure_alert_approval_table()

    next_status = "approved" if approved else "rejected"

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE alert_approvals
        SET
            status = %s,
            decided_at = CURRENT_TIMESTAMP,
            decided_by = %s,
            source = %s
        WHERE id = %s
          AND status = 'pending'
    """, (
        next_status,
        str(decided_by) if decided_by is not None else None,
        source,
        approval_id
    ))

    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()

    return updated


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
            products.id,
            products.asin

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


def get_user_product_state(user_id, product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            last_seen_price,
            last_notified_price,
            pending_alert_old_price,
            pending_alert_new_price,
            pending_alert_prime
        FROM user_product_state
        WHERE user_id = %s
          AND product_id = %s
    """, (user_id, product_id))

    row = cursor.fetchone()
    conn.close()

    return row


def get_user_product_states_by_product(product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            user_products.user_id,
            users.telegram_id,
            user_product_state.last_seen_price,
            user_product_state.last_notified_price,
            user_product_state.pending_alert_old_price,
            user_product_state.pending_alert_new_price,
            user_product_state.pending_alert_prime
        FROM user_products
        JOIN users
            ON users.id = user_products.user_id
        LEFT JOIN user_product_state
            ON user_product_state.user_id = user_products.user_id
            AND user_product_state.product_id = user_products.product_id
        WHERE user_products.product_id = %s
    """, (product_id,))

    rows = cursor.fetchall()
    conn.close()

    return rows


def ensure_product_promotion_state_table():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
CREATE TABLE IF NOT EXISTS product_promotion_state (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    alert_old_price NUMERIC(10,2),
    alert_new_price NUMERIC(10,2),
    alert_prime BOOLEAN,
    stop_alerts_from_date DATE,
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id)
)
""")

    cursor.execute("""
        ALTER TABLE product_promotion_state
        ADD COLUMN IF NOT EXISTS stop_alerts_from_date DATE
    """)

    conn.commit()
    conn.close()


def get_active_product_promotion_state(product_id):
    ensure_product_promotion_state_table()

    conn = get_connection()
    cursor = conn.cursor()

    # Auto-encerramento de promoções que atingiram a data de corte definida pelo admin.
    cursor.execute("""
        UPDATE product_promotion_state
        SET
            is_active = FALSE,
            ended_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE product_id = %s
          AND is_active = TRUE
          AND stop_alerts_from_date IS NOT NULL
          AND stop_alerts_from_date <= CURRENT_DATE
    """, (product_id,))

    cursor.execute("""
        SELECT
            alert_old_price,
            alert_new_price,
            alert_prime,
            started_at
        FROM product_promotion_state
        WHERE product_id = %s
          AND is_active = TRUE
                    AND (
                            stop_alerts_from_date IS NULL
                            OR stop_alerts_from_date > CURRENT_DATE
                    )
    """, (product_id,))

    row = cursor.fetchone()
    conn.close()

    return row


def upsert_active_product_promotion_state(product_id, alert_old_price, alert_new_price, alert_prime=False):
    ensure_product_promotion_state_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO product_promotion_state (
            product_id,
            is_active,
            alert_old_price,
            alert_new_price,
            alert_prime,
            stop_alerts_from_date,
            started_at,
            ended_at
        ) VALUES (%s, TRUE, %s, %s, %s, NULL, CURRENT_TIMESTAMP, NULL)
        ON CONFLICT (product_id) DO UPDATE
        SET
            is_active = TRUE,
            alert_old_price = EXCLUDED.alert_old_price,
            alert_new_price = EXCLUDED.alert_new_price,
            alert_prime = EXCLUDED.alert_prime,
            stop_alerts_from_date = NULL,
            started_at = CASE
                WHEN product_promotion_state.is_active = TRUE
                THEN product_promotion_state.started_at
                ELSE CURRENT_TIMESTAMP
            END,
            ended_at = NULL,
            updated_at = CURRENT_TIMESTAMP
    """, (
        product_id,
        alert_old_price,
        alert_new_price,
        alert_prime
    ))

    conn.commit()
    conn.close()


def set_product_promotion_stop_date(product_id, stop_from_date):
    ensure_product_promotion_state_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE product_promotion_state
        SET
            stop_alerts_from_date = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE product_id = %s
          AND is_active = TRUE
    """, (
        stop_from_date,
        product_id
    ))

    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return updated


def deactivate_product_promotion_state(product_id):
    ensure_product_promotion_state_table()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE product_promotion_state
        SET
            is_active = FALSE,
            ended_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE product_id = %s
          AND is_active = TRUE
    """, (product_id,))

    conn.commit()
    conn.close()


def upsert_user_product_state(
    user_id,
    product_id,
    last_seen_price=None,
    last_notified_price=None,
    pending_alert_old_price=None,
    pending_alert_new_price=None,
    pending_alert_prime=None
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO user_product_state (
            user_id,
            product_id,
            last_seen_price,
            last_notified_price,
            pending_alert_old_price,
            pending_alert_new_price,
            pending_alert_prime
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, product_id) DO UPDATE
        SET
            last_seen_price = EXCLUDED.last_seen_price,
            last_notified_price = EXCLUDED.last_notified_price,
            pending_alert_old_price = EXCLUDED.pending_alert_old_price,
            pending_alert_new_price = EXCLUDED.pending_alert_new_price,
            pending_alert_prime = EXCLUDED.pending_alert_prime,
            updated_at = CURRENT_TIMESTAMP
    """, (
        user_id,
        product_id,
        last_seen_price,
        last_notified_price,
        pending_alert_old_price,
        pending_alert_new_price,
        pending_alert_prime
    ))

    conn.commit()
    conn.close()


def create_user_product_state_if_missing(user_id, product_id, last_seen_price=None, last_notified_price=None):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO user_product_state (
            user_id,
            product_id,
            last_seen_price,
            last_notified_price
        ) VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, product_id) DO NOTHING
    """, (
        user_id,
        product_id,
        last_seen_price,
        last_notified_price
    ))

    conn.commit()
    conn.close()


def delete_user_product_state(user_id, product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM user_product_state
        WHERE user_id = %s
          AND product_id = %s
    """, (user_id, product_id))

    conn.commit()
    conn.close()


def get_product_price_history(product_id, limit=500):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT public_price, prime_price
        FROM price_history
        WHERE product_id = %s
        ORDER BY id DESC
        LIMIT %s
    """, (product_id, limit))

    rows = cursor.fetchall()
    conn.close()

    return rows


def get_best_price(public_price, prime_price):
    if public_price is None and prime_price is None:
        return None
    if public_price is None:
        return prime_price
    if prime_price is None:
        return public_price
    return min(public_price, prime_price)


def get_best_price_info(public_price, prime_price):
    best_price = get_best_price(public_price, prime_price)
    if best_price is None:
        return None, False
    prime_best = (
        prime_price is not None
        and prime_price == best_price
        and (public_price is None or prime_price < public_price)
    )
    return best_price, prime_best


def get_product_current_best_price(product_id):
    last_prices = get_last_prices(product_id)
    if last_prices is None:
        return None
    return get_best_price(last_prices.public_price, last_prices.prime_price)


def get_product_current_best_price_info(product_id):
    last_prices = get_last_prices(product_id)
    if last_prices is None:
        return None, False
    return get_best_price_info(last_prices.public_price, last_prices.prime_price)


def get_product_promotion_baseline_best_price(product_id, current_best_price):
    if current_best_price is None:
        return None

    rows = get_product_price_history(product_id, limit=500)
    if not rows:
        return None

    for public_price, prime_price in rows[1:]:
        previous_price = get_best_price(public_price, prime_price)
        if previous_price is None:
            continue
        # only consider a previous baseline if it is higher than the current price
        # (i.e. the current price is an actual drop / promotion)
        if previous_price > current_best_price:
            return previous_price

    return None


def get_product_promotion_baseline(product_id):
    rows = get_product_price_history(product_id, limit=720)
    if not rows:
        return None

    current_best = get_best_price(rows[0][0], rows[0][1])
    if current_best is None:
        return None

    for public_price, prime_price in rows[1:]:
        previous_best = get_best_price(public_price, prime_price)
        if previous_best is None:
            continue
        # only return a baseline when the previous best was higher than current
        # meaning the current_best is a drop compared to that previous price
        if previous_best > current_best:
            return previous_best

    return None


def get_product_promotion_baseline_recent(product_id, days=7, limit=720):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT public_price, prime_price
        FROM price_history
        WHERE product_id = %s
          AND captured_at >= NOW() - make_interval(days => %s)
        ORDER BY id DESC
        LIMIT %s
    """, (product_id, days, limit))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return None

    current_best = get_best_price(rows[0][0], rows[0][1])
    if current_best is None:
        return None

    for public_price, prime_price in rows[1:]:
        previous_best = get_best_price(public_price, prime_price)
        if previous_best is None:
            continue
        if previous_best > current_best:
            return previous_best

    return None


def initialize_user_product_state(telegram_id, product_id, last_seen_price=None, last_notified_price=None):
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return False

    user_id = user[0]
    create_user_product_state_if_missing(
        user_id,
        product_id,
        last_seen_price,
        last_notified_price
    )

    return True


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

    cursor.execute("""
        DELETE FROM user_product_state
        WHERE user_id = %s
          AND product_id = %s
    """, (user_id, product_id))

    conn.commit()
    conn.close()


def remove_all_monitorings_by_telegram_id(telegram_id):
    user = get_user_by_telegram_id(telegram_id)

    if not user:
        return 0

    user_id = user[0]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM user_products
        WHERE user_id = %s
    """, (user_id,))
    removed_monitorings = cursor.rowcount

    cursor.execute("""
        DELETE FROM user_product_state
        WHERE user_id = %s
    """, (user_id,))

    conn.commit()
    conn.close()

    return removed_monitorings


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


def get_last_valid_prices(product_id):
    """Como get_last_prices, mas para cada campo usa o último registro
    não-nulo do histórico. Útil como fallback quando o registro mais
    recente tem public_price ou prime_price NULL."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            (SELECT public_price FROM price_history
             WHERE product_id = %s AND public_price IS NOT NULL
             ORDER BY id DESC LIMIT 1) AS public_price,
            (SELECT prime_price FROM price_history
             WHERE product_id = %s AND prime_price IS NOT NULL
             ORDER BY id DESC LIMIT 1) AS prime_price
    """, (product_id, product_id))

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


def get_users_with_active_monitorings():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT users.id, users.telegram_id
        FROM user_products
        JOIN users
            ON users.id = user_products.user_id
        JOIN products
            ON products.id = user_products.product_id
        WHERE products.active = 1
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_users_who_received_product_alert(product_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT users.id, users.telegram_id
        FROM notification_queue queue
        JOIN users
            ON users.id = queue.user_id
        JOIN user_products
            ON user_products.user_id = users.id
           AND user_products.product_id = queue.product_id
        WHERE queue.product_id = %s
          AND queue.status = 'sent'
          AND queue.new_price IS NOT NULL
        ORDER BY users.id ASC
    """, (product_id,))

    rows = cursor.fetchall()
    conn.close()

    return rows


def enqueue_notifications_bulk(
    user_ids,
    message,
    product_id=None,
    button_url=None,
    old_price=None,
    new_price=None,
    is_prime=None,
):
    ensure_notification_queue_table()

    clean_user_ids = []
    for user_id in user_ids:
        try:
            clean_user_ids.append(int(user_id))
        except (TypeError, ValueError):
            continue

    if not clean_user_ids:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        WITH input_users AS (
            SELECT DISTINCT UNNEST(%s::INTEGER[]) AS user_id
        ),
        inserted AS (
            INSERT INTO notification_queue (
                user_id,
                product_id,
                message,
                status,
                available_at,
                button_url,
                old_price,
                new_price,
                is_prime,
                updated_at
            )
            SELECT
                input_users.user_id,
                %s,
                %s,
                'pending',
                CURRENT_TIMESTAMP,
                %s,
                %s,
                %s,
                %s,
                CURRENT_TIMESTAMP
            FROM input_users
            WHERE NOT EXISTS (
                SELECT 1
                FROM notification_queue queue
                WHERE queue.user_id = input_users.user_id
                  AND queue.product_id IS NOT DISTINCT FROM %s
                  AND queue.status IN ('pending', 'sending')
                  AND queue.new_price IS NOT DISTINCT FROM %s
                  AND COALESCE(queue.is_prime, FALSE) = COALESCE(%s, FALSE)
            )
            RETURNING id
        )
        SELECT COUNT(*)
        FROM inserted
        """,
        (
            clean_user_ids,
            product_id,
            message,
            button_url,
            old_price,
            new_price,
            is_prime,
            product_id,
            new_price,
            is_prime,
        ),
    )

    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return int(row[0]) if row else 0


def get_products_lowest_prices(limit_products=200, start_date=None, end_date=None):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            p.id,
            p.name,
            p.color,
            p.asin,
            MIN(best.best_price) AS lowest_price
        FROM products p
        JOIN (
            SELECT
                product_id,
                LEAST(
                    COALESCE(public_price, 99999999),
                    COALESCE(prime_price, 99999999)
                ) AS best_price
            FROM price_history
                        WHERE (public_price IS NOT NULL OR prime_price IS NOT NULL)
              AND (%s::DATE IS NULL OR captured_at >= %s::DATE)
              AND (%s::DATE IS NULL OR captured_at < (%s::DATE + INTERVAL '1 day'))
        ) AS best
            ON best.product_id = p.id
        WHERE p.active = 1
        GROUP BY p.id, p.name, p.color, p.asin
        ORDER BY p.display_order ASC, p.id ASC
        LIMIT %s
        """,
        (
            start_date,
            start_date,
            end_date,
            end_date,
            max(1, int(limit_products)),
        ),
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


def get_report_stats():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(DISTINCT user_id) FROM user_products) AS users_monitoring,
            (
                SELECT p.name
                FROM user_products up
                JOIN products p ON p.id = up.product_id
                GROUP BY p.id, p.name
                ORDER BY COUNT(up.user_id) DESC
                LIMIT 1
            ) AS top_product,
            (
                SELECT COUNT(up.user_id)
                FROM user_products up
                JOIN products p ON p.id = up.product_id
                GROUP BY p.id
                ORDER BY COUNT(up.user_id) DESC
                LIMIT 1
            ) AS top_product_count
    """)

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "total_users": row[0],
        "users_monitoring": row[1],
        "top_product": row[2],
        "top_product_count": row[3],
    }


def delete_old_prices(days=60):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM price_history
        WHERE captured_at < NOW() - (%s * INTERVAL '1 day')
    """, (days,))

    cursor.execute("""
        DELETE FROM notification_queue
        WHERE created_at < NOW() - (%s * INTERVAL '1 day')
    """, (days,))

    cursor.execute("""
        DELETE FROM alert_approvals
        WHERE requested_at < NOW() - (%s * INTERVAL '1 day')
    """, (days,))

    conn.commit()
    conn.close()


# def main():
#     create_tables()

# if __name__ == "__main__":
#     main()