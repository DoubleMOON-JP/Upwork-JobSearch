JobSearch ソース一式  v3.22（2026-08-18）
====================================================================

■ このリリースの内容：main.py の分割（第1段階）

  main.py が2,573行まで大きくなり、画面を1つ直すたびに
  アプリ本体を触る状態になっていたため、画面を独立ファイルへ切り出す。

  今回はライセンス一覧を切り出した。あわせて、以後の画面を
  同じ形で切り出せるよう、共通部品を admin_ui.py に独立させた。

      main.py            2,573行 → 1,896行（-677行）
      admin_ui.py        新規 238行   画面共通の部品
      admin_licenses.py  新規 541行   ライセンス一覧＋操作API

  ※ 機能の変更は一切ない。画面のHTMLは1文字も変えていない。


■ 同梱ファイル

  main.py             FastAPIアプリ本体・管理トップ・紹介リンク管理ほか
  admin_ui.py         管理画面の共通部品          ← v3.22 で新規
  admin_licenses.py   ライセンス一覧＋操作API      ← v3.22 で新規
  settings_admin.py   AI設定（為替レート）の画面   ← v3.20 で新規
  evaluate.py         サーバー側 Gemini 採点
  sites.py            対応求人サイト定義
  requirements.txt    依存パッケージ

  ※ 同梱していない（この期間に変更していない）ファイル
     database.py / db_redesign.py / rate_limit.py / payments.py /
     plans.py / mailer.py / runtime.txt / frontend/ 配下


■ 画面を切り出すときの型（次の画面も同じ形で足せる）

  1. 画面モジュールを作り、module直下に router = APIRouter() を置く
  2. 認証は main.py から関数を受け取る（循環importを避けるため）

        _verify_any = None
        def build_xxx_router(verify_any):
            global _verify_any
            _verify_any = verify_any
            return router
        def verify(credentials = Depends(security)):
            return _verify_any(credentials)

  3. main.py の末尾に2行足す

        from admin_xxx import build_xxx_router
        app.include_router(build_xxx_router(verify_any))

  4. 共通で使うもの（UI_TEXT / esc / ui_text / plan_label_ui）は
     admin_ui.py から import する。admin_ui.py は何もimportしないため、
     どこから読んでも循環参照にならない。

  【重要】コードを移すときに再インデントしないこと。
  画面のHTMLは f-string で書かれており、インデントを変えると
  文字列の中身が変わってしまう。そのため router 方式にしている。


■ 残りの分割候補（未着手）

      /admin 管理トップ        548行
      /admin/referrals        262行 + API 90行程度
      /staff スタッフ画面      155行
      プロンプト編集画面        111行

  すべて切り出すと main.py は約900行になる見込み。


■ 検証済みの内容（v3.22）

  ・ルート46本が変更前後で完全に一致（消失・重複なし）
  ・未定義の名前なし（3ファイルとも静的解析で確認）
  ・移動した475行のうち変更は16行のみ。すべて @app.→@router. と
    Depends(verify_any)→Depends(verify) の置換。HTMLの変更は0行
  ・スタブ環境で import し、ルーター8本の組み立てと認証注入を確認


■ 注意（従来から変わらず）

  ・evaluate.py は全サイト共通。触ったらUpworkの回帰テストを行うこと。
  ・main.py と各画面モジュールのHTMLは f-string。波括弧は {{ }} にすること。
  ・polar-sdk は 2026-09-06 の更新課金テストが終わるまで上げないこと。
  ・リリースのたびに main.py の APP_VERSION を更新する（1行のみ）。
