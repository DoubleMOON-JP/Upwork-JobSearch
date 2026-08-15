# sites.py
# ─────────────────────────────────────────────────────────────
# 対応求人サイト定義の単一情報源。
#
# 新しい求人サイトを追加する手順（4つ）:
#   1. 下の SITES に1エントリ追加する
#   2. frontend/landing_{site}.html （そのサイト向けLP）を1枚置く
#   3. 管理画面でそのサイト用のプロンプトを1本登録し、有効化する
#   4. hub.html の対応サイト一覧にリンクを1行足す
#
# 準備が整うまでは "enabled": False にしておくこと。
# True にすると /for/{site} と /app/{site} が即座に公開され、
# 紹介リンクの着地先プルダウンにも現れる。LPが無い状態で選ばれると、
# リンクを踏んだ人が404に着地してしまう。
#
# サイト追加時は抽出ロジックやプロンプト設計の検討が必ず発生するため、
# 設定はDBではなくコード側に置いている（管理画面の管理対象を増やさない）。
# 一方、プロンプト本文は調整頻度が高いため DB + 管理画面で管理する。
# ─────────────────────────────────────────────────────────────

SITES = {
    "upwork": {
        # 画面に出す表示名
        "label": "Upwork",
        # /app/{site} の STEP1 見出し
        "paste_heading": "Paste jobs from Upwork",
        # 貼り付け欄のプレースホルダー（コピー手順の案内）
        # 「読み込みを待つ」を必ず入れること。Upworkは求人を数件ずつ遅延読み込みする
        # ため、開いた直後に全選択すると2〜3件しかコピーされない。しかもエラーは
        # 出ず「求人が少なかった」ようにしか見えないので、利用者は原因に気づけない。
        "paste_placeholder": (
            "Go to Upwork's job search page (not Saved Jobs or Invites) and sort by "
            "\"Most Recent\". Scroll to the bottom and wait until every listing has "
            "loaded — Upwork loads them a few at a time, and anything not yet on "
            "screen will not be copied. Then press Ctrl/⌘+A to select the page and "
            "Ctrl/⌘+C to copy. Paste it here — multiple pages are fine, noise is ignored."
        ),
        # 貼り付け欄の下に出す補足
        "paste_tip": (
            "Tip: scroll to the bottom first so every listing loads, and use the "
            "job search page — not Saved Jobs or Invites."
        ),
        # CSVダウンロード時のファイル名
        "csv_filename": "upwork_result.csv",
        # 採点プロンプト内で「どのサイトから貼られたか」をAIに伝える文言
        "prompt_source_label": "Upwork",
        # プロンプト内で無視させたい定型ノイズ（サイト固有）
        "prompt_noise_hint": (
            "headers, footers, \"Skip skills\", \"more about\", profile/nav text, \"© Upwork\""
        ),
        # 公開中かどうか。False にすると /app/{site} は404になる（準備中サイト用）
        "enabled": True,
    },

    "freelancer": {
        "label": "Freelancer.com",
        "paste_heading": "Paste jobs from Freelancer.com",
        # Upworkと違い遅延読み込みではなく、ページ送り（1ページ20件）。
        # 複数ページをまとめて貼らせない理由は3つ:
        #   ・応答時間（Gemini呼び出しのタイムアウトは90秒）
        #   ・ページを跨ぐとヘッダー・フッターが繰り返し混入する
        #   ・出力トークン上限。JSONが途中で切れるとパースに失敗し、
        #     全件失われる（利用者はやり直しになる）
        "paste_placeholder": (
            "Go to Freelancer.com and open Browse › Projects, then sort by "
            "\"Latest\". Press Ctrl/⌘+A to select the page and Ctrl/⌘+C to copy. "
            "Paste it here. Score one page at a time — when you are done, go to "
            "the next page and repeat. Noise is ignored."
        ),
        "paste_tip": (
            "Tip: one page at a time gives the most reliable results. "
            "Use Projects — not Contests or Freelancers."
        ),
        "csv_filename": "freelancer_result.csv",
        "prompt_source_label": "Freelancer.com",
        # フィルター欄には Urgent / Recruiter / Python など、実際の案件にも
        # 現れる語が並ぶ。前処理で切り落とすが、取りこぼしに備えて
        # プロンプト側にも構造ルールを持たせる（下の trim と二重化）。
        "prompt_noise_hint": (
            "the left filter sidebar (Project type, Fixed price, Skills, "
            "Listing type, Project location, Languages and the words listed "
            "under them), headers, footers, page numbers, the Messages panel, "
            "\"Registered Users\", \"Total Jobs Posted\", \"Copyright ©\""
        ),
        # ── 貼付テキストの切り出し ──────────────────────────
        # 一覧本体の前後を機械的に落とす。フィルター欄・ヘッダー・
        # VAT警告・フッターがまとめて消え、トークン数も減る。
        # 目印が見つからなければ何もしない（evaluate.py 側の実装）。
        # サイトが文言を変えても、最悪は現状（プロンプト任せ）に戻るだけ。
        "trim_start_after": "Top results",
        # 終了の目印に "Messages"（チャット欄）を使わないこと。
        # 案件の説明文に "messages" が現れることがあり、そこで切ると
        # 以降の求人が丸ごと失われる。"Help & Support" はフッター専用で、
        # 求人説明に現れる可能性が極めて低い。
        # チャット欄の数行（Messages/Search/Chats/Requests）は残るが、
        # 求人を取りこぼすより無害と判断している。
        "trim_end_before": "Help & Support",
        "enabled": False,   # LPとプロンプトが揃うまで非公開
    },
}

# サイト未指定時のフォールバック先。
DEFAULT_SITE = "upwork"


def is_valid_site(site: str) -> bool:
    """公開中のサイトかどうか。未定義・準備中は False。"""
    entry = SITES.get(site)
    return bool(entry and entry.get("enabled"))


def get_site(site: str) -> dict:
    """サイト定義を取得。未知のサイトは DEFAULT_SITE にフォールバック。"""
    return SITES.get(site) or SITES[DEFAULT_SITE]


def site_label(site: str) -> str:
    """管理画面などの表示用。未知の値はそのまま返す（過去データが消えないように）。"""
    entry = SITES.get(site)
    return entry["label"] if entry else (site or "(未割当)")


def enabled_sites() -> list:
    """公開中サイトの id 一覧。管理画面のプルダウン等で使う。"""
    return [k for k, v in SITES.items() if v.get("enabled")]
