# バックエンド リデザイン 統合手順

リデザイン後のサーバー一式（新規・変更モジュール）と、既存 `main.py` / `database.py` への組み込み手順です。
既存の大きな `main.py`（管理画面HTML等）は壊さず、**足す・外すの最小差分**で移行します。

## 追加するファイル（この一式）

| ファイル | 役割 |
|---|---|
| `plans.py` | プラン定義（付与月数・月間上限・レート秒数）の単一情報源 |
| `db_redesign.py` | DBマイグレーション＋サブスク紐づけ・使用量の関数 |
| `rate_limit.py` | 2分制限＋月間上限（コスト防御） |
| `evaluate.py` | サーバー側Gemini採点（貼付テキスト解析＋採点）/ `POST /evaluate` |
| `payments.py` | Polar Webhook（＋2社目を足せるアダプタ層）/ `POST /webhook/{provider}` |

`database.py` は**そのまま**使います（追加分は `db_redesign.py` に分離）。

## 環境変数（Render）

```
GEMINI_API_KEY        = 自分のGeminiキー（サーバー保持）
POLAR_WEBHOOK_SECRET  = Polarダッシュボードで発行するWebhook署名シークレット
POLAR_PRODUCT_1MONTH  = Polarで作成した商品ID（1ヶ月・唯一のプラン）
（既存）ADMIN_USER / ADMIN_PASSWORD / BASE_URL はそのまま
```

## `requirements.txt` に追記

```
polar-sdk
```
（`httpx` は既存の 0.27.2 を流用）

## `main.py` の変更（差分だけ）

### 1) 起動時に migrate を呼ぶ（init_db の直後）

```python
from database import init_db
from db_redesign import migrate      # ← 追加

app = FastAPI(title="Upwork JobSearch API", version="3.0.0")
init_db()
migrate()                            # ← 追加：subscription_id/provider 列・usage表を用意
```

### 2) ルーターを登録（app 定義の後どこでも）

```python
from evaluate import router as evaluate_router   # ← 追加
from payments import router as payments_router   # ← 追加
app.include_router(evaluate_router)              # POST /evaluate
app.include_router(payments_router)              # POST /webhook/{provider}
```

### 3) CORS を自社フロント向けに（任意で厳格化）

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://upwork.doublemoon.biz"],  # ["*"] から自社ドメインへ
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

### 4) 外すエンドポイント（リデザインで不要）

- `@app.get("/download/extension")` … Chrome拡張配布。**削除**
- 拡張向けの selectors 配信は残っていても無害だが、`get_license_with_config` の
  返却から `selectors` / `exclude_skills` / `extension` バージョンを外すと綺麗
  （＝ `database.py` の `get_license_with_config` を軽量化。必須ではない）

### 5) `/license/validate` の位置づけ

Webログインの認証にそのまま流用可。ただし返却の重い config（プロンプト全文等）は
Web版では不要なら削ってよい。**採点は `/evaluate` が担う**ため、
ログイン時は「有効/無効・残日数・プラン」だけ返す軽量版でも十分。

## 動作フロー（リデザイン後）

```
[Webフロント] --license_key + pasted_text + profile--> POST /evaluate
   → validate_license → rate_limit(2分/月間上限) → Gemini採点 → jobs[] を返す
[Polar] --Webhook--> POST /webhook/polar
   → 署名検証 → create_license/extend_license/deactivate（自動）
```

## 動作確認の順番

1. Renderに環境変数を設定してデプロイ（`migrate()` が列・表を自動作成）
2. `POST /evaluate` に実際の貼付テキストを投げ、jobs[] が返るか確認
3. 2分以内に再送し 429（rate_limited）になるか確認
4. Polar sandbox（`sandbox-api.polar.sh`）でテスト購入 → `/webhook/polar` 着信 →
   licenses にキーが発行され provider/subscription_id が入るか確認
5. sandbox で解約 → ライセンスが無効化（または失効まで有効）になるか確認

## 補足

- `selectors` / `excludes` テーブルは削除せず放置（安全）。使わないだけ。
- プランの月間上限・レート秒数は `plans.py` の1箇所で調整可能。
- 2社目のMoRは `payments.py` に `PaddleAdapter` を実装して `ADAPTERS` に足すだけ。
