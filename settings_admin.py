# settings_admin.py
# ─────────────────────────────────────────────────────────────
# AI設定（為替レート）の管理画面。システム管理者のみ。
#
# 【main.py から分けた理由】
# main.py は既に1900行を超えている。画面を1つ足すたびにあのファイル全体を
# 触ることになり、無関係な箇所を壊す危険が積み上がる。
# ここはルート2つで完結し、他の画面と依存関係がないため独立させた。
#
# 【main.py に足す2行】ファイル末尾に置く。
#     from settings_admin import build_settings_router
#     app.include_router(build_settings_router(verify_admin))
#
# verify_admin を引数で受け取るのは循環importを避けるため。
# main.py が settings_admin を import するので、逆向きには import できない。
# 引数で渡せば、認証の実装は main.py の1か所のままにできる。
#
# 【画面にJavaScriptを使わない理由】
# 管理画面の他のページは fetch + JSON だが、ここは素のフォーム送信にした。
# main.py のHTMLはf-stringで組み立てており、JS内の波括弧を二重にする必要がある。
# 過去にこれで <script> ブロックごと構文エラーになった記録がある（README参照）。
# 保存が年2回の画面でその危険を負う理由がない。
# ─────────────────────────────────────────────────────────────
import json
import re
from datetime import date

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse

from database import get_conn, get_ai_settings
from sites import SITES, site_label

# キー名は evaluate.py を定義元とする。両方に文字列を書くと、
# 片方だけ直したときに「保存はできるのに採点に反映されない」という
# 原因の分かりにくい不具合になる。
from evaluate import SETTING_RATES, SETTING_RATES_UPDATED, build_currency_block

# 見直しの推奨間隔（月）。対応予定 No.22「為替レートの見直し（半年ごと）」に対応する。
REVIEW_INTERVAL_MONTHS = 6

# 通貨コードはISO 4217（英大文字3文字）。
_CODE_RE = re.compile(r"[A-Z]{3}")

# 「INR = 0.0115」「INR: 0.0115」「INR 0.0115」のいずれも受ける。
# JSONより打ち間違いが起きにくいため、こちらを標準の書き方にしている。
_LINE_RE = re.compile(r"^([A-Za-z]+)\s*[=:,]?\s*([-+0-9.eE]+)$")


# ── DB（ai_settings の読み書き）──────────────────────────
def _save_setting(key: str, value: str) -> None:
    """ai_settings を1件保存する。行が無ければ作る。

    database.py の update_ai_setting() は UPDATE のみで、行が存在しないと
    何も起きずに成功を返す。為替レートの行は初期データに含まれていないため、
    それを使うと「保存したのに反映されない」状態になる。ここでは UPSERT する。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_settings (key, value, note)
                        VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                (key, value, _NOTE.get(key, "")),
            )


# 新規作成時にだけ入る説明。DBを直接見た人が用途を追えるようにするため。
_NOTE = {
    SETTING_RATES: "対USDレート（JSON）。管理画面 /admin/settings から編集する",
    SETTING_RATES_UPDATED: "為替レートの最終更新日。保存時に自動で入る",
}


# ── 入力の検証 ────────────────────────────────────────────
def parse_rates_input(raw: str):
    """画面の入力を検証する。(正規化済みdict, エラー文のリスト) を返す。

    エラーが1つでもあれば dict は None にして、部分的な保存を防ぐ。
    半分だけ保存されると「直したつもりで直っていない」状態になり、
    採点結果を見ても気づけないため。
    """
    raw = (raw or "").strip()
    if not raw:
        return {}, []          # 空欄＝未設定に戻す（参照表を差し込まなくなる）

    # 他から貼り付けた場合に備えてJSONも受ける。
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            return None, ["JSON形式のようですが読み取れませんでした。"
                          "カンマ・引用符・波括弧を確認してください。"]
        if not isinstance(data, dict):
            return None, ['JSONは {"INR": 0.0115} の形にしてください。']
        items = list(data.items())
    else:
        items = []
        for n, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue       # 空行とコメント行は無視
            m = _LINE_RE.match(line)
            if not m:
                return None, [f"{n}行目が読み取れません：「{line}」",
                              "「INR = 0.0115」のように、通貨コードと数値を1行ずつ書いてください。"]
            items.append((m.group(1), m.group(2)))

    rates, errors = {}, []
    for code, value in items:
        code = str(code).strip().upper()
        if not _CODE_RE.fullmatch(code):
            errors.append(f"「{code}」は通貨コードとして使えません。"
                          f"英字3文字にしてください（例：INR、CAD、GBP）")
            continue
        try:
            rate = float(value)
        except (TypeError, ValueError):
            errors.append(f"{code} の値「{value}」が数値ではありません")
            continue
        if rate <= 0:
            errors.append(f"{code} の値は0より大きい数にしてください（現在：{rate:g}）")
            continue
        if code in rates:
            errors.append(f"{code} が2回書かれています")
            continue
        rates[code] = rate

    return (None if errors else rates), errors


def format_rates(rates: dict) -> str:
    """画面に表示する形（1行1通貨）。読みやすさを優先し、通貨コード順に並べる。"""
    return "\n".join(f"{code} = {rate:g}" for code, rate in sorted(rates.items()))


def load_rates(settings: dict) -> dict:
    """保存済みのレートを読む。壊れていれば空dict（画面側で警告する）。"""
    try:
        data = json.loads(str(settings.get(SETTING_RATES) or "").strip() or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def next_review(updated: str) -> str:
    """次回の見直し目安。更新日が読めなければ空文字。"""
    try:
        d = date.fromisoformat(updated)
    except (TypeError, ValueError):
        return ""
    month = d.month - 1 + REVIEW_INTERVAL_MONTHS
    return f"{d.year + month // 12}年{month % 12 + 1}月頃"


def esc(v) -> str:
    """HTMLへ埋め込む際のエスケープ。main.py の同名関数と同じ実装。
    import すると循環参照になるため、ここに持つ（5行なので重複を許容する）。"""
    return (
        str("" if v is None else v)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


# ── 画面 ──────────────────────────────────────────────────
_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A;
       font-size: 13px; padding: 24px; }
.container { max-width: 900px; margin: 0 auto; }
.header { background: #1A2B4A; color: white; padding: 14px 20px; border-radius: 8px;
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: 16px; }
.header h1 { font-size: 16px; }
.header a { color: #9FB0CC; font-size: 12px; text-decoration: none; }
.card { background: white; border-radius: 8px; padding: 20px 24px; margin-bottom: 16px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.card h2 { font-size: 14px; color: #1A2B4A; margin-bottom: 14px;
           padding-bottom: 6px; border-bottom: 2px solid #EBF3FB; }
textarea { width: 100%; min-height: 150px; padding: 10px 12px; border: 1px solid #BFCFDF;
           border-radius: 5px; font-size: 13px; font-family: monospace; resize: vertical; }
.btn { padding: 10px 24px; border: none; border-radius: 5px; font-size: 13px;
       font-weight: bold; cursor: pointer; margin-top: 12px; background: #C55A11;
       color: white; }
.msg { padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-size: 12px;
       line-height: 1.8; }
.msg.ok { background: #E2EFDA; color: #375623; }
.msg.error { background: #FCE4D6; color: #843C0C; }
.note { font-size: 11px; color: #777; margin-top: 8px; line-height: 1.8; }
.help { background: #FFF8E7; border: 1px solid #FFD966; padding: 10px 12px;
        border-radius: 5px; font-size: 11.5px; color: #7F6000; line-height: 1.8;
        margin-bottom: 12px; }
pre { background: #F0F4F8; border: 1px solid #DCE6F1; border-radius: 5px;
      padding: 12px; font-size: 11.5px; line-height: 1.6; white-space: pre-wrap;
      word-break: break-word; }
table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
th { background: #2E3A4E; color: white; padding: 7px 10px; text-align: left; }
td { padding: 6px 10px; border-bottom: 1px solid #F0F0F0; }
ol { margin: 6px 0 0 20px; font-size: 12px; line-height: 2; }
"""


def _render(rates: dict, raw_text: str, settings: dict,
            message: str = "", is_error: bool = False) -> HTMLResponse:
    updated = str(settings.get(SETTING_RATES_UPDATED) or "").strip()
    review = next_review(updated)

    # 実際にAIへ送られる文面。ここで見せておけば、採点してみるまで
    # 結果が分からない状態にならない。
    preview = build_currency_block({"multi_currency": True}, settings)
    if preview.strip():
        preview_html = f"<pre>{esc(preview.strip())}</pre>"
    else:
        preview_html = ('<p style="font-size:12px;color:#777">'
                        'レートが未設定のため、採点プロンプトには何も追加されません。'
                        '（この機能を入れる前と同じ動作です）</p>')

    # 参照表を受け取るサイト。sites.py の multi_currency で決まる。
    targets = [site_label(sid) for sid, conf in SITES.items()
               if conf.get("multi_currency") and conf.get("enabled")]
    targets_text = "、".join(targets) if targets else "（対象サイトなし）"

    msg_html = ""
    if message:
        msg_html = (f'<div class="msg {"error" if is_error else "ok"}">'
                    f'{message}</div>')

    rows = "".join(
        f"<tr><td style='font-family:monospace'>{esc(c)}</td>"
        f"<td style='text-align:right;font-family:monospace'>{r:g}</td>"
        f"<td style='color:#777'>1 {esc(c)} = {r:g} USD</td></tr>"
        for c, r in sorted(rates.items())
    ) or ('<tr><td colspan="3" style="text-align:center;color:#999;padding:16px">'
          '未設定</td></tr>')

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<title>AI設定（為替レート） - JobSearch</title>
<style>{_STYLE}</style></head><body>
<div class="container">

<div class="header">
  <h1>💱 AI設定 — 為替レート</h1>
  <a href="/admin">← 管理画面に戻る</a>
</div>

{msg_html}

<div class="card">
  <h2>レートの設定</h2>
  <div class="help">
    <b>1行に1通貨、「通貨コード = 対USDレート」の形で書いてください。</b><br>
    例：<code>INR = 0.0115</code>（1インドルピー ＝ 0.0115米ドル）<br>
    ・通貨コードは英字3文字（INR／CAD／GBP／EUR／AUD など）<br>
    ・<code>#</code> で始まる行と空行は無視されます<br>
    ・<b>USD は書かなくて構いません</b>（1 USD = 1 USD は自明なため）<br>
    ・空欄で保存すると未設定に戻り、採点プロンプトには何も追加されなくなります
  </div>
  <form method="post" action="/admin/settings/exchange-rates">
    <textarea name="rates" spellcheck="false"
      placeholder="INR = 0.0115&#10;CAD = 0.73&#10;GBP = 1.27">{esc(raw_text)}</textarea>
    <button type="submit" class="btn">保存</button>
  </form>
  <div class="note">
    最終更新：<b>{esc(updated) if updated else "未設定"}</b>
    {f"／次回の見直し目安：<b>{esc(review)}</b>" if review else ""}<br>
    ※ 更新日は保存時に自動で記録されます（対応予定 No.22 の半年ごとの見直し用）。<br>
    ※ 保存後は<b>再デプロイ不要</b>で、次の採点からすぐ反映されます。
  </div>
</div>

<div class="card">
  <h2>現在の設定内容</h2>
  <table>
    <thead><tr><th>通貨</th><th style="text-align:right">レート</th><th>意味</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>

<div class="card">
  <h2>採点プロンプトに追加される内容</h2>
  <p style="font-size:12px;color:#777;margin-bottom:10px">
    対象サイト：<b>{esc(targets_text)}</b>
    （sites.py の <code>multi_currency</code> で決まります。Upwork は対象外です）
  </p>
  {preview_html}
  <div class="note">
    ここに表示されている文面が、対象サイトの採点時にプロンプトへ追加されます。<br>
    <b>レートの「使い方」の指示は、各サイトのプロンプト本文に書いてください。</b>
    この画面が持つのは数値だけです。
  </div>
</div>

<div class="card">
  <h2>プロンプトからの移行手順</h2>
  <p style="font-size:12px;color:#777">
    Freelancer.com のプロンプト（ID 7）の <code>[Currency]</code> セクションに
    レートが直接書かれている場合は、次の順で移してください。
    この順序なら、レートが一瞬も欠けません。
  </p>
  <ol>
    <li>プロンプト ID 7 の <code>[Currency]</code> にある数値を、上の欄に写して保存する</li>
    <li>同じ貼付テキストで採点し、換算結果が変わらないことを確認する
        <span style="color:#777">（この時点ではレートが二重に入っているが、同じ値なので問題ない）</span></li>
    <li>確認できたら、プロンプト ID 7 から <code>[Currency]</code> セクションを削除する</li>
    <li>もう一度同じテキストで採点し、換算が維持されていることを確認する</li>
  </ol>
</div>

</div></body></html>""")


# ── ルート ────────────────────────────────────────────────
def build_settings_router(verify_admin) -> APIRouter:
    """管理画面のルートを組み立てて返す。

    verify_admin を引数で受け取るのは循環importを避けるため
    （main.py がこのモジュールを import するので、逆向きにはできない）。
    """
    router = APIRouter()

    @router.get("/admin/settings", response_class=HTMLResponse)
    async def settings_page(username: str = Depends(verify_admin)):
        settings = get_ai_settings() or {}
        rates = load_rates(settings)
        broken = bool(str(settings.get(SETTING_RATES) or "").strip()) and not rates
        return _render(
            rates, format_rates(rates), settings,
            message=("保存されている値が読み取れません。書き直して保存してください。"
                     "（現在、採点プロンプトには何も追加されていません）" if broken else ""),
            is_error=broken,
        )

    @router.post("/admin/settings/exchange-rates", response_class=HTMLResponse)
    async def save_rates(rates: str = Form(""), username: str = Depends(verify_admin)):
        parsed, errors = parse_rates_input(rates)

        if errors:
            # 入力はそのまま画面に返す。打ち直しをさせないため。
            settings = get_ai_settings() or {}
            return _render(
                load_rates(settings), rates, settings,
                message="保存していません。次の点を直してください。<br>・"
                        + "<br>・".join(esc(e) for e in errors),
                is_error=True,
            )

        today = date.today().isoformat()
        if parsed:
            _save_setting(SETTING_RATES,
                          json.dumps(parsed, ensure_ascii=False, sort_keys=True))
            _save_setting(SETTING_RATES_UPDATED, today)
            msg = (f"{len(parsed)}件のレートを保存しました。"
                   f"次の採点から反映されます（再デプロイ不要）。")
        else:
            # 空欄での保存＝未設定に戻す。更新日も消し、
            # 「設定されているのに更新日だけ残っている」状態を作らない。
            _save_setting(SETTING_RATES, "")
            _save_setting(SETTING_RATES_UPDATED, "")
            msg = ("未設定に戻しました。採点プロンプトには何も追加されません"
                   "（この機能を入れる前と同じ動作です）。")

        settings = get_ai_settings() or {}
        return _render(load_rates(settings), rates, settings, message=msg)

    return router
