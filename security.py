# security.py
# ─────────────────────────────────────────────────────────────
# セキュリティ対策の判定ロジック（v3.37）。main.py のミドルウェアと認証から使う。
# FastAPI に依存しない純粋な関数・クラスだけを置き、単体でテストできるようにしている。
#
#   1. 管理者パスワードの「未設定なら開かない」判定 … admin_password_usable()
#   2. 管理画面の書き込み操作の送信元チェック（CSRF対策）… is_cross_site_write()
#   3. ログイン失敗回数の制限 … LoginThrottle
#   4. セキュリティヘッダー … SECURITY_HEADERS
#
# 【決めごと（2026-11-06 Koji）】情報をできるだけ持たない方針のため、
#   ログイン失敗の記録はメモリ上だけに持ち、DB・ログには残さない（再起動で消える）。
#   記録の単位はログインIDで、IPアドレスは使わない。
# ─────────────────────────────────────────────────────────────
import threading
import time
from urllib.parse import urlsplit

# ── 1. 管理者パスワード ──────────────────────────────────
# 以前の main.py は ADMIN_PASSWORD 未設定時に "changeme" を使っていた。
# Render を作り直すなどで環境変数が消えると、誰でも管理画面に入れてしまう。
# 未設定・既定値のままなら管理者ログインそのものを受け付けない。
INSECURE_ADMIN_PASSWORDS = {"", "changeme"}


def admin_password_usable(password: str | None) -> bool:
    return (password or "").strip() not in INSECURE_ADMIN_PASSWORDS


# ── 2. CSRF対策（送信元の確認）──────────────────────────
# 管理画面はBasic認証のため、ブラウザは他サイトからのリクエストにも資格情報を付けて送る。
# 書き込み系（POST等）は、送信元が自サイトであることを Origin（無ければ Referer）で確かめる。
#   ・Origin が自サイト以外、または "null" → 拒否
#   ・Origin が無く Referer が自サイト以外 → 拒否
#   ・どちらも無い → 許可（ブラウザ以外のツール。ブラウザは他サイトからの POST に必ず Origin を付ける）
PROTECTED_PREFIXES = ("/admin", "/staff")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _netloc(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").lower()
    except ValueError:
        return ""


def allowed_hosts(base_url: str, request_host: str) -> set[str]:
    hosts = {_netloc(base_url), (request_host or "").lower()}
    return {h for h in hosts if h}


def is_cross_site_write(method: str, path: str, origin: str | None,
                        referer: str | None, hosts: set[str]) -> bool:
    """拒否すべき（他サイトからの書き込み）なら True。"""
    if (method or "").upper() not in UNSAFE_METHODS:
        return False
    if not any(path == p or path.startswith(p + "/") for p in PROTECTED_PREFIXES):
        return False
    if origin is not None and origin != "":
        if origin.strip().lower() == "null":
            return True
        return _netloc(origin) not in hosts
    if referer:
        return _netloc(referer) not in hosts
    return False


# ── 3. ログイン失敗回数の制限 ──────────────────────────
# 同じログインIDで WINDOW 秒以内に MAX_FAILURES 回失敗したら、LOCK 秒間そのIDのログインを止める。
# 止めている間は正しいパスワードでも通さない（総当たりを続けても当たらないようにするため）。
# 他人がわざと失敗させて本人を締め出すこともできるが、15分で自動解除され、
# 管理者は別のIDなので影響を受けない。
class LoginThrottle:
    MAX_FAILURES = 10
    WINDOW = 15 * 60
    LOCK = 15 * 60
    MAX_TRACKED = 5000      # 記録するID数の上限（存在しないIDを大量に試されてもメモリが増え続けないように）

    def __init__(self, now=time.monotonic):
        self._now = now
        self._fail: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(login_id: str) -> str:
        return (login_id or "").strip().lower()[:128]

    def locked_for(self, login_id: str) -> int:
        """ロック中なら残り秒数、ロックされていなければ 0。"""
        k = self._key(login_id)
        with self._lock:
            until = self._locked_until.get(k, 0)
            left = until - self._now()
            if left <= 0:
                self._locked_until.pop(k, None)
                return 0
            return int(left) + 1

    def record_failure(self, login_id: str) -> None:
        k = self._key(login_id)
        now = self._now()
        with self._lock:
            if len(self._fail) >= self.MAX_TRACKED and k not in self._fail:
                self._prune(now)
                if len(self._fail) >= self.MAX_TRACKED:
                    self._fail.pop(next(iter(self._fail)))
            times = [t for t in self._fail.get(k, []) if now - t < self.WINDOW]
            times.append(now)
            self._fail[k] = times
            if len(times) >= self.MAX_FAILURES:
                self._locked_until[k] = now + self.LOCK
                self._fail.pop(k, None)

    def record_success(self, login_id: str) -> None:
        with self._lock:
            self._fail.pop(self._key(login_id), None)

    def _prune(self, now: float) -> None:
        for k in [k for k, ts in self._fail.items() if all(now - t >= self.WINDOW for t in ts)]:
            self._fail.pop(k, None)


# ── 4. セキュリティヘッダー ──────────────────────────────
# ・frame-ancestors 'none' / X-Frame-Options … 他サイトへの埋め込み（クリックジャッキング）を防ぐ
# ・nosniff … ファイルの種類の取り違えを防ぐ
# ・Referrer-Policy … 他サイトへ移動するときにURLのパス（?ref= など）を渡さない
# ・HSTS … 常に https で接続させる。サブドメインには付けない（メール等に影響させないため）
# スクリプトや画像の読み込み元を絞る本格的な CSP は、各ページにインラインのスクリプトが多いため入れていない。
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000",
}
