# plans.py
# ─────────────────────────────────────────────────────────────
# プラン定義の単一情報源。
# 既存 database.py の create_license が使うプラン名（1month/3month/6month/1year）に合わせる。
#   months       : ライセンス付与月数
#   monthly_cap  : 月間の採点回数上限（Geminiコスト防御）
# rate_limit.py / evaluate.py / payments.py がここを参照する。
# ─────────────────────────────────────────────────────────────

# プランは 1month の1種類に統一。
# 将来プランを増やす場合はここに行を足すだけ（payments.py の対応表も併せて更新）。
PLANS = {
    "1month": {"months": 1, "monthly_cap": 100},
}

# 連打防止：同一ライセンスの採点間隔（秒）
RATE_LIMIT_SECONDS = 120  # 2分に1回


def plan_months(plan: str) -> int:
    return PLANS.get(plan, PLANS["1month"])["months"]


def plan_monthly_cap(plan: str) -> int:
    return PLANS.get(plan, PLANS["1month"])["monthly_cap"]
