# MOONpicker

フリーランス向け求人サイトの求人を、利用者のプロフィールに照らして AI が 0〜100 点で採点し、
Apply / Maybe / Skip に分類する Web サービス。

- 本番 URL: https://moonpicker.com/
- 運営: DoubleMoonTrading Co.（有限会社ダブルムーントレーディング／埼玉県八潮市）
- 対応サイト: Upwork・Freelancer.com（いずれも稼働中）／Guru.com・We Work Remotely・Remote OK（計画中）
- 現行版: **v3.34.0**

このファイルがリポジトリ唯一の README。以前は `README.txt`（v3.32 リリースノート）と
`README_v3.33.txt`（v3.33 リリースノート）が併存していたが、2026-09-15 に本ファイルへ統合した。
個々のリリースの経緯は `claude/引継ぎメモ_*.md` に残っている。

---

## 版数とデプロイの確認

```
GET https://moonpicker.com/health
{"service":"MOONpicker API","version":"3.34.0","commit":"bbb51d4","status":"running"}
```

- `APP_VERSION`（`main.py` 冒頭）は**宣言した版数**。リリースのたびにこの1行だけを更新する
- `commit` は Render が渡す環境変数から取る**実際に動いているコード**。
  APP_VERSION の更新を忘れても必ず変わるので、**デプロイの判定はこちらで行う**

> **版数の更新漏れは過去に2回起きている。** v3.9〜v3.20 は 3.8.0 のまま、
> v3.29 は main.py を同梱しなかったため 3.28.0 のままだった。
> いずれも「欠番」ではなく「表示の欠落」。

---

## 設計上の前提（変更してはいけない方針）

| 方針 | 理由 |
|---|---|
| 求人サイトへ自動アクセスしない | 各サイトの利用規約でスクレイピングが禁止されている。システムは求人サイトに一切接続せず、利用者が手動でコピーした求人テキストのみを処理する |
| 1 オリジン＋パス分割で構成 | サイト別サブドメインにすると localStorage がオリジン単位で隔離され、プロフィールとライセンスがサイトごとに分断される。他社商標をホスト名に含むリスクも避ける |
| プロフィールをサーバーに保存しない | 個人情報の管理負担とリスクを回避。localStorage のみに保存する |
| Gemini API キーはサーバーが保持 | 利用者がキーを用意する必要をなくす |
| コスト防御は二段構え | レート制限（120 秒に 1 回）＋月間上限（プラン別） |
| 外部サービスを増やさない | 解析タグ・Pixel の類は入れない。計測は自社の `lp_visits` で行う |
| `db_redesign.py` の変更は追加のみ | 既存の動作を壊さないため。削除ゼロを守る |

---

## URL 構成

| URL | 内容 | 実装 |
|---|---|---|
| `/` | トップ（ハブ・対応サイト一覧） | `frontend/hub.html` |
| `/r/{code}` | 紹介リンク。訪問を記録して `/for/{site}?ref={code}` へ 302 | `main.py` |
| `/for/{site}` | サイト別 LP。**到達を `lp_visits` に記録する** | `frontend/landing_{site}.html` |
| `/app` | ハブへ 301（旧URL互換） | `main.py` |
| `/app/{site}` | サイト別アプリ本体（HTML 1枚を共用し `sites.py` の文言を差し込む） | `frontend/index.html` |
| `/privacy` | プライバシーポリシー | `frontend/privacy.html` |
| `/campaign` | SNS 拡散キャンペーン | `frontend/campaign.html` |
| `/thanks` | 決済完了ページ（Polar の Success URL の遷移先） | `frontend/thanks.html` |
| `/static/*` | LP の紹介動画・ポスター画像 | `frontend/static/` |
| `/health` | 稼働確認・デプロイ判定 | `main.py` |
| `/ping` | 稼働確認（サーバー時刻） | `main.py` |
| `/promo` | 広告欄。未設定・OFF なら表示されない | `main.py` |
| `/evaluate` | 採点 API（POST） | `evaluate.py` |
| `/webhook/{provider}` | 決済 Webhook（POST・例 `/webhook/polar`） | `payments.py` |
| `/license/validate` | ライセンス認証＋設定一括取得（POST） | `main.py` |
| `/license/by-checkout/{id}` | 購入完了ページ用のキー引き当て | `main.py` |
| `/version/{component}` | バージョン確認 API | `main.py` |
| `/download/excel` | DB に保存したファイルの配信 | `main.py` |
| `/mypage` | 廃止。ハブへ 301 | `main.py` |

### 管理画面（Basic 認証）

| URL | 権限 | 実装 |
|---|---|---|
| `/admin` | 管理者 | `admin_home.py` |
| `/admin/licenses` | 管理者＋スタッフ | `admin_licenses.py` |
| `/admin/prompts` | 管理者 | `admin_prompts.py` |
| `/admin/referrals` | 管理者＋スタッフ | `admin_referrals.py` |
| `/admin/lp-visits` | 管理者 | `admin_lp_visits.py` |
| 為替レート設定 | 管理者 | `settings_admin.py` |
| `/staff` | 管理者＋スタッフ | `staff_console.py` |

認証は Basic 認証のみ。フォーム認証・セッションは導入していない（既存の全ルートを
書き換えずに済むため）。その代わり**ログアウトはできない**。
管理者は環境変数、スタッフは `staff_members` テーブルと照合する。

スタッフ資格で管理者専用画面に来た場合は **401 ではなく 403** を返す。
401 だとブラウザが再度パスワードを尋ね、何度入れ直しても入れない状態になるため。

**新しいジョブサイトを追加する場合**は `frontend/landing_{site}.html` を 1 枚置くだけでよい。
`/for/{site}` は汎用ルートなのでコード変更もデプロイ設定の変更も不要。
サイト名は英小文字・数字・ハイフン 32 文字以内（パストラバーサル対策）。

---

## ファイル構成

```
リポジトリ直下
├── main.py               FastAPI アプリ本体・基本ルート・認証・ルーター登録
├── database.py           ライセンス／プロンプト／AI 設定／配布ファイル
├── db_redesign.py        DB マイグレーション＋広告欄＋紹介リンク＋LP到達＋スタッフ照合
├── evaluate.py           サーバー側 Gemini 採点（POST /evaluate）
├── rate_limit.py         レート制限＋月間上限（暦月集計）
├── payments.py           決済 Webhook（POST /webhook/{provider}）
├── plans.py              プラン定義の単一情報源
├── sites.py              対応求人サイト定義の単一情報源
├── mailer.py             さくら SMTP でのライセンスキー送付
├── admin_ui.py           管理画面の共通部品（UI_TEXT・esc）
├── admin_home.py         管理トップ
├── admin_licenses.py     ライセンス一覧
├── admin_prompts.py      プロンプト管理
├── admin_referrals.py    紹介リンク管理
├── admin_lp_visits.py    LP到達の集計（広告の効果測定）
├── settings_admin.py     為替レート設定
├── staff_console.py      スタッフ用画面
├── requirements.txt
├── runtime.txt           python-3.12.7 に固定
└── frontend/
    ├── hub.html                トップ（ハブ）
    ├── landing_upwork.html     Upwork 向け LP
    ├── landing_freelancer.html Freelancer.com 向け LP
    ├── index.html              アプリ本体
    ├── privacy.html
    ├── campaign.html
    ├── thanks.html
    └── static/
        ├── moonpicker_demo.mp4         24.03秒 / 1280×720 / 無音
        └── moonpicker_demo_poster.jpg  1280×720。og:image を兼ねる
```

### 画面モジュールの型（今後の追加はこの形で）

1. モジュール直下に `router = APIRouter()` を置く
2. 認証は `main.py` から関数で受け取る
   ```python
   _verify = None
   def build_xxx_router(verify_fn):
       global _verify
       _verify = verify_fn
       return router
   def verify(credentials = Depends(security)):
       return _verify(credentials)
   ```
3. `main.py` の末尾に2行足す（`verify_admin` / `verify_any` の定義より**後**である必要がある）
4. 共通部品は `admin_ui.py` から import する

- **コードを移すときに再インデントしないこと。** HTML は f-string のため、
  インデントを変えると文字列の中身が変わる。そのため router 方式にしている
- **ルート定義の順序。** `/admin/prompts/new` は `/admin/prompts/{prompt_id}` より先に。
  モジュール内の並び順がそのまま登録順になる
- **環境変数を使う画面は `os.environ` から直接読む**（main.py を import すると循環参照）。
  既定値を main.py と揃えること。`admin_referrals.py` の `BASE_URL` がその例
- **渡す認証関数を間違えない。** 管理者専用は `verify_admin`、スタッフも通すなら `verify_any`

---

## デプロイ環境

Render のダッシュボードで手動管理している（**Blueprint は使用していない**）。
`render.yaml` は実態を反映していなかったため削除済み。

| 項目 | 値 |
|---|---|
| サービス種別 | Web Service（Python） |
| ビルドコマンド | `pip install -r requirements.txt` |
| 起動コマンド | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| データベース | PostgreSQL（Basic プラン） |
| カスタムドメイン | `moonpicker.com` |
| DNS | さくらインターネット |
| デプロイ | GitHub への push で自動 |

`/static/` 配下（動画・ポスター画像）も GitHub の `frontend/static/` で管理する。
Render Disk や外部ストレージは使わない（外部サービスを増やさない方針）。
**差し替えは同じファイル名で上書きする。** HTML 側でファイル名を固定しているため、
HTML は触らずに済む。ポスターは `og:image` を兼ねるので、
差し替え後は Facebook の Sharing Debugger で再取得させること。

### 環境変数

| 変数名 | 内容 |
|---|---|
| `DATABASE_URL` | PostgreSQL 接続文字列（Render が自動設定） |
| `GEMINI_API_KEY` | Gemini API キー（必須） |
| `ADMIN_USER` / `ADMIN_PASSWORD` | 管理画面の Basic 認証。既定値は `admin` / `changeme` |
| `BASE_URL` | サービスの基準 URL。**コード上の既定値は旧ドメインのままなので、Render 側で必ず設定すること** |
| `RENDER_GIT_COMMIT` | Render が自動設定。`/health` の `commit` に使う |

決済（Polar）とメール（さくら SMTP）の変数は `payments.py` / `mailer.py` を参照。
**`SMTP_USER` がそのまま送信元アドレスになる**（`MAIL_FROM` のような変数は無い）。

---

## プラン

| プラン ID | 名称 | 月額 | 月間採点回数 |
|---|---|---|---|
| `1month` | Basic | $9 | 100 回 |
| `1month_pro` | Pro | $15 | 300 回 |
| `trial` | トライアル | $0 | 10 回 |

回数は**全対応サイト共通**で消費する。1 ライセンスで全サイトを利用できる。

プランを増減する場合は `plans.py` と `payments.py` の `PRODUCT_TO_PLAN` を**必ず両方**更新すること。
`STRICT_PRODUCT_MAPPING = True` のため、対応表にない商品 ID は発行を拒否する（誤発行防止）。

---

## 無料トライアルの設計（v3.32・v3.33）

### 役割の分担

- **期間は Polar のチェックアウトリンク側**で決まる（商品には設定しない。
  商品に設定すると商品 ID が変わり `PRODUCT_TO_PLAN` の突合が壊れる）
- **回数（10 回）はコード側**で決まる（`plans.py` の `trial` プラン）

**トライアルを配るかどうかはリンクを作るかどうかで決まる。**
コードが入っていてもリンクを作らなければ何も起きない。

### 有効期限（v3.32）

`create_license()` は `expires_at` 引数を持つ。既定は `None`。

- `None` … 従来どおり `date.today() + plan_months(plan)` か月
- 日付 … その日付をそのまま有効期限にする（**当日を含む**）
- 過去日 … 今日まで引き上げる

`payments.py` は `status == "trialing"` のとき `trial_end`
（無ければ `current_period_end`）を拾って渡す。

> **この修正がない状態では、1日トライアルで +31 日のライセンスが出ていた。**
> さらに転換時の `extend_license()` がその 9/25 を基点に延ばし、
> $9 の支払いで約2か月ぶんのアクセスが発生していた（2026-08-25〜27 実測）。

`extend_license()` は触っていない。`base = max(current, today)` は元から正しく、
入口が直れば結果も自動的に正しくなるため。

### プランの往復（v3.33）

- **発行時**：`status="trialing"` なら plan を `trial` にして発行
- **転換時**：`extend_license` の直後、plan が `trial` のときだけ
  `update_license_plan()` で本来のプランへ戻す

**`apply_plan_change()` ではなく `update_license_plan()` を使う。**
前者は使い残しの繰り越しを行うため、転換月の上限が 10 + 100 = 110 回になる。
$9 を払った月は素直に 100 回であるべき。

### ★ `_notify_plan_change()` を止めている理由（重要）

`AUTO_APPLY_PLAN_CHANGE = True` のため、`subscription.updated` が届くと
「商品 ID から解決したプラン」と「ライセンスのプラン」を比べて自動で書き換える処理が走る。

トライアル中のライセンスは `plan="trial"`、商品は Basic のままなので、
ここでは**必ず「trial → 1month」というズレとして見える**。
`subscription.updated` は解約予約やカード情報の更新でも飛ぶため、
放置するとトライアル中に上限が 100 回へ増える。

**エラーは出ず、ログに `PLAN CHANGE APPLIED` と残るだけなので気づけない。**
そのためトライアル中は自動変更を止め、本来のプランへ戻すのは RENEW（本課金の入金）だけの仕事とした。

`AUTO_APPLY_PLAN_CHANGE` 自体は `True` のまま。Basic ⇔ Pro の変更では正しく動いている。

なお、転換の約3秒前に `subscription.updated` が飛ぶことを実測している。
この保護がないと、そこで意図しないプラン変更が起きる。

### 承知のうえで残している挙動

- **月をまたぐと最大 20 回になる。** `rate_limit.py` の集計単位は暦月。
  9/10 開始なら 9月分10回＋10月分10回。厳密に 10 回にするには集計方法の変更が必要だが、
  AI コストで約 $0.15 のため許容する（2026-08-30 Koji 判断）。
  **1か月トライアルは必ず月をまたぐ**ので、LP には「10 evaluations per month」と表記して実装と揃えている
- **メールに課金開始日を書かない。** 保持しているのは `trial_end` を日付に丸めた値で、
  Polar が実際に課金する瞬間とは1日ずれ得る。正確な日付は Polar の確認メールに任せる
- **管理画面のプラン選択肢に「トライアル（10回）」が現れる。** 手動でトライアルキーを配れる利点がある一方、
  有料顧客のプランを誤って `trial` に変えると上限が 10 回に落ちる
- **同一メールでの複数トライアルは防いでいない。** 重複チェックは `subscription_id` のみ。
  Polar のトライアルはカード登録が必須のため、繰り返しの手間に見合わないと判断した

---

## 決済 Webhook（Polar）の約束事

- `subscription.created` のみ新規発行
- `order.paid` は `billing_reason == "subscription_cycle"` のときだけ延長
- `billing_reason == "subscription_create"` の注文は無視（$0 のトライアル請求）
- `subscription_update` 理由の注文は無視（日割り課金による二重発行を防ぐ）
- `subscription.active` / `subscription.cycled` は**購読しなくてよい**（`order.paid` で足りる）

### ★ 触ってはいけない設定

- **Polar Legacy 署名を維持すること。** 新しい Webhook エンドポイントを作らず、
  **必ず既存のエンドポイントを編集する**（Polar は 2026-09-08 以降の新規エンドポイントを
  Standard Webhooks に切り替えた）
- **「Reset Secret」に触らないこと。** Standard Webhooks へ切り替わり、ライセンス発行が壊れる

### 診断

`Polar → Settings → Webhooks → Deliveries` が最速の切り分け手段。
「イベントが届いていない」のか「届いたが処理されていない」のかをここで判定できる。

---

## 計測（広告・紹介リンク）

媒体のクリック数は媒体ごとに定義が違う（誤タップの扱い・ボット除外の方針）ため、
**自社側で数える。** 媒体を変えても同じ物差しで比較できる。

| 段階 | 見る場所 |
|---|---|
| LP 到達 | `/admin/lp-visits`（`lp_visits` 表） |
| CTA クリック相当 | Polar → Webhooks → Deliveries の `checkout.created` 件数 |
| トライアル申込 | `/admin/licenses` |
| 有料転換 | Polar・管理画面 |

**`referral_visits` と `lp_visits` は意図的に別の表。** 前者は `/r/{code}` を踏んだ回数、
後者は `/for/{site}` が表示された回数。定義が違うので統合しない。

### ★ ボット判定の限界

`is_bot` は User-Agent による簡易判定で、**Meta のクローラを捕まえられない。**
広告の下書きを保存・プレビュー・審査するたびにLPが数十回叩かれ、それが
「人間の紹介リンク経由」として記録される（2026-09-14〜15 に実測）。

**見分け方は時刻の密度。** 5 秒以内に 3 件以上まとまっていたら機械。
1 回の解析で「与えられた URL（ref 付き）」と「canonical / og:url（ref 無し）」の
2 件が記録されるため、塊の中で ref 付き・無しがほぼ 1:1 になるのも特徴。

`visited_at` は **UTC**。広告管理画面の表示（Asia/Tokyo）とは 9 時間ずれる。

### トライアル枠の条件表示（v3.34）

両 LP のトライアル枠は `id="trial-band" hidden` で既定は非表示。
紹介コードを持って来た訪問者にだけ JS が `hidden` を外す。

- 一般の訪問者 … $9 / $15 のみが見える
- `/r/{code}` から来た人 … トライアル枠が現れる

購入ボタン（`a[data-checkout]`）には `reference_id` と `utm_source` が付与され、
Polar がチェックアウトのメタデータに格納して購入成立時に引き継ぐ。
**通常プランの購入でも紹介コードは引き継がれる。**

保存は `localStorage`（キー `js_ref`・60日）。別のブラウザや端末で購入されると
追跡できないため、**計測は「取りこぼしのある下限値」として扱うこと。**

---

## 開発時の注意点（過去に踏んだ地雷）

- **Python は 3.12 に固定必須。** 3.14 だと pydantic-core のビルドに失敗する。`runtime.txt` で固定済み
- **管理画面の HTML は f-string。** 波括弧は `{{ }}`、バックスラッシュは `\\` でエスケープする。
  JS 文字列内に `\n` と書くと本物の改行になり、`<script>` ブロック全体が構文エラーで死ぬ
- **FastAPI のルート順序。** 静的なパスを動的なパスより先に定義する
- **採点プロンプトで出力言語を固定しない。** 利用者の「AI への要望」がプロンプト末尾で上書きする設計のため、
  テンプレート側に "in English" と書くと要望が効かなくなる
- **CSV の URL 列は廃止済み。** 手動コピーでは URL が本文に含まれないため
- **メールの疎通確認は本番同等の本文で行う。** 件名「送信テスト」本文「テストデス」のような
  空メールは迷惑メール判定される。実際のライセンスメール（英語・長文）は受信トレイに着弾した
- **`fastapi==0.115.0` の Starlette は `FileResponse` の Range 対応が入る前の版と思われる。**
  動画のシーク時に取り直しが起きる可能性があるが実害は小さい

---

## 既知のずれ・未対応

コードは動いているが、記述と実態が食い違っている箇所。

- **CORS の `allow_origins` が旧ドメインのまま**（`jobsearch.doublemoon.biz` /
  `upwork.doublemoon.biz`）。`moonpicker.com` が入っていない。
  フロントは同一オリジンから配信しているため実害は出ていないが、設定としては誤り
- **`BASE_URL` のコード上の既定値が旧ドメイン。** Render 側で設定済みのため実害なし
- **`LEGACY_HOSTS` に `upwork.doublemoon.biz` が残っている。** 旧サブドメインは
  2026-09-07 に撤去済みのため、このミドルウェアは現在は素通り
- **`main.py` の冒頭 docstring が「v3.8」のまま**
- **`main.py` のコメントに「切り出しの型は README.txt を参照」とある。**
  README.txt は本ファイルへ統合したため、参照先を README.md に直すこと
- **`lp_visits` に `user_agent` 列が無い。** あれば `facebookexternalhit` を確実に弾ける
- **`subscription.past_due` を購読していない。** トライアル終了時のカード失敗に気づくのが遅れる。
  **2026-10-07 以降に初めて飛び得る**
- **`order.refunded` が金額を見ていない。** 一部返金でもライセンス全体が無効化される
- **`payments.py` のコメントに誤り**（動作には影響しない）
  - 112 / 209 行目 … 「自動反映しない」とあるが `AUTO_APPLY_PLAN_CHANGE = True` なので自動反映する
  - 408 行目 … 「checkout_id は order.paid にも含まれる」とあるが、更新時の `order.paid` では
    `checkout_id` が null だった（2026-08-26 実測）
- **アプリ側の対象サイト切替 UI が無い。** 現在はハブ（`/`）から選び直す

---

## 履歴の所在

- 各リリースの経緯・実測ログ … `claude/引継ぎメモ_*.md`
- 広告・宣伝方針 … `claude/方針転換_マイクロインフルエンサー_20260915.md`
- 管理表 … `対応予定一覧_*.xlsx` / `広告準備タスク_*.xlsx` / `システム仕様書_*.xlsx`
