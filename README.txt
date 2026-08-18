JobSearch ソース一式  v3.23（2026-08-18）
====================================================================

■ このリリースの内容：main.py の分割（第2段階）

  スタッフ用画面と紹介リンク管理を独立モジュールへ切り出した。
  互いに依存しないため、片方に問題が出てももう片方に影響しない。

      main.py             1,895行 → 1,341行（-554行）
      staff_console.py    新規 206行   /staff（英語）
      admin_referrals.py  新規 470行   紹介リンク管理＋コード登録・CSV出力

  ※ 機能の変更は一切ない。画面のHTMLは1文字も変えていない。

  【分割の経過】
      v3.21  main.py 2,573行（分割前）
      v3.22  1,896行  ← ライセンス一覧＋共通部品を切り出し
      v3.23  1,341行  ← スタッフ画面＋紹介リンク管理を切り出し


■ 同梱ファイル

  main.py             ルーティング・画面配信・管理トップ・プロンプト管理・認証
  admin_ui.py         管理画面の共通部品（何もimportしない）
  admin_licenses.py   ライセンス一覧＋操作API           ← v3.22
  staff_console.py    スタッフ用画面 /staff             ← v3.23 新規
  admin_referrals.py  紹介リンク管理                    ← v3.23 新規
  settings_admin.py   AI設定（為替レート）              ← v3.20
  evaluate.py         サーバー側 Gemini 採点
  sites.py            対応求人サイト定義
  requirements.txt    依存パッケージ

  ※ 同梱していない（この期間に変更していない）ファイル
     database.py / db_redesign.py / rate_limit.py / payments.py /
     plans.py / mailer.py / runtime.txt / frontend/ 配下


■ 画面を切り出すときの型（次の画面も同じ形で足せる）

  1. モジュール直下に router = APIRouter() を置く
  2. 認証は main.py から関数を受け取る

        _verify_any = None
        def build_xxx_router(verify_any):
            global _verify_any
            _verify_any = verify_any
            return router
        def verify(credentials = Depends(security)):
            return _verify_any(credentials)

  3. main.py の末尾に2行足す
  4. 共通で使うもの（UI_TEXT / esc / ui_text / plan_label_ui）は
     admin_ui.py から import する

  【重要】コードを移すときに再インデントしないこと。
  画面のHTMLは f-string で書かれており、インデントを変えると
  文字列の中身が変わってしまう。そのため router 方式にしている。

  【環境変数を使う画面】main.py を import すると循環参照になるため、
  os.environ から直接読む。既定値を main.py と揃えること
  （admin_referrals.py の BASE_URL がその例）。


■ 残りの分割候補（未着手）

      /admin 管理トップ        548行
      プロンプト編集画面        111行 ＋ プロンプトAPI 約50行

  すべて切り出すと main.py は約630行になる見込み。
  ただし /admin は管理トップの画面内に担当者マスタ・広告欄・配布ファイルの
  UIを含み、それぞれのAPIも main.py にある。切り出すなら
  admin_home.py としてAPIごと移すのが自然。


■ 検証済みの内容（v3.23）

  ・ルート46本が変更前後で完全に一致（消失・重複なし）
  ・未定義の名前なし（6ファイルとも静的解析で確認）
  ・移動した565行のうち変更は12行のみ。すべて @app.→@router. と
    Depends(verify_any)→Depends(verify) の置換。HTMLの変更は0行
  ・スタブ環境で全モジュールを import し、ルーターの組み立てと
    認証注入、BASE_URL の既定値を確認


■ 注意（従来から変わらず）

  ・evaluate.py は全サイト共通。触ったらUpworkの回帰テストを行うこと。
  ・HTMLは f-string。波括弧は {{ }} にすること。
  ・polar-sdk は 2026-09-06 の更新課金テストが終わるまで上げないこと。
  ・リリースのたびに main.py の APP_VERSION を更新する（1行のみ）。
