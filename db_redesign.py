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

                -- ── 購入完了ページ（/thanks）でのキー表示用 ──────────
                -- PolarのチェックアウトID。/thanks のURLに付く値と一致するため、
                -- これを鍵にして「今まさに購入した本人」にだけキーを返す。
                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS checkout_id TEXT;
                CREATE INDEX IF NOT EXISTS idx_licenses_checkout
                    ON licenses(checkout_id);

                -- ── メール送付の状態（管理画面での見落とし防止）──────
                --   sent    … 送信成功
                --   failed  … 送信失敗（要手動対応。一覧で赤表示）
                --   manual  … 管理画面からの手動発行（メールは送っていない）
                --   NULL    … 不明（この機能を入れる前に発行された分）
                -- ── 紹介リンク（SNS流入計測）────────────────────
                -- スタッフやインフルエンサーに配る短縮URL /r/{code} の定義。
                -- channel（X/LinkedIn等）と owner（担当者）を分けて持つことで、
                -- 「チャネル別」「担当者別」「投稿別」の3通りの集計ができる。
                CREATE TABLE IF NOT EXISTS referrals (
                    id         SERIAL PRIMARY KEY,
                    code       TEXT NOT NULL UNIQUE,
                    channel    TEXT,
                    owner      TEXT,
                    note       TEXT,
                    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                -- 訪問は1件ずつ日時つきで残す（集計してからでは期間を切り直せない）。
                -- リセットは行わず、画面側で期間を指定して集計する。
                CREATE TABLE IF NOT EXISTS referral_visits (
                    id         SERIAL PRIMARY KEY,
                    code       TEXT NOT NULL,
                    visited_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_bot     BOOLEAN NOT NULL DEFAULT FALSE
                );
                CREATE INDEX IF NOT EXISTS idx_ref_visits
                    ON referral_visits(code, visited_at);

                -- 購入元の紹介コード。Polarのメタデータ経由で受け取る。
                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS ref_code TEXT;
                CREATE INDEX IF NOT EXISTS idx_licenses_ref ON licenses(ref_code);

                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS mail_status  TEXT;
                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS mail_sent_at TIMESTAMP;
                ALTER TABLE licenses ADD COLUMN IF NOT EXISTS mail_error   TEXT;
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


# ── チェックアウト紐づけ／メール送付状態 ───────────────────
# /thanks でのキー表示と、メール送信失敗の検知に使う。

# /thanks からキーを返してよい時間（発行からの分数）。
# checkout_id はURLに載るため、ブラウザ履歴や共有リンクから漏れうる。
# 「購入直後の本人」以外には返さないよう、短い時間で締める。
CHECKOUT_LOOKUP_WINDOW_MINUTES = 30


def link_checkout(license_key: str, checkout_id: str) -> None:
    """発行したライセンスにチェックアウトIDを紐づける。"""
    if not checkout_id:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE licenses SET checkout_id = %s WHERE license_key = %s",
                (checkout_id, license_key),
            )


def set_mail_status(license_key: str, status: str, error: str = None) -> None:
    """メール送付の結果を記録する。status は sent / failed / manual。
    ここで例外を出すとWebhookが落ちるため、呼び出し側で握りつぶすこと。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status == "sent":
                cur.execute(
                    """UPDATE licenses
                          SET mail_status = 'sent',
                              mail_sent_at = CURRENT_TIMESTAMP,
                              mail_error = NULL
                        WHERE license_key = %s""",
                    (license_key,),
                )
            else:
                cur.execute(
                    """UPDATE licenses
                          SET mail_status = %s, mail_error = %s
                        WHERE license_key = %s""",
                    (status, (error or "")[:500], license_key),
                )


# ── 紹介リンク（SNS流入計測）────────────────────────────
# 訪問と購入を別々に記録し、集計は都度SQLで行う。
# 累積で持ち、リセットはしない（消すと過去を見返せなくなるため）。

# 明らかな巡回ロボットは訪問数から除く。完全な判別はできないので、
# 「疑わしいものに印を付ける」程度の扱いにとどめる。
_BOT_HINTS = ("bot", "crawler", "spider", "slurp", "curl", "wget",
              "python-requests", "headless", "preview", "monitor")


def _looks_like_bot(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    if not ua:
        return True          # UAが無いのは通常のブラウザではない
    return any(h in ua for h in _BOT_HINTS)


def list_referrals(include_inactive: bool = True):
    """紹介コードの一覧。"""
    sql = "SELECT * FROM referrals"
    if not include_inactive:
        sql += " WHERE is_active = TRUE"
    sql += " ORDER BY is_active DESC, created_at DESC"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def create_referral(code: str, channel: str = "", owner: str = "",
                    note: str = "") -> dict:
    """紹介コードを登録する。重複時は例外。"""
    code = (code or "").strip()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO referrals (code, channel, owner, note)
                        VALUES (%s, %s, %s, %s) RETURNING *""",
                (code, channel or None, owner or None, note or None),
            )
            return dict(cur.fetchone())


def set_referral_active(code: str, active: bool) -> None:
    """停止／再開。削除はしない（過去の実績を残すため）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE referrals SET is_active = %s WHERE code = %s",
                (active, code),
            )


def referral_exists(code: str) -> bool:
    if not code:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM referrals WHERE code = %s", (code,))
            return cur.fetchone() is not None


def record_referral_visit(code: str, user_agent: str = "") -> None:
    """訪問を1件記録する。未登録コードは記録しない（無効なURLの乱造を防ぐ）。
    ここで例外が出てもリダイレクト自体は続行させること。"""
    if not referral_exists(code):
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO referral_visits (code, is_bot) VALUES (%s, %s)",
                (code, _looks_like_bot(user_agent)),
            )


def set_license_ref(license_key: str, ref_code: str) -> None:
    """発行したライセンスに紹介コードを紐づける。"""
    if not ref_code:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE licenses SET ref_code = %s WHERE license_key = %s",
                (ref_code, license_key),
            )


def referral_stats(date_from: str = None, date_to: str = None):
    """コードごとの成績。

    訪問・購入は「期間内に発生した件数」、継続中は「現時点で有効な件数」。
    この2つは性質が違うため、列を分けて返す（画面側でもそう表示する）。
    date_from / date_to は 'YYYY-MM-DD'。None なら全期間。
    """
    where_v, where_l, params_v, params_l = [], [], [], []
    if date_from:
        where_v.append("v.visited_at >= %s")
        params_v.append(date_from)
        where_l.append("l.created_at >= %s")
        params_l.append(date_from)
    if date_to:
        # 終了日を含めるため翌日未満で比較する
        where_v.append("v.visited_at < (%s::date + 1)")
        params_v.append(date_to)
        where_l.append("l.created_at < (%s::date + 1)")
        params_l.append(date_to)
    vw = (" AND " + " AND ".join(where_v)) if where_v else ""
    lw = (" AND " + " AND ".join(where_l)) if where_l else ""

    sql = f"""
        SELECT r.code, r.channel, r.owner, r.note, r.is_active, r.created_at,
               COALESCE(v.visits, 0)      AS visits,
               COALESCE(v.human, 0)       AS human_visits,
               COALESCE(p.purchases, 0)   AS purchases,
               COALESCE(a.active_cnt, 0)  AS active_cnt,
               a.plans                    AS active_plans
          FROM referrals r
          LEFT JOIN (
               SELECT v.code,
                      COUNT(*)                                   AS visits,
                      COUNT(*) FILTER (WHERE v.is_bot = FALSE)   AS human
                 FROM referral_visits v
                WHERE TRUE {vw}
                GROUP BY v.code
          ) v ON v.code = r.code
          LEFT JOIN (
               SELECT l.ref_code, COUNT(*) AS purchases
                 FROM licenses l
                WHERE l.ref_code IS NOT NULL {lw}
                GROUP BY l.ref_code
          ) p ON p.ref_code = r.code
          LEFT JOIN (
               SELECT l.ref_code,
                      COUNT(*)                    AS active_cnt,
                      STRING_AGG(l.plan, ',')     AS plans
                 FROM licenses l
                WHERE l.ref_code IS NOT NULL
                  AND l.status = 'active'
                  AND l.expires_at >= CURRENT_DATE
                GROUP BY l.ref_code
          ) a ON a.ref_code = r.code
         ORDER BY r.is_active DESC, COALESCE(p.purchases, 0) DESC,
                  COALESCE(v.visits, 0) DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params_v + params_l)
            return [dict(r) for r in cur.fetchall()]


def referral_detail_rows():
    """明細（ライセンス1件ごと）。インフルエンサーへの支払根拠などに使う。"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT l.ref_code, r.channel, r.owner,
                          l.license_key, l.email, l.plan, l.status,
                          l.created_at, l.expires_at
                     FROM licenses l
                     LEFT JOIN referrals r ON r.code = l.ref_code
                    WHERE l.ref_code IS NOT NULL
                    ORDER BY l.created_at DESC"""
            )
            return [dict(r) for r in cur.fetchall()]


def get_license_row(license_key: str):
    """ライセンス1件を取得する（管理画面のキー再送で使用）。"""
    if not license_key:
        return None
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM licenses WHERE license_key = %s", (license_key,)
            )
            row = cur.fetchone()
    return dict(row) if row else None


def find_license_by_checkout(checkout_id: str):
    """チェックアウトIDからライセンスを引く。発行直後の一定時間だけ返す。
    見つからない場合と時間切れの場合は、区別せず None を返す
    （IDの推測に手がかりを与えないため）。"""
    if not checkout_id:
        return None
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT license_key, plan, expires_at, status
                     FROM licenses
                    WHERE checkout_id = %s
                      AND status = 'active'
                      AND created_at > CURRENT_TIMESTAMP
                                       - (%s * INTERVAL '1 minute')""",
                (checkout_id, CHECKOUT_LOOKUP_WINDOW_MINUTES),
            )
            row = cur.fetchone()
    return dict(row) if row else None


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
