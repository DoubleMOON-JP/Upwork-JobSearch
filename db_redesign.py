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
                -- プラン変更時の繰り越し用。指定した月に限り、この値を月間上限として使う。
                ALTER TABLE usage_tracking ADD COLUMN IF NOT EXISTS cap_override       INTEGER;
                ALTER TABLE usage_tracking ADD COLUMN IF NOT EXISTS cap_override_month TEXT;

                -- 広告欄（1行だけ使う。未設定ならフロントに表示されない）
                CREATE TABLE IF NOT EXISTS promo (
                    id      INTEGER PRIMARY KEY DEFAULT 1,
                    title   TEXT NOT NULL DEFAULT '',
                    body    TEXT NOT NULL DEFAULT '',
                    url     TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO promo (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

                -- マルチ求人サイト対応：プロンプトをサイト単位で持つ。
                -- NULL = 未割当（どのサイトでも使われない。旧プロンプトの保管用）
                ALTER TABLE prompts ADD COLUMN IF NOT EXISTS site TEXT;
                CREATE INDEX IF NOT EXISTS idx_prompts_site_active
                    ON prompts(site, is_active);
            """)

            # 既存データの移行（初回のみ実質的に効く）。
            # 現在有効なプロンプト（v3.1想定）だけを upwork に紐付け、
            # 旧バージョンは未割当(NULL)のまま保管する。
            cur.execute("""
                UPDATE prompts SET site = 'upwork'
                 WHERE is_active = 1 AND site IS NULL
            """)


# ── 広告欄 ────────────────────────────────────────────────
def get_promo() -> dict:
    """フロント/管理画面から参照。常に1行を返す。"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT title, body, url, enabled FROM promo WHERE id = 1")
            row = cur.fetchone()
    if not row:
        return {"title": "", "body": "", "url": "", "enabled": 0}
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    return d


def save_promo(title: str, body: str, url: str, enabled: bool) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO promo (id, title, body, url, enabled)
                VALUES (1, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, body = EXCLUDED.body,
                    url = EXCLUDED.url, enabled = EXCLUDED.enabled
                """,
                (title or "", body or "", url or "", 1 if enabled else 0),
            )
    return {"status": "ok"}


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


def refund_usage(license_key: str, period_month: str) -> None:
    """
    採点が失敗した時に、消費した当月カウントを1つ戻す。
    月をまたいだ後の巻き戻しを防ぐため、period_month が一致する場合のみ減算する。
    最終実行日時（連打制限）は意図的に戻さない：失敗を繰り返した時に
    Gemini へ連続リクエストが飛ぶのを避けるため。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE usage_tracking
                   SET month_count = GREATEST(month_count - 1, 0)
                 WHERE license_key = %s AND period_month = %s
                """,
                (license_key, period_month),
            )


# ── プラン変更（上限の繰り越しを含む） ──────────────────────
def apply_plan_change(license_key: str, new_plan: str, now: datetime = None) -> dict:
    """
    ライセンスのプランを変更する。有効期限は変更しない。

    上位プランへの変更時は、旧プランで使い残した回数を新プランの上限に加算する。
      例）Standard(100回)で80回使用 → Proへ変更
          当月の実効上限 = 100 + 300 = 400、使用済み80 → 残り320回

    この繰り越しは「変更した月」に限って有効で、翌月からは新プランの上限に戻る。
    下位プランへの変更（ダウングレード）では繰り越しを行わない。
    """
    from plans import plan_monthly_cap          # 循環importを避けるため関数内でimport
    from database import update_license_plan

    now = now or datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT plan FROM licenses WHERE license_key = %s", (license_key,))
            row = cur.fetchone()
    if not row:
        return {"success": False, "message": "ライセンスキーが見つかりません"}

    old_plan = row["plan"]
    if old_plan == new_plan:
        return {"success": False, "message": f"すでに {new_plan} です"}

    old_cap = plan_monthly_cap(old_plan)
    new_cap = plan_monthly_cap(new_plan)

    # 当月の使用回数（月が変わっていれば0扱い）
    usage = get_usage(license_key)
    used = usage["month_count"] if usage and usage.get("period_month") == current_month else 0

    result = update_license_plan(license_key, new_plan)
    if not result.get("success"):
        return result

    carried_over = 0
    effective_cap = new_cap

    if new_cap > old_cap:
        # 上位プランへの変更：旧プランの残り回数を繰り越す
        carried_over = max(old_cap - used, 0)
        effective_cap = new_cap + carried_over + used   # 使用済み分を足して実効上限にする
        _set_cap_override(license_key, current_month, effective_cap)

    result.update({
        "old_plan":      old_plan,
        "new_plan":      new_plan,
        "used":          used,
        "carried_over":  carried_over,
        "monthly_cap":   effective_cap,
        "remaining":     max(effective_cap - used, 0),
        "period_month":  current_month,
    })
    return result


def _set_cap_override(license_key: str, period_month: str, cap: int) -> None:
    """指定した月に限り有効な月間上限を保存する（プラン変更の繰り越し用）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usage_tracking
                       (license_key, period_month, month_count, cap_override, cap_override_month)
                VALUES (%s, %s, 0, %s, %s)
                ON CONFLICT (license_key) DO UPDATE SET
                    cap_override       = EXCLUDED.cap_override,
                    cap_override_month = EXCLUDED.cap_override_month
                """,
                (license_key, period_month, cap, period_month),
            )
