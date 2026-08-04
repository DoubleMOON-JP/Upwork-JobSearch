# JobSearch

フリーランス向け求人サイトの求人を、利用者のプロフィールに照らして AI が 0〜100 点で採点し、
Apply / Maybe / Skip に分類する Web サービス。

- 本番 URL: https://jobsearch.doublemoon.biz/
- 運営: DoubleMoonTrading Co.（有限会社ダブルムーントレーディング／埼玉県八潮市）
- 対応サイト: Upwork（稼働中）／Freelancer.com・Guru.com・We Work Remotely・Remote OK（計画中）

---

## 設計上の前提（変更してはいけない方針）

| 方針 | 理由 |
|---|---|
| 求人サイトへ自動アクセスしない | 各サイトの利用規約でスクレイピングが禁止されている。システムは求人サイトに一切接続せず、利用者が手動でコピーした求人テキストのみを処理する |
| 1 オリジン＋パス分割で構成 | サイト別サブドメインにすると localStorage がオリジン単位で隔離され、プロフィールとライセンスがサイトごとに分断される。他社商標をホスト名に含むリスクも避ける |
| プロフィールをサーバーに保存しない | 個人情報の管理負担とリスクを回避。localStorage のみに保存する |
| Gemini API キーはサーバーが保持 | 利用者がキーを用意する必要をなくす |
| コスト防御は二段構え | レート制限（120 秒に 1 回）＋月間上限（プラン別） |

---

## URL 構成

| URL | 内容 | 配信ファイル |
|---|---|---|
| `/` | トップ（ハブ・対応サイト一覧） | `frontend/hub.html` |
| `/for/{site}` | サイト別 LP | `frontend/landing_{site}.html` |
| `/app` | アプリ本体 | `frontend/index.html` |
| `/privacy` | プライバシーポリシー | `frontend/privacy.html` |
| `/campaign` | SNS 拡散キャンペーン | `frontend/campaign.html` |
| `/mypage` | マイページ | `main.py` 内で生成 |
| `/admin` | 管理画面（Basic 認証） | `main.py` 内で生成 |
| `/admin/licenses` | ライセンス一覧（Basic 認証） | `main.py` 内で生成 |

**新しいジョブサイトを追加する場合**は `frontend/landing_{site}.html` を 1 枚置くだけでよい。
`/for/{site}` は汎用ルートなのでコード変更もデプロイ設定の変更も不要。
サイト名は英小文字・数字・ハイフン 32 文字以内（パストラバーサル対策）。

### 旧ドメインからのリダイレクト

`upwork.doublemoon.biz` へのアクセスは `main.py` のミドルウェアが新ドメインへ転送する。

- `/` → `/for/upwork`（301）
- その他のパス → 同一パス（GET/HEAD は 301、それ以外は 308）

**Render のカスタムドメインから `upwork.doublemoon.biz` を削除するとリダイレクトが止まる。**
移行が完全に落ち着くまでは残しておくこと。

---

## ファイル構成

```
リポジトリ直下
├── main.py               FastAPI アプリ本体・管理画面・各ルート
├── database.py           ライセンス／プロンプト／AI 設定／配布ファイル
├── db_redesign.py        DB マイグレーション＋広告欄＋使用量＋プラン変更
├── evaluate.py           サーバー側 Gemini 採点（POST /evaluate）
├── rate_limit.py         レート制限＋月間上限
├── payments.py           決済 Webhook（POST /webhook/{provider}）
├── plans.py              プラン定義の単一情報源
├── requirements.txt
├── runtime.txt           python-3.12.7 に固定
└── frontend/
    ├── hub.html              トップ（ハブ）
    ├── landing_upwork.html   Upwork 向け LP
    ├── index.html            アプリ本体
    ├── privacy.html
    └── campaign.html
```

---

## デプロイ環境

Render のダッシュボードで手動管理している（**Blueprint は使用していない**）。
`render.yaml` は実態を反映していなかったため削除済み。以下がその設定内容の記録。

| 項目 | 値 |
|---|---|
| サービス種別 | Web Service（Python） |
| ビルドコマンド | `pip install -r requirements.txt` |
| 起動コマンド | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| データベース | PostgreSQL（Basic プラン） |
| カスタムドメイン | `jobsearch.doublemoon.biz`（正）／`upwork.doublemoon.biz`（リダイレクト用に保持） |
| DNS | さくらインターネットで CNAME を Render に向ける |
| デプロイ | GitHub への push で自動 |

### 環境変数

| 変数名 | 内容 |
|---|---|
| `DATABASE_URL` | PostgreSQL 接続文字列（Render が自動設定） |
| `GEMINI_API_KEY` | Gemini API キー（必須） |
| `ADMIN_USER` / `ADMIN_PASSWORD` | 管理画面の Basic 認証 |
| `BASE_URL` | サービスの基準 URL。既定値 `https://jobsearch.doublemoon.biz` |
| `POLAR_WEBHOOK_SECRET` | 決済 Webhook の署名検証用 ※未設定 |
| `POLAR_PRODUCT_1MONTH` | Polar 上の商品 ID（Standard） ※未設定 |
| `POLAR_PRODUCT_1MONTH_PRO` | Polar 上の商品 ID（Pro） ※未設定 |

---

## プラン

| プラン ID | 名称 | 月額 | 月間採点回数 |
|---|---|---|---|
| `1month` | Standard | $9 | 100 回 |
| `1month_pro` | Pro | $15 | 300 回 |

回数は**全対応サイト共通**で消費する。1 ライセンスで全サイトを利用できる。

プランを増減する場合は `plans.py` と `payments.py` の `PRODUCT_TO_PLAN` を**必ず両方**更新すること。
`STRICT_PRODUCT_MAPPING = True` のため、対応表にない商品 ID は発行を拒否する（誤発行防止）。

---

## 開発時の注意点（過去に踏んだ地雷）

- **Python は 3.12 に固定必須。** 3.14 だと pydantic-core のビルドに失敗する。`runtime.txt` で固定済み。
- **管理画面の HTML は f-string。** 波括弧は `{{ }}`、バックスラッシュは `\\` でエスケープすること。
  JS 文字列内に `\n` と書くと本物の改行になり、`<script>` ブロック全体が構文エラーで死ぬ。
- **FastAPI のルート順序。** `/admin/prompts/new` は `/admin/prompts/{prompt_id}` より先に定義する。
- **採点プロンプトで出力言語を固定しない。** 利用者の「AI への要望」がプロンプト末尾で上書きする設計のため、
  テンプレート側に "in English" と書くと要望が効かなくなる。
- **Polar Webhook。** `subscription.created` のみ新規発行、`subscription_cycle` の `order.paid` は延長のみ、
  `subscription_update` 理由の注文は無視（日割り課金のため二重発行を防ぐ）。
- **CSV の URL 列は廃止済み。** 手動コピーでは URL が本文に含まれないため。

---

## 未対応事項

- Polar Webhook の登録と環境変数の設定
- `requirements.txt` の `polar-sdk` を有効化
- 購入ボタンの設置（`landing_upwork.html` の `data-checkout` 箇所）
- `/thanks`（購入完了）・`/pricing`（料金）ページ
- `mailer.py`（さくら SMTP でのライセンスキー送付）
- アプリ側の対象サイト切替 UI（2 サイト目の追加時）
