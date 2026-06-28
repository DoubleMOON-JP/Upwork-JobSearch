"""
database.py - ライセンス管理＋プロンプト・セレクター・除外リスト管理DB
"""
import psycopg2
import secrets
import string
import json
from datetime import datetime, date
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """テーブル初期化＋初期データ投入"""
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
        CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(license_key);

        CREATE TABLE IF NOT EXISTS prompts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            version      TEXT    NOT NULL,
            name         TEXT    NOT NULL,
            template     TEXT    NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 0,
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            note         TEXT
        );

        CREATE TABLE IF NOT EXISTS selectors (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            version      TEXT    NOT NULL,
            service      TEXT    NOT NULL DEFAULT 'upwork',
            config_json  TEXT    NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 0,
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            note         TEXT
        );

        CREATE TABLE IF NOT EXISTS excludes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT    NOT NULL,
            keyword      TEXT    NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 1,
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ai_settings (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            key       TEXT    NOT NULL UNIQUE,
            value     TEXT    NOT NULL,
            note      TEXT
        );

        CREATE TABLE IF NOT EXISTS app_versions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            component     TEXT    NOT NULL,
            version       TEXT    NOT NULL,
            release_note  TEXT,
            released_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

    # ─ 初期データ投入 ─
    _seed_initial_data(conn)
    conn.close()


def _seed_initial_data(conn):
    """初回起動時の初期データ"""
    # バージョン情報
    if conn.execute("SELECT COUNT(*) FROM app_versions").fetchone()[0] == 0:
        for comp, ver, note in [
            ('extension', '1.0.0', '初回リリース'),
            ('excel',     '1.0.0', '初回リリース'),
        ]:
            conn.execute(
                "INSERT INTO app_versions (component, version, release_note) VALUES (?,?,?)",
                (comp, ver, note)
            )

    # プロンプトの初期データ
    if conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == 0:
        default_prompt = """あなたはフリーランサーの案件評価アシスタントです。
以下のフリーランサープロフィールに基づいて各案件を0〜100点でスコアリングしてください。

【フリーランサープロフィール】
スキル: {skills}
得意カテゴリ: {category}
希望最低時給: {min_rate}{exclude_line}{prefer_line}

【評価基準】
- スキルの一致度（高いほど高得点）
- 予算・時給が希望最低時給以上か
- 避けたいキーワードが含まれる場合は大幅減点
- 優先したいキーワードが含まれる場合は加点

【評価対象案件】
{jobs_text}

【出力形式】
JSON形式のみで回答してください。
{{
  "results": [
    {{
      "index": 0,
      "score": 85,
      "reason": "スコアの理由を1〜2文で日本語で",
      "recommendation": "応募推奨"
    }}
  ]
}}
recommendationは「応募推奨」「様子見」「スキップ」のいずれか。
resultsの件数は評価対象案件と同じ件数にすること。"""

        conn.execute(
            """INSERT INTO prompts (version, name, template, is_active, note)
               VALUES (?, ?, ?, 1, ?)""",
            ('v1.0', 'Upwork案件評価プロンプト v1.0', default_prompt, '初回リリース版')
        )

    # セレクター定義の初期データ
    if conn.execute("SELECT COUNT(*) FROM selectors WHERE service = 'upwork'").fetchone()[0] == 0:
        default_selectors = {
            "title_selector":   "h3.job-tile-title a, h2.job-tile-title a",
            "section_selector": "section[data-ev-label-prefix], section.job-tile",
            "budget_keywords":  ["Fixed", "Hourly", "$", "Budget"],
            "posted_keywords":  ["Posted", "ago", "hours", "days", "minutes"],
            "skill_class_includes": ["token", "skill"],
            "url_base":         "https://www.upwork.com",
            "search_url_base":  "https://www.upwork.com/nx/find-work/best-matches?q=",
            "max_jobs":         20,
            "description_min_length": 30,
            "description_max_length": 300,
        }
        conn.execute(
            """INSERT INTO selectors (version, service, config_json, is_active, note)
               VALUES (?, 'upwork', ?, 1, ?)""",
            ('v1.0', json.dumps(default_selectors, ensure_ascii=False), '初回リリース版')
        )

    # 除外リストの初期データ
    if conn.execute("SELECT COUNT(*) FROM excludes").fetchone()[0] == 0:
        default_excludes = [
            ('skill_tags', 'Previous skills'),
            ('skill_tags', 'Update list'),
            ('skill_tags', 'Skip skills'),
            ('skill_tags', 'Next skills'),
            ('skill_tags', 'Show more'),
            ('skill_tags', 'Show less'),
        ]
        for category, keyword in default_excludes:
            conn.execute(
                "INSERT INTO excludes (category, keyword) VALUES (?,?)",
                (category, keyword)
            )

    # AI設定の初期データ
    if conn.execute("SELECT COUNT(*) FROM ai_settings").fetchone()[0] == 0:
        for key, value, note in [
            ('default_model',          'gemini-2.5-flash',     'デフォルトのGeminiモデル'),
            ('max_output_tokens',      '4096',                 'AI応答の最大トークン数'),
            ('temperature',            '0.3',                  '応答のランダム性（0〜1）'),
            ('response_mime_type',     'application/json',     '応答の形式'),
            ('gemini_api_base',        'https://generativelanguage.googleapis.com/v1beta/models', 'Gemini APIのベースURL'),
            ('max_jobs_per_evaluate',  '20',                   '1回の評価で送る最大件数'),
        ]:
            conn.execute(
                "INSERT INTO ai_settings (key, value, note) VALUES (?,?,?)",
                (key, value, note)
            )

    conn.commit()


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
    today = date.today()
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
    }


def extend_license(license_key: str, months: int = 1) -> dict:
    from dateutil.relativedelta import relativedelta
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM licenses WHERE license_key = ?", (license_key,)
    ).fetchone()
    if not row:
        conn.close()
        return {'success': False, 'message': 'ライセンスキーが見つかりません'}
    current = date.fromisoformat(row['expires_at'])
    base    = max(current, date.today())
    new_exp = (base + relativedelta(months=months)).isoformat()
    conn.execute(
        "UPDATE licenses SET expires_at = ? WHERE license_key = ?",
        (new_exp, license_key)
    )
    conn.commit()
    conn.close()
    return {'success': True, 'new_expires_at': new_exp}


def get_all_licenses() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM licenses ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    """有効なプロンプトを取得"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM prompts WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_all_prompts() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM prompts ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_prompt(version: str, name: str, template: str, note: str = '') -> dict:
    """新しいプロンプトを作成（is_active=0で作成）"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO prompts (version, name, template, is_active, note)
           VALUES (?, ?, ?, 0, ?)""",
        (version, name, template, note)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {'id': new_id, 'version': version, 'name': name}


def activate_prompt(prompt_id: int) -> dict:
    """指定のプロンプトを有効化（他は無効化）"""
    conn = get_conn()
    conn.execute("UPDATE prompts SET is_active = 0")
    conn.execute("UPDATE prompts SET is_active = 1 WHERE id = ?", (prompt_id,))
    conn.commit()
    conn.close()
    return {'success': True, 'activated_id': prompt_id}


# ──────────────────────────────────────────
# セレクター管理
# ──────────────────────────────────────────
def get_active_selectors(service: str = 'upwork') -> dict:
    """有効なセレクター定義を取得"""
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM selectors WHERE service = ? AND is_active = 1
           ORDER BY created_at DESC LIMIT 1""",
        (service,)
    ).fetchone()
    conn.close()
    if not row:
        return {}
    result = dict(row)
    result['config'] = json.loads(result['config_json'])
    return result


def get_all_selectors(service: str = 'upwork') -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM selectors WHERE service = ? ORDER BY created_at DESC",
        (service,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_selectors(version: str, service: str, config: dict, note: str = '') -> dict:
    """新しいセレクター定義を作成"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO selectors (version, service, config_json, is_active, note)
           VALUES (?, ?, ?, 0, ?)""",
        (version, service, json.dumps(config, ensure_ascii=False), note)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {'id': new_id, 'version': version}


def activate_selectors(selector_id: int, service: str = 'upwork') -> dict:
    """指定のセレクター定義を有効化"""
    conn = get_conn()
    conn.execute("UPDATE selectors SET is_active = 0 WHERE service = ?", (service,))
    conn.execute("UPDATE selectors SET is_active = 1 WHERE id = ?", (selector_id,))
    conn.commit()
    conn.close()
    return {'success': True, 'activated_id': selector_id}


# ──────────────────────────────────────────
# 除外リスト管理
# ──────────────────────────────────────────
def get_excludes(category: str = 'skill_tags') -> list:
    """除外キーワードリストを取得"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT keyword FROM excludes WHERE category = ? AND is_active = 1",
        (category,)
    ).fetchall()
    conn.close()
    return [r['keyword'] for r in rows]


def get_all_excludes() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM excludes ORDER BY category, keyword"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_exclude(category: str, keyword: str) -> dict:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO excludes (category, keyword) VALUES (?,?)",
            (category, keyword)
        )
        conn.commit()
        result = {'success': True}
    except Exception as e:
        result = {'success': False, 'message': str(e)}
    finally:
        conn.close()
    return result


def delete_exclude(exclude_id: int) -> dict:
    conn = get_conn()
    conn.execute("DELETE FROM excludes WHERE id = ?", (exclude_id,))
    conn.commit()
    conn.close()
    return {'success': True}


# ──────────────────────────────────────────
# AI設定管理
# ──────────────────────────────────────────
def get_ai_settings() -> dict:
    """全AI設定をdict形式で取得"""
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM ai_settings").fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def update_ai_setting(key: str, value: str) -> dict:
    conn = get_conn()
    conn.execute(
        "UPDATE ai_settings SET value = ? WHERE key = ?",
        (value, key)
    )
    conn.commit()
    conn.close()
    return {'success': True}


# ──────────────────────────────────────────
# バージョン情報
# ──────────────────────────────────────────
def get_latest_version(component: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM app_versions WHERE component = ? ORDER BY released_at DESC LIMIT 1",
        (component,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


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
        'cache_expires_in_sec': 3600,  # 拡張機能側で1時間キャッシュ
    }
