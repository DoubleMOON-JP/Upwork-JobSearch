# evaluate.py
# ─────────────────────────────────────────────────────────────
# サーバー側 Gemini 採点（リデザインの中核）。
#   - 入力：ライセンスキー ＋ ユーザーがUpworkから貼り付けた生テキスト ＋ プロフィール
#   - 処理：貼付テキストを求人ごとに分割・抽出し、プロフィール適合度で採点
#   - Geminiキーはサーバー保持（env GEMINI_API_KEY）。モデルは ai_settings で管理
#   - レート制限＋月間上限を通過した場合のみ実行
#   - 出力：従来CSVと同じ列に対応する JSON（title/budget/skills/score/recommendation/reason/url）
# main.py で include_router(evaluate_router) する。エンドポイントは POST /evaluate
# ─────────────────────────────────────────────────────────────
import os
import json
import re

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from database import validate_license, get_active_prompt, get_ai_settings
from rate_limit import check_and_consume

router = APIRouter()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"


# ── 貼付テキスト解析＋採点用のプロンプトを組み立てる ──────────
def build_prompt(base_template: str, profile: dict, ai_request: str, pasted_text: str) -> str:
    """
    base_template : DBの有効プロンプト（採点基準の本体）
    profile       : スキル・時給・避けたいKW・優先KW 等
    ai_request    : ユーザーからAIへの自由要望
    pasted_text   : Upworkからコピーした生テキスト（複数求人・ヘッダー等ゴミ混じり可）
    """
    profile_lines = []
    if profile.get("skills"):        profile_lines.append(f"- Skills: {profile['skills']}")
    if profile.get("categories"):    profile_lines.append(f"- Categories: {profile['categories']}")
    if profile.get("min_rate"):      profile_lines.append(f"- Minimum acceptable rate: {profile['min_rate']}")
    if profile.get("avoid_keywords"):profile_lines.append(f"- Keywords to avoid: {profile['avoid_keywords']}")
    if profile.get("prefer_keywords"):profile_lines.append(f"- Preferred keywords: {profile['prefer_keywords']}")
    if profile.get("highlights"):    profile_lines.append(f"- Experience highlights: {profile['highlights']}")
    profile_block = "\n".join(profile_lines) if profile_lines else "(no profile provided)"

    ai_request_line = ""
    if ai_request and ai_request.strip():
        ai_request_line = (
            f"\nUser's request to the AI (treat as top priority): {ai_request.strip()}\n"
        )

    return f"""{base_template}

## Freelancer profile
{profile_block}
{ai_request_line}
## Raw pasted text from Upwork (may contain multiple jobs plus navigation/footer noise)
Split this into individual job postings. IGNORE anything that is not a job
(headers, footers, "Skip skills", "more about", profile/nav text, "© Upwork" etc.).
For EACH job, extract its fields and score it against the profile.

<<<PASTED_TEXT_START>>>
{pasted_text}
<<<PASTED_TEXT_END>>>

## Output format (STRICT)
Return ONLY a JSON array, no prose, no markdown fences. Each element:
{{
  "title": string,
  "budget": string,
  "posted": string,
  "skills": string,            // comma separated
  "score": integer,            // 0-100 fit against the profile
  "recommendation": string,    // one of: "Apply", "Maybe", "Skip"
  "reason": string             // short reason in English
}}
Sort by score descending."""


def _extract_text(gemini_json: dict) -> str:
    try:
        return gemini_json["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_usage(gemini_json: dict) -> dict:
    """Geminiが返す正確なトークン数（推測ではなく実測値）。コスト算出に使用。"""
    u = gemini_json.get("usageMetadata", {}) or {}
    prompt_tokens = u.get("promptTokenCount", 0)
    output_tokens = u.get("candidatesTokenCount", 0)
    total_tokens  = u.get("totalTokenCount", prompt_tokens + output_tokens)
    return {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens":  total_tokens,
    }


def _parse_json_array(text: str):
    """Geminiの返答から JSON 配列を安全に取り出す。```json フェンス等を除去。"""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("JSON array not found in model output")
    return json.loads(cleaned[start:end + 1])


@router.post("/evaluate")
async def evaluate(request: Request):
    if not GEMINI_API_KEY:
        raise HTTPException(500, "server is missing GEMINI_API_KEY")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    license_key = (body.get("license_key") or "").strip()
    pasted_text = (body.get("pasted_text") or "").strip()
    profile     = body.get("profile") or {}
    ai_request  = body.get("ai_request") or ""
    threshold   = int(body.get("score_threshold") or 0)

    if not license_key:
        raise HTTPException(400, "license_key is required")
    if not pasted_text:
        raise HTTPException(400, "pasted_text is empty")

    # ① ライセンス認証
    lic = validate_license(license_key)
    if not lic.get("valid"):
        return JSONResponse(status_code=403, content={"status": "invalid", **lic})

    # ② レート制限＋月間上限（コスト防御）
    quota = check_and_consume(license_key, lic["plan"])

    # ③ プロンプト・モデルを取得
    prompt_row = get_active_prompt() or {}
    base_template = prompt_row.get("template", "You are an assistant that scores Upwork jobs.")
    model = (get_ai_settings() or {}).get("default_model", DEFAULT_MODEL)

    prompt = build_prompt(base_template, profile, ai_request, pasted_text)

    # ④ Gemini 呼び出し（Kojiのキーで）
    url = GEMINI_ENDPOINT.format(model=model)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, params={"key": GEMINI_API_KEY}, json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"gemini request failed: {e}")

    if resp.status_code != 200:
        raise HTTPException(502, f"gemini error {resp.status_code}: {resp.text[:300]}")

    gemini_json = resp.json()
    text = _extract_text(gemini_json)
    usage = _extract_usage(gemini_json)
    try:
        jobs = _parse_json_array(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"could not parse model output: {e}")

    # ⑤ 閾値で「表示対象」を判定しつつ、全件返す（フィルタはフロント側でも可）
    matched = [j for j in jobs if int(j.get("score", 0)) >= threshold]

    # コスト算出用：Renderのログに実測トークン数を記録（Gemini APIレスポンスの正確な値）
    print(f"[evaluate] model={model} jobs={len(jobs)} "
          f"prompt_tokens={usage['prompt_tokens']} output_tokens={usage['output_tokens']} "
          f"total_tokens={usage['total_tokens']}")

    return {
        "status": "ok",
        "count": len(jobs),
        "matched": len(matched),
        "threshold": threshold,
        "quota": quota,
        "token_usage": usage,   # 実測値。プロンプト/出力/合計トークン数
        "jobs": jobs,
    }
