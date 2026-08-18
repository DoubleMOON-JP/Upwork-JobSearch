JobSearch ソース一式  v3.24（2026-08-18）
====================================================================

■ このリリースの内容：main.py の分割（第3段階・完了）

  管理トップとプロンプト管理を切り出し、分割を完了した。

      main.py            1,340行 → 524行（-816行）
      admin_home.py      新規 705行  /admin ＋ 広告欄・担当者マスタ・配布ファイルAPI
      admin_prompts.py   新規 224行  プロンプト編集画面＋作成/更新/有効化API

  あわせて、使わなくなった import を40件整理した。

  【分割の経過】
      v3.21  2,573行（分割前）
      v3.22  1,896行  ライセンス一覧＋共通部品
      v3.23  1,341行  スタッフ画面＋紹介リンク管理
      v3.24    524行  管理トップ＋プロンプト管理   ← 当初比 80%削減

  ※ 機能の変更は一切ない。画面のHTMLは1文字も変えていない。


■ main.py に残っているもの（16ルート）

  すべて利用者向け、または全体に関わるもの。管理画面は1つも残っていない。

      /            /r/{code}      /for/{site}    /app
      /app/{site}  /privacy       /campaign      /thanks
      /health      /promo         /ping          /mypage
      /license/validate           /license/by-checkout/{checkout_id}
      /version/{component}        /download/excel

  ほかに：起動処理、CORS、旧ドメインのリダイレクト、
  認証と権限判定（verify_admin / verify_any）、各モジュールの登録。


■ ファイル構成

  main.py             ルーティング・画面配信・認証・各モジュールの登録   524行
  admin_ui.py         管理画面の共通部品（何もimportしない）           238行
  admin_home.py       管理トップ＋広告欄・担当者マスタ・配布ファイル      705行
  admin_licenses.py   ライセンス一覧＋操作API                        541行
  admin_referrals.py  紹介リンク管理                                470行
  admin_prompts.py    プロンプト管理                                224行
  staff_console.py    スタッフ用画面 /staff                         206行
  settings_admin.py   AI設定（為替レート）                          396行
  evaluate.py         サーバー側 Gemini 採点                        461行
  sites.py            対応求人サイト定義                            140行
  requirements.txt    依存パッケージ

  ※ 同梱していない（この期間に変更していない）ファイル
     database.py / db_redesign.py / rate_limit.py / payments.py /
     plans.py / mailer.py / runtime.txt / frontend/ 配下


■ 画面モジュールと権限の対応

  verify_any（管理者＋スタッフ）  admin_licenses / admin_referrals / staff_console
  verify_admin（管理者のみ）      admin_home / admin_prompts / settings_admin

  main.py の末尾で、それぞれに対応する認証関数を渡している。
  渡す関数を間違えると権限が緩む／厳しくなるため、追加時は必ず確認すること。


■ 画面を切り出すときの型

  1. モジュール直下に router = APIRouter() を置く
  2. 認証は main.py から関数を受け取る

        _verify = None
        def build_xxx_router(verify_fn):
            global _verify
            _verify = verify_fn
            return router
        def verify(credentials = Depends(security)):
            return _verify(credentials)

  3. main.py の末尾に2行足す
  4. 共通で使うもの（UI_TEXT / esc / ui_text / plan_label_ui）は
     admin_ui.py から import する

  【重要1】コードを移すときに再インデントしないこと。
  画面のHTMLは f-string で書かれており、インデントを変えると
  文字列の中身が変わってしまう。そのため router 方式にしている。

  【重要2】ルート定義の順序に注意。/admin/prompts/new は
  /admin/prompts/{prompt_id} より先に定義すること（逆にすると
  new が {prompt_id} として解釈される）。モジュール内の並び順が登録順になる。

  【重要3】環境変数を使う画面は os.environ から直接読む
  （main.py を import すると循環参照になる）。既定値を main.py と揃えること。
  admin_referrals.py の BASE_URL がその例。


■ 検証済みの内容（v3.24）

  ・ルート46本が変更前後で完全に一致（消失・重複なし）
  ・未定義の名前なし（8ファイルとも静的解析で確認）
  ・移動した806行のうち変更は26行のみ。すべて @app.→@router. と
    Depends(verify_admin)→Depends(verify) の置換。HTMLの変更は0行
  ・import 整理40件は「本体で使われていない名前」だけを機械的に削除し、
    削除後に未定義参照が出ないことを再確認
  ・スタブ環境で全モジュールを import し、ルーター組み立てと認証注入を確認
  ・/admin/prompts/new が {prompt_id} より先に定義されていることを確認


■ 注意（従来から変わらず）

  ・evaluate.py は全サイト共通。触ったらUpworkの回帰テストを行うこと。
  ・HTMLは f-string。波括弧は {{ }} にすること。
  ・polar-sdk は 2026-09-06 の更新課金テストが終わるまで上げないこと。
  ・リリースのたびに main.py の APP_VERSION を更新する（1行のみ）。
