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
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Request, HTTPException

from database import create_license, extend_license
from db_redesign import (
    find_license_by_subscription, link_subscription, deactivate_license,
    link_checkout, set_mail_status, set_license_ref,
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

# 無料トライアルで発行するときのプラン名（plans.py に定義がある）。
# 商品IDは Basic のままなので PRODUCT_TO_PLAN では解決できない。
# status="trialing" を根拠に、発行の直前で差し替える。
#   ・トライアル中 … このプラン（月10回）
#   ・本課金へ移行 … 商品IDから解決した本来のプラン（Basic 100回 / Pro 300回）へ戻す
# ★戻す処理を落とすと、$9を払った顧客が月10回しか使えない状態になる。
#   エラーは出ず、顧客が11件目で初めて気づくため、最も見つけにくい壊れ方になる。
TRIAL_PLAN = "trial"


def _record_mail_status(license_key: str, status: str, error: str = None) -> None:
    """送付結果をDBに残す。ここで失敗してもWebhookは落とさない
    （記録できなくても、ライセンス発行そのものは成立しているため）。"""
    try:
        set_mail_status(license_key, status, error)
    except Exception as e:
        log.error("could not record mail status (%s) for %s: %s",
                  status, license_key, e)


def _alert_failure(email: str, license_key: str, plan: str, reason: str) -> None:
    """送付失敗を運営者へメールで知らせる。

    SMTPが落ちている場合はこの通知自体も届かないため、これだけに頼らないこと。
    管理画面の「未送信（赤）」表示が本命の検知手段で、これはその補助。
    通知の送信で例外が出ても握りつぶす（Webhookを落とさないため）。"""
    try:
        import mailer
        mailer.send_failure_alert(email, license_key, plan, reason)
    except Exception as e:
        log.error("could not send failure alert: %s", e)


def _mail_license_key(email: str, license_key: str, plan: str,
                      expires_at: Optional[str] = None) -> None:
    """ライセンスキーをメール送付し、結果をDBに記録する。失敗しても処理は続行する。
    mailer は遅延importにしてある（未配置でもサーバーが起動できるようにするため）。

    送信できなかった場合は mail_status='failed' を立てる。管理画面の一覧が
    赤く表示されるので、ログを見ていなくても気づける。"""
    try:
        import mailer
        ok = mailer.send_license_key(email, license_key, plan, expires_at)
        if ok:
            _record_mail_status(license_key, "sent")
        else:
            log.error("MANUAL ACTION REQUIRED: mail not sent. key=%s to=%s",
                      license_key, email)
            _record_mail_status(license_key, "failed", "send returned false")
            _alert_failure(email, license_key, plan, "send returned false")
    except Exception as e:
        log.error("MANUAL ACTION REQUIRED: mailer error (%s). key=%s to=%s",
                  e, license_key, email)
        _record_mail_status(license_key, "failed", str(e))
        _alert_failure(email, license_key, plan, str(e))


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
    # PolarのチェックアウトID。/thanks でのキー表示に使う（無くても発行は成立する）。
    checkout_id: str = ""
    # 紹介コード（SNS流入計測）。付いていないことの方が多い。
    ref_code: str = ""
    # 無料トライアル中に発行する場合の有効期限（trial_end の日付）。
    # None なら従来どおりプランの月数から計算する。
    expires_at: Optional[date] = None
    # 無料トライアルとして申し込まれたか（Polarの status="trialing"）。
    # expires_at の有無で判定しないのは、日付の取得に失敗しても
    # 「トライアルである」という事実は変わらないため。
    is_trial: bool = False


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

    # ★トライアル中は自動変更しない（v3.33）。
    # トライアルのライセンスは plan="trial" だが、商品IDは Basic のままなので
    # ここでは必ず「trial → 1month」というズレとして見える。
    # AUTO_APPLY_PLAN_CHANGE=True のまま放置すると、subscription.updated が
    # 1回飛んだだけで（解約予約・カード情報の更新などでも飛ぶ）トライアル中に
    # 上限が100回へ増えてしまい、10回の制限が意味を失う。
    # トライアルから本来のプランへ戻すのは RENEW（本課金の入金）だけの仕事とする。
    if current_plan == TRIAL_PLAN:
        log.info("plan notice ignored while on trial: license=%s subscription=%s",
                 existing.get("license_key"), ev.subscription_id)
        return

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

    # 商品IDから解決した「本来のプラン」を控えておく。
    # トライアル発行では ev.plan を trial に差し替えるため、
    # 本課金へ移行したときに戻す先が分からなくなるのを防ぐ。
    paid_plan = ev.plan

    if ev.kind == EventKind.ACTIVATE:
        if existing:
            # 同じ契約の再送（Webhookは再送されることがある）。二重発行しない。
            log.info("activate ignored, license already exists for %s", ev.subscription_id)
            return
        # 無料トライアルなら、上限10回のトライアルプランで発行する（v3.33）。
        # 商品IDは Basic のままなので、ここで差し替えないと100回で発行される。
        if ev.is_trial:
            ev.plan = TRIAL_PLAN
            log.info("trial checkout -> issuing with plan=%s (paid plan will be %s)",
                     TRIAL_PLAN, paid_plan)
        # expires_at はトライアル発行時のみ入る。None なら従来どおり
        # プランの月数から計算される（既存の呼び出しと同じ挙動）。
        res = create_license(ev.email, ev.plan, expires_at=ev.expires_at)
        key = res["license_key"]
        link_subscription(key, ev.provider, ev.subscription_id)
        # チェックアウトIDを紐づける。/thanks がこれを鍵にキーを取りに来る。
        # 失敗してもメールは届くので、ここでは落とさない。
        try:
            link_checkout(key, ev.checkout_id)
        except Exception as e:
            log.error("could not link checkout_id for %s: %s", key, e)
        # 紹介コード。計測用の付加情報なので、失敗しても発行は続行する。
        try:
            if ev.ref_code:
                set_license_ref(key, ev.ref_code)
        except Exception as e:
            log.error("could not link ref_code for %s: %s", key, e)
        log.info("license issued %s for %s (ref=%s, expires=%s%s)",
                 key, ev.subscription_id, ev.ref_code or "-",
                 res.get("expires_at"), " TRIAL" if ev.expires_at else "")
        # 顧客へライセンスキーをメール送付。
        # 送信に失敗しても例外は出さない（mailer側で握りつぶす）。Webhookを落とすと
        # Polarが再送し、二重処理の原因になるため。失敗時はログを見て手動送付する。
        _mail_license_key(ev.email, key, ev.plan, res.get("expires_at"))

    elif ev.kind == EventKind.RENEW:
        if existing:
            extend_license(existing["license_key"], months)
            log.info("license extended %s (+%dm)", existing["license_key"], months)
            # ★トライアルからの本課金移行（v3.33）。
            # extend_license は有効期限しか更新しないため、plan は trial のまま残る。
            # ここで本来のプランへ戻さないと、$9を払った顧客が月10回で止まる。
            # 繰り越し付きの apply_plan_change ではなく update_license_plan を使う。
            # 支払いが始まった月は素直に100回（Basic）とするのが説明のつく形で、
            # 繰り越し（10+100=110回）は金額の根拠がないため。
            if existing.get("plan") == TRIAL_PLAN and paid_plan != TRIAL_PLAN:
                try:
                    from database import update_license_plan
                    r = update_license_plan(existing["license_key"], paid_plan)
                    if r.get("success"):
                        log.info("trial converted: license=%s plan %s -> %s",
                                 existing["license_key"], TRIAL_PLAN, paid_plan)
                    else:
                        log.error("MANUAL ACTION REQUIRED: trial conversion failed "
                                  "for %s (%s). Change the plan to %s in /admin.",
                                  existing["license_key"], r.get("message"), paid_plan)
                except Exception as e:
                    # ここで例外を上げるとWebhookが500になり、Polarが再送して
                    # 期限が二重に延びる。記録だけ残して200を返し切る。
                    log.error("MANUAL ACTION REQUIRED: trial conversion error "
                              "for %s (%s). Change the plan to %s in /admin.",
                              existing["license_key"], e, paid_plan)
        else:
            # 初回購入の入金だけ先に届いた場合の保険。発行して紐づける。
            res = create_license(ev.email, ev.plan)
            link_subscription(res["license_key"], ev.provider, ev.subscription_id)
            try:
                link_checkout(res["license_key"], ev.checkout_id)
            except Exception as e:
                log.error("could not link checkout_id for %s: %s",
                          res["license_key"], e)
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


def _parse_utc_date(value: Any) -> Optional[date]:
    """Polar の日時文字列を date にする（例 "2026-08-26T01:44:05.647917Z"）。

    SDKのモデルを介さず生JSONを読んでいるため通常は文字列で来るが、
    将来 datetime が来ても壊れないよう両方受ける。
    解釈できなければ None を返し、呼び出し側は従来の計算に落ちる。
    """
    if value is None:
        return None
    if isinstance(value, datetime):     # datetime は date の派生なので先に判定する
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        # 末尾の "Z" を解釈できない版があるためオフセット表記に置き換える
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        log.warning("could not parse date: %r", value)
        return None


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
        # トライアル発行のときだけ値が入る。それ以外は None のまま渡す。
        expires_at: Optional[date] = None
        # 無料トライアルとして申し込まれたか（v3.33）。
        is_trial = False

        if event_type in self.ACTIVATE_EVENTS:
            # サブスクリプション本体のイベント → id がそのまま契約ID
            subscription_id = str(_get(data, "id", default="") or "")
            kind = EventKind.ACTIVATE
            # 無料トライアル付きで申し込まれた場合、この時点の status は
            # "trialing" で、trial_end にトライアル終了日時が入る。
            # 2026-08-25 の実測では current_period_end も同じ値だったので、
            # trial_end が取れなければそちらを使う。
            # ここを渡さないと、1日トライアルでも1か月分の期限で発行される。
            if str(_get(data, "status", default="") or "") == "trialing":
                is_trial = True
                expires_at = _parse_utc_date(
                    _get(data, "trial_end") or _get(data, "current_period_end")
                )
                log.info("trial subscription %s -> licence expires %s",
                         subscription_id, expires_at)

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

        # チェックアウトID。subscription.created / order.paid / subscription.updated
        # のいずれにも含まれることを実測で確認済み（2026-08-06）。
        # 無くても発行処理そのものは成立するため、取れなければ空文字のまま進める。
        checkout_id = str(_get(data, "checkout_id", default="") or "")
        if not checkout_id:
            co = _get(data, "checkout")
            checkout_id = str(_get(co, "id", default="") or "")

        # 紹介コード。Polarのチェックアウトリンクに付けたクエリは
        # メタデータへ格納される。reference_id と utm_source の両方を
        # 送っているので、取れた方を使う（Polar側の仕様変更に対する保険）。
        ref_code = ""
        meta = _get(data, "metadata")
        if isinstance(meta, dict):
            for key in ("reference_id", "utm_source", "ref"):
                val = meta.get(key)
                if val:
                    ref_code = str(val).strip()[:64]
                    break

        log.info("polar event accepted: %s (%s)", event_type, kind)
        return PaymentEvent(
            kind=kind,
            provider=self.provider,
            email=_customer_email(data),
            subscription_id=subscription_id,
            product_id=_product_id(data),
            checkout_id=checkout_id,
            ref_code=ref_code,
            expires_at=expires_at,
            is_trial=is_trial,
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
