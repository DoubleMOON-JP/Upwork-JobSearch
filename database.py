"""
database.py - ライセンス管理＋プロンプト／AI設定／配布ファイル管理DB
PostgreSQL版（Render Basic Plan $7/月）

※ リデザイン(v3.0)でDOMセレクター・除外リストは廃止した。
   既存DBの selectors / excludes テーブルは残るが、コードからは一切参照しない。
"""
import os
import secrets
import string
import json
from datetime import datetime, date
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@contextmanager
def get_conn():
    """コンテキストマネージャ：自動的にcommit/closeする"""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """テーブル初期化＋初期データ投入"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS licenses (
                    id            SERIAL PRIMARY KEY,
                    license_key   TEXT NOT NULL UNIQUE,
                    email         TEXT NOT NULL,
                    plan          TEXT NOT NULL DEFAULT '1month',
                    status        TEXT NOT NULL DEFAULT 'active',
                    expires_at    DATE NOT NULL,
                    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    note          TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(license_key);

                CREATE TABLE IF NOT EXISTS prompts (
                    id           SERIAL PRIMARY KEY,
                    version      TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    template     TEXT NOT NULL,
                    is_active    INTEGER NOT NULL DEFAULT 0,
                    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    note         TEXT
                );

                CREATE TABLE IF NOT EXISTS ai_settings (
                    id        SERIAL PRIMARY KEY,
                    key       TEXT NOT NULL UNIQUE,
                    value     TEXT NOT NULL,
                    note      TEXT
                );

                CREATE TABLE IF NOT EXISTS app_versions (
                    id            SERIAL PRIMARY KEY,
                    component     TEXT NOT NULL,
                    version       TEXT NOT NULL,
                    release_note  TEXT,
                    released_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS files (
                    id            SERIAL PRIMARY KEY,
                    component     TEXT NOT NULL,
                    filename      TEXT NOT NULL,
                    content_type  TEXT NOT NULL,
                    file_data     BYTEA NOT NULL,
                    version       TEXT NOT NULL,
                    is_active     INTEGER NOT NULL DEFAULT 0,
                    uploaded_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    note          TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_files_component ON files(component, is_active);
            """)

    _seed_initial_data()


def _seed_initial_data():
    """初回起動時の初期データ"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # バージョン情報
            cur.execute("SELECT COUNT(*) FROM app_versions")
            if cur.fetchone()[0] == 0:
                for comp, ver, note in [
                    ('excel', '1.0.0', '初回リリース'),
                ]:
                    cur.execute(
                        "INSERT INTO app_versions (component, version, release_note) VALUES (%s,%s,%s)",
                        (comp, ver, note)
                    )

            # プロンプトの初期データ
            cur.execute("SELECT COUNT(*) FROM prompts")
            if cur.fetchone()[0] == 0:
                default_prompt = """You are an assistant that evaluates Upwork job postings
for a freelancer, and scores each job from 0 to 100.

[Evaluation criteria]
- Skill match against the freelancer's profile (higher match = higher score)
- Whether the budget or hourly rate meets or exceeds the minimum desired rate
- Apply a large penalty when a "keyword to avoid" appears in the job
- Apply a bonus when a "preferred keyword" appears in the job
- Prefer recently posted jobs and clearly written requirements
- If the user's request to the AI is provided, treat it as the top priority

[Recommendation]
- 80-100 -> "Apply"
- 55-79  -> "Maybe"
- 0-54   -> "Skip"

The freelancer profile, the pasted job text, and the required output format
are appended below by the server. Follow that output format exactly."""

                cur.execute(
                    """INSERT INTO prompts (version, name, template, is_active, note)
                       VALUES (%s, %s, %s, 1, %s)""",
                    ('v1.0', 'Upwork Job Evaluation Prompt v1.0', default_prompt, 'Initial release')
                )

            # AI設定
            cur.execute("SELECT COUNT(*) FROM ai_settings")
            if cur.fetchone()[0] == 0:
                for key, value, note in [
                    ('default_model',          'gemini-3.5-flash',     'デフォルトのGeminiモデル'),
                    ('max_output_tokens',      '4096',                 'AI応答の最大トークン数'),
                    ('temperature',            '0.3',                  '応答のランダム性（0〜1）'),
                    ('response_mime_type',     'application/json',     '応答の形式'),
                    ('gemini_api_base',        'https://generativelanguage.googleapis.com/v1beta/models', 'Gemini APIのベースURL'),
                    ('max_jobs_per_evaluate',  '20',                   '1回の評価で送る最大件数'),
                ]:
                    cur.execute(
                        "INSERT INTO ai_settings (key, value, note) VALUES (%s,%s,%s)",
                        (key, value, note)
                    )


# ──────────────────────────────────────────
# ライセンス管理
# ──────────────────────────────────────────
# ライセンスキーの接頭辞。DMJS = Double Moon Job Search。
# 旧キー(UPWK-)は検証側が接頭辞に依存していないため、そのまま有効に使い続けられる。
# ここを変えても既存ライセンスには一切影響しない（新規発行分にのみ適用）。
LICENSE_KEY_PREFIX = 'DMJS'


def generate_license_key() -> str:
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return LICENSE_KEY_PREFIX + '-' + '-'.join(parts)


def create_license(email: str, plan: str = '1month', note: str = '') -> dict:
    from dateutil.relativedelta import relativedelta
    from plans import plan_months   # プラン定義は plans.py に一本化

    months = plan_months(plan)
    key = generate_license_key()
    expires_at = date.today() + relativedelta(months=months)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO licenses (license_key, email, plan, status, expires_at, note)
                   VALUES (%s, %s, %s, 'active', %s, %s)""",
                (key, email, plan, expires_at, note)
            )
    return {
        'license_key': key, 'email': email,
        'plan': plan, 'expires_at': expires_at.isoformat()
    }


def validate_license(license_key: str) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM licenses WHERE license_key = %s", (license_key,)
            )
            row = cur.fetchone()

    if not row:
        return {'valid': False, 'reason': 'invalid_key', 'message': 'ライセンスキーが見つかりません'}
    if row['status'] != 'active':
        return {'valid': False, 'reason': 'inactive', 'message': 'ライセンスが無効化されています'}

    expires = row['expires_at']
    today = date.today()
    if today > expires:
        return {
            'valid':      False,
            'reason':     'expired',
            'message':    'ライセンスの有効期限が切れています',
            'expires_at': expires.isoformat(),
        }

    days_left = (expires - today).days
    return {
        'valid':      True,
        'email':      row['email'],
        'plan':       row['plan'],
        'expires_at': expires.isoformat(),
        'days_left':  days_left,
    }


def extend_license(license_key: str, months: int = 1) -> dict:
    from dateutil.relativedelta import relativedelta

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM licenses WHERE license_key = %s", (license_key,)
            )
            row = cur.fetchone()

            if not row:
                return {'success': False, 'message': 'ライセンスキーが見つかりません'}

            current = row['expires_at']
            base = max(current, date.today())
            new_exp = base + relativedelta(months=months)

            cur.execute(
                "UPDATE licenses SET expires_at = %s WHERE license_key = %s",
                (new_exp, license_key)
            )

    return {'success': True, 'new_expires_at': new_exp.isoformat()}


def update_license_plan(license_key: str, plan: str) -> dict:
    """
    ライセンスのプランを変更する（管理画面からの手動操作用）。

    Polar側でプラン変更（Standard⇔Pro）が行われても自動反映はしていないため、
    運営者が管理画面から手動で合わせる。有効期限は変更しない。
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT plan FROM licenses WHERE license_key = %s", (license_key,))
            row = cur.fetchone()
            if not row:
                return {'success': False, 'message': 'ライセンスキーが見つかりません'}
            old_plan = row['plan']
            cur.execute(
                "UPDATE licenses SET plan = %s WHERE license_key = %s",
                (plan, license_key)
            )
    return {'success': True, 'old_plan': old_plan, 'new_plan': plan}


def get_all_licenses() -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get('expires_at'), date):
            d['expires_at'] = d['expires_at'].isoformat()
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].isoformat()
        result.append(d)
    return result


def get_license_stats() -> dict:
    """管理画面の統計用。件数だけをDB側で数える（全件取得を避ける）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                                                    AS total,
                    COUNT(*) FILTER (WHERE expires_at >= CURRENT_DATE
                                       AND status = 'active')                   AS active,
                    COUNT(*) FILTER (WHERE expires_at <  CURRENT_DATE)           AS expired,
                    COUNT(*) FILTER (WHERE status <> 'active')                   AS inactive
                FROM licenses
            """)
            row = cur.fetchone()
    return {'total': row[0], 'active': row[1], 'expired': row[2], 'inactive': row[3]}


def search_licenses(keyword: str = '', status: str = 'all',
                    limit: int = 50, offset: int = 0) -> dict:
    """
    ライセンス一覧ページ用。キーワード検索・状態絞り込み・ページ送りに対応。
      keyword : ライセンスキー／メールアドレスの部分一致（大文字小文字は区別しない）
      status  : all / active（有効）/ expired（期限切れ）/ inactive（無効化）
    """
    where, params = [], []
    if keyword:
        where.append("(license_key ILIKE %s OR email ILIKE %s)")
        params += [f'%{keyword}%', f'%{keyword}%']
    if status == 'active':
        where.append("expires_at >= CURRENT_DATE AND status = 'active'")
    elif status == 'expired':
        where.append("expires_at < CURRENT_DATE")
    elif status == 'inactive':
        where.append("status <> 'active'")
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM licenses {where_sql}", params)
            total = cur.fetchone()['c']
            cur.execute(
                f"""SELECT * FROM licenses {where_sql}
                    ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                params + [limit, offset]
            )
            rows = cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get('expires_at'), date):
            d['expires_at'] = d['expires_at'].isoformat()
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].isoformat()
        result.append(d)
    return {'rows': result, 'total': total}


# 決済に紐づくライセンスを削除できるようになるまでの日数（失効後）。
# 短すぎると、支払い失敗のリトライ中（Polarは最大21日リトライする）に削除してしまい、
# リトライ成功時に別のキーが新規発行されてしまう。
PAID_LICENSE_DELETE_GRACE_DAYS = 30


def _can_delete(row: dict, grace_days: int = PAID_LICENSE_DELETE_GRACE_DAYS):
    """削除してよいライセンスかを判定する。(可否, 理由) を返す。"""
    from datetime import timedelta
    is_paid = bool(row.get('subscription_id'))
    expires = row['expires_at']
    if isinstance(expires, str):
        expires = date.fromisoformat(expires)

    if not is_paid:
        # 手動発行（テスト用・キャンペーン等）はいつでも削除可
        return True, ''
    if expires >= date.today():
        return False, '決済に紐づく有効なライセンスは削除できません（失効を待ってください）'
    if expires > date.today() - timedelta(days=grace_days):
        remain = (expires + timedelta(days=grace_days) - date.today()).days
        return False, (f'決済に紐づくライセンスは失効から{grace_days}日経過後に削除できます'
                       f'（あと{remain}日）')
    return True, ''


def delete_license(license_key: str) -> dict:
    """
    ライセンスを削除する（使用量の記録も併せて削除）。
    決済に紐づくものは、誤って有効な契約を消さないよう条件付き。
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM licenses WHERE license_key = %s", (license_key,))
            row = cur.fetchone()
            if not row:
                return {'success': False, 'message': 'ライセンスキーが見つかりません'}

            ok, reason = _can_delete(dict(row))
            if not ok:
                return {'success': False, 'message': reason}

            cur.execute("DELETE FROM usage_tracking WHERE license_key = %s", (license_key,))
            cur.execute("DELETE FROM licenses WHERE license_key = %s", (license_key,))

    print(f"[admin] license deleted: {license_key} ({row['email']})")
    return {'success': True, 'license_key': license_key, 'email': row['email']}


def delete_expired_licenses(older_than_days: int = 30) -> dict:
    """
    指定日数より前に失効したライセンスをまとめて削除する。
    決済に紐づくものは PAID_LICENSE_DELETE_GRACE_DAYS の条件も満たす必要がある。
    """
    grace = max(int(older_than_days), PAID_LICENSE_DELETE_GRACE_DAYS)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM licenses
                    WHERE expires_at < CURRENT_DATE - make_interval(days => %s)""",
                (int(older_than_days),)
            )
            candidates = [dict(r) for r in cur.fetchall()]

            deleted = []
            for row in candidates:
                ok, _ = _can_delete(row, grace_days=grace)
                if not ok:
                    continue
                cur.execute("DELETE FROM usage_tracking WHERE license_key = %s",
                            (row['license_key'],))
                cur.execute("DELETE FROM licenses WHERE license_key = %s",
                            (row['license_key'],))
                deleted.append({'license_key': row['license_key'], 'email': row['email']})

    for d in deleted:
        print(f"[admin] license deleted (bulk): {d['license_key']} ({d['email']})")
    return {'success': True, 'deleted_count': len(deleted), 'deleted': deleted,
            'skipped_count': len(candidates) - len(deleted)}


def export_licenses_csv() -> str:
    rows = get_all_licenses()
    lines = ['id,license_key,email,plan,status,expires_at,created_at,note']
    for r in rows:
        lines.append(
            f'{r["id"]},{r["license_key"]},{r["email"]},'
            f'{r["plan"]},{r["status"]},{r["expires_at"]},'
            f'{r["created_at"]},{r["note"] or ""}'
        )
    return '\n'.join(lines)


# ──────────────────────────────────────────
# プロンプト管理
# ──────────────────────────────────────────
def get_active_prompt(site: str) -> dict:
    """指定サイトで有効なプロンプトを返す。
    サイトは必須。該当が無ければ空dict（呼び出し側でフォールバック文言を使う）。"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM prompts
                    WHERE is_active = 1 AND site = %s
                    ORDER BY created_at DESC LIMIT 1""",
                (site,)
            )
            row = cur.fetchone()
    return dict(row) if row else {}


def get_all_prompts() -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM prompts ORDER BY created_at DESC")
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].isoformat()
        result.append(d)
    return result


def create_prompt(version: str, name: str, template: str, note: str = '',
                  site: str | None = None) -> dict:
    """site=None は「未割当」。どのサイトの採点にも使われない保管状態。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO prompts (version, name, template, is_active, note, site)
                   VALUES (%s, %s, %s, 0, %s, %s) RETURNING id""",
                (version, name, template, note, site or None)
            )
            new_id = cur.fetchone()[0]
    return {'id': new_id, 'version': version, 'name': name, 'site': site}


def update_prompt(prompt_id: int, version: str, name: str, template: str,
                  note: str = '', site: str | None = None) -> dict:
    """既存プロンプトの上書き保存。サイトの付け替えもここで行う。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE prompts
                      SET version = %s, name = %s, template = %s, note = %s, site = %s
                    WHERE id = %s""",
                (version, name, template, note, site or None, prompt_id)
            )
            # 未割当に戻した場合、有効フラグが残っていると採点対象が消えるため落とす
            cur.execute(
                "UPDATE prompts SET is_active = 0 WHERE id = %s AND site IS NULL",
                (prompt_id,)
            )
    return {'success': True, 'id': prompt_id, 'site': site}


def activate_prompt(prompt_id: int) -> dict:
    """同一サイト内でのみ排他。他サイトの有効プロンプトには影響させない。
    未割当(site IS NULL)のプロンプトは有効化できない。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT site FROM prompts WHERE id = %s", (prompt_id,))
            row = cur.fetchone()
            if not row:
                return {'success': False, 'message': 'プロンプトが見つかりません'}
            site = row[0]
            if not site:
                return {'success': False,
                        'message': 'サイトが未割当のため有効化できません。編集画面でサイトを選んでください'}
            cur.execute("UPDATE prompts SET is_active = 0 WHERE site = %s", (site,))
            cur.execute("UPDATE prompts SET is_active = 1 WHERE id = %s", (prompt_id,))
    return {'success': True, 'activated_id': prompt_id, 'site': site}


# ──────────────────────────────────────────
# AI設定管理
# ──────────────────────────────────────────
def get_ai_settings() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM ai_settings")
            rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


def update_ai_setting(key: str, value: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_settings SET value = %s WHERE key = %s",
                (value, key)
            )
    return {'success': True}


# ──────────────────────────────────────────
# バージョン情報
# ──────────────────────────────────────────
def get_latest_version(component: str) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM app_versions WHERE component = %s ORDER BY released_at DESC LIMIT 1",
                (component,)
            )
            row = cur.fetchone()
    if not row:
        return {}
    result = dict(row)
    if isinstance(result.get('released_at'), datetime):
        result['released_at'] = result['released_at'].isoformat()
    return result


# ──────────────────────────────────────────
# 配布ファイル管理（拡張機能zip／Excelファイル）
# ──────────────────────────────────────────
def upload_file(component: str, filename: str, content_type: str,
                 file_data: bytes, version: str, note: str = '') -> dict:
    """
    ファイルをDBに保存し、同時にapp_versionsへバージョンを記録する。
    保存と同時に同コンポーネントの既存ファイルは非アクティブ化し、
    今回アップロードしたものをアクティブにする（＝最新版として配信）。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE files SET is_active = 0 WHERE component = %s",
                (component,)
            )
            cur.execute(
                """INSERT INTO files (component, filename, content_type, file_data,
                                      version, is_active, note)
                   VALUES (%s, %s, %s, %s, %s, 1, %s) RETURNING id""",
                (component, filename, content_type, psycopg2.Binary(file_data), version, note)
            )
            new_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO app_versions (component, version, release_note)
                   VALUES (%s, %s, %s)""",
                (component, version, note)
            )
    return {'success': True, 'id': new_id, 'component': component, 'version': version}


def get_active_file(component: str) -> dict:
    """配信用：現在アクティブなファイルの実体を取得する"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM files WHERE component = %s AND is_active = 1
                   ORDER BY uploaded_at DESC LIMIT 1""",
                (component,)
            )
            row = cur.fetchone()
    if not row:
        return {}
    result = dict(row)
    if isinstance(result.get('uploaded_at'), datetime):
        result['uploaded_at'] = result['uploaded_at'].isoformat()
    return result


def get_all_files(component: str = None) -> list:
    """管理画面用：ファイル一覧（バイナリ本体は含めない）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if component:
                cur.execute(
                    """SELECT id, component, filename, content_type, version,
                              is_active, uploaded_at, note
                       FROM files WHERE component = %s ORDER BY uploaded_at DESC""",
                    (component,)
                )
            else:
                cur.execute(
                    """SELECT id, component, filename, content_type, version,
                              is_active, uploaded_at, note
                       FROM files ORDER BY component, uploaded_at DESC"""
                )
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get('uploaded_at'), datetime):
            d['uploaded_at'] = d['uploaded_at'].isoformat()
        result.append(d)
    return result


def activate_file(file_id: int) -> dict:
    """指定ファイルを最新版として有効化（同コンポーネントの他は無効化）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT component FROM files WHERE id = %s", (file_id,))
            row = cur.fetchone()
            if not row:
                return {'success': False, 'message': 'ファイルが見つかりません'}
            component = row['component']
            cur.execute("UPDATE files SET is_active = 0 WHERE component = %s", (component,))
            cur.execute("UPDATE files SET is_active = 1 WHERE id = %s", (file_id,))
    return {'success': True, 'activated_id': file_id}


def delete_file(file_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM files WHERE id = %s", (file_id,))
    return {'success': True}


# ──────────────────────────────────────────
# 統合：ライセンス認証と設定取得を一括
# ──────────────────────────────────────────
def get_license_with_config(license_key: str) -> dict:
    """
    ライセンス認証結果を返す（Webログイン用の軽量版）。

    リデザイン前は採点プロンプト全文やDOMセレクターも返していたが、
    採点はサーバー側（/evaluate）で完結するためフロントには不要であり、
    プロンプトが有効キー1本で外部から読めてしまうため返却をやめた。
    """
    lic = validate_license(license_key)
    if not lic.get('valid'):
        return {'status': 'invalid', **lic}

    excel_ver = get_latest_version('excel')

    return {
        'status': 'valid',
        'license': {
            'email':      lic['email'],
            'plan':       lic['plan'],
            'expires_at': lic['expires_at'],
            'days_left':  lic['days_left'],
        },
        'versions': {
            'excel': excel_ver.get('version', '1.0.0'),
        },
    }
