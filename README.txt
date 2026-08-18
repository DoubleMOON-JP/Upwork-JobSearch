JobSearch ソース一式  v3.25（2026-08-18）
====================================================================

■ このリリースの内容：求人の取りこぼし対策（第1段階）

  変更したファイルは evaluate.py の1箇所だけ（＋APP_VERSION）。

  【背景】
  同じ貼付テキスト（Upwork 30件）で3回採点したところ、
  出力件数が 26件 / 29件 / 15件 とばらついた。
  落ちた求人を調べると、点数と強く相関していた。

      3回目で残った15件   … 平均 60.3点
      3回目で落ちた14件   … 平均 15.4点
      3回とも落ちた求人   … Inventory Tracking Database with QR Codes

  つまりランダムな取りこぼしではなく、AIが「プロフィールに合わない求人は
  出さなくてよい」と判断して省いていた。

  【原因と考えられる箇所】
  従来の指示は次の3行だった。

      Split this into individual job postings. IGNORE anything that is not a job
      (…noise…).
      For EACH job, extract its fields and score it against the profile.

  「IGNORE anything that is not a job」は本来ヘッダー・広告の除外を指すが、
  「合わない求人＝出さなくてよい」と拡大解釈される余地があった。

  【変更内容】
  ノイズ除去の指示は残したまま、直後に Completeness セクションを追加した。

      ## Completeness (STRICT)
      Output EVERY job posting you find. This rule overrides any tendency
      to be concise.
      - A real job posting is never "not a job", however irrelevant, low-paid,
        vague or badly written it is. Irrelevance is not a reason to leave it
        out: give it a low score and still return it.
      - Do NOT return only the best matches, only the top few, or a shortened
        selection. There is no upper limit on how many jobs you may return.
      - Do NOT merge two separate postings into one entry.
      - Never invent a job that is not present in the text.

  あわせて、除外対象に adverts / promoted banners / related searches /
  category lists を明記した（求人一覧には実質広告が混ざるため）。

  【効果の測り方】
  同じ貼付テキスト（sha256 34be63c5…、30件）でもう3回採点する。
      3回とも30件そろう  → 対策完了
      まだ落ちる          → 第2段階（件数をプログラムで数えてAIに渡す）へ

  ※ 第2段階は固定値ではなく、貼付テキストから毎回数える方式にする。
    最終ページで件数が少ない場合にも対応するため。
    また数え間違いで新たな取りこぼしを作らないよう、
    「ちょうどN件」ではなく「少なくともN件」と伝える設計とする。


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
  evaluate.py         サーバー側 Gemini 採点                        471行  ← 変更
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


■ 検証済みの内容（v3.25）

  ・プロンプトの差分が意図した1箇所のみ（Upwork・Freelancer.com 双方）
  ・採点基準・セキュリティ規則・応募要件の抽出指示・出力仕様・
    点数降順の指示は1文字も変わっていない
  ・貼付テキスト以降（フェンス〜末尾）が新旧で完全一致
  ・為替レート参照表の位置と内容が従来どおり（Freelancer.com）
  ・ユーザーの自由要望ブロックが従来どおり末尾に付く
  ・サイト固有のノイズ除去指示を消していないこと
  ・全10ファイルが構文エラーなし


■ 検証済みの内容（v3.24 から継続）

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
