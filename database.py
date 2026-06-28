"""
database.py - ライセンス管理DB
SQLiteを使用。将来的にPostgreSQLへ移行可能な設計。
"""
import sqlite3
import secrets
import string
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path("upwork_monitor.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """テーブル初期化"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS licenses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key   TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL,
            plan          TEXT    NOT NULL DEFAULT '1month',
            status        TEXT    NOT NULL DEFAULT 'active',
            expires_at    DATE    NOT NULL,
            created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            note          TEXT
        );

        CREATE TABLE IF NOT EXISTS app_versions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            component     TEXT    NOT NULL,
            version       TEXT    NOT NULL,
            release_note  TEXT,
            released_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_licenses_key
            ON licenses(license_key);
    """)
    conn.commit()

    # バージョン情報の初期データ
    cur = conn.execute(
        "SELECT COUNT(*) FROM app_versions WHERE component = 'extension'"
    )
    if cur.fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO app_versions (component, version, release_note) VALUES (?,?,?)",
            ('extension', '1.0.0', '初回リリース')
        )
        conn.execute(
            "INSERT INTO app_versions (component, version, release_note) VALUES (?,?,?)",
            ('excel', '1.0.0', '初回リリース')
        )
        conn.commit()
    conn.close()


def generate_license_key() -> str:
    """UPWK-XXXX-XXXX-XXXX 形式のライセンスキーを生成"""
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return 'UPWK-' + '-'.join(parts)


def create_license(email: str, plan: str = '1month', note: str = '') -> dict:
    """ライセンスキーを発行してDBに保存"""
    from dateutil.relativedelta import relativedelta

    plan_months = {'1month': 1, '3month': 3, '6month': 6, '1year': 12}
    months = plan_months.get(plan, 1)

    key        = generate_license_key()
    expires_at = (date.today() + relativedelta(months=months)).isoformat()

    conn = get_conn()
    conn.execute(
        """INSERT INTO licenses (license_key, email, plan, status, expires_at, note)
           VALUES (?, ?, ?, 'active', ?, ?)""",
        (key, email, plan, expires_at, note)
    )
    conn.commit()
    conn.close()
    return {'license_key': key, 'email': email, 'plan': plan, 'expires_at': expires_at}


def validate_license(license_key: str) -> dict:
    """ライセンスキーの有効性を確認"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM licenses WHERE license_key = ?", (license_key,)
    ).fetchone()
    conn.close()

    if not row:
        return {'valid': False, 'reason': 'invalid_key', 'message': 'ライセンスキーが見つかりません'}

    if row['status'] != 'active':
        return {'valid': False, 'reason': 'inactive', 'message': 'ライセンスが無効化されています'}

    expires = date.fromisoformat(row['expires_at'])
    today   = date.today()

    if today > expires:
        return {
            'valid':      False,
            'reason':     'expired',
            'message':    'ライセンスの有効期限が切れています',
            'expires_at': row['expires_at'],
        }

    days_left = (expires - today).days
    return {
        'valid':      True,
        'email':      row['email'],
        'plan':       row['plan'],
        'expires_at': row['expires_at'],
        'days_left':  days_left,
        'message':    f'有効（残り{days_left}日）',
    }


def extend_license(license_key: str, months: int = 1) -> dict:
    """ライセンスの有効期限を延長"""
    from dateutil.relativedelta import relativedelta

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM licenses WHERE license_key = ?", (license_key,)
    ).fetchone()

    if not row:
        conn.close()
        return {'success': False, 'message': 'ライセンスキーが見つかりません'}

    current_expires = date.fromisoformat(row['expires_at'])
    base_date       = max(current_expires, date.today())
    new_expires     = (base_date + relativedelta(months=months)).isoformat()

    conn.execute(
        "UPDATE licenses SET expires_at = ? WHERE license_key = ?",
        (new_expires, license_key)
    )
    conn.commit()
    conn.close()
    return {'success': True, 'new_expires_at': new_expires}


def get_all_licenses() -> list:
    """全ライセンス一覧を取得（管理者用）"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM licenses ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_version(component: str) -> dict:
    """最新バージョンを取得"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM app_versions WHERE component = ? ORDER BY released_at DESC LIMIT 1",
        (component,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def export_licenses_csv() -> str:
    """ライセンスDBをCSV形式で出力（バックアップ用）"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM licenses ORDER BY created_at DESC").fetchall()
    conn.close()

    lines = ['id,license_key,email,plan,status,expires_at,created_at,note']
    for r in rows:
        lines.append(
            f'{r["id"]},{r["license_key"]},{r["email"]},'
            f'{r["plan"]},{r["status"]},{r["expires_at"]},'
            f'{r["created_at"]},{r["note"] or ""}'
        )
    return '\n'.join(lines)
