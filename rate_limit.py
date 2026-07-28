# rate_limit.py
# ─────────────────────────────────────────────────────────────
# 採点リクエストのコスト防御。
#   - 連打防止：同一ライセンスは RATE_LIMIT_SECONDS（既定120秒）に1回まで
#   - 月間上限：プランごとの monthly_cap を超えたら拒否
# evaluate.py が採点実行の直前に check_and_consume() を呼ぶ。
# ─────────────────────────────────────────────────────────────
from datetime import datetime, timezone

from fastapi import HTTPException

from plans import RATE_LIMIT_SECONDS, plan_monthly_cap
from db_redesign import get_usage, record_usage, refund_usage


def check_and_consume(license_key: str, plan: str) -> dict:
    """
    OKなら使用量を1消費して {'remaining': n} を返す。
    NGなら HTTPException(429) を送出。

    採点が失敗した場合は、呼び出し側で release() を呼んで消費を戻すこと。
    """
    now = datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")
    cap = plan_monthly_cap(plan)

    usage = get_usage(license_key)

    # プラン変更で繰り越しがある月は、そちらを月間上限として使う。
    # （上位プランへ変更した際、旧プランの使い残しを加算した値が入っている）
    if usage and usage.get("cap_override") and usage.get("cap_override_month") == current_month:
        cap = max(cap, int(usage["cap_override"]))

    # ① 連打チェック
    if usage and usage.get("last_evaluated_at"):
        last = usage["last_evaluated_at"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds()
        if elapsed < RATE_LIMIT_SECONDS:
            wait = int(RATE_LIMIT_SECONDS - elapsed)
            raise HTTPException(
                status_code=429,
                detail={"reason": "rate_limited",
                        "message": f"Please wait {wait} seconds",
                        "retry_after_sec": wait},
            )

    # ② 当月カウント（月が変わったらリセット）
    if usage and usage.get("period_month") == current_month:
        count = usage["month_count"]
    else:
        count = 0

    # ③ 月間上限チェック
    if count >= cap:
        raise HTTPException(
            status_code=429,
            detail={"reason": "monthly_limit",
                    "message": f"Monthly limit reached ({cap} evaluations)",
                    "monthly_cap": cap},
        )

    # ④ 消費を記録
    count += 1
    record_usage(license_key, now, current_month, count)
    return {
        "remaining": cap - count,
        "monthly_cap": cap,
        "period_month": current_month,   # 失敗時の巻き戻しに使う
    }


def release(license_key: str, quota: dict) -> None:
    """
    採点が失敗した時に、check_and_consume() で消費した1回分を戻す。
    AI側の障害で利用者の月間回数が減らないようにするための処置。
    """
    period_month = (quota or {}).get("period_month")
    if not period_month:
        return
    try:
        refund_usage(license_key, period_month)
    except Exception:
        # 巻き戻しに失敗しても採点処理のエラー通知を妨げない
        pass
