"""
database.py - ライセンス管理＋プロンプト・セレクター・除外リスト管理DB
PostgreSQL版（Render Basic Plan $7/月）
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

                CREATE TABLE IF NOT EXISTS selectors (
                    id           SERIAL PRIMARY KEY,
                    version      TEXT NOT NULL,
                    service      TEXT NOT NULL DEFAULT 'upwork',
                    config_json  TEXT NOT NULL,
                    is_active    INTEGER NOT NULL DEFAULT 0,
                    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    note         TEXT
                );

                CREATE TABLE IF NOT EXISTS excludes (
                    id           SERIAL PRIMARY KEY,
                    category     TEXT NOT NULL,
                    keyword      TEXT NOT NULL,
                    is_active    INTEGER NOT NULL DEFAULT 1,
                    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                    ('extension', '1.0.0', '初回リリース'),
                    ('excel',     '1.0.0', '初回リリース'),
                ]:
                    cur.execute(
                        "INSERT INTO app_versions (component, version, release_note) VALUES (%s,%s,%s)",
                        (comp, ver, note)
                    )

            # プロンプトの初期データ
            cur.execute("SELECT COUNT(*) FROM prompts")
            if cur.fetchone()[0] == 0:
                default_prompt = """You are an assistant that evaluates Upwork jobs for a freelancer.
Score each job from 0 to 100 based on the freelancer profile below.

[Freelancer Profile]
Skills: {skills}
Preferred categories: {category}
Minimum desired rate: {min_rate}{exclude_line}{prefer_line}{ai_request_line}

[Evaluation criteria]
- Skill match (higher match = higher score)
- Whether the budget or hourly rate meets or exceeds the minimum desired rate
- Apply a large penalty when a "keyword to avoid" appears in the job
- Apply a bonus when a "preferred keyword" appears in the job
- If the user's request to the AI is provided, treat it as the top priority

[Jobs to evaluate]
{jobs_text}

[Output format]
Respond ONLY in JSON, with no extra text.
{{
  "results": [
    {{
      "index": 0,
      "score": 85,
      "reason": "Briefly explain the score in 1-2 English sentences",
      "recommendation": "Apply"
    }}
  ]
}}
"recommendation" must be exactly one of: "Apply", "Maybe", "Skip".
The number of items in "results" must equal the number of jobs provided."""

                cur.execute(
                    """INSERT INTO prompts (version, name, template, is_active, note)
                       VALUES (%s, %s, %s, 1, %s)""",
                    ('v1.0', 'Upwork Job Evaluation Prompt v1.0', default_prompt, 'Initial release')
                )

            # セレクター定義
            cur.execute("SELECT COUNT(*) FROM selectors WHERE service = 'upwork'")
            if cur.fetchone()[0] == 0:
                default_selectors = {
                    "title_selector":   "h3.job-tile-title a, h2.job-tile-title a",
                    "section_selector": "section[data-ev-label-prefix], section.job-tile",
                    "budget_keywords":  ["Fixed", "Hourly", "$", "Budget"],
                    "posted_keywords":  ["Posted", "ago", "hours", "days", "minutes"],
                    "skill_class_includes": ["token", "skill"],
                    "url_base":         "https://www.upwork.com",
                    "search_url_base":  "https://www.upwork.com/nx/find-work/best-matches?q=",
                    "max_jobs":         30,
                    "description_min_length": 30,
                    "description_max_length": 300,
                }
                cur.execute(
                    """INSERT INTO selectors (version, service, config_json, is_active, note)
                       VALUES (%s, 'upwork', %s, 1, %s)""",
                    ('v1.0', json.dumps(default_selectors, ensure_ascii=False), '初回リリース版')
                )

            # 除外リスト
            cur.execute("SELECT COUNT(*) FROM excludes")
            if cur.fetchone()[0] == 0:
                default_excludes = [
                    ('skill_tags', 'Previous skills'),
                    ('skill_tags', 'Update list'),
                    ('skill_tags', 'Skip skills'),
                    ('skill_tags', 'Next skills'),
                    ('skill_tags', 'Show more'),
                    ('skill_tags', 'Show less'),
                ]
                for category, keyword in default_excludes:
                    cur.execute(
                        "INSERT INTO excludes (category, keyword) VALUES (%s,%s)",
                        (category, keyword)
                    )

            # AI設定
            cur.execute("SELECT COUNT(*) FROM ai_settings")
            if cur.fetchone()[0] == 0:
                for key, value, note in [
                    ('default_model',          'gemini-2.5-flash',     'デフォルトのGeminiモデル'),
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
def generate_license_key() -> str:
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return 'UPWK-' + '-'.join(parts)


def create_license(email: str, plan: str = '1month', note: str = '') -> dict:
    from dateutil.relativedelta import relativedelta
    plan_months = {'1month': 1, '3month': 3, '6month': 6, '1year': 12}
    months = plan_months.get(plan, 1)
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
def get_active_prompt() -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM prompts WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
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


def create_prompt(version: str, name: str, template: str, note: str = '') -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO prompts (version, name, template, is_active, note)
                   VALUES (%s, %s, %s, 0, %s) RETURNING id""",
                (version, name, template, note)
            )
            new_id = cur.fetchone()[0]
    return {'id': new_id, 'version': version, 'name': name}


def activate_prompt(prompt_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE prompts SET is_active = 0")
            cur.execute("UPDATE prompts SET is_active = 1 WHERE id = %s", (prompt_id,))
    return {'success': True, 'activated_id': prompt_id}


# ──────────────────────────────────────────
# セレクター管理
# ──────────────────────────────────────────
def get_active_selectors(service: str = 'upwork') -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT * FROM selectors WHERE service = %s AND is_active = 1
                   ORDER BY created_at DESC LIMIT 1""",
                (service,)
            )
            row = cur.fetchone()
    if not row:
        return {}
    result = dict(row)
    result['config'] = json.loads(result['config_json'])
    if isinstance(result.get('created_at'), datetime):
        result['created_at'] = result['created_at'].isoformat()
    return result


def get_all_selectors(service: str = 'upwork') -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM selectors WHERE service = %s ORDER BY created_at DESC",
                (service,)
            )
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].isoformat()
        result.append(d)
    return result


def create_selectors(version: str, service: str, config: dict, note: str = '') -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO selectors (version, service, config_json, is_active, note)
                   VALUES (%s, %s, %s, 0, %s) RETURNING id""",
                (version, service, json.dumps(config, ensure_ascii=False), note)
            )
            new_id = cur.fetchone()[0]
    return {'id': new_id, 'version': version}


def activate_selectors(selector_id: int, service: str = 'upwork') -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE selectors SET is_active = 0 WHERE service = %s", (service,))
            cur.execute("UPDATE selectors SET is_active = 1 WHERE id = %s", (selector_id,))
    return {'success': True, 'activated_id': selector_id}


# ──────────────────────────────────────────
# 除外リスト管理
# ──────────────────────────────────────────
def get_excludes(category: str = 'skill_tags') -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT keyword FROM excludes WHERE category = %s AND is_active = 1",
                (category,)
            )
            rows = cur.fetchall()
    return [r[0] for r in rows]


def get_all_excludes() -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM excludes ORDER BY category, keyword")
            rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].isoformat()
        result.append(d)
    return result


def add_exclude(category: str, keyword: str) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO excludes (category, keyword) VALUES (%s,%s)",
                    (category, keyword)
                )
        return {'success': True}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def delete_exclude(exclude_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM excludes WHERE id = %s", (exclude_id,))
    return {'success': True}


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
    ライセンス認証と一緒に、プロンプト・セレクター・除外リスト・AI設定を返す
    """
    lic = validate_license(license_key)
    if not lic.get('valid'):
        return {'status': 'invalid', **lic}

    prompt    = get_active_prompt()
    selectors = get_active_selectors('upwork')
    excludes  = get_excludes('skill_tags')
    ai_set    = get_ai_settings()
    ext_ver   = get_latest_version('extension')
    excel_ver = get_latest_version('excel')

    return {
        'status': 'valid',
        'license': {
            'email':      lic['email'],
            'plan':       lic['plan'],
            'expires_at': lic['expires_at'],
            'days_left':  lic['days_left'],
        },
        'config': {
            'prompt': {
                'version':  prompt.get('version', ''),
                'name':     prompt.get('name', ''),
                'template': prompt.get('template', ''),
            },
            'selectors': {
                'version': selectors.get('version', ''),
                'service': selectors.get('service', 'upwork'),
                'config':  selectors.get('config', {}),
            },
            'exclude_skills': excludes,
            'ai_settings': ai_set,
        },
        'versions': {
            'extension': ext_ver.get('version', '1.0.0'),
            'excel':     excel_ver.get('version', '1.0.0'),
        },
        'cache_expires_in_sec': 3600,
    }
