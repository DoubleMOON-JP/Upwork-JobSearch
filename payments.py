# payments.py
# ─────────────────────────────────────────────────────────────
# 決済Webhook処理：プロバイダ非依存のコア ＋ Polarアダプタ。
# MoRを後から足せる形（各社アダプタが署名検証＋翻訳→共通コアへ集約）。
# ※ 既存 database.py に整合：
#     - プラン名は 1month / 3month / 6month / 1year
#     - create_license(email, plan) は dict を返す（['license_key'] で取得）
#     - サブスク紐づけ/無効化は db_redesign.py の関数を使用
# main.py で include_router(payments_router)。エンドポイントは POST /webhook/{provider}
#
# 【重要】イベントの割り当て方針（二重発行を防ぐため）
#   ・新規発行は「サブスクリプション作成」イベントだけで行う。
#   ・期限延長は「入金された注文」イベントだけで行う（お金が動いた時のみ延長）。
#   ・1回の購入では subscription.created と order.* の両方が飛んでくるため、
#     どちらでも発行してしまうとライセンスが2枚できる。役割を明確に分けること。
#   ・契約の突合キーは常に「サブスクリプションID」に統一する（注文IDは使わない）。
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Request, HTTPException

from database import create_license, extend_license
from db_redesign import (
    find_license_by_subscription, link_subscription, deactivate_license,
)
from plans import plan_months, DEFAULT_PLAN

router = APIRouter()
log = logging.getLogger("payments")


# ── 運用方針のスイッチ ─────────────────────────────────────
# 解約時の扱い：
#   False（推奨・方針B）… 解約予約が入っても支払い済み期間の終了まで使わせる。
#                          期間終了時の revoked / 返金でのみ無効化する。
#   True （方針A）      … 解約の連絡が来た時点で即座に無効化する。
CANCEL_IMMEDIATELY = False

# 商品IDの突合を厳格にするか：
#   True  … 対応表に無い商品IDは拒否する（プランが複数あるため必須）
#   False … 対応表に無い商品IDは DEFAULT_PLAN として扱う
# ★プランが2種類になったため True。False に戻すと、環境変数の設定漏れがあった時に
#   Proプランの購入者へ標準プランのライセンスを誤発行してしまう。
STRICT_PRODUCT_MAPPING = True

# プラン変更をWebhookで自動反映するか：
#   True  … 検知した時点で自動的にプランを変更し、繰り越しも自動で付与する
#   False … ログに警告を出すだけ。運営者が管理画面から手動で変更する
# 有効期限は変更しない方針のため、自動反映しても課金と権利がズレることはない。
# 管理画面の「プラン変更」ボタンは、自動反映が失敗した場合の修正手段として残してある。
AUTO_APPLY_PLAN_CHANGE = True


def _mail_license_key(email: str, license_key: str, plan: str,
                      expires_at: Optional[str] = None) -> None:
    """ライセンスキーをメール送付する。失敗しても処理は続行する。
    mailer は遅延importにしてある（未配置でもサーバーが起動できるようにするため）。"""
    try:
        import mailer
        ok = mailer.send_license_key(email, license_key, plan, expires_at)
        if not ok:
            log.error("MANUAL ACTION REQUIRED: mail not sent. key=%s to=%s",
                      license_key, email)
    except Exception as e:
        log.error("MANUAL ACTION REQUIRED: mailer error (%s). key=%s to=%s",
                  e, license_key, email)


class EventKind(str, Enum):
    ACTIVATE    = "activate"     # 新規購入 → 発行
    RENEW       = "renew"        # 更新の入金 → 延長
    CANCEL      = "cancel"       # 失効/返金 → 無効化
    PLAN_NOTICE = "plan_notice"  # 契約内容の変更 → ログに記録するだけ（自動反映しない）


@dataclass
class PaymentEvent:
    kind: EventKind
    provider: str
    email: str
    subscription_id: str
    product_id: str
    plan: Optional[str] = None


# 各MoRの商品ID → 自社プラン名。
# Polarで商品を作成したら、それぞれのIDを Render の環境変数に設定する：
#   POLAR_PRODUCT_1MONTH      … 標準プラン（$9/月・月100回）
#   POLAR_PRODUCT_1MONTH_PRO  … 上位プラン（$15/月・月300回）
# ★未設定のままだとダミー値のままになり、実際の購入は拒否される（誤発行より安全側）。
PRODUCT_TO_PLAN = {
    "polar": {
        os.environ.get("POLAR_PRODUCT_1MONTH",     "prod_1month_unset"):     "1month",
        os.environ.get("POLAR_PRODUCT_1MONTH_PRO", "prod_1month_pro_unset"): "1month_pro",
    },
    # "paddle": { ... },  # 将来：足すだけ
}


def resolve_plan(provider: str, product_id: str) -> Optional[str]:
    plan = PRODUCT_TO_PLAN.get(provider, {}).get(product_id)
    if plan:
        return plan
    if STRICT_PRODUCT_MAPPING:
        # 環境変数の設定漏れ、または未登録の商品。誤ったプランで発行せず拒否する。
        log.error(
            "unknown product id %s/%s - check POLAR_PRODUCT_* environment variables",
            provider, product_id,
        )
        return None
    log.warning("unknown product id %s/%s -> fallback to %s", provider, product_id, DEFAULT_PLAN)
    return DEFAULT_PLAN


# ── プロバイダ非依存のコア ─────────────────────────────────
def _notify_plan_change(ev: PaymentEvent, existing) -> None:
    """
    Polar側で契約内容が変更された時に、自社ライセンスとのズレをログに出す。
    自動でDBを書き換えないのは、意図しないプラン変更や上限の増減を避けるため。
    ズレが出た場合は管理画面から手動でプランを変更する運用。
    """
    if not existing:
        log.info("plan notice for unknown subscription %s (ignored)", ev.subscription_id)
        return

    new_plan = PRODUCT_TO_PLAN.get(ev.provider, {}).get(ev.product_id)
    current_plan = existing.get("plan")

    if new_plan is None:
        log.warning(
            "PLAN NOTICE: subscription=%s license=%s - unknown product %s "
            "(check POLAR_PRODUCT_* environment variables)",
            ev.subscription_id, existing.get("license_key"), ev.product_id,
        )
        return

    if new_plan != current_plan:
        if AUTO_APPLY_PLAN_CHANGE:
            from db_redesign import apply_plan_change
            res = apply_plan_change(existing["license_key"], new_plan)
            log.warning(
                "PLAN CHANGE APPLIED: license=%s  %s -> %s  carried_over=%s remaining=%s",
                existing.get("license_key"), current_plan, new_plan,
                res.get("carried_over"), res.get("remaining"),
            )
            return
        # ★管理画面から手動でプランを変更する必要がある
        log.warning(
            "PLAN CHANGE DETECTED: license=%s email=%s  %s -> %s  "
            "(subscription=%s) *** MANUAL ACTION REQUIRED in /admin ***",
            existing.get("license_key"), existing.get("email"),
            current_plan, new_plan, ev.subscription_id,
        )
    else:
        log.info("plan notice: no change for %s (%s)", existing.get("license_key"), current_plan)


def handle_payment_event(ev: PaymentEvent) -> None:
    if not ev.subscription_id:
        # 突合キーが無いイベントは処理しない（誤発行・重複発行の防止）
        log.warning("event without subscription_id ignored: %s/%s", ev.provider, ev.kind)
        return

    existing = find_license_by_subscription(ev.provider, ev.subscription_id)

    # 契約内容の変更通知：DBは書き換えず、プランのズレをログに残すだけ。
    # （リリース初期はプラン変更を管理画面から手動で反映する運用のため）
    if ev.kind == EventKind.PLAN_NOTICE:
        _notify_plan_change(ev, existing)
        return

    if ev.plan is None:
        ev.plan = resolve_plan(ev.provider, ev.product_id)
    if ev.plan is None:
        raise HTTPException(400, f"unknown product: {ev.provider}/{ev.product_id}")

    months = plan_months(ev.plan)

    if ev.kind == EventKind.ACTIVATE:
        if existing:
            # 同じ契約の再送（Webhookは再送されることがある）。二重発行しない。
            log.info("activate ignored, license already exists for %s", ev.subscription_id)
            return
        res = create_license(ev.email, ev.plan)        # dict を返す
        key = res["license_key"]
        link_subscription(key, ev.provider, ev.subscription_id)
        log.info("license issued %s for %s", key, ev.subscription_id)
        # 顧客へライセンスキーをメール送付。
        # 送信に失敗しても例外は出さない（mailer側で握りつぶす）。Webhookを落とすと
        # Polarが再送し、二重処理の原因になるため。失敗時はログを見て手動送付する。
        _mail_license_key(ev.email, key, ev.plan, res.get("expires_at"))

    elif ev.kind == EventKind.RENEW:
        if existing:
            extend_license(existing["license_key"], months)
            log.info("license extended %s (+%dm)", existing["license_key"], months)
        else:
            # 初回購入の入金だけ先に届いた場合の保険。発行して紐づける。
            res = create_license(ev.email, ev.plan)
            link_subscription(res["license_key"], ev.provider, ev.subscription_id)
            log.info("license issued on renew event %s", res["license_key"])
            _mail_license_key(ev.email, res["license_key"], ev.plan, res.get("expires_at"))

    elif ev.kind == EventKind.CANCEL:
        if existing:
            deactivate_license(existing["license_key"])
            log.info("license deactivated %s", existing["license_key"])


# ── アダプタ ───────────────────────────────────────────────
def _get(obj: Any, *names, default=None):
    """polar-sdk のオブジェクト／dict のどちらでも値を取り出せるようにする。"""
    for name in names:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            val = getattr(obj, name, None)
            if val is not None:
                return val
    return default


def _customer_email(data: Any) -> str:
    for holder in ("customer", "user"):
        obj = _get(data, holder)
        if obj is not None:
            email = _get(obj, "email")
            if email:
                return str(email)
    return str(_get(data, "customer_email", default="") or "")


def _product_id(data: Any) -> str:
    product = _get(data, "product")
    if product is not None:
        pid = _get(product, "id")
        if pid:
            return str(pid)
    return str(_get(data, "product_id", default="") or "")


class PaymentAdapter:
    provider = "base"

    def verify_and_parse(self, body: bytes, headers) -> Optional[PaymentEvent]:
        raise NotImplementedError


class PolarAdapter(PaymentAdapter):
    provider = "polar"

    # 新規発行を担当するイベント（サブスク作成のみ）
    ACTIVATE_EVENTS = {"subscription.created"}
    # 期限延長を担当するイベント（入金された注文のみ）
    ORDER_PAID_EVENTS = {"order.paid"}
    # 即時無効化するイベント（アクセス失効・返金）
    REVOKE_EVENTS = {"subscription.revoked", "order.refunded"}
    # 解約予約（方針Bでは無効化しない）
    CANCEL_REQUEST_EVENTS = {"subscription.canceled"}
    # 契約内容の変更（プラン変更など）。自動反映せず、ログに残すだけ。
    PLAN_NOTICE_EVENTS = {"subscription.updated"}

    def verify_and_parse(self, body: bytes, headers) -> Optional[PaymentEvent]:
        # Polarは Standard Webhooks 形式。公式Python SDKで署名検証。
        import json
        from polar_sdk.webhooks import validate_event, WebhookVerificationError
        secret = os.environ["POLAR_WEBHOOK_SECRET"]
        try:
            validate_event(body, dict(headers), secret)
        except WebhookVerificationError:
            raise HTTPException(401, "invalid signature")
        except Exception as e:
            # 署名検証は validate_event の内部で先に行われるため、ここに来た時点で
            # 署名は正しい。SDKのバージョンが古く、新しいイベント型（order.paid など）を
            # モデル化できない場合に発生する。内容は下の生JSONから読むので処理は続行する。
            log.warning("polar-sdk could not model payload (%s); using raw JSON", e)

        # 署名検証はSDKに任せ、中身は生JSONから読む。
        # SDKのモデルはバージョンによってフィールド名や型（Enum）が変わり、
        # 取得に失敗するとイベントが無言で無視されるため、ここでは依存しない。
        try:
            event = json.loads(body)
        except Exception:
            log.error("webhook body is not valid JSON; ignored")
            return None

        event_type = str(event.get("type") or "")
        data = event.get("data")

        # ══════════════════════════════════════════════════════
        # 【一時的な調査用ログ】/thanks へのライセンス表示を検討するため、
        # Polarのペイロードに checkout_id が含まれるかを確認する。
        # 確認が済んだらこのブロックごと削除すること。
        # 個人情報をログに残さないよう、値ではなくキー名のみを出力する。
        # ══════════════════════════════════════════════════════
        try:
            if isinstance(data, dict):
                log.info("PAYLOAD-KEYS [%s]: %s", event_type, sorted(data.keys()))
                hits = {}
                for k, v in data.items():
                    if "checkout" in k.lower():
                        hits[k] = v if not isinstance(v, (dict, list)) else type(v).__name__
                co = data.get("checkout")
                if isinstance(co, dict):
                    hits["checkout.id"] = co.get("id")
                log.info("PAYLOAD-CHECKOUT [%s]: %s", event_type, hits or "NOT FOUND")
        except Exception as _e:
            log.warning("payload inspection failed: %s", _e)
        # ══════════ 一時的な調査用ログ ここまで ══════════

        if event_type in self.ACTIVATE_EVENTS:
            # サブスクリプション本体のイベント → id がそのまま契約ID
            subscription_id = str(_get(data, "id", default="") or "")
            kind = EventKind.ACTIVATE

        elif event_type in self.ORDER_PAID_EVENTS:
            # 注文イベント → 契約IDは subscription_id 側にある（id は注文IDなので使わない）
            subscription_id = str(_get(data, "subscription_id", default="") or "")
            if not subscription_id:
                sub = _get(data, "subscription")
                subscription_id = str(_get(sub, "id", default="") or "")
            if not subscription_id:
                # 単発購入（サブスクではない注文）は現在の商品構成では発生しない
                log.info("order without subscription ignored: %s", event_type)
                return None
            # billing_reason は purchase / subscription_create /
            # subscription_cycle / subscription_update のいずれか。
            # 期限を延長してよいのは subscription_cycle（＝更新）だけ。
            #   purchase / subscription_create … 初回購入。subscription.created 側で発行済み
            #   subscription_update            … プラン変更の差額請求。日割りの差額しか
            #                                    入金されないため、延長すると1ヶ月分の
            #                                    取りこぼしになる（方針：期限は変えない）
            raw_reason = _get(data, "billing_reason", default="")
            reason = str(getattr(raw_reason, "value", raw_reason) or "")
            if reason != "subscription_cycle":
                log.info("order ignored (billing_reason=%s)", reason)
                return None
            kind = EventKind.RENEW

        elif event_type in self.REVOKE_EVENTS:
            subscription_id = str(
                _get(data, "subscription_id", default="")
                or _get(data, "id", default="")
                or ""
            )
            kind = EventKind.CANCEL

        elif event_type in self.PLAN_NOTICE_EVENTS:
            subscription_id = str(_get(data, "id", default="") or "")
            kind = EventKind.PLAN_NOTICE

        elif event_type in self.CANCEL_REQUEST_EVENTS:
            if not CANCEL_IMMEDIATELY:
                # 方針B：支払い済み期間の終了まで使わせる。
                # 期間終了時に subscription.revoked が届くので、そこで無効化する。
                log.info("cancel request received, keeping access until period end")
                return None
            subscription_id = str(_get(data, "id", default="") or "")
            kind = EventKind.CANCEL

        else:
            log.info("polar event ignored (not handled): %s", event_type)
            return None

        log.info("polar event accepted: %s (%s)", event_type, kind)
        return PaymentEvent(
            kind=kind,
            provider=self.provider,
            email=_customer_email(data),
            subscription_id=subscription_id,
            product_id=_product_id(data),
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
