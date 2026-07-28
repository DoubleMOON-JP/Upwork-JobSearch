# plans.py
# ─────────────────────────────────────────────────────────────
# プラン定義の単一情報源。
#   months       : ライセンス付与月数
#   monthly_cap  : 月間の採点回数上限（Geminiコスト防御）
#   label        : 管理画面などに表示する名称
# database.py / main.py / rate_limit.py / payments.py がここを参照する。
#
# ★プランを増減する場合は、必ず payments.py の PRODUCT_TO_PLAN も更新すること。
#   STRICT_PRODUCT_MAPPING = True のため、対応表に無い商品IDは発行を拒否する。
# ─────────────────────────────────────────────────────────────

PLANS = {
    # 標準プラン（$9/月）
    "1month":     {"months": 1, "monthly_cap": 100, "label": "Standard（月100回）"},
    # 上位プラン（$15/月）
    "1month_pro": {"months": 1, "monthly_cap": 300, "label": "Pro（月300回）"},
}

# 対応表に無いプラン名が来た時のフォールバック先。
# 旧プラン（3month/6month/1year）で発行済みのライセンスが残っていても、
# ここを経由して標準プラン相当として扱われる。
DEFAULT_PLAN = "1month"

# 連打防止：同一ライセンスの採点間隔（秒）
RATE_LIMIT_SECONDS = 120  # 2分に1回


def plan_months(plan: str) -> int:
    return PLANS.get(plan, PLANS[DEFAULT_PLAN])["months"]


def plan_monthly_cap(plan: str) -> int:
    return PLANS.get(plan, PLANS[DEFAULT_PLAN])["monthly_cap"]


def plan_label(plan: str) -> str:
    """未知のプラン名はそのまま返す（旧プランの表示が消えないように）。"""
    entry = PLANS.get(plan)
    return entry["label"] if entry else plan


def is_valid_plan(plan: str) -> bool:
    return plan in PLANS
