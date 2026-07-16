# payments.py
# ─────────────────────────────────────────────────────────────
# 決済Webhook処理：プロバイダ非依存のコア ＋ Polarアダプタ。
# MoRを後から足せる形（各社アダプタが署名検証＋翻訳→共通コアへ集約）。
# ※ 既存 database.py に整合：
#     - プラン名は 1month / 3month / 6month / 1year
#     - create_license(email, plan) は dict を返す（['license_key'] で取得）
#     - サブスク紐づけ/無効化は db_redesign.py の関数を使用
# main.py で include_router(payments_router)。エンドポイントは POST /webhook/{provider}
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Request, HTTPException

from database import create_license, extend_license
from db_redesign import (
    find_license_by_subscription, link_subscription, deactivate_license,
)
from plans import plan_months

router = APIRouter()


class EventKind(str, Enum):
    ACTIVATE = "activate"   # 新規購入 → 発行
    RENEW    = "renew"      # 更新成功 → 延長
    CANCEL   = "cancel"     # 解約/返金 → 無効化（or 失効まで放置）


@dataclass
class PaymentEvent:
    kind: EventKind
    provider: str
    email: str
    subscription_id: str
    product_id: str
    plan: Optional[str] = None


# 各MoRの商品ID → 自社プラン名。プランは 1month の1種類に統一。
# 商品作成後、環境変数 POLAR_PRODUCT_1MONTH に実IDを設定する。
PRODUCT_TO_PLAN = {
    "polar": {
        os.environ.get("POLAR_PRODUCT_1MONTH", "prod_1month"): "1month",
    },
    # "paddle": { ... },  # 将来：足すだけ
}


def resolve_plan(provider: str, product_id: str) -> Optional[str]:
    # プランが1種類なので、対応表に無い商品IDでも 1month にフォールバックする。
    # （複数プランに増やしたら、この行を消して対応表のみに厳格化する）
    return PRODUCT_TO_PLAN.get(provider, {}).get(product_id, "1month")


# ── プロバイダ非依存のコア ─────────────────────────────────
def handle_payment_event(ev: PaymentEvent) -> None:
    if ev.plan is None:
        ev.plan = resolve_plan(ev.provider, ev.product_id)
    if ev.plan is None:
        raise HTTPException(400, f"unknown product: {ev.provider}/{ev.product_id}")

    months = plan_months(ev.plan)

    if ev.kind == EventKind.ACTIVATE:
        existing = find_license_by_subscription(ev.provider, ev.subscription_id)
        if existing:
            extend_license(existing["license_key"], months)
        else:
            res = create_license(ev.email, ev.plan)        # dict を返す
            key = res["license_key"]
            link_subscription(key, ev.provider, ev.subscription_id)
            # TODO: 顧客へ license_key をメール送付（Polar benefit でも可）

    elif ev.kind == EventKind.RENEW:
        lic = find_license_by_subscription(ev.provider, ev.subscription_id)
        if lic:
            extend_license(lic["license_key"], months)

    elif ev.kind == EventKind.CANCEL:
        lic = find_license_by_subscription(ev.provider, ev.subscription_id)
        if lic:
            # 方針A（即時無効化）:
            deactivate_license(lic["license_key"])
            # 方針B（推奨・期限まで使わせる）: 上行を消し、更新が来なければ自然失効させる


# ── アダプタ ───────────────────────────────────────────────
class PaymentAdapter:
    provider = "base"
    def verify_and_parse(self, body: bytes, headers) -> Optional[PaymentEvent]:
        raise NotImplementedError


class PolarAdapter(PaymentAdapter):
    provider = "polar"

    def verify_and_parse(self, body: bytes, headers) -> Optional[PaymentEvent]:
        # Polarは Standard Webhooks 形式。公式Python SDKで署名検証（import名はSDK版で要確認）
        from polar_sdk.webhooks import validate_event, WebhookVerificationError
        secret = os.environ["POLAR_WEBHOOK_SECRET"]
        try:
            event = validate_event(body, dict(headers), secret)
        except WebhookVerificationError:
            raise HTTPException(401, "invalid signature")

        t = event.type
        data = event.data

        if t in ("subscription.created", "order.created"):
            kind = EventKind.ACTIVATE
        elif t == "subscription.updated" and getattr(data, "status", "") == "active":
            kind = EventKind.RENEW
        elif t in ("subscription.canceled", "subscription.revoked", "order.refunded"):
            kind = EventKind.CANCEL
        else:
            return None

        return PaymentEvent(
            kind=kind,
            provider=self.provider,
            email=data.customer.email,
            subscription_id=(getattr(data, "id", "") or getattr(data, "subscription_id", "")),
            product_id=data.product.id,
        )


ADAPTERS = {
    "polar": PolarAdapter(),
    # "paddle": PaddleAdapter(),
}


@router.post("/webhook/{provider}")
async def payment_webhook(provider: str, request: Request):
    adapter = ADAPTERS.get(provider)
    if adapter is None:
        raise HTTPException(404, "unknown provider")
    body = await request.body()
    ev = adapter.verify_and_parse(body, request.headers)
    if ev is None:
        return {"status": "ignored"}
    handle_payment_event(ev)
    return {"status": "ok"}
