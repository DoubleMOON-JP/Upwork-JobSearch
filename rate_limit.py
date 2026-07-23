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
from db_redesign import get_usage, record_usage


def check_and_consume(license_key: str, plan: str) -> dict:
    """
    OKなら使用量を1消費して {'remaining': n} を返す。
    NGなら HTTPException(429) を送出。
    """
    now = datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")
    cap = plan_monthly_cap(plan)

    usage = get_usage(license_key)

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
    return {"remaining": cap - count, "monthly_cap": cap}
