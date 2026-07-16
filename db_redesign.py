# db_redesign.py
# ─────────────────────────────────────────────────────────────
# リデザインで必要になるDB追加分。既存 database.py には手を入れず、
# こちらを import して migrate() を起動時に1回呼ぶ。
#   - licenses に subscription_id / provider 列を追加（MoR連携用）
#   - usage_tracking テーブルを追加（レート制限＋月間上限用）
#   - サブスク紐づけ／無効化／使用量の関数を提供
#   ※ selectors / excludes テーブルはリデザインでは未使用（削除はせず放置＝安全）
# ─────────────────────────────────────────────────────────────
from datetime import datetime, timezone

from psycopg2.extras import RealDictCursor

from database import get_conn  # 既存の接続関数を流用


def migrate():
    """起動時に1回呼ぶ。既に適用済みでも安全（IF NOT EXISTS）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS subscription_id TEXT;
                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS provider        TEXT;
                CREATE INDEX IF NOT EXISTS idx_licenses_sub
                    ON licenses(provider, subscription_id);

                CREATE TABLE IF NOT EXISTS usage_tracking (
                    license_key       TEXT PRIMARY KEY,
                    last_evaluated_at TIMESTAMPTZ,
                    period_month      TEXT,               -- 'YYYY-MM'
                    month_count       INTEGER NOT NULL DEFAULT 0
                );
            """)


# ── サブスク紐づけ（payments.py から使用） ──────────────────
def find_license_by_subscription(provider: str, subscription_id: str):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM licenses WHERE provider = %s AND subscription_id = %s",
                (provider, subscription_id),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def link_subscription(license_key: str, provider: str, subscription_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE licenses SET provider = %s, subscription_id = %s WHERE license_key = %s",
                (provider, subscription_id, license_key),
            )


def deactivate_license(license_key: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE licenses SET status = 'inactive' WHERE license_key = %s",
                (license_key,),
            )


# ── 使用量（rate_limit.py から使用） ───────────────────────
def get_usage(license_key: str):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM usage_tracking WHERE license_key = %s", (license_key,)
            )
            row = cur.fetchone()
    return dict(row) if row else None


def record_usage(license_key: str, now: datetime, period_month: str, month_count: int):
    """UPSERT で最終採点時刻と当月カウントを保存。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usage_tracking (license_key, last_evaluated_at, period_month, month_count)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (license_key) DO UPDATE SET
                    last_evaluated_at = EXCLUDED.last_evaluated_at,
                    period_month      = EXCLUDED.period_month,
                    month_count       = EXCLUDED.month_count
                """,
                (license_key, now, period_month, month_count),
            )
