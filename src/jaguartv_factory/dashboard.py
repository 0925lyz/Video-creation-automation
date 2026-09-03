from __future__ import annotations

import json
import base64
import hashlib
import hmac
import html
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

import yaml

from .core import (
    append_event,
    category_active_today,
    candidate_has_review_outputs,
    connect_db,
    discover,
    download_top,
    generate_review_index,
    inspect_url,
    ingest_uploaded_media,
    inventory_root,
    list_candidates,
    media_duration,
    media_dimensions,
    now_iso,
    platform_from_url,
    produce_top,
    resolve_config_path,
    tracking_links,
    workspace_dir,
)
from .publisher import auto_enqueue_approved_publication, publication_state_for_candidates
from .publication_cancellation import cancel_publication
from .production import assert_candidate_ready_for_review
from .posters import (
    PosterError,
    add_poster_attachment,
    approve_poster,
    delete_poster_attachment,
    delete_poster,
    import_poster,
    list_posters,
    poster_counts,
    poster_detail,
    poster_download_name,
    poster_upload_limits,
    reorder_poster_attachments,
    replace_poster_attachment,
    resolve_poster_attachment,
    resolve_poster_file,
    save_poster_content,
)
from .original_factory import (
    OriginalFactoryError,
    approve_originals,
    delete_originals,
    import_original_video,
    list_original_items,
    original_counts,
    original_detail,
    original_download_name,
    original_upload_limits,
    register_original_download,
    resolve_original_file,
)
from .publish_flow import (
    create_publish_operation,
    generate_publish_copy_preview,
    list_publish_accounts,
    platform_capabilities,
)
from .publishing_copywriter import (
    call_copywriter_model,
    copywriter_ai_status,
    openai_copywriter_model,
    deepseek_copywriter_model,
)
from .sessions import check_session, delete_session, list_sessions, save_session
from .youtube_analytics import (
    analytics_accounts,
    analytics_history,
    analytics_ranking,
    analytics_summary,
    backfill_report,
    channel_import_report,
    channel_import_status,
    publication_latest,
    retry_publication_sync,
    set_backfill_status,
)
from .server_store import (
    complete_chunked_upload,
    find_upload,
    init_chunked_upload,
    list_uploads,
    public_url,
    save_upload,
    save_upload_chunk,
    storage_root,
)
from .cta import delete_cta_asset, import_cta_stream, list_cta_assets
from .review_edit import manual_cut_review_output, replace_review_output_design
from .source_imports import (
    SOURCE_TYPE,
    SOURCE_IMPORT_CATEGORY_LABELS,
    TARGET_APPROVED,
    TARGET_LABELS,
    attach_source_import_candidate,
    complete_source_import,
    create_source_import,
    create_uploaded_source_import,
    fail_source_import,
    normalize_import_url,
    normalize_source_category,
    normalize_target_area,
    source_import_row,
    sync_source_import_workflow_status,
)
from .trends import list_hot_keywords, run_trends_job, start_trends_scheduler, trends_today


WEB_ROOT = Path(__file__).resolve().parent / "web"
BRAND_ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets" / "brand"
PLATFORMS = ("youtube", "x", "facebook", "tiktok", "kwai")
PUBLISH_TARGETS = (*PLATFORMS, "instagram", "other")
EVENT_TYPES = ("landing_click", "download_started", "install", "registration", "first_watch")
REVIEW_DECISIONS = ("APPROVED", "REVISION_REQUIRED")
PART_PACKAGE_PATTERN = re.compile(r"^(?P<parent>.+)_part(?P<number>\d+)$")
COPYWRITER_MODES = {"tv", "generic"}
COPYWRITER_PLATFORMS = {"shorts", "tiktok", "kwai", "facebook", "whatsapp", "email", "seo"}
COPYWRITER_TONES = {"viral", "trust", "urgent", "friendly"}
PUBLIC_UPLOAD_KINDS = {"design_image"}
ADMIN_COOKIE_NAME = "jaguartv_admin"
ADMIN_SESSION_SECONDS = 7 * 24 * 3600
MATERIAL_CATEGORY_LABEL = "素材"
INITIAL_CATEGORY_RULES = (
    ("ai短剧", (
        "ai短剧", "ai 短剧", "短剧", "微短剧", "竖屏剧", "ai drama", "ai short drama",
        "short drama", "mini drama", "micro drama", "drama ia", "série ia", "serie ia",
        "novela ia", "história gerada por ia", "historia gerada por ia",
    )),
    ("明星名人歌手", (
        "celebridade", "celebrity", "famoso", "famosa", "famosos", "famosas", "artista",
        "cantor", "cantora", "singer", "ator", "atriz", "influencer", "anitta", "ludmilla",
        "ivete sangalo", "pabllo vittar", "luísa sonza", "luisa sonza", "iza", "alok",
        "isis valverde", "nathalia dill", "amora mautner",
        "gusttavo lima", "whindersson", "neymar atriz", "明星", "名人", "歌手", "艺人",
        "演员", "网红", "安妮塔", "卢德米拉",
    )),
    ("足球球星", (
        "neymar", "vinicius", "vinícius", "vini jr", "vini junior", "rodrygo",
        "endrick", "richarlison", "raphinha", "casemiro", "marquinhos", "alisson",
        "ronaldinho", "ronaldo fenômeno", "ronaldo fenomeno", "pelé", "pele", "messi",
        "cristiano ronaldo", "mbappé", "mbappe", "haaland", "craque", "artilheiro",
        "bola de ouro", "球星", "内马尔", "维尼修斯", "罗德里戈", "恩德里克", "梅西",
        "C罗", "姆巴佩", "哈兰德", "金球奖",
    )),
    ("足球类", (
        "futebol", "football", "soccer", "libertadores", "brasileirão", "brasileirao",
        "copa do brasil", "palmeiras", "flamengo", "cruzeiro", "corinthians", "botafogo",
        "são paulo", "sao paulo", "cerro porteño", "cerro porteno", "gols", "melhores momentos",
        "sccp", "fiel torcedor", "portland timbers", "cruz azul", "rosario central",
        "club atlético", "club atletico", "los angeles fc", "lafc", "querétaro", "queretaro",
        "seattle sounders", "guadalajara",
        "巴甲", "足球", "解放者杯", "南美杯", "巴西杯", "帕尔梅拉斯", "弗拉门戈",
    )),
    ("新闻类", (
        "notícia", "noticias", "notícias", "news", "g1", "cnn brasil", "eleições",
        "eleicoes", "presidente", "tse", "dólar", "dolar", "inflação", "inflacao",
        "previsão do tempo", "previsao do tempo", "tarifa", "congresso", "lula",
        "新闻", "大选", "总统", "通胀", "天气", "汇率",
    )),
    ("音乐类", (
        "música", "musica", "music", "funk", "sertanejo", "anitta", "ludmilla",
        "brega", "mpb", "spotify", "festival de música", "festival de musica", "show",
        "viral song", "歌曲", "音乐", "放克", "乡村音乐", "演唱会", "音乐节",
    )),
    ("肥皂剧（电视剧、电影）", (
        "novela", "telenovela", "globoplay", "globo", "resumo da novela", "spoiler",
        "tela quente", "filme", "filmes", "cinema", "movie", "movies", "série",
        "serie", "series", "电视剧", "电影", "肥皂剧", "环球台", "剧情", "剧集",
    )),
    ("少儿剧", (
        "infantil", "criança", "crianca", "kids", "children", "desenho", "cartoon",
        "animação", "animacao", "nursery", "儿童", "少儿", "动画", "卡通", "亲子",
    )),
    ("成人频道", (
        "adulto", "adult", "canal adulto", "18+", "nsfw", "sensual", "成人",
    )),
    ("纪录片（美食、动物、地区发展）", (
        "documentário", "documentario", "documentary", "comida", "culinária", "culinaria",
        "gastronomia", "animal", "animais", "natureza", "desenvolvimento", "região",
        "regiao", "história", "historia", "纪录片", "美食", "动物", "自然", "地区发展",
    )),
    ("综艺", (
        "programa", "reality", "variedades", "show de tv", "entretenimento", "humor",
        "comédia", "comedia", "综艺", "娱乐", "真人秀", "喜剧",
    )),
    ("社交挑战", (
        "desafio", "challenge", "tiktok brasil", "#fyp", "para você", "para voce",
        "paravoce", "#viral", "reels", "meme", "trend", "tendência", "tendencia",
        "挑战", "热门挑战", "社交", "梗图", "爆款", "病毒",
    )),
    ("舞蹈", (
        "dança", "danca", "dance", "coreografia", "choreography", "passinho", "舞蹈", "跳舞",
    )),
    ("教程及优点展示类", (
        "tutorial", "tutorial completo", "passo a passo", "how to", "como instalar",
        "como usar", "guia", "instalação", "instalacao", "setup", "vantagens",
        "benefícios", "beneficios", "recursos", "features", "demonstração",
        "demonstracao", "review de produto", "comparação", "comparacao",
        "安装教程", "下载教程", "使用教程", "优点", "优势", "好处",
        "功能展示", "产品展示", "演示", "介绍", "评测", "对比", "使用方法", "怎么用",
    )),
    ("官方性质类", (
        "oficial", "anúncio oficial", "anuncio oficial", "comunicado oficial",
        "nota oficial", "site oficial", "app oficial", "perfil oficial", "lançamento",
        "lancamento", "atualização", "atualizacao", "release", "institucional",
        "empresa", "marca", "equipe oficial", "官方", "官方公告", "官方账号",
        "官网", "官宣", "公告", "声明", "品牌", "公司", "正式发布",
    )),
    ("合作类", (
        "parceria", "parceiro", "colaboração", "colaboracao", "collab",
        "cooperação", "cooperacao", "afiliado", "afiliados", "patrocínio",
        "patrocinio", "sponsor", "cupom", "promoção", "promocao", "representante",
        "revendedor", "invite", "合作", "联名", "推广合作", "商务合作", "赞助",
        "代理", "渠道", "分销", "合伙", "优惠码",
    )),
    ("运营教学类", (
        "operação", "operacao", "gestão", "gestao", "estratégia operacional",
        "tutorial de operação", "como operar", "tráfego", "trafego", "métricas",
        "metricas", "campanha", "funil", "funnel", "marketing", "monetização",
        "monetizacao", "crm", "kpi", "运营", "运营教学", "运营教程", "后台运营",
        "账号运营", "发布运营", "数据分析", "投放", "增长", "转化", "留存",
    )),
    ("教程及答疑类", (
        "perguntas frequentes", "faq", "dúvidas", "duvidas", "respostas",
        "perguntas e respostas", "q&a", "ajuda", "problema", "como resolver",
        "solução", "solucao", "suporte", "atendimento", "答疑", "问答",
        "问题解答", "常见问题", "教程答疑", "问题", "怎么办", "帮助", "支持",
        "客服", "故障", "解决方法",
    )),
)
INITIAL_CATEGORY_LABELS = [label for label, _ in INITIAL_CATEGORY_RULES]
SHARED_CATEGORY_LABELS = ("素材",)
IMPORT_ONLY_CATEGORY_LABELS = (
    "教程及优点展示类", "官方性质类", "合作类", "运营教学类", "教程及答疑类",
    *SHARED_CATEGORY_LABELS,
)
FACTORY_CATEGORY_LABELS = tuple(
    label
    for label in INITIAL_CATEGORY_LABELS
    if label not in IMPORT_ONLY_CATEGORY_LABELS or label in SHARED_CATEGORY_LABELS
) + SHARED_CATEGORY_LABELS
FACTORY_SOURCE_TYPE = "factory"
DEFAULT_YOUTUBE_CATEGORY_ACCOUNTS = {
    "新闻类": "consumer_main",
    "足球球星": "consumer_football",
    "足球类": "consumer_football",
    "ai短剧": "consumer_entertainment",
    "明星名人歌手": "consumer_entertainment",
    "音乐类": "consumer_entertainment",
    "肥皂剧（电视剧、电影）": "consumer_entertainment",
    "少儿剧": "consumer_entertainment",
    "纪录片（美食、动物、地区发展）": "consumer_entertainment",
    "综艺": "consumer_entertainment",
    "社交挑战": "consumer_entertainment",
    "舞蹈": "consumer_entertainment",
    "教程及优点展示类": "consumer_guide",
    "官方性质类": "consumer_guide",
    "合作类": "partner_main",
    "运营教学类": "partner_academia",
    "教程及答疑类": "consumer_guide",
}
YOUTUBE_SOURCE_BLOCKED_ACCOUNTS = {"consumer_main", "consumer_football", "consumer_entertainment"}
YOUTUBE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_AUTH_LINK_MAX_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_X_OAUTH_SCOPES = ("tweet.read", "users.read", "tweet.write", "media.write", "offline.access")
X_OAUTH_AUTH_URL = "https://x.com/i/oauth2/authorize"
X_OAUTH_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_USERS_ME_URL = "https://api.x.com/2/users/me"
ACCOUNT_ALIASES = {
    "jaguartv_vivo": "jaguartv_vivo",
    "jaguartv vivo": "jaguartv_vivo",
    "consumer_main": "consumer_main",
    "jaguartv hoje": "consumer_main",
    "yt_hoje": "consumer_main",
    "consumer_football": "consumer_football",
    "jaguartv futebol": "consumer_football",
    "consumer_guide": "consumer_guide",
    "jaguartv guia": "consumer_guide",
    "consumer_entertainment": "consumer_entertainment",
    "jaguartv entretenimento": "consumer_entertainment",
    "partner_main": "partner_main",
    "jaguartv parceiros": "partner_main",
    "partner_embaixador": "partner_embaixador",
    "jaguartv embaixador": "partner_embaixador",
    "partner_revendedor": "partner_revendedor",
    "jaguartv revendedor": "partner_revendedor",
    "partner_academia": "partner_academia",
    "academia jaguartv": "partner_academia",
}
X_ACCOUNT_SLOTS = (
    "consumer_main",
    "consumer_football",
    "consumer_guide",
    "consumer_entertainment",
    "partner_main",
    "partner_embaixador",
    "partner_revendedor",
    "partner_academia",
)
X_REQUIRED_PUBLISH_SCOPES = {"tweet.write", "media.write", "offline.access"}
YOUTUBE_SOURCE_BLOCKED_ACCOUNTS = {*YOUTUBE_SOURCE_BLOCKED_ACCOUNTS, "jaguartv_vivo"}
SOURCE_MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
PRODUCTION_RUNNING_STATUS = "PRODUCTION_RUNNING"


def public_brand_asset_path(requested: str) -> Path | None:
    relative = unquote(requested).lstrip("/")
    prefix = "assets/brand/"
    if not relative.startswith(prefix):
        return None
    root = BRAND_ASSET_ROOT.resolve()
    path = (root / relative.removeprefix(prefix)).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def upload_kind_requires_token(kind: str) -> bool:
    return True


def loopback_client(address: str) -> bool:
    return address in {"127.0.0.1", "::1", "localhost"}


def initial_category_for_text(*values: Any) -> str:
    chunks = [str(value or "").lower() for value in values if str(value or "").strip()]
    for label, needles in INITIAL_CATEGORY_RULES:
        if chunks and any(needle in chunks[0] for needle in needles):
            return label
    haystack = " ".join(chunks[1:] if len(chunks) > 1 else chunks)
    for label, needles in INITIAL_CATEGORY_RULES:
        if any(needle in haystack for needle in needles):
            return label
    return "未分类"


def normalize_keyword(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def explicit_category_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return next((label for label in INITIAL_CATEGORY_LABELS if label == text or label in text), "")


def category_for_inventory_source(category: str, source_type: str) -> str:
    label = str(category or "未分类")
    if source_type == SOURCE_TYPE:
        return label if label in IMPORT_ONLY_CATEGORY_LABELS else "未分类"
    return label if label in FACTORY_CATEGORY_LABELS else "未分类"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def int_value(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def validated_positive_int(value: Any, name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


def copywriter_ai_model_name(value: str | None = None) -> str:
    return str(value or os.environ.get("JAGUARTV_OPENAI_MODEL") or "gpt-5.2").strip()


def copywriter_ai_model_candidates(primary: str | None = None) -> list[str]:
    return [copywriter_ai_model_name(primary), deepseek_copywriter_model({})]


def copywriter_request(payload: dict[str, Any]) -> dict[str, Any]:
    input_text = str(payload.get("input") or "").strip()
    if not input_text:
        raise ValueError("input is required")
    if len(input_text) > 800:
        raise ValueError("input must be 800 characters or fewer")
    mode = str(payload.get("mode") or "tv").strip().lower()
    platform = str(payload.get("platform") or "shorts").strip().lower()
    tone = str(payload.get("tone") or "viral").strip().lower()
    if mode not in COPYWRITER_MODES:
        raise ValueError(f"mode must be one of {sorted(COPYWRITER_MODES)}")
    if platform not in COPYWRITER_PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(COPYWRITER_PLATFORMS)}")
    if tone not in COPYWRITER_TONES:
        raise ValueError(f"tone must be one of {sorted(COPYWRITER_TONES)}")
    count = int_value(payload.get("count"), 5)
    if count not in {3, 5, 8}:
        raise ValueError("count must be 3, 5, or 8")
    heat = int_value(payload.get("heat"), 7)
    if heat < 1 or heat > 10:
        raise ValueError("heat must be between 1 and 10")
    cta = str(payload.get("cta") or ("Baixe em Jarg.top" if mode == "tv" else "Saiba mais")).strip()
    if len(cta) > 180:
        raise ValueError("cta must be 180 characters or fewer")
    return {
        "input": input_text,
        "mode": mode,
        "platform": platform,
        "tone": tone,
        "heat": heat,
        "count": count,
        "cta": cta,
    }


def copywriter_prompt(request: dict[str, Any]) -> str:
    mode_rules = (
        "TV product promotion mode: write Brazilian Portuguese marketing copy for JaguarTV. "
        "Product facts: JaguarTV, Jarg.top, live TV, sports, movies, series, Android phone, Android TV, TV box, Brazil. "
        "Never promise specific copyrighted channels, guaranteed free access, prices, or availability unless the user supplied them. "
        "Use Jarg.top only as the download/action destination."
        if request["mode"] == "tv"
        else "Generic content mode: write directly about the user's keywords as publishable Brazilian Portuguese content. "
        "Do not write advice about marketing, copywriting, campaigns, or how to talk about the topic. "
        "Do not mention JaguarTV, Jarg.top, TV ao vivo, Android TV, download sites, or any TV product. "
        "If the keywords describe football, street football, a challenge, Brazil, food, music, health, education, or any other topic, make the output about that topic itself."
    )
    return f"""
You are a senior Brazilian Portuguese copywriter and Chinese bilingual reviewer.
The operator inputs Chinese keywords and needs ready-to-publish pt-BR copy plus Chinese audit translations.

User keywords in Chinese:
{request['input']}

Generation settings:
- mode: {request['mode']}
- platform: {request['platform']}
- tone: {request['tone']}
- viral intensity: {request['heat']}/10
- variant count: {request['count']}
- CTA/action: {request['cta']}

Rules:
{mode_rules}
- Output must be natural Brazilian Portuguese, not European Portuguese.
- Keep claims honest and avoid unverifiable guarantees.
- Generate exactly {request['count']} titles and exactly {request['count']} platform captions.
- Captions should match the platform and be usable without extra editing.
- Also include a Chinese audit translation that helps a Chinese-speaking operator review the pt-BR meaning.
- Return JSON only. No markdown, no code fences, no commentary.

Return this exact JSON shape:
{{
  "strategy": "multi-line pt-BR strategy summary",
  "titles": ["pt-BR title 1"],
  "captions": ["1. [Platform] pt-BR caption 1"],
  "cta": "pt-BR CTA/action",
  "hashtags": "#Tag1 #Tag2",
  "emails": [
    {{"name": "pt-BR stage name", "subject": "pt-BR subject", "preview": "pt-BR preview", "body": "pt-BR body", "cta": "pt-BR CTA"}}
  ],
  "seo": {{"title": "pt-BR SEO title", "description": "pt-BR meta description", "keywords": ["keyword"]}},
  "zhAudit": {{
    "strategy": "中文审核策略说明",
    "titles": ["中文标题含义 1"],
    "captions": ["1. [平台] 中文正文含义 1"],
    "cta": "中文 CTA 含义",
    "hashtags": "中文标签含义",
    "emails": [
      {{"name": "中文阶段名", "subject": "中文主题含义", "preview": "中文预览含义", "body": "中文正文含义", "cta": "中文 CTA"}}
    ],
    "seo": {{"title": "中文 SEO 标题含义", "description": "中文 SEO 描述含义", "keywords": ["中文关键词"]}}
  }},
  "note": "pt-BR compliance/performance note"
}}
""".strip()


def extract_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", clean, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        clean = fence.group(1).strip()
    if not clean.startswith("{"):
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model response did not contain JSON")
        clean = clean[start:end + 1]
    try:
        data = json.loads(clean)
    except json.JSONDecodeError as error:
        raise ValueError("model response was not valid JSON") from error
    if not isinstance(data, dict):
        raise ValueError("model response JSON must be an object")
    return data


def _string_list(value: Any, *, expected: int | None = None, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"model field {field} must be a list")
    items = [str(item).strip() for item in value if str(item or "").strip()]
    if expected is not None and len(items) < expected:
        raise ValueError(f"model field {field} must contain at least {expected} items")
    return items[:expected] if expected else items


def _email_list(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"model field {field} must be a list")
    emails: list[dict[str, str]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        emails.append({
            "name": str(item.get("name") or "").strip(),
            "subject": str(item.get("subject") or "").strip(),
            "preview": str(item.get("preview") or "").strip(),
            "body": str(item.get("body") or "").strip(),
            "cta": str(item.get("cta") or "").strip(),
        })
    if not emails:
        raise ValueError(f"model field {field} must contain at least one email")
    return emails


def post_json(endpoint: str, headers: dict[str, str], body: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any]]:
    request = Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except Exception as error:
        response = getattr(error, "fp", None)
        status = int(getattr(error, "code", 0) or 0)
        raw = response.read() if response else b""
        if not status:
            raise RuntimeError(str(error)) from error
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"non-JSON model response: {raw[:180]!r}") from error
    return status, data


def normalize_ai_copywriter_result(
    result: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    seo = result.get("seo") or {}
    zh_audit = result.get("zhAudit") or {}
    if not isinstance(seo, dict) or not isinstance(zh_audit, dict):
        raise ValueError("model response is missing seo or zhAudit objects")
    zh_seo = zh_audit.get("seo") or {}
    if not isinstance(zh_seo, dict):
        raise ValueError("model response is missing zhAudit.seo object")
    normalized = {
        "mode": request["mode"],
        "strategy": str(result.get("strategy") or "").strip(),
        "titles": _string_list(result.get("titles"), expected=request["count"], field="titles"),
        "captions": _string_list(result.get("captions"), expected=request["count"], field="captions"),
        "cta": str(result.get("cta") or request["cta"]).strip(),
        "hashtags": str(result.get("hashtags") or "").strip(),
        "emails": _email_list(result.get("emails"), "emails"),
        "seo": {
            "title": str(seo.get("title") or "").strip(),
            "description": str(seo.get("description") or "").strip(),
            "keywords": _string_list(seo.get("keywords") or [], field="seo.keywords"),
        },
        "zhAudit": {
            "strategy": str(zh_audit.get("strategy") or "").strip(),
            "titles": _string_list(zh_audit.get("titles"), expected=request["count"], field="zhAudit.titles"),
            "captions": _string_list(zh_audit.get("captions"), expected=request["count"], field="zhAudit.captions"),
            "cta": str(zh_audit.get("cta") or "").strip(),
            "hashtags": str(zh_audit.get("hashtags") or "").strip(),
            "emails": _email_list(zh_audit.get("emails"), "zhAudit.emails"),
            "seo": {
                "title": str(zh_seo.get("title") or "").strip(),
                "description": str(zh_seo.get("description") or "").strip(),
                "keywords": _string_list(zh_seo.get("keywords") or [], field="zhAudit.seo.keywords"),
            },
        },
        "note": str(result.get("note") or "").strip(),
    }
    for field in ("strategy", "cta", "hashtags", "note"):
        if not normalized[field]:
            raise ValueError(f"model field {field} is required")
    if not normalized["seo"]["title"] or not normalized["seo"]["description"]:
        raise ValueError("model seo.title and seo.description are required")
    if not normalized["zhAudit"]["strategy"]:
        raise ValueError("model zhAudit.strategy is required")
    return normalized


def generate_copywriter_with_ai(payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    request = copywriter_request(payload)
    active_config = config or {}
    result, source, model, errors = call_copywriter_model(
        active_config,
        copywriter_prompt(request),
        lambda text: normalize_ai_copywriter_result(extract_json_object(text), request),
    )
    if errors:
        result["primary_model"] = openai_copywriter_model(active_config)
    return {**result, "source": source, "model": model}


def signed_upload_url(upload_id: str, lifetime_sec: int = 24 * 3600) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", str(upload_id or "")):
        return ""
    secret = os.environ.get("JAGUARTV_UPLOAD_SIGNING_SECRET", "").strip() or os.environ.get(
        "JAGUARTV_UPLOAD_TOKEN", ""
    ).strip()
    if not secret:
        return f"/api/uploads/{upload_id}/download"
    expires = int(time.time()) + max(60, min(int(lifetime_sec), 7 * 24 * 3600))
    payload = f"{upload_id}:{expires}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"/api/uploads/{upload_id}/download?expires={expires}&signature={signature}"


def upload_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in list_uploads(config):
        row = dict(item)
        row["path"] = ""
        row["download_url"] = signed_upload_url(str(item.get("id") or ""))
        rows.append(row)
    return rows


def candidate_source_media(config: dict[str, Any], candidate_id: str) -> Path | None:
    candidate_id = unquote(candidate_id).strip()
    if not candidate_id:
        return None
    connection = connect_db(config)
    if not connection.execute("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)).fetchone():
        return None
    work = workspace_dir(config) / "jobs" / candidate_id
    return next(
        (
            path for path in work.glob("source.*")
            if path.is_file() and path.suffix.lower() in SOURCE_MEDIA_SUFFIXES
        ),
        None,
    )


def production_recovery_status(config: dict[str, Any], candidate_id: str) -> str:
    try:
        assert_candidate_ready_for_review(config, candidate_id)
        return "READY_FOR_REVIEW"
    except RuntimeError:
        pass
    if candidate_source_media(config, candidate_id):
        return "DOWNLOADED"
    return "PRODUCTION_FAILED"


def recover_interrupted_productions(config: dict[str, Any]) -> int:
    connection = connect_db(config)
    rows = connection.execute(
        "SELECT id FROM candidates WHERE status=?", (PRODUCTION_RUNNING_STATUS,)
    ).fetchall()
    recovered = 0
    for row in rows:
        candidate_id = str(row["id"])
        status = production_recovery_status(config, candidate_id)
        timestamp = now_iso()
        payload = {
            "recovered_from": PRODUCTION_RUNNING_STATUS,
            "status": status,
            "reason": "dashboard service restarted before production task finished",
        }
        connection.execute(
            "UPDATE candidates SET status=?,updated_at=? WHERE id=?",
            (status, timestamp, candidate_id),
        )
        append_event(connection, candidate_id, "PRODUCTION_RECOVERED", payload)
        recovered += 1
    connection.commit()
    return recovered


def review_output_asset_by_id(config: dict[str, Any], asset_id: str) -> dict[str, Any] | None:
    asset_id = unquote(asset_id).strip()
    if not asset_id:
        return None
    for assets in review_output_index(config).values():
        for asset in assets:
            if str(asset.get("id") or "") == asset_id:
                return asset
    return None


def review_package_metadata(config: dict[str, Any], package_id: str) -> dict[str, Any]:
    package_id = package_id.split(":", 1)[0].strip()
    if not package_id:
        return {}
    for root in (workspace_dir(config) / "ready_for_review", storage_root(config) / "review"):
        path = root / package_id / "metadata.json"
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def review_asset_package_id(asset_id: str) -> str:
    return unquote(str(asset_id or "")).split(":", 1)[0].strip()


def review_slice_parent_id(candidate_id: str, parent_id: str = "", source_id: str = "") -> str:
    parent = str(parent_id or "").strip()
    for value in (str(candidate_id or "").strip(), str(source_id or "").strip()):
        match = PART_PACKAGE_PATTERN.match(value.removeprefix("review:"))
        if match:
            return parent or match.group("parent")
    return parent if parent and PART_PACKAGE_PATTERN.match(str(candidate_id or "").strip()) else ""


def should_collapse_review_slice(
    connection: Any,
    outputs_by_candidate: dict[str, list[dict[str, Any]]],
    candidate_id: str,
    parent_id: str = "",
    source_id: str = "",
) -> bool:
    parent = review_slice_parent_id(candidate_id, parent_id, source_id)
    if not parent or parent == candidate_id or not outputs_by_candidate.get(parent):
        return False
    return connection.execute("SELECT 1 FROM candidates WHERE id=?", (parent,)).fetchone() is not None


def resolve_production_candidate(
    config: dict[str, Any], candidate_id: str, options: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    requested = unquote(str(candidate_id or "")).strip()
    requested_asset = ""
    if "::asset::" in requested:
        requested, requested_asset = requested.split("::asset::", 1)
        requested = requested.strip()
        requested_asset = requested_asset.strip()

    patched_options = dict(options or {})
    if requested_asset and isinstance(patched_options.get("design"), dict):
        design = dict(patched_options["design"])
        design.setdefault("base_asset_id", requested_asset)
        patched_options["design"] = design
    if not requested_asset and isinstance(patched_options.get("design"), dict):
        requested_asset = str((patched_options.get("design") or {}).get("base_asset_id") or "").strip()

    connection = connect_db(config)
    row = connection.execute("SELECT id FROM candidates WHERE id=?", (requested,)).fetchone()
    if row:
        return requested, patched_options

    if requested_asset:
        output_asset = review_output_asset_by_id(config, requested_asset)
        asset_package = review_asset_package_id(str((output_asset or {}).get("id") or requested_asset))
        if asset_package:
            metadata = review_package_metadata(config, asset_package)
            source_candidate = str(metadata.get("source_job_id") or "").strip()
            if source_candidate:
                row = connection.execute("SELECT id FROM candidates WHERE id=?", (source_candidate,)).fetchone()
                if row:
                    return source_candidate, patched_options
            return asset_package, patched_options

    metadata = review_package_metadata(config, requested)
    source_candidate = str(metadata.get("source_job_id") or "").strip()
    if source_candidate:
        row = connection.execute("SELECT id FROM candidates WHERE id=?", (source_candidate,)).fetchone()
        if row:
            return source_candidate, patched_options

    part_match = PART_PACKAGE_PATTERN.match(requested)
    if part_match:
        parent = part_match.group("parent")
        row = connection.execute("SELECT id FROM candidates WHERE id=?", (parent,)).fetchone()
        if row:
            return parent, patched_options

    return requested, patched_options


def materialize_review_candidate(config: dict[str, Any], package_id: str) -> bool:
    package_id = package_id.split(":", 1)[0].strip()
    metadata = review_package_metadata(config, package_id)
    if not metadata:
        return False
    connection = connect_db(config)
    if connection.execute("SELECT id FROM candidates WHERE id=?", (package_id,)).fetchone():
        return True
    source = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
    segment = metadata.get("segment") if isinstance(metadata.get("segment"), dict) else {}
    url = str(source.get("url") or "")
    platform = str(source.get("platform") or platform_from_url(url) or "server").strip().lower()
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO candidates(
          id,parent_id,platform,source_id,url,title,description,duration,view_count,
          detected_language,score,status,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            package_id,
            str(metadata.get("source_job_id") or "").strip() or None,
            platform,
            f"review:{package_id}",
            url,
            str(source.get("title") or metadata.get("publishing_text") or package_id),
            str(source.get("description") or ""),
            float(segment.get("duration_sec") or 0),
            0,
            "",
            0,
            "READY_FOR_REVIEW",
            json.dumps(metadata, ensure_ascii=False),
            timestamp,
            timestamp,
        ),
    )
    append_event(connection, package_id, "CANDIDATE_MATERIALIZED_FROM_REVIEW", {"package": package_id})
    connection.commit()
    return True


def candidate_design_info(config: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    requested_asset = ""
    if "::asset::" in candidate_id:
        candidate_id, requested_asset = candidate_id.split("::asset::", 1)
    output_asset = review_output_asset_by_id(config, requested_asset) if requested_asset else None
    output_path = Path(str((output_asset or {}).get("_path") or ""))
    resolve_candidate_id = f"{candidate_id}::asset::{requested_asset}" if requested_asset else candidate_id
    production_candidate_id, _ = resolve_production_candidate(config, resolve_candidate_id)
    if output_path.is_file():
        output_width, output_height = media_dimensions(output_path)
        package_metadata = review_package_metadata(
            config, review_asset_package_id(str((output_asset or {}).get("id") or ""))
        )
        segment = package_metadata.get("segment") if isinstance(package_metadata.get("segment"), dict) else {}
        content_duration = float(segment.get("duration_sec") or media_duration(output_path))
        return {
            "candidate_id": candidate_id,
            "source_candidate_id": production_candidate_id,
            "source_preview_url": str((output_asset or {}).get("video_url") or ""),
            "design_canvas_width": int(output_width),
            "design_canvas_height": int(output_height),
            "source_fit": "contain",
            "design_base_asset_id": str((output_asset or {}).get("id") or ""),
            "design_base_variant": str((output_asset or {}).get("variant") or ""),
            "content_duration_sec": content_duration,
        }
    source_media = candidate_source_media(config, candidate_id)
    result = {
        "candidate_id": candidate_id,
        "source_candidate_id": production_candidate_id,
        "source_preview_url": "",
        "design_canvas_width": 1080,
        "design_canvas_height": 1920,
        "source_fit": "contain",
        "design_base_asset_id": "",
        "design_base_variant": "",
    }
    if source_media is None:
        return result
    source_width, source_height = media_dimensions(source_media)
    result.update({
        "source_preview_url": f"/api/candidates/{quote(candidate_id, safe='')}/source?v={int(source_media.stat().st_mtime)}",
        "design_canvas_width": int(source_width),
        "design_canvas_height": int(source_height),
        "source_fit": "contain",
    })
    return result


def system_health(config: dict[str, Any]) -> dict[str, Any]:
    disk = shutil.disk_usage(storage_root(config))
    runtimes = {}
    for name in ("ffmpeg", "ffprobe", "yt-dlp", "deno", "node", "tesseract"):
        path = shutil.which(name)
        runtimes[name] = {"ok": bool(path), "path": path or ""}
    node_major = 0
    if runtimes["node"]["ok"]:
        result = subprocess.run([str(runtimes["node"]["path"]), "--version"], text=True, capture_output=True, check=False)
        try:
            node_major = int((result.stdout or "").strip().lstrip("v").split(".", 1)[0])
        except ValueError:
            node_major = 0
    runtime = "deno" if runtimes["deno"]["ok"] else "node" if node_major >= 22 else ""
    runtimes["node"]["version_major"] = node_major
    sessions = list_sessions(config)
    ready_sessions = sorted({str(item.get("platform") or "") for item in sessions if item.get("status") == "READY"})
    return {
        "status": "ok",
        "generated_at": now_iso(),
        "storage": {"free_bytes": disk.free, "total_bytes": disk.total},
        "runtimes": runtimes,
        "youtube_runtime": runtime,
        "ready_sessions": ready_sessions,
        "upload": {
            "enabled": True,
            "chunk_bytes": int((config.get("storage", {}) or {}).get("upload_chunk_bytes", 8 * 1024 * 1024)),
            "max_bytes": int((config.get("storage", {}) or {}).get("max_upload_bytes", 2 * 1024 * 1024 * 1024)),
        },
        "copywriter_ai": copywriter_ai_status(config),
    }


def trends_status(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("trends", {}) or {}
    today_keywords = list_hot_keywords(config, None)
    latest_date = ""
    latest_count = 0
    connection = connect_db(config)
    latest = connection.execute(
        """
        SELECT date,COUNT(*) count
        FROM hot_keywords
        GROUP BY date
        ORDER BY date DESC
        LIMIT 1
        """
    ).fetchone()
    if latest:
        latest_date = str(latest["date"] or "")
        latest_count = int(latest["count"] or 0)
    return {
        "enabled": settings.get("enabled", True) is not False,
        "generated_at": now_iso(),
        "today_count": len(today_keywords),
        "latest_date": latest_date,
        "latest_count": latest_count,
        "source": str(settings.get("source") or "google_trends"),
        "geo": str(settings.get("geo") or "BR"),
        "schedule_timezone": str(settings.get("schedule_timezone") or config.get("run", {}).get("timezone") or "UTC"),
        "cron": str(settings.get("cron") or "0 8 * * *"),
    }


def dashboard_overview(config: dict[str, Any]) -> dict[str, Any]:
    connection = connect_db(config)
    status_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status,COUNT(*) count FROM candidates GROUP BY status")
    }
    parent_status_counts = {
        row["status"]: row["count"]
        for row in connection.execute(
            """
            SELECT status,COUNT(*) count
            FROM candidates
            WHERE COALESCE(parent_id,'')=''
            GROUP BY status
            """
        )
    }
    publication_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status,COUNT(*) count FROM publications GROUP BY status")
    }
    latest_metrics = connection.execute(
        """
        WITH ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY candidate_id,platform ORDER BY captured_at DESC,id DESC
          ) rank
          FROM performance_snapshots
        )
        SELECT
          COALESCE(SUM(views),0) views,
          COALESCE(SUM(likes),0) likes,
          COALESCE(SUM(comments),0) comments,
          COALESCE(SUM(shares),0) shares,
          COALESCE(SUM(clicks),0) clicks,
          COALESCE(SUM(installs),0) installs,
          COALESCE(SUM(registrations),0) registrations
        FROM ranked WHERE rank=1
        """
    ).fetchone()
    total = sum(parent_status_counts.values())
    ready = status_counts.get("READY_FOR_REVIEW", 0)
    approved = status_counts.get("APPROVED", 0)
    orphan_reviews = server_review_rows(config, exclude={
        row["id"] for row in connection.execute("SELECT id FROM candidates")
    })
    orphan_status_counts: dict[str, int] = {}
    for item in orphan_reviews:
        orphan_status_counts[item["status"]] = orphan_status_counts.get(item["status"], 0) + 1
    total += len(orphan_reviews)
    ready += orphan_status_counts.get("READY_FOR_REVIEW", 0)
    approved += orphan_status_counts.get("APPROVED", 0)
    merged_status_counts = dict(parent_status_counts)
    for key, value in orphan_status_counts.items():
        merged_status_counts[key] = merged_status_counts.get(key, 0) + value
    published = connection.execute(
        "SELECT COUNT(DISTINCT candidate_id) count FROM publications WHERE status='PUBLISHED'"
    ).fetchone()["count"]
    metrics = dict(latest_metrics)
    conversions = conversion_totals(connection)
    # Attributed events (server-side postbacks) take precedence over manually
    # entered platform snapshots wherever both exist.
    clicks = conversions["landing_click"] or metrics["clicks"]
    installs = conversions["install"] or metrics["installs"]
    registrations = conversions["registration"] or metrics["registrations"]
    first_watch = conversions["first_watch"]
    conversion_rate = registrations / clicks if clicks else 0.0
    activation_rate = first_watch / registrations if registrations else 0.0
    return {
        "generated_at": now_iso(),
        "kpis": {
            "inventory": total,
            "ready": ready,
            "approved": approved,
            "scheduled": publication_counts.get("QUEUED", 0) + publication_counts.get("SCHEDULED", 0),
            "published": published,
            **metrics,
            "clicks": clicks,
            "installs": installs,
            "registrations": registrations,
            "first_watch": first_watch,
            "download_started": conversions["download_started"],
            "conversion_rate": conversion_rate,
            "activation_rate": activation_rate,
        },
        "funnel": [
            {"label": "发现素材", "value": total},
            {"label": "完成制作", "value": ready + approved + published},
            {"label": "审核通过", "value": approved + published},
            {"label": "已发布", "value": published},
            {"label": "落地页点击", "value": clicks},
            {"label": "开始下载", "value": conversions["download_started"]},
            {"label": "安装", "value": installs},
            {"label": "注册", "value": registrations},
            {"label": "首次观看", "value": first_watch},
        ],
        "candidate_statuses": merged_status_counts,
        "publication_statuses": publication_counts,
        "platforms": platform_performance(connection),
        "keywords": keyword_performance(connection),
    }


def platform_performance(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY candidate_id,platform ORDER BY captured_at DESC,id DESC
          ) rank
          FROM performance_snapshots
        ), metric AS (
          SELECT platform,SUM(views) views,SUM(clicks) clicks,SUM(installs) installs,
                 SUM(registrations) registrations,SUM(likes+comments+shares) engagements
          FROM ranked WHERE rank=1 GROUP BY platform
        ), posted AS (
          SELECT platform,COUNT(*) posts FROM publications WHERE status='PUBLISHED' GROUP BY platform
        )
        SELECT COALESCE(metric.platform,posted.platform) platform,
               COALESCE(posts,0) posts,COALESCE(views,0) views,
               COALESCE(clicks,0) clicks,COALESCE(installs,0) installs,
               COALESCE(registrations,0) registrations,COALESCE(engagements,0) engagements
        FROM metric LEFT JOIN posted ON posted.platform=metric.platform
        UNION ALL
        SELECT posted.platform,posts,0,0,0,0,0 FROM posted
        WHERE posted.platform NOT IN (SELECT platform FROM metric)
        """
    ).fetchall()
    mapped = {row["platform"]: dict(row) for row in rows}
    return [mapped.get(platform, {"platform": platform, "posts": 0, "views": 0, "clicks": 0, "installs": 0, "registrations": 0, "engagements": 0}) for platform in PLATFORMS]


def keyword_performance(connection: Any) -> list[dict[str, Any]]:
    metric_rows = connection.execute(
        """
        WITH ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY candidate_id,platform ORDER BY captured_at DESC,id DESC
          ) rank
          FROM performance_snapshots
        )
        SELECT candidate_id,SUM(views) views,SUM(clicks) clicks,SUM(registrations) registrations
        FROM ranked WHERE rank=1 GROUP BY candidate_id
        """
    ).fetchall()
    metrics = {row["candidate_id"]: dict(row) for row in metric_rows}
    conversion_rows = connection.execute(
        "SELECT candidate_id,event_type,COUNT(*) count FROM conversion_events GROUP BY candidate_id,event_type"
    ).fetchall()
    conversions: dict[str, dict[str, int]] = {}
    for row in conversion_rows:
        conversions.setdefault(row["candidate_id"], {})[row["event_type"]] = row["count"]
    grouped: dict[str, dict[str, Any]] = {}
    for row in connection.execute("SELECT id,status,metadata_json FROM candidates"):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        keyword = str(metadata.get("keyword") or "未标记关键词")
        entry = grouped.setdefault(keyword, {
            "keyword": keyword, "candidates": 0, "produced": 0, "views": 0,
            "clicks": 0, "registrations": 0, "first_watch": 0,
        })
        entry["candidates"] += 1
        entry["produced"] += int(row["status"] in {"READY_FOR_REVIEW", "APPROVED"})
        candidate_metrics = metrics.get(row["id"], {})
        candidate_conversions = conversions.get(row["id"], {})
        entry["views"] += int(candidate_metrics.get("views", 0))
        entry["clicks"] += int(candidate_conversions.get("landing_click", 0) or candidate_metrics.get("clicks", 0))
        entry["registrations"] += int(candidate_conversions.get("registration", 0) or candidate_metrics.get("registrations", 0))
        entry["first_watch"] += int(candidate_conversions.get("first_watch", 0))
    values = list(grouped.values())
    for value in values:
        value["score"] = round(
            value["first_watch"] * 40 + value["registrations"] * 20
            + value["clicks"] * 0.5 + value["views"] * 0.001,
            2,
        )
    return sorted(values, key=lambda item: (item["score"], item["candidates"]), reverse=True)[:12]


def review_output_index(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    workspace_root = workspace_dir(config) / "ready_for_review"
    server_root = storage_root(config) / "review"
    package_ids: set[str] = set()
    for root in (workspace_root, server_root):
        if root.exists():
            package_ids.update(
                path.name for path in root.iterdir()
                if path.is_dir() and any(child.is_file() and child.suffix.lower() == ".mp4" for child in path.iterdir())
            )

    index: dict[str, list[dict[str, Any]]] = {}
    for package_id in sorted(package_ids):
        local_dir = workspace_root / package_id
        server_dir = server_root / package_id
        local_video = local_dir / "video.mp4"
        server_video = server_dir / "video.mp4"
        local_first_video = next((path for path in sorted(local_dir.glob("*.mp4")) if path.is_file()), None)
        server_first_video = next((path for path in sorted(server_dir.glob("*.mp4")) if path.is_file()), None)
        if local_video.is_file():
            media_dir = local_dir
            media_relative = f"{package_id}/video.mp4"
        elif server_video.is_file():
            media_dir = server_dir
            media_relative = f"review/{package_id}/video.mp4"
        elif local_first_video is not None:
            media_dir = local_dir
            media_relative = f"{package_id}/{local_first_video.name}"
        elif server_first_video is not None:
            media_dir = server_dir
            media_relative = f"review/{package_id}/{server_first_video.name}"
        else:
            continue

        metadata_path = local_dir / "metadata.json"
        if not metadata_path.is_file():
            metadata_path = server_dir / "metadata.json"
        server_files: dict[str, Any] = {}
        review_metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                review_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                server_files = review_metadata.get("server_storage", {}).get("files", {}) or {}
            except (json.JSONDecodeError, OSError):
                pass
        batch_label = str(review_metadata.get("batch_label") or "").strip()
        strategy = review_metadata.get("strategy") or {}
        content_type = str(strategy.get("content_type") or review_metadata.get("content_type") or "").strip()

        cover = media_dir / "cover.jpg"
        cover_url = ""
        if cover.is_file():
            cover_relative = media_relative.rsplit("/", 1)[0] + "/cover.jpg"
            cover_url = f"/media/{quote(cover_relative, safe='/')}?v={int(cover.stat().st_mtime)}"

        part_match = PART_PACKAGE_PATTERN.match(package_id)
        part_number = int(part_match.group("number")) if part_match else None
        variant_files = sorted(
            path for path in media_dir.glob("*.mp4")
            if path.name != "video.mp4" and "FB版" not in path.name
        )
        mp4_files = variant_files or [media_dir / "video.mp4"]
        for video in [path for path in mp4_files if path.is_file()]:
            version = int(video.stat().st_mtime)
            video_relative = media_relative.rsplit("/", 1)[0] + f"/{video.name}"
            video_url = f"/media/{quote(video_relative, safe='/')}?v={version}"
            server_url = str(server_files.get(video.name, {}).get("url") or "")
            if not server_url and server_video.is_file():
                server_url = public_url(config, f"review/{package_id}/{video.name}")
            metadata_variant = str(review_metadata.get("variant") or "").strip()
            variant = (
                metadata_variant
                if video.name == "video.mp4" and metadata_variant
                else "通用版" if "通用版" in video.name or video.name == "video.mp4"
                else ""
            )
            file_batch_label = batch_label or ("文案设计版" if "文案设计版" in video.name else "")
            is_batch_output = bool(file_batch_label) and (
                file_batch_label in video.name or (video.name == "video.mp4" and content_type == "design_overlay")
            )
            base_label = (
                file_batch_label
                if is_batch_output
                else f"片段 {part_number:02d}" if part_number is not None else "成片"
            )
            asset = {
                "id": package_id if video.name == "video.mp4" else f"{package_id}:{video.stem}",
                "label": f"{base_label} · {variant}" if variant else base_label,
                "batch_label": file_batch_label if is_batch_output else "",
                "content_type": content_type,
                "variant": variant,
                "part_number": part_number,
                "video_url": video_url,
                "download_url": f"{video_url}&download=1",
                "server_url": server_url,
                "cover_url": cover_url,
                "filename": f"{package_id}.mp4" if video.name == "video.mp4" else video.name,
                "_path": str(video),
                "_metadata_path": str(metadata_path) if metadata_path.is_file() else "",
            }
            index.setdefault(package_id, []).append(asset)
            if part_match:
                index.setdefault(part_match.group("parent"), []).append(asset)

    for assets in index.values():
        unique = {asset["id"]: asset for asset in assets}
        assets[:] = sorted(
            unique.values(),
            key=lambda asset: (
                asset["part_number"] is not None,
                asset["part_number"] if asset["part_number"] is not None else 0,
                0 if asset.get("variant") == "通用版" else 1,
                asset["id"],
            ),
        )
    return index


def public_output_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in asset.items() if not key.startswith("_")}


def download_claim_rows(config: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT dc.*,c.title
            FROM download_claims dc
            LEFT JOIN candidates c ON c.id=dc.candidate_id
            ORDER BY dc.downloaded_at DESC,id DESC LIMIT ?
            """,
            (limit,),
        )
    ]


def save_download_claim(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").split(":", 1)[0].strip()
    asset_id = str(payload.get("asset_id") or candidate).strip()
    filename = Path(str(payload.get("filename") or "")).name
    variant = str(payload.get("variant") or "").strip()
    publisher = str(payload.get("publisher") or "").strip()
    publish_platform = str(payload.get("publish_platform") or "").strip().lower()
    if not candidate or not publisher:
        raise ValueError("candidate_id and publisher are required before downloading")
    if publish_platform and publish_platform not in PUBLISH_TARGETS:
        raise ValueError(f"publish_platform must be one of {PUBLISH_TARGETS}")
    connection = connect_db(config)
    row = connection.execute("SELECT id,status FROM candidates WHERE id=?", (candidate,)).fetchone()
    server_package = storage_root(config) / "review" / candidate
    local_package = workspace_dir(config) / "ready_for_review" / candidate
    if not row and not server_package.exists() and not local_package.exists():
        raise ValueError("candidate does not exist")
    if row and row["status"] != "APPROVED":
        raise ValueError(f"candidate must be APPROVED before downloading (current status: {row['status']})")
    timestamp = now_iso()
    cursor = connection.execute(
        """
        INSERT INTO download_claims(
          candidate_id,asset_id,filename,variant,publisher,publish_platform,note,downloaded_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            candidate,
            asset_id,
            filename,
            variant,
            publisher,
            publish_platform,
            str(payload.get("note") or "").strip(),
            timestamp,
        ),
    )
    connection.commit()
    return {"id": int(cursor.lastrowid), "candidate_id": candidate, "downloaded_at": timestamp}


def update_download_claim_metrics(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    claim_id = int_value(payload.get("claim_id"))
    if not claim_id:
        raise ValueError("claim_id is required")
    metrics = {field: int_value(payload.get(field)) for field in ("views", "clicks", "registrations")}
    extra = payload.get("extra_data") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra_data must be an object")
    connection = connect_db(config)
    row = connection.execute("SELECT candidate_id,publish_platform FROM download_claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise ValueError("download claim does not exist")
    timestamp = now_iso()
    connection.execute(
        """
        UPDATE download_claims
        SET views=?,clicks=?,registrations=?,extra_data=?,metrics_updated_at=?
        WHERE id=?
        """,
        (
            metrics["views"], metrics["clicks"], metrics["registrations"],
            json.dumps(extra, ensure_ascii=False), timestamp, claim_id,
        ),
    )
    if row["publish_platform"] in PLATFORMS:
        connection.execute(
            """
            INSERT INTO performance_snapshots(
              candidate_id,platform,captured_at,views,clicks,registrations
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                row["candidate_id"], row["publish_platform"], timestamp,
                metrics["views"], metrics["clicks"], metrics["registrations"],
            ),
        )
    connection.commit()
    return {"id": claim_id, "updated_at": timestamp}


def safe_remove_tree(path: Path, allowed_roots: list[Path]) -> int:
    try:
        resolved = path.resolve()
    except OSError:
        return 0
    allowed = [root.resolve() for root in allowed_roots]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError(f"refusing to delete outside managed storage: {path}")
    if resolved.is_dir():
        size = sum(child.stat().st_size for child in resolved.rglob("*") if child.is_file())
        shutil.rmtree(resolved)
        return size
    if resolved.is_file():
        size = resolved.stat().st_size
        resolved.unlink()
        return size
    return 0


def candidate_package_ids(candidate: str) -> set[str]:
    return {candidate}


def collect_package_ids_for_candidate(config: dict[str, Any], candidate: str) -> set[str]:
    ids = candidate_package_ids(candidate)
    roots = [workspace_dir(config) / "ready_for_review", storage_root(config) / "review"]
    for root in roots:
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_dir() and (path.name == candidate or path.name.startswith(f"{candidate}_part")):
                ids.add(path.name)
                continue
            metadata_path = path / "metadata.json"
            if not path.is_dir() or not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            metadata_ids = {
                str(metadata.get("job_id") or ""),
                str(metadata.get("source_job_id") or ""),
                str(metadata.get("source_candidate_id") or ""),
            }
            source = metadata.get("source") or {}
            if isinstance(source, dict):
                metadata_ids.add(str(source.get("candidate_id") or ""))
            if candidate in metadata_ids:
                ids.add(path.name)
    return ids


def inventory_paths_from_package(package_dir: Path) -> list[Path]:
    metadata_path = package_dir / "metadata.json"
    if not metadata_path.is_file():
        return []
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    paths: list[Path] = []
    for variant in metadata.get("output_variants") or []:
        if isinstance(variant, dict) and variant.get("inventory_path"):
            paths.append(Path(str(variant["inventory_path"])).expanduser())
    return paths


def delete_candidates(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    raw_ids = payload.get("candidate_ids")
    if raw_ids is None:
        raw_ids = [payload.get("candidate_id") or payload.get("id")]
    if not isinstance(raw_ids, list):
        raise ValueError("candidate_ids must be a list")
    candidate_ids = [
        str(candidate).split(":", 1)[0].strip()
        for candidate in raw_ids
        if str(candidate or "").strip()
    ]
    candidate_ids = list(dict.fromkeys(candidate_ids))
    if not candidate_ids:
        raise ValueError("select at least one candidate")

    workspace = workspace_dir(config)
    storage = storage_root(config)
    inventory = inventory_root(config)
    managed_roots = [
        workspace / "jobs",
        workspace / "ready_for_review",
        storage / "review",
        inventory,
    ]
    deleted_bytes = 0
    deleted_items: list[dict[str, Any]] = []
    connection = connect_db(config)
    for candidate in candidate_ids:
        package_ids = collect_package_ids_for_candidate(config, candidate)
        inventory_paths: list[Path] = []
        for package_id in package_ids:
            for root in (workspace / "ready_for_review", storage / "review"):
                package_dir = root / package_id
                if package_dir.exists():
                    inventory_paths.extend(inventory_paths_from_package(package_dir))
        removed_paths: list[str] = []
        for inventory_path in inventory_paths:
            if inventory_path.exists():
                deleted_bytes += safe_remove_tree(inventory_path, managed_roots)
                removed_paths.append(str(inventory_path))
                parent = inventory_path.parent.resolve()
                inventory_boundary = inventory.resolve()
                while parent != parent.parent and parent != inventory_boundary:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        for package_id in package_ids:
            for path in (workspace / "ready_for_review" / package_id, storage / "review" / package_id):
                if path.exists():
                    deleted_bytes += safe_remove_tree(path, managed_roots)
                    removed_paths.append(str(path))
        job_dir = workspace / "jobs" / candidate
        if job_dir.exists():
            deleted_bytes += safe_remove_tree(job_dir, managed_roots)
            removed_paths.append(str(job_dir))
        for table in (
            "events",
            "publications",
            "performance_snapshots",
            "conversion_events",
            "feedback_actions",
            "render_jobs",
        ):
            connection.execute(f"DELETE FROM {table} WHERE candidate_id=?", (candidate,))
        cursor = connection.execute("DELETE FROM candidates WHERE id=?", (candidate,))
        deleted_items.append({
            "candidate_id": candidate,
            "removed_from_db": bool(cursor.rowcount),
            "package_ids": sorted(package_ids),
            "paths": removed_paths,
        })
    connection.commit()
    return {
        "deleted": len(deleted_items),
        "bytes_freed": deleted_bytes,
        "items": deleted_items,
    }


def source_keyword_metadata(connection: Any, candidate_id: str) -> dict[str, str]:
    if not candidate_id:
        return {}
    source_row = connection.execute(
        "SELECT metadata_json FROM candidates WHERE id=?", (candidate_id,)
    ).fetchone()
    if not source_row:
        return {}
    try:
        source_metadata = json.loads(source_row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return {
        "keyword": str(source_metadata.get("keyword") or ""),
        "category": str(source_metadata.get("category") or ""),
    }


def canonical_account_id(account: str) -> str:
    normalized = re.sub(r"\s+", " ", str(account or "").strip().lower())
    return ACCOUNT_ALIASES.get(normalized, normalized)


def youtube_category_account_routes(config: dict[str, Any]) -> dict[str, str]:
    configured = (config.get("publishing", {}) or {}).get("youtube_category_accounts") or {}
    routes = dict(DEFAULT_YOUTUBE_CATEGORY_ACCOUNTS)
    if isinstance(configured, dict):
        for category, account in configured.items():
            category_name = str(category or "").strip()
            account_id = str(account or "").strip()
            if category_name:
                routes[category_name] = account_id
    return routes


def publication_source_context(connection: Any, candidate_id: str) -> dict[str, str]:
    row = connection.execute(
        """
        SELECT id,parent_id,platform,url,title,description,metadata_json
        FROM candidates WHERE id=?
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        return {}
    parent_row = None
    parent_id = str(row["parent_id"] or "")
    if parent_id:
        parent_row = connection.execute(
            """
            SELECT id,platform,url,title,description,metadata_json
            FROM candidates WHERE id=?
            """,
            (parent_id,),
        ).fetchone()

    def metadata_for(item: Any) -> dict[str, Any]:
        if not item:
            return {}
        try:
            return json.loads(item["metadata_json"] or "{}")
        except json.JSONDecodeError:
            return {}

    metadata = metadata_for(row)
    parent_metadata = metadata_for(parent_row)
    source_row = parent_row or row
    source_blob = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
    source_platform = (
        str(source_row["platform"] or "")
        or str(source_blob.get("platform") or "")
        or platform_from_url(str(source_row["url"] or ""))
    ).strip().lower()
    source_url = str(source_row["url"] or source_blob.get("url") or "")
    source_keyword = str(metadata.get("keyword") or parent_metadata.get("keyword") or "")
    source_category = str(metadata.get("category") or parent_metadata.get("category") or "")
    category = initial_category_for_text(
        source_keyword,
        source_category,
        row["title"],
        row["description"],
        source_platform,
    )
    return {
        "source_platform": source_platform or platform_from_url(source_url),
        "source_url": source_url,
        "category": category,
        "keyword": source_keyword or source_category,
        "parent_id": parent_id,
    }


def resolve_publication_account(
    config: dict[str, Any],
    connection: Any,
    candidate_id: str,
    platform: str,
    requested_account: str,
) -> tuple[str, dict[str, str]]:
    account = str(requested_account or "").strip()
    context = publication_source_context(connection, candidate_id)
    if platform != "youtube":
        return account, context
    if account:
        return account, context
    category = context.get("category") or ""
    if category == "成人频道":
        raise ValueError("adult-category candidates cannot be auto-scheduled to YouTube")
    account = youtube_category_account_routes(config).get(category, "")
    if not account:
        raise ValueError(f"cannot auto-select a YouTube account for category: {category or 'unknown'}")
    return account, context


def assert_youtube_source_allowed(platform: str, account: str, context: dict[str, str]) -> None:
    if platform != "youtube":
        return
    account_id = canonical_account_id(account)
    if account_id not in YOUTUBE_SOURCE_BLOCKED_ACCOUNTS:
        return
    source_platform = str(context.get("source_platform") or "").strip().lower()
    source_url = str(context.get("source_url") or "")
    if source_platform == "youtube" or platform_from_url(source_url) == "youtube":
        raise ValueError(
            f"YouTube source candidates cannot be scheduled to YouTube account {account_id}; "
            "choose a non-YouTube source or a non-YouTube publish platform"
        )


def youtube_oauth_redirect_uri(config: dict[str, Any]) -> str:
    explicit = os.environ.get("JAGUARTV_GOOGLE_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_base = (
        os.environ.get("JAGUARTV_PUBLIC_BASE_URL", "").strip()
        or str((config.get("server", {}) or {}).get("public_base_url") or "").strip()
        or str((config.get("storage", {}) or {}).get("dashboard_base_url") or "").strip()
        or "https://factory.jarg.top"
    )
    return public_base.rstrip("/") + "/oauth/youtube/callback"


def youtube_oauth_credentials(config: dict[str, Any]) -> dict[str, str]:
    client_id = os.environ.get("JAGUARTV_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("JAGUARTV_GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = youtube_oauth_redirect_uri(config)
    missing = [
        name for name, value in {
            "JAGUARTV_GOOGLE_CLIENT_ID": client_id,
            "JAGUARTV_GOOGLE_CLIENT_SECRET": client_secret,
            "JAGUARTV_OAUTH_TOKEN_KEY": os.environ.get("JAGUARTV_OAUTH_TOKEN_KEY", "").strip(),
        }.items() if not value
    ]
    if missing:
        raise RuntimeError("missing OAuth server configuration: " + ", ".join(missing))
    return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}


def oauth_state_secret() -> str:
    secret = (
        os.environ.get("JAGUARTV_OAUTH_STATE_SECRET", "").strip()
        or os.environ.get("JAGUARTV_DASHBOARD_TOKEN", "").strip()
    )
    if not secret:
        raise RuntimeError("missing OAuth state secret: set JAGUARTV_OAUTH_STATE_SECRET")
    return secret


def sign_oauth_state(payload: str) -> str:
    return hmac.new(oauth_state_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_youtube_auth_link(account: str, expires_at: int) -> str:
    canonical = canonical_account_id(account)
    if canonical not in set(ACCOUNT_ALIASES.values()):
        raise ValueError(f"unknown YouTube account: {canonical or 'empty'}")
    payload = f"youtube-oauth-start:{canonical}:{int(expires_at)}"
    return sign_oauth_state(payload)


def youtube_auth_link_is_valid(query: dict[str, list[str]], *, now: int | None = None) -> bool:
    account = canonical_account_id(str((query.get("account") or [""])[0]))
    expires_raw = str((query.get("expires") or [""])[0]).strip()
    signature = str((query.get("signature") or [""])[0]).strip()
    if not account or not expires_raw or not signature:
        return False
    try:
        expires_at = int(expires_raw)
        expected = sign_youtube_auth_link(account, expires_at)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current or expires_at - current > YOUTUBE_AUTH_LINK_MAX_TTL_SECONDS:
        return False
    return hmac.compare_digest(signature, expected)


def youtube_auth_link(config: dict[str, Any], account: str, *, expires_at: int) -> str:
    canonical = canonical_account_id(account)
    callback = youtube_oauth_redirect_uri(config)
    callback_path = "/oauth/youtube/callback"
    if not callback.endswith(callback_path):
        raise ValueError("YouTube OAuth redirect URI must end with /oauth/youtube/callback")
    base_url = callback[:-len(callback_path)]
    params = {
        "account": canonical,
        "expires": str(int(expires_at)),
        "signature": sign_youtube_auth_link(canonical, expires_at),
    }
    return f"{base_url}/oauth/youtube/start?{urlencode(params)}"


def make_oauth_state(account: str) -> str:
    canonical = canonical_account_id(account or "consumer_football")
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(12)
    payload = f"{canonical}:{timestamp}:{nonce}"
    signature = sign_oauth_state(payload)
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii").rstrip("=")


def parse_oauth_state(value: str) -> str:
    if not value:
        raise ValueError("missing OAuth state; start from /oauth/youtube/start")
    padded = value + ("=" * (-len(value) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as error:
        raise ValueError("invalid OAuth state") from error
    account, timestamp, nonce, signature = decoded.rsplit(":", 3)
    payload = f"{account}:{timestamp}:{nonce}"
    if not hmac.compare_digest(signature, sign_oauth_state(payload)):
        raise ValueError("invalid OAuth state signature")
    try:
        issued_at = int(timestamp)
    except ValueError as error:
        raise ValueError("invalid OAuth state timestamp") from error
    if abs(int(time.time()) - issued_at) > 3600:
        raise ValueError("OAuth state expired; start authorization again")
    return canonical_account_id(account)


def encrypt_oauth_secret(secret_value: str) -> str:
    key = os.environ.get("JAGUARTV_OAUTH_TOKEN_KEY", "").strip()
    if not key:
        raise RuntimeError("missing JAGUARTV_OAUTH_TOKEN_KEY")
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("missing openssl; cannot encrypt OAuth token")
    result = subprocess.run(
        [
            openssl, "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-base64", "-A",
            "-pass", "env:JAGUARTV_OAUTH_TOKEN_KEY",
        ],
        input=secret_value,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "JAGUARTV_OAUTH_TOKEN_KEY": key},
    )
    if result.returncode != 0:
        raise RuntimeError("openssl failed to encrypt OAuth token")
    return result.stdout.strip()


def encrypt_refresh_token(refresh_token: str) -> str:
    return encrypt_oauth_secret(refresh_token)


def ensure_oauth_tables(config: dict[str, Any]) -> None:
    connection = connect_db(config)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS youtube_channel_auths (
          account TEXT PRIMARY KEY,
          channel_id TEXT NOT NULL DEFAULT '',
          channel_title TEXT NOT NULL DEFAULT '',
          scopes TEXT NOT NULL DEFAULT '',
          encrypted_refresh_token TEXT NOT NULL DEFAULT '',
          token_type TEXT NOT NULL DEFAULT '',
          expires_in INTEGER NOT NULL DEFAULT 0,
          authorized_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS x_oauth_states (
          state TEXT PRIMARY KEY,
          account TEXT NOT NULL,
          code_verifier TEXT NOT NULL,
          redirect_uri TEXT NOT NULL DEFAULT '',
          scopes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS x_account_auths (
          account TEXT PRIMARY KEY,
          x_user_id TEXT NOT NULL DEFAULT '',
          username TEXT NOT NULL DEFAULT '',
          display_name TEXT NOT NULL DEFAULT '',
          scopes TEXT NOT NULL DEFAULT '',
          encrypted_access_token TEXT NOT NULL DEFAULT '',
          encrypted_refresh_token TEXT NOT NULL DEFAULT '',
          token_type TEXT NOT NULL DEFAULT '',
          expires_in INTEGER NOT NULL DEFAULT 0,
          expires_at TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'PENDING_CONFIRMATION',
          authorized_at TEXT NOT NULL,
          confirmed_at TEXT,
          revoked_at TEXT,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    connection.commit()


def post_form_json(
    url: str,
    form: dict[str, str],
    *,
    timeout: int = 20,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_authorized_youtube_channel(access_token: str) -> dict[str, str]:
    query = urlencode({"part": "snippet", "mine": "true"})
    request = Request(
        f"{YOUTUBE_CHANNELS_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    items = payload.get("items") or []
    if not items:
        raise RuntimeError("authorized Google account did not return a YouTube channel")
    first = items[0]
    snippet = first.get("snippet") or {}
    return {
        "channel_id": str(first.get("id") or ""),
        "channel_title": str(snippet.get("title") or ""),
    }


def youtube_oauth_start_url(config: dict[str, Any], account: str = "consumer_football") -> str:
    credentials = youtube_oauth_credentials(config)
    account_id = canonical_account_id(account or "consumer_football")
    params = {
        "client_id": credentials["client_id"],
        "redirect_uri": credentials["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(YOUTUBE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": make_oauth_state(account_id),
    }
    return f"{GOOGLE_OAUTH_AUTH_URL}?{urlencode(params)}"


def save_youtube_oauth_callback(config: dict[str, Any], query: dict[str, list[str]]) -> dict[str, str]:
    if error := str((query.get("error") or [""])[0]).strip():
        raise ValueError(f"Google OAuth returned error: {error}")
    code = str((query.get("code") or [""])[0]).strip()
    if not code:
        raise ValueError("missing OAuth code")
    account = parse_oauth_state(str((query.get("state") or [""])[0]).strip())
    credentials = youtube_oauth_credentials(config)
    token = post_form_json(GOOGLE_OAUTH_TOKEN_URL, {
        "code": code,
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "redirect_uri": credentials["redirect_uri"],
        "grant_type": "authorization_code",
    })
    refresh_token = str(token.get("refresh_token") or "").strip()
    access_token = str(token.get("access_token") or "").strip()
    if not refresh_token:
        raise RuntimeError("Google did not return refresh_token; restart from /oauth/youtube/start")
    if not access_token:
        raise RuntimeError("Google did not return access_token")
    channel = get_authorized_youtube_channel(access_token)
    timestamp = now_iso()
    ensure_oauth_tables(config)
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO youtube_channel_auths(
          account,channel_id,channel_title,scopes,encrypted_refresh_token,
          token_type,expires_in,authorized_at,updated_at,metadata_json,status
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account) DO UPDATE SET
          channel_id=excluded.channel_id,
          channel_title=excluded.channel_title,
          scopes=excluded.scopes,
          encrypted_refresh_token=excluded.encrypted_refresh_token,
          token_type=excluded.token_type,
          expires_in=excluded.expires_in,
          updated_at=excluded.updated_at,
          metadata_json=excluded.metadata_json,
          status='AUTHORIZED',
          last_error_category='',
          last_error_summary=''
        """,
        (
            account,
            channel["channel_id"],
            channel["channel_title"],
            str(token.get("scope") or " ".join(YOUTUBE_OAUTH_SCOPES)),
            encrypt_refresh_token(refresh_token),
            str(token.get("token_type") or ""),
            int(token.get("expires_in") or 0),
            timestamp,
            timestamp,
            json.dumps({"provider": "google_oauth", "redirect_uri": credentials["redirect_uri"]}, ensure_ascii=False),
            "AUTHORIZED",
        ),
    )
    connection.commit()
    return {
        "account": account,
        "channel_id": channel["channel_id"],
        "channel_title": channel["channel_title"],
        "authorized_at": timestamp,
    }


def x_oauth_redirect_uri(config: dict[str, Any]) -> str:
    explicit = os.environ.get("JAGUARTV_X_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public_base = (
        os.environ.get("JAGUARTV_PUBLIC_BASE_URL", "").strip()
        or str((config.get("server", {}) or {}).get("public_base_url") or "").strip()
        or str((config.get("storage", {}) or {}).get("dashboard_base_url") or "").strip()
        or "https://factory.jarg.top"
    )
    return public_base.rstrip("/") + "/oauth/x/callback"


def x_oauth_credentials(config: dict[str, Any]) -> dict[str, str]:
    client_id = os.environ.get("JAGUARTV_X_CLIENT_ID", "").strip()
    client_secret = os.environ.get("JAGUARTV_X_CLIENT_SECRET", "").strip()
    redirect_uri = x_oauth_redirect_uri(config)
    missing = [
        name for name, value in {
            "JAGUARTV_X_CLIENT_ID": client_id,
            "JAGUARTV_OAUTH_TOKEN_KEY": os.environ.get("JAGUARTV_OAUTH_TOKEN_KEY", "").strip(),
        }.items() if not value
    ]
    if missing:
        raise RuntimeError("missing X OAuth server configuration: " + ", ".join(missing))
    return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}


def x_oauth_scopes() -> tuple[str, ...]:
    raw = os.environ.get("JAGUARTV_X_SCOPES", "").strip()
    scopes = tuple(part for part in raw.split() if part) if raw else DEFAULT_X_OAUTH_SCOPES
    if not scopes:
        raise RuntimeError("missing X OAuth scopes")
    for scope in scopes:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", scope):
            raise RuntimeError(f"invalid X OAuth scope: {scope}")
    return scopes


def pkce_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_x_oauth_state(config: dict[str, Any], account: str) -> dict[str, str]:
    account_id = canonical_account_id(account or "consumer_main")
    if account_id not in X_ACCOUNT_SLOTS:
        raise ValueError(f"unknown X account slot: {account_id}")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)[:96]
    scopes = " ".join(x_oauth_scopes())
    timestamp = now_iso()
    expires_at = datetime.fromtimestamp(time.time() + 3600, tz=timezone.utc).isoformat()
    ensure_oauth_tables(config)
    connection = connect_db(config)
    connection.execute(
        """
        INSERT INTO x_oauth_states(state,account,code_verifier,redirect_uri,scopes,created_at,expires_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (state, account_id, verifier, x_oauth_redirect_uri(config), scopes, timestamp, expires_at),
    )
    connection.commit()
    return {"state": state, "account": account_id, "code_verifier": verifier, "scopes": scopes}


def x_oauth_start_url(config: dict[str, Any], account: str = "consumer_main") -> str:
    credentials = x_oauth_credentials(config)
    state = make_x_oauth_state(config, account)
    params = {
        "response_type": "code",
        "client_id": credentials["client_id"],
        "redirect_uri": credentials["redirect_uri"],
        "scope": state["scopes"],
        "state": state["state"],
        "code_challenge": pkce_code_challenge(state["code_verifier"]),
        "code_challenge_method": "S256",
    }
    return f"{X_OAUTH_AUTH_URL}?{urlencode(params, quote_via=quote)}"


def consume_x_oauth_state(config: dict[str, Any], state: str) -> dict[str, str]:
    if not state:
        raise ValueError("missing OAuth state; start from /oauth/x/start")
    ensure_oauth_tables(config)
    connection = connect_db(config)
    row = connection.execute("SELECT * FROM x_oauth_states WHERE state=?", (state,)).fetchone()
    if not row:
        raise ValueError("invalid X OAuth state")
    if row["used_at"]:
        raise ValueError("X OAuth state was already used; start authorization again")
    try:
        expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid X OAuth state expiry") from error
    if datetime.now(timezone.utc) > expires_at:
        raise ValueError("X OAuth state expired; start authorization again")
    connection.execute("UPDATE x_oauth_states SET used_at=? WHERE state=?", (now_iso(), state))
    connection.commit()
    return {
        "account": str(row["account"] or ""),
        "code_verifier": str(row["code_verifier"] or ""),
        "redirect_uri": str(row["redirect_uri"] or ""),
        "scopes": str(row["scopes"] or ""),
    }


def x_token_headers(credentials: dict[str, str]) -> dict[str, str]:
    client_secret = credentials.get("client_secret", "")
    if not client_secret:
        return {}
    raw = f"{credentials['client_id']}:{client_secret}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def get_authorized_x_user(access_token: str) -> dict[str, str]:
    query = urlencode({"user.fields": "id,name,username,profile_image_url"})
    request = Request(
        f"{X_USERS_ME_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or {}
    user_id = str(data.get("id") or "")
    username = str(data.get("username") or "")
    if not user_id or not username:
        raise RuntimeError("authorized X account did not return user id and username")
    return {
        "x_user_id": user_id,
        "username": username,
        "display_name": str(data.get("name") or username),
    }


def save_x_oauth_callback(config: dict[str, Any], query: dict[str, list[str]]) -> dict[str, str]:
    if error := str((query.get("error") or [""])[0]).strip():
        raise ValueError(f"X OAuth returned error: {error}")
    code = str((query.get("code") or [""])[0]).strip()
    if not code:
        raise ValueError("missing OAuth code")
    state = consume_x_oauth_state(config, str((query.get("state") or [""])[0]).strip())
    credentials = x_oauth_credentials(config)
    token_form = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": credentials["client_id"],
        "redirect_uri": state["redirect_uri"] or credentials["redirect_uri"],
        "code_verifier": state["code_verifier"],
    }
    token = post_form_json(
        X_OAUTH_TOKEN_URL,
        token_form,
        headers=x_token_headers(credentials),
    )
    access_token = str(token.get("access_token") or "").strip()
    refresh_token = str(token.get("refresh_token") or "").strip()
    if not access_token:
        raise RuntimeError("X did not return access_token")
    if not refresh_token:
        raise RuntimeError("X did not return refresh_token; confirm offline.access is enabled")
    user = get_authorized_x_user(access_token)
    timestamp = now_iso()
    expires_in = int(token.get("expires_in") or 0)
    expires_at = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).isoformat() if expires_in else ""
    scopes = str(token.get("scope") or state["scopes"] or " ".join(x_oauth_scopes()))
    ensure_oauth_tables(config)
    connection = connect_db(config)
    encrypted_access = encrypt_oauth_secret(access_token)
    encrypted_refresh = encrypt_oauth_secret(refresh_token)
    metadata_json = json.dumps(
        {"provider": "x_oauth_pkce", "redirect_uri": state["redirect_uri"]},
        ensure_ascii=False,
    )
    duplicate = connection.execute(
        """
        SELECT account,status,confirmed_at
        FROM x_account_auths
        WHERE x_user_id=? AND account<>?
        ORDER BY CASE status WHEN 'AUTHORIZED' THEN 0 WHEN 'PENDING_CONFIRMATION' THEN 1 ELSE 2 END,
                 updated_at DESC
        LIMIT 1
        """,
        (user["x_user_id"], state["account"]),
    ).fetchone()
    result_account = state["account"]
    result_status = "PENDING_CONFIRMATION"
    if duplicate:
        result_account = str(duplicate["account"] or "")
        result_status = "AUTHORIZED" if str(duplicate["status"] or "") == "AUTHORIZED" else "PENDING_CONFIRMATION"
        confirmed_at = str(duplicate["confirmed_at"] or "") if result_status == "AUTHORIZED" else ""
        connection.execute(
            """
            UPDATE x_account_auths
            SET username=?,display_name=?,scopes=?,encrypted_access_token=?,encrypted_refresh_token=?,
                token_type=?,expires_in=?,expires_at=?,status=?,authorized_at=?,confirmed_at=?,
                revoked_at='',updated_at=?,metadata_json=?
            WHERE account=?
            """,
            (
                user["username"], user["display_name"], scopes, encrypted_access, encrypted_refresh,
                str(token.get("token_type") or ""), expires_in, expires_at, result_status, timestamp,
                confirmed_at, timestamp, metadata_json, result_account,
            ),
        )
    else:
        connection.execute(
            """
            INSERT INTO x_account_auths(
              account,x_user_id,username,display_name,scopes,encrypted_access_token,
              encrypted_refresh_token,token_type,expires_in,expires_at,status,
              authorized_at,confirmed_at,revoked_at,updated_at,metadata_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account) DO UPDATE SET
              x_user_id=excluded.x_user_id,
              username=excluded.username,
              display_name=excluded.display_name,
              scopes=excluded.scopes,
              encrypted_access_token=excluded.encrypted_access_token,
              encrypted_refresh_token=excluded.encrypted_refresh_token,
              token_type=excluded.token_type,
              expires_in=excluded.expires_in,
              expires_at=excluded.expires_at,
              status=excluded.status,
              authorized_at=excluded.authorized_at,
              confirmed_at='',
              revoked_at='',
              updated_at=excluded.updated_at,
              metadata_json=excluded.metadata_json
            """,
            (
                state["account"], user["x_user_id"], user["username"], user["display_name"], scopes,
                encrypted_access, encrypted_refresh, str(token.get("token_type") or ""), expires_in,
                expires_at, "PENDING_CONFIRMATION", timestamp, "", "", timestamp, metadata_json,
            ),
        )
    connection.commit()
    return {
        "account": result_account,
        "requested_account": state["account"],
        "x_user_id": user["x_user_id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "authorized_at": timestamp,
        "status": result_status,
        "duplicate": bool(duplicate),
    }


def x_auth_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_oauth_tables(config)
    connection = connect_db(config)
    rows = []
    for row in connection.execute(
        """
        SELECT account,x_user_id,username,display_name,scopes,token_type,expires_in,
               expires_at,status,authorized_at,confirmed_at,revoked_at,updated_at
        FROM x_account_auths
        ORDER BY account
        """
    ):
        item = dict(row)
        item["authorization_url"] = f"/oauth/x/start?account={quote(str(row['account'] or ''), safe='')}"
        rows.append(item)
    return rows


def verify_x_auths(config: dict[str, Any], accounts: list[str] | None = None) -> dict[str, Any]:
    requested = [canonical_account_id(account) for account in (accounts or list(X_ACCOUNT_SLOTS))]
    if not requested or any(account not in X_ACCOUNT_SLOTS for account in requested):
        raise ValueError("unknown X account slot")
    availability = {item["id"]: item for item in list_publish_accounts(config, "x")}
    results: list[dict[str, str]] = []
    from .x_publisher import x_access_token

    for account in X_ACCOUNT_SLOTS:
        if account not in requested:
            continue
        item = availability.get(account) or {
            "id": account,
            "username": "",
            "status": "UNAVAILABLE",
            "status_reason": "X 账号尚未授权",
        }
        username = str(item.get("username") or "")
        if item.get("status") != "AVAILABLE":
            results.append({
                "account": account,
                "username": username,
                "status": "UNAVAILABLE",
                "reason": str(item.get("status_reason") or "X 账号不可用"),
            })
            continue
        try:
            x_access_token(config, account)
        except Exception as error:
            reason = str(error)[:240] or "X token refresh failed"
            if any(marker in reason for marker in ("HTTP 400", "HTTP 401", "no refresh token")):
                connection = connect_db(config)
                connection.execute(
                    "UPDATE x_account_auths SET status='NEEDS_REAUTH',updated_at=? WHERE account=?",
                    (now_iso(), account),
                )
                connection.commit()
            results.append({"account": account, "username": username, "status": "FAILED", "reason": reason})
        else:
            results.append({"account": account, "username": username, "status": "AVAILABLE", "reason": ""})
    verified = sum(item["status"] == "AVAILABLE" for item in results)
    return {"verified": verified, "failed": len(results) - verified, "results": results}


def update_x_auth(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    account = canonical_account_id(str(payload.get("account") or ""))
    action = str(payload.get("action") or "").strip().lower()
    if action == "verify_all":
        return verify_x_auths(config)
    if account not in X_ACCOUNT_SLOTS or action not in {"confirm", "revoke", "verify"}:
        raise ValueError("account and action=confirm|revoke|verify are required")
    if action == "verify":
        return verify_x_auths(config, [account])
    ensure_oauth_tables(config)
    connection = connect_db(config)
    row = connection.execute(
        """
        SELECT account,x_user_id,scopes,encrypted_access_token,encrypted_refresh_token,status
        FROM x_account_auths WHERE account=?
        """,
        (account,),
    ).fetchone()
    if not row:
        raise ValueError("X authorization does not exist for this account")
    timestamp = now_iso()
    if action == "confirm":
        missing = sorted(X_REQUIRED_PUBLISH_SCOPES - set(str(row["scopes"] or "").split()))
        if missing:
            raise ValueError("X authorization is missing required scopes: " + ", ".join(missing))
        if not str(row["encrypted_access_token"] or "") or not str(row["encrypted_refresh_token"] or ""):
            raise ValueError("X authorization is missing access token or refresh token")
        duplicate = connection.execute(
            """
            SELECT account FROM x_account_auths
            WHERE x_user_id=? AND account<>? AND status='AUTHORIZED'
            LIMIT 1
            """,
            (str(row["x_user_id"] or ""), account),
        ).fetchone()
        if duplicate:
            raise ValueError(f"X user is already authorized in slot {duplicate['account']}")
        connection.execute(
            "UPDATE x_account_auths SET status='AUTHORIZED',confirmed_at=?,revoked_at='',updated_at=? WHERE account=?",
            (timestamp, timestamp, account),
        )
    else:
        connection.execute(
            """
            UPDATE x_account_auths
            SET status='REVOKED',encrypted_access_token='',encrypted_refresh_token='',
                revoked_at=?,updated_at=?
            WHERE account=?
            """,
            (timestamp, timestamp, account),
        )
    connection.commit()
    updated = [item for item in x_auth_rows(config) if item["account"] == account][0]
    return updated


def oauth_result_html(title: str, lines: list[str], *, ok: bool) -> str:
    color = "#137333" if ok else "#b3261e"
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px; line-height: 1.6; color: #202124; }}
    h1 {{ color: {color}; }}
    code {{ background: #f1f3f4; padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <ul>{items}</ul>
</body>
</html>"""


def sign_admin_session(token: str, expires_at: int) -> str:
    payload = f"v1.{expires_at}"
    signature = hmac.new(
        token.encode("utf-8"),
        f"{ADMIN_COOKIE_NAME}:{payload}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def admin_session_is_valid(token: str, session: str, *, now: int | None = None) -> bool:
    if not token or not session:
        return False
    version, separator, remainder = session.partition(".")
    expires_text, separator_two, provided_signature = remainder.partition(".")
    if version != "v1" or not separator or not separator_two:
        return False
    try:
        expires_at = int(expires_text)
    except ValueError:
        return False
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current or expires_at > current + ADMIN_SESSION_SECONDS + 60:
        return False
    expected = sign_admin_session(token, expires_at).rsplit(".", 1)[1]
    return secrets.compare_digest(provided_signature, expected)


def candidate_rows(
    config: dict[str, Any], status: str | None = None, limit: int | None = 100
) -> list[dict[str, Any]]:
    rows = list_candidates(config, status, limit)
    result = []
    connection = connect_db(config)
    imports_by_candidate = {
        str(row["candidate_id"]): dict(row)
        for row in connection.execute(
            "SELECT * FROM source_imports WHERE candidate_id != ''"
        )
    }
    unattached_imports = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM source_imports WHERE candidate_id = '' ORDER BY created_at DESC"
        )
    ]
    outputs_by_candidate = review_output_index(config)
    failures = {
        row["candidate_id"]: dict(row)
        for row in connection.execute(
            """
            SELECT e.candidate_id,e.event_type,e.payload_json,e.created_at
            FROM events e
            INNER JOIN (
              SELECT candidate_id,MAX(id) id FROM events
              WHERE event_type IN ('DOWNLOAD_FAILED','PRODUCTION_FAILED','QA_FAILED','BLOCKED_RIGHTS')
              GROUP BY candidate_id
            ) latest ON latest.id=e.id
            """
        )
    }
    collapsed_ids: set[str] = set()
    for row in rows:
        candidate_id = str(row["id"] or "")
        if should_collapse_review_slice(
            connection,
            outputs_by_candidate,
            candidate_id,
            str(row["parent_id"] or ""),
            str(row["source_id"] or ""),
        ):
            collapsed_ids.add(candidate_id)
            continue
        item = dict(row)
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(item.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            pass
        parent_metadata = source_keyword_metadata(connection, str(item.get("parent_id") or ""))
        source_keyword = str(metadata.get("keyword") or parent_metadata.get("keyword") or "")
        source_category = str(metadata.get("category") or parent_metadata.get("category") or "")
        item.pop("metadata_json", None)
        item["published_flag"] = bool(item.get("published_flag"))
        item["source_candidate_id"] = str(item.get("id") or "")
        source_import = imports_by_candidate.get(candidate_id) or {}
        source_import_metadata: dict[str, Any] = {}
        try:
            source_import_metadata = json.loads(source_import.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            pass
        selected_import_category = str(source_import_metadata.get("category") or "")
        if selected_import_category:
            source_category = selected_import_category
        item["source_type"] = str(source_import.get("source_type") or "")
        item["source_import_id"] = str(source_import.get("id") or "")
        item["import_method"] = str(source_import.get("import_method") or "")
        item["import_source"] = "导入视频" if source_import else ""
        item["target_area"] = str(source_import.get("target_area") or "")
        item["target_area_label"] = TARGET_LABELS.get(item["target_area"], "")
        item["imported_at"] = str(source_import.get("created_at") or "")
        item["download_status"] = str(source_import.get("download_status") or "")
        item["import_operator"] = str(source_import.get("operator_id") or "")
        item["review_source"] = str(source_import.get("review_source") or "")
        item["import_error_category"] = str(source_import.get("error_category") or "")
        item["import_error_summary"] = str(source_import.get("error_summary") or "")
        item["keyword"] = source_keyword
        inferred_category = (
            source_category
            if source_category == MATERIAL_CATEGORY_LABEL
            else initial_category_for_text(
                source_category,
                source_keyword,
                item.get("title"),
                item.get("description"),
            )
            if source_import
            else initial_category_for_text(
                source_keyword,
                source_category,
                item.get("title"),
                item.get("description"),
            )
        )
        item["initial_category"] = category_for_inventory_source(
            inferred_category, item["source_type"]
        )
        item["initial_keyword"] = source_keyword or source_category
        item["score_breakdown"] = metadata.get("score_breakdown") or {}
        analysis = metadata.get("analysis") or {}
        strategy = analysis.get("strategy") or {}
        item["content_type"] = str(strategy.get("content_type") or "unknown")
        item["segment_strategy"] = str(strategy.get("segment_strategy") or "")
        item["audio_policy"] = str(strategy.get("audio_policy") or "")
        segments = analysis.get("segments") or []
        item["highlight_score"] = max((float(segment.get("highlight_score") or 0) for segment in segments), default=0.0)
        thumbnail = metadata.get("thumbnail") or ""
        if not thumbnail and isinstance(metadata.get("thumbnails"), list) and metadata["thumbnails"]:
            last = metadata["thumbnails"][-1]
            thumbnail = last.get("url", "") if isinstance(last, dict) else ""
        item["thumbnail_url"] = str(thumbnail)
        outputs = outputs_by_candidate.get(str(item["id"]), [])
        primary_output = outputs[0] if outputs else {}
        item["output_assets"] = [public_output_asset(asset) for asset in outputs]
        item["output_count"] = len(outputs)
        item["display_title"] = (
            str(primary_output.get("filename") or "").removesuffix(".mp4")
            if primary_output else str(item.get("title") or "")
        )
        item["cover_url"] = str(primary_output.get("cover_url") or "")
        item["video_url"] = str(primary_output.get("video_url") or "")
        item["download_url"] = str(primary_output.get("download_url") or "")
        item["server_url"] = str(primary_output.get("server_url") or "")
        metadata_path = Path(str(primary_output.get("_metadata_path") or ""))
        if metadata_path.is_file():
            try:
                review_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                item["server_url"] = str(
                    review_metadata.get("server_storage", {}).get("files", {}).get("video.mp4", {}).get("url") or ""
                )
                item["content_type"] = str(review_metadata.get("content_type") or item["content_type"])
                item["segment_strategy"] = str(review_metadata.get("segment_strategy") or item["segment_strategy"])
                item["audio_policy"] = str(review_metadata.get("audio_policy") or item["audio_policy"])
                item["batch_label"] = str(review_metadata.get("batch_label") or "")
                item["highlight_score"] = float(
                    review_metadata.get("segment", {}).get("highlight_score") or item["highlight_score"]
                )
            except (json.JSONDecodeError, OSError):
                pass
        failure = failures.get(item["id"])
        item["failure_event"] = ""
        item["failure_detail"] = ""
        item["failure_at"] = ""
        if failure and item["status"] in {"DOWNLOAD_FAILED", "PRODUCTION_FAILED", "QA_FAILED", "BLOCKED_RIGHTS"}:
            try:
                failure_payload = json.loads(failure["payload_json"] or "{}")
            except json.JSONDecodeError:
                failure_payload = {}
            detail = str(failure_payload.get("error") or failure_payload.get("stderr") or "").strip()
            item["failure_event"] = failure["event_type"]
            item["failure_detail"] = detail[-4000:]
            item["failure_at"] = failure["created_at"]
        result.append(item)
    for source_import in unattached_imports:
        actual_status = str(source_import.get("actual_workflow_status") or "IMPORT_PENDING")
        if status and actual_status != status:
            continue
        normalized_url = str(source_import.get("normalized_url") or "")
        title = str(source_import.get("original_title") or normalized_url or "导入视频")
        result.append({
            "id": f"source-import:{source_import['id']}",
            "source_candidate_id": "",
            "source_import_placeholder": True,
            "platform": str(source_import.get("source_platform") or ""),
            "url": normalized_url,
            "title": title,
            "display_title": title,
            "description": "",
            "duration": float(source_import.get("duration_sec") or 0),
            "score": 0,
            "status": actual_status,
            "created_at": str(source_import.get("created_at") or ""),
            "updated_at": str(source_import.get("updated_at") or ""),
            "source_type": str(source_import.get("source_type") or SOURCE_TYPE),
            "source_import_id": str(source_import.get("id") or ""),
            "import_method": str(source_import.get("import_method") or ""),
            "import_source": "导入视频",
            "target_area": str(source_import.get("target_area") or ""),
            "target_area_label": TARGET_LABELS.get(str(source_import.get("target_area") or ""), ""),
            "imported_at": str(source_import.get("created_at") or ""),
            "download_status": str(source_import.get("download_status") or ""),
            "import_operator": str(source_import.get("operator_id") or ""),
            "review_source": str(source_import.get("review_source") or ""),
            "import_error_category": str(source_import.get("error_category") or ""),
            "import_error_summary": str(source_import.get("error_summary") or ""),
            "initial_category": "未分类",
            "initial_keyword": "",
            "keyword": "",
            "content_type": "external_import",
            "segment_strategy": "",
            "audio_policy": "",
            "highlight_score": 0,
            "thumbnail_url": "",
            "cover_url": "",
            "video_url": "",
            "download_url": "",
            "server_url": "",
            "output_assets": [],
            "output_count": 0,
            "published_flag": False,
            "publication_state": {},
            "score_breakdown": {},
            "failure_event": "",
            "failure_detail": str(source_import.get("error_summary") or ""),
            "failure_at": str(source_import.get("updated_at") or ""),
        })
    existing = {str(item.get("id") or "") for item in result} | collapsed_ids
    if status in {None, "", "READY_FOR_REVIEW", "APPROVED", "REVISION_REQUIRED"}:
        for item in server_review_rows(config, exclude=existing):
            if status and item["status"] != status:
                continue
            result.append(item)
            if limit is not None and len(result) >= limit:
                break
    publication_states = publication_state_for_candidates(
        config,
        [str(item.get("id") or "") for item in result if str(item.get("id") or "")],
    )
    for item in result:
        item["publication_state"] = publication_states.get(str(item.get("id") or ""), {})
    return result


def candidate_page(
    config: dict[str, Any],
    *,
    status: str | None = None,
    source_type: str = "",
    platform: str = "",
    category: str = "",
    search: str = "",
    sort: str = "time",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    source_type = str(source_type or "").strip()
    if source_type not in {"", FACTORY_SOURCE_TYPE, SOURCE_TYPE}:
        raise ValueError("unsupported source_type filter")
    platform = str(platform or "").strip().lower()
    category = str(category or "").strip()
    query = str(search or "").strip().lower()
    sort = str(sort or "time").strip().lower()
    if sort not in {"time", "updated"}:
        raise ValueError("unsupported inventory sort")
    rows = candidate_rows(config, status, None)

    def matches(item: dict[str, Any], *, include_source: bool = True) -> bool:
        if include_source:
            item_source_type = str(item.get("source_type") or "")
            if source_type == SOURCE_TYPE and item_source_type != SOURCE_TYPE:
                return False
            if source_type == FACTORY_SOURCE_TYPE and item_source_type == SOURCE_TYPE:
                return False
        if platform and str(item.get("platform") or "").lower() != platform:
            return False
        if category and str(item.get("initial_category") or "未分类") != category:
            return False
        if query:
            values = (
                item.get("display_title"), item.get("title"), item.get("id"),
                item.get("initial_category"), item.get("initial_keyword"), item.get("keyword"),
            )
            if query not in " ".join(str(value or "").lower() for value in values):
                return False
        return True

    base_rows = [item for item in rows if matches(item, include_source=False)]
    filtered = [item for item in base_rows if matches(item)]
    if sort == "updated":
        filtered.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    else:
        filtered.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("updated_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
    total = len(filtered)
    pages = (total + page_size - 1) // page_size
    if pages and page > pages:
        page = pages
    start = (page - 1) * page_size
    source_import_count = sum(
        1 for item in base_rows if str(item.get("source_type") or "") == SOURCE_TYPE
    )
    factory_count = len(base_rows) - source_import_count
    return {
        "items": filtered[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "source_counts": {"all": factory_count, SOURCE_TYPE: source_import_count},
        "filters": {
            "status": status or "",
            "source_type": source_type,
            "platform": platform,
            "category": category,
            "search": query,
            "sort": sort,
        },
    }


def server_review_rows(config: dict[str, Any], exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    review_root = storage_root(config) / "review"
    if not review_root.exists():
        return []
    connection = connect_db(config)
    outputs_by_candidate = review_output_index(config)
    items: list[dict[str, Any]] = []
    for metadata_path in sorted(review_root.glob("*/metadata.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        package_dir = metadata_path.parent
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        candidate = str(metadata.get("job_id") or package_dir.name)
        if candidate in exclude:
            continue
        source_candidate_id = str(metadata.get("source_job_id") or "").strip()
        if should_collapse_review_slice(connection, outputs_by_candidate, candidate, source_candidate_id, candidate):
            continue
        review_state = "READY_FOR_REVIEW"
        review_file = package_dir / "review.json"
        if review_file.exists():
            try:
                decision = str(json.loads(review_file.read_text(encoding="utf-8")).get("decision") or "").lower()
                if decision == "approved":
                    review_state = "APPROVED"
                elif decision in {"revision_required", "revision", "rejected"}:
                    review_state = "REVISION_REQUIRED"
            except (json.JSONDecodeError, OSError):
                pass
        source = metadata.get("source") or {}
        segment = metadata.get("segment") or {}
        strategy = {
            "content_type": metadata.get("content_type") or "unknown",
            "segment_strategy": metadata.get("segment_strategy") or "",
            "audio_policy": metadata.get("audio_policy") or "",
        }
        media_prefix = f"review/{package_dir.name}"
        video = package_dir / "video.mp4"
        cover = package_dir / "cover.jpg"
        outputs = outputs_by_candidate.get(package_dir.name, [])
        primary_output = outputs[0] if outputs else {}
        updated_at = datetime.fromtimestamp(metadata_path.stat().st_mtime, tz=timezone.utc).isoformat()
        source_keywords = source_keyword_metadata(connection, str(metadata.get("source_job_id") or ""))
        source_keyword = str(metadata.get("keyword") or source_keywords.get("keyword") or "")
        source_category = str(metadata.get("category") or source_keywords.get("category") or "")
        items.append({
            "id": candidate,
            "source_candidate_id": source_candidate_id or candidate,
            "platform": str(source.get("platform") or "server"),
            "source_id": source_candidate_id,
            "url": str(source.get("url") or ""),
            "title": str(source.get("title") or candidate),
            "display_title": str(primary_output.get("filename") or candidate).removesuffix(".mp4"),
            "description": "",
            "duration": float(segment.get("duration_sec") or 0),
            "view_count": 0,
            "detected_language": "",
            "score": 0,
            "status": review_state,
            "created_at": updated_at,
            "updated_at": updated_at,
            "keyword": source_keyword,
            "initial_category": (
                source_category
                if source_category == MATERIAL_CATEGORY_LABEL
                else initial_category_for_text(
                    source_keyword,
                    source_category,
                    source.get("title"),
                    source.get("platform"),
                )
            ),
            "initial_keyword": source_keyword or source_category,
            "score_breakdown": {},
            "batch_label": str(metadata.get("batch_label") or ""),
            "content_type": str(strategy["content_type"]),
            "segment_strategy": str(strategy["segment_strategy"]),
            "audio_policy": str(strategy["audio_policy"]),
            "highlight_score": float(segment.get("highlight_score") or 0),
            "thumbnail_url": "",
            "cover_url": str(primary_output.get("cover_url") or (
                f"/media/{media_prefix}/cover.jpg?v={int(cover.stat().st_mtime)}" if cover.exists() else ""
            )),
            "video_url": str(primary_output.get("video_url") or (
                f"/media/{media_prefix}/video.mp4?v={int(video.stat().st_mtime)}" if video.exists() else ""
            )),
            "download_url": str(primary_output.get("download_url") or ""),
            "server_url": str(primary_output.get("server_url") or (
                public_url(config, f"{media_prefix}/video.mp4") if video.exists() else ""
            )),
            "output_assets": [public_output_asset(asset) for asset in outputs],
            "output_count": len(outputs),
            "failure_event": "",
            "failure_detail": "",
            "failure_at": "",
            "published_flag": False,
        })
    return items


def publication_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    rows = [
        dict(row) for row in connection.execute(
            """
            SELECT publications.*,candidates.title FROM publications
            LEFT JOIN candidates ON candidates.id=publications.candidate_id
            ORDER BY COALESCE(publications.scheduled_at,publications.created_at) DESC LIMIT 200
            """
        )
    ]
    current_names: dict[str, str] = {}
    for platform in ("youtube", "x"):
        accounts = list_publish_accounts(config, platform)
        for account in accounts:
            name = str(account.get("display_name") or account.get("username") or account.get("id") or "")
            if name:
                current_names[(platform, str(account.get("id") or ""))] = name
    for item in rows:
        account = str(item.get("account") or "")
        label = current_names.get((str(item.get("platform") or ""), account))
        if label:
            item["account_label"] = label
    return rows


def worker_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [dict(row) for row in connection.execute("SELECT * FROM workers ORDER BY last_seen DESC")]


def render_job_rows(config: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    connection = connect_db(config)
    rows = []
    for row in connection.execute(
        """
        SELECT render_jobs.*,candidates.title
        FROM render_jobs
        LEFT JOIN candidates ON candidates.id=render_jobs.candidate_id
        ORDER BY render_jobs.updated_at DESC
        LIMIT ?
        """,
        (max(1, min(500, int(limit))),),
    ):
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        rows.append(item)
    return rows


def feedback_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = connect_db(config)
    return [dict(row) for row in connection.execute("SELECT * FROM feedback_actions ORDER BY id DESC LIMIT 100")]


def register_coordinator(config: dict[str, Any], port: int) -> None:
    connection = connect_db(config)
    host = socket.gethostname()
    connection.execute(
        """
        INSERT INTO workers(id,name,role,host,status,current_job,last_seen,metadata_json)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET status=excluded.status,last_seen=excluded.last_seen,
          current_job=excluded.current_job,metadata_json=excluded.metadata_json
        """,
        (f"{host}-dashboard", "本地控制台", "coordinator", host, "ONLINE", "", now_iso(), json.dumps({"port": port})),
    )
    connection.commit()


def save_publication(config: dict[str, Any], payload: dict[str, Any]) -> int:
    candidate = str(payload.get("candidate_id") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    if not candidate or platform not in PLATFORMS:
        raise ValueError("candidate_id and a supported platform are required")
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    if row["status"] != "APPROVED":
        raise ValueError(
            f"candidate must be APPROVED before scheduling (current status: {row['status']}); "
            "submit a review decision via POST /api/review first"
        )
    account, source_context = resolve_publication_account(
        config,
        connection,
        candidate,
        platform,
        str(payload.get("account") or ""),
    )
    assert_youtube_source_allowed(platform, account, source_context)
    timestamp = now_iso()
    cursor = connection.execute(
        """
        INSERT INTO publications(
          candidate_id,platform,account,scheduled_at,status,operation_type,review_status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate,
            platform,
            account,
            payload.get("scheduled_at") or None,
            "QUEUED",
            "PUBLICATION",
            "APPROVED",
            timestamp,
            timestamp,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def update_publication_status(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    publication_id = int_value(payload.get("publication_id"))
    status = str(payload.get("status") or "").strip().upper()
    if not publication_id or status not in {"QUEUED", "SCHEDULED", "PUBLISHED", "FAILED"}:
        raise ValueError("publication_id and a supported status are required")
    connection = connect_db(config)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT candidate_id FROM publications WHERE id=?", (publication_id,)
        ).fetchone()
        if not row:
            raise ValueError("publication does not exist")
        timestamp = now_iso()
        connection.execute(
            "UPDATE publications SET status=?,published_at=?,updated_at=? WHERE id=?",
            (status, timestamp if status == "PUBLISHED" else None, timestamp, publication_id),
        )
        if status == "PUBLISHED":
            connection.execute(
                "UPDATE candidates SET published_flag=1,updated_at=? WHERE id=?",
                (timestamp, row["candidate_id"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"publication_id": publication_id, "candidate_id": row["candidate_id"], "status": status}


def save_callback(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").strip()
    publisher = str(payload.get("publisher") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    if not candidate or not publisher:
        raise ValueError("candidate_id and publisher are required")
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {PLATFORMS}")
    metrics: dict[str, int] = {}
    for field in ("views", "clicks", "registrations"):
        value = payload.get(field, 0)
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a non-negative integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} must be a non-negative integer") from error
        if parsed < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        metrics[field] = parsed
    callback_at = str(payload.get("timestamp") or now_iso()).strip()
    try:
        datetime.fromisoformat(callback_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO8601") from error
    extra = payload.get("extra_data") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra_data must be an object")
    connection = connect_db(config)
    if not connection.execute("SELECT 1 FROM candidates WHERE id=?", (candidate,)).fetchone():
        raise ValueError("candidate does not exist")
    connection.execute("BEGIN IMMEDIATE")
    cursor = connection.execute(
        """
        INSERT INTO callback_logs(
          candidate_id,video_id,publisher,platform,views,clicks,registrations,extra_data,callback_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate, str(payload.get("video_id") or ""), publisher, platform,
            metrics["views"], metrics["clicks"], metrics["registrations"],
            json.dumps(extra, ensure_ascii=False), callback_at,
        ),
    )
    snapshot = connection.execute(
        """
        INSERT INTO performance_snapshots(
          candidate_id,platform,captured_at,views,clicks,registrations
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            candidate, platform, callback_at,
            metrics["views"], metrics["clicks"], metrics["registrations"],
        ),
    )
    for index in range(metrics["registrations"]):
        connection.execute(
            """
            INSERT INTO conversion_events(
              candidate_id,platform,hook_version,event_type,occurred_at,visitor_id,payload_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                candidate, platform, str(payload.get("hook_version") or ""),
                "registration", callback_at, "",
                json.dumps({
                    "source": "callback",
                    "publisher": publisher,
                    "video_id": str(payload.get("video_id") or ""),
                    "ordinal": index + 1,
                }, ensure_ascii=False),
            ),
        )
    connection.commit()
    print(f"callback {callback_at} candidate={candidate} platform={platform}")
    return {
        "success": True,
        "log_id": int(cursor.lastrowid),
        "snapshot_id": int(snapshot.lastrowid),
        "registration_events": metrics["registrations"],
    }


def move_candidate_to_review(config: dict[str, Any], candidate: str) -> dict[str, Any]:
    candidate = candidate.strip()
    if not candidate:
        raise ValueError("candidate_id is required")
    gate = assert_candidate_ready_for_review(config, candidate)
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    if row["status"] != "READY_FOR_REVIEW":
        raise ValueError("standard production gate passed but candidate is not finalized")
    return {"candidate_id": candidate, "status": row["status"], "gate": gate}


def save_metrics(config: dict[str, Any], payload: dict[str, Any]) -> int:
    candidate = str(payload.get("candidate_id") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    if not candidate or platform not in PLATFORMS:
        raise ValueError("candidate_id and a supported platform are required")
    fields = ("views", "likes", "comments", "shares", "clicks", "installs", "registrations")
    values = [int_value(payload.get(field)) for field in fields]
    connection = connect_db(config)
    cursor = connection.execute(
        f"INSERT INTO performance_snapshots(candidate_id,platform,captured_at,{','.join(fields)}) VALUES(?,?,?,{','.join('?' for _ in fields)})",
        (candidate, platform, payload.get("captured_at") or now_iso(), *values),
    )
    connection.commit()
    propose_feedback(connection, candidate, platform, dict(zip(fields, values)))
    return int(cursor.lastrowid)


def propose_feedback(connection: Any, candidate: str, platform: str, metrics: dict[str, int]) -> None:
    views = metrics["views"]
    if views < 1_000:
        return
    registration_rate = metrics["registrations"] / views
    share_rate = metrics["shares"] / views
    if registration_rate < 0.002 and share_rate < 0.01:
        return
    row = connection.execute("SELECT metadata_json FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        return
    try:
        keyword = str(json.loads(row["metadata_json"] or "{}").get("keyword") or "")
    except json.JSONDecodeError:
        keyword = ""
    reason = f"{platform}: 注册率 {registration_rate:.2%}，分享率 {share_rate:.2%}"
    score = registration_rate * 1000 + share_rate * 100
    connection.execute(
        "INSERT INTO feedback_actions(candidate_id,keyword,action_type,reason,score,status,created_at) VALUES(?,?,?,?,?,'PROPOSED',?)",
        (candidate, keyword, "BOOST_KEYWORD", reason, score, now_iso()),
    )
    connection.commit()


def conversion_totals(connection: Any, candidate_id: str | None = None) -> dict[str, int]:
    """Deduplicated conversion counts from JaguarTV postback events.

    A visitor is counted once per (candidate, event_type); anonymous events
    (empty visitor_id) fall back to raw row counts.
    """
    where = "WHERE candidate_id=?" if candidate_id else ""
    args = (candidate_id,) if candidate_id else ()
    rows = connection.execute(
        f"""
        SELECT event_type,
               COUNT(DISTINCT CASE WHEN visitor_id!='' THEN candidate_id||':'||visitor_id END)
                 + SUM(CASE WHEN visitor_id='' THEN 1 ELSE 0 END) AS total
        FROM conversion_events {where} GROUP BY event_type
        """,
        args,
    ).fetchall()
    totals = {event_type: 0 for event_type in EVENT_TYPES}
    for row in rows:
        if row["event_type"] in totals:
            totals[row["event_type"]] = int(row["total"] or 0)
    return totals


def candidate_from_utm_content(
    utm_content: str,
    known_candidates: set[str],
    explicit_hook: str = "",
) -> tuple[str, str]:
    value = str(utm_content or "").strip()
    if not value:
        return "", explicit_hook
    if value in known_candidates:
        return value, explicit_hook
    candidate, separator, parsed_hook = value.rpartition("_")
    if separator and candidate in known_candidates and parsed_hook:
        return candidate, explicit_hook or parsed_hook
    return value, explicit_hook


def save_events(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, int]:
    """Ingest one event or a batch: {"events": [...]} or a single event object."""
    events = payload.get("events") if isinstance(payload.get("events"), list) else [payload]
    connection = connect_db(config)
    known = {
        row["id"] for row in connection.execute("SELECT id FROM candidates")
    }
    saved, skipped = 0, 0
    for event in events:
        if not isinstance(event, dict):
            skipped += 1
            continue
        event_type = str(event.get("event_type") or "").strip().lower()
        utm_content = str(event.get("utm_content") or "").strip()
        candidate = str(event.get("candidate_id") or "").strip()
        hook_version = str(event.get("hook_version") or "").strip()
        if utm_content and not candidate:
            candidate, hook_version = candidate_from_utm_content(utm_content, known, hook_version)
        if event_type not in EVENT_TYPES or not candidate:
            skipped += 1
            continue
        if candidate not in known:
            skipped += 1
            continue
        connection.execute(
            """
            INSERT INTO conversion_events
              (candidate_id,platform,hook_version,event_type,occurred_at,visitor_id,payload_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                candidate,
                str(event.get("platform") or event.get("utm_source") or "").strip().lower(),
                hook_version,
                event_type,
                str(event.get("occurred_at") or now_iso()),
                str(event.get("visitor_id") or ""),
                json.dumps(event.get("payload") or {}, ensure_ascii=False),
            ),
        )
        saved += 1
    connection.commit()
    if not saved:
        raise ValueError("no valid events; require event_type in "
                         f"{EVENT_TYPES} and a known candidate_id/utm_content")
    return {"saved": saved, "skipped": skipped}


def attribution_report(config: dict[str, Any], candidate: str) -> dict[str, Any]:
    connection = connect_db(config)
    row = connection.execute("SELECT id,title,status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    totals = conversion_totals(connection, candidate)
    by_platform: dict[str, dict[str, int]] = {}
    for event in connection.execute(
        "SELECT platform,event_type,COUNT(*) count FROM conversion_events WHERE candidate_id=? GROUP BY platform,event_type",
        (candidate,),
    ):
        entry = by_platform.setdefault(event["platform"] or "unknown", {})
        entry[event["event_type"]] = event["count"]
    recent = [
        dict(item) for item in connection.execute(
            "SELECT event_type,platform,hook_version,occurred_at,visitor_id FROM conversion_events "
            "WHERE candidate_id=? ORDER BY occurred_at DESC LIMIT 50",
            (candidate,),
        )
    ]
    return {
        "candidate_id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "tracking_links": tracking_links(config, candidate),
        "funnel": totals,
        "by_platform": by_platform,
        "recent_events": recent,
    }


def sync_child_slice_review_status(
    connection: Any,
    parent_candidate: str,
    decision: str,
    *,
    timestamp: str,
    reviewer: str = "",
) -> list[str]:
    children = [
        dict(row) for row in connection.execute(
            """
            SELECT id,source_id,status
            FROM candidates
            WHERE parent_id=?
              AND status IN ('READY_FOR_REVIEW','APPROVED','REVISION_REQUIRED')
            """,
            (parent_candidate,),
        )
    ]
    updated: list[str] = []
    for child in children:
        child_id = str(child["id"] or "")
        if not review_slice_parent_id(child_id, parent_candidate, str(child["source_id"] or "")):
            continue
        if str(child["status"] or "") == decision:
            continue
        connection.execute(
            "UPDATE candidates SET status=?,updated_at=? WHERE id=?",
            (decision, timestamp, child_id),
        )
        append_event(
            connection,
            child_id,
            f"REVIEW_{decision}",
            {"reviewer": reviewer, "synced_from_parent": parent_candidate},
        )
        updated.append(child_id)
    return updated


def save_review(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").strip()
    decision = str(payload.get("decision") or "").strip().upper()
    if not candidate or decision not in REVIEW_DECISIONS:
        raise ValueError(f"candidate_id and decision in {REVIEW_DECISIONS} are required")
    connection = connect_db(config)
    row = connection.execute("SELECT id,status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        review_file = storage_root(config) / "review" / candidate / "review.json"
        if not review_file.parent.exists():
            raise ValueError("candidate does not exist")
        timestamp = now_iso()
        review_file.write_text(
            json.dumps({
                "decision": decision.lower(),
                "note": str(payload.get("note") or ""),
                "reviewer": str(payload.get("reviewer") or ""),
                "reviewed_at": timestamp,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"candidate_id": candidate, "status": decision, "source": "server_review_package"}
    if row["status"] not in {"READY_FOR_REVIEW", "APPROVED", "REVISION_REQUIRED"}:
        raise ValueError(f"candidate status {row['status']} cannot be reviewed")
    timestamp = now_iso()
    connection.execute(
        "UPDATE candidates SET status=?,updated_at=? WHERE id=?", (decision, timestamp, candidate)
    )
    connection.execute(
        "INSERT INTO events(candidate_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (candidate, f"REVIEW_{decision}", json.dumps({
            "note": str(payload.get("note") or ""),
            "reviewer": str(payload.get("reviewer") or ""),
        }, ensure_ascii=False), timestamp),
    )
    sync_source_import_workflow_status(
        connection,
        candidate,
        decision,
        event_type=f"REVIEW_{decision}",
        actor=str(payload.get("reviewer") or "dashboard"),
        payload={"note": str(payload.get("note") or "")},
    )
    synced_children = sync_child_slice_review_status(
        connection,
        candidate,
        decision,
        timestamp=timestamp,
        reviewer=str(payload.get("reviewer") or ""),
    )
    connection.commit()
    review_file = workspace_dir(config) / "ready_for_review" / candidate / "review.json"
    if review_file.parent.exists():
        review_file.write_text(
            json.dumps({
                "decision": decision.lower(),
                "note": str(payload.get("note") or ""),
                "reviewer": str(payload.get("reviewer") or ""),
                "reviewed_at": timestamp,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    result = {"candidate_id": candidate, "status": decision, "synced_children": synced_children}
    if decision == "APPROVED":
        result["publication"] = auto_enqueue_approved_publication(
            config,
            candidate,
            reviewer=str(payload.get("reviewer") or ""),
            review_decision_at=timestamp,
        )
    return result


def keywords_file_path(config: dict[str, Any]) -> Path:
    return resolve_config_path(config, config.get("sources", {}).get("keywords_file", "config/keywords.jaguartv.yaml"))


def load_keyword_groups(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = keywords_file_path(config)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    groups = []
    for name, group in (data or {}).items():
        group = group or {}
        groups.append({
            "name": name,
            "weight": float(group.get("weight", 1.0)),
            "days": group.get("days") or [],
            "enabled": group.get("enabled", True) is not False,
            "active_today": category_active_today(group),
            "terms": group.get("terms") or {},
        })
    return groups


def save_keyword_group(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Create/update/delete one keyword group; keeps YAML on disk as the
    single source of truth so CLI and UI stay in sync."""
    name = str(payload.get("name") or "").strip()
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("valid group name is required (letters/digits/underscores)")
    path = keywords_file_path(config)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    if payload.get("delete"):
        if name not in data:
            raise ValueError("group does not exist")
        del data[name]
    else:
        terms = payload.get("terms") or {}
        if not isinstance(terms, dict) or not any(isinstance(v, list) and v for v in terms.values()):
            raise ValueError("terms must map languages to non-empty lists, e.g. {\"en\": [\"goal\"]}")
        group: dict[str, Any] = {
            "weight": max(0.0, min(2.0, float(payload.get("weight", 1.0)))),
            "terms": {str(k): [str(t).strip() for t in v if str(t).strip()] for k, v in terms.items()},
        }
        days = payload.get("days") or []
        valid_days = [d for d in (str(x).strip().lower()[:3] for x in days) if d in
                      {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}]
        if valid_days:
            group["days"] = valid_days
        if payload.get("enabled") is False:
            group["enabled"] = False
        data[name] = group
    backup = path.with_suffix(".yaml.bak")
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"saved": name, "groups": len(data)}


def category_keyword_rows(config: dict[str, Any], date_value: str | None = None) -> dict[str, Any]:
    target = trends_today(config) if not date_value or date_value == "today" else date_value
    rows = list_hot_keywords(config, target)
    groups = load_keyword_groups(config)
    term_to_category: dict[str, str] = {}
    for group in groups:
        terms_by_language = group.get("terms") or {}
        group_category = (
            explicit_category_label(group.get("category"))
            or explicit_category_label(group.get("category_label"))
            or explicit_category_label(group.get("name"))
        )
        flattened_terms = [
            str(term)
            for terms in terms_by_language.values()
            if isinstance(terms, list)
            for term in terms
            if str(term).strip()
        ]
        for term in flattened_terms:
            category = initial_category_for_text(term)
            if category not in INITIAL_CATEGORY_LABELS and group_category:
                category = group_category
            if category in INITIAL_CATEGORY_LABELS:
                term_to_category[normalize_keyword(term)] = category

    grouped = {
        label: {"label": label, "keywords": [], "count": 0}
        for label in INITIAL_CATEGORY_LABELS
    }
    seen_by_category: dict[str, set[str]] = {label: set() for label in INITIAL_CATEGORY_LABELS}
    for item in rows:
        keyword = str(item.get("keyword") or "").strip()
        if not keyword:
            continue
        source = str(item.get("source") or "").strip()
        category = explicit_category_label(source)
        if not category:
            category = term_to_category.get(normalize_keyword(keyword), "")
        if not category:
            category = initial_category_for_text(keyword)
        if category not in INITIAL_CATEGORY_LABELS:
            category = "新闻类" if source.lower().startswith("google") else "社交挑战"
        key = normalize_keyword(keyword)
        if key in seen_by_category[category]:
            continue
        seen_by_category[category].add(key)
        grouped[category]["keywords"].append({
            "keyword": keyword,
            "source": source,
            "created_at": str(item.get("created_at") or ""),
        })

    for label, row in grouped.items():
        row["count"] = len(row["keywords"])
    return {
        "date": target,
        "generated_at": now_iso(),
        "rows": [grouped[label] for label in INITIAL_CATEGORY_LABELS],
    }


def skip_candidate(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate = str(payload.get("candidate_id") or "").strip()
    if not candidate:
        raise ValueError("candidate_id is required")
    connection = connect_db(config)
    row = connection.execute("SELECT status FROM candidates WHERE id=?", (candidate,)).fetchone()
    if not row:
        raise ValueError("candidate does not exist")
    if row["status"] not in {"DISCOVERED", "SKIPPED"}:
        raise ValueError(f"only DISCOVERED candidates can be skipped (current: {row['status']})")
    connection.execute("UPDATE candidates SET status='SKIPPED',updated_at=? WHERE id=?", (now_iso(), candidate))
    connection.commit()
    return {"candidate_id": candidate, "status": "SKIPPED"}


def system_settings(config: dict[str, Any]) -> dict[str, Any]:
    kit_name = str(config.get("brand", {}).get("default_kit") or "jaguartv")
    kit = (config.get("brand", {}).get("kits") or {}).get(kit_name) or {}
    edit = config.get("edit", {})
    return {
        "config_path": str(config.get("_path") or ""),
        "workspace": str(config.get("run", {}).get("workspace", "workspace")),
        "sources_enabled": config.get("sources", {}).get("enabled", []),
        "edit": {
            "render_engine": str(edit.get("render_engine", "ffmpeg")),
            "output_duration_sec": edit.get("output_duration_sec", [12, 30]),
            "max_segments_per_source": int(edit.get("max_segments_per_source", 3)),
            "layout_mode": str(edit.get("layout_mode", "original")),
        },
        "remotion": config.get("remotion", {}),
        "brand": {
            "kit": kit_name,
            "cta": str(config.get("brand", {}).get("default_cta") or ""),
            "watermark": kit.get("watermark", {}),
            "cover": kit.get("cover", {"mode": "frame", "image": ""}),
            "endcard": kit.get("endcard", {}),
        },
    }


def save_system_settings(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    config_path = Path(str(config.get("_path") or "")).expanduser().resolve()
    if not config_path.exists():
        raise ValueError("config file does not exist")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data.setdefault("edit", {})
    data.setdefault("brand", {}).setdefault("kits", {})
    kit_name = str(data.get("brand", {}).get("default_kit") or "jaguartv")
    kit = data["brand"]["kits"].setdefault(kit_name, {})
    kit.setdefault("watermark", {})
    kit.setdefault("cover", {})
    kit.setdefault("endcard", {})

    edit_payload = payload.get("edit") or {}
    if "output_duration_sec" in edit_payload:
        values = edit_payload.get("output_duration_sec") or [12, 30]
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError("output_duration_sec must be [min,max]")
        minimum = min(30, max(12, int(values[0])))
        maximum = min(30, max(minimum, int(values[1])))
        data["edit"]["output_duration_sec"] = [minimum, maximum]
    if "max_segments_per_source" in edit_payload:
        data["edit"]["max_segments_per_source"] = max(1, min(10, int(edit_payload.get("max_segments_per_source") or 3)))
    if "layout_mode" in edit_payload:
        layout = str(edit_payload.get("layout_mode") or "original").strip().lower()
        if layout not in {"original", "vertical"}:
            raise ValueError("layout_mode must be original or vertical")
        data["edit"]["layout_mode"] = layout
        data["edit"]["aspect_ratio"] = "source" if layout == "original" else "9:16"
    if "render_engine" in edit_payload:
        engine = str(edit_payload.get("render_engine") or "ffmpeg").strip().lower()
        if engine not in {"ffmpeg", "remotion"}:
            raise ValueError("render_engine must be ffmpeg or remotion")
        data["edit"]["render_engine"] = engine

    remotion_payload = payload.get("remotion") or {}
    if isinstance(remotion_payload, dict):
        data.setdefault("remotion", {})
        for field in ("top_badge", "bottom_headline", "bottom_subline", "endcard_cta"):
            if field in remotion_payload:
                data["remotion"][field] = str(remotion_payload.get(field) or "").strip()
        for field in ("content_bgm_volume", "endcard_bgm_volume"):
            if field in remotion_payload:
                data["remotion"][field] = max(0.0, min(1.0, float(remotion_payload.get(field) or 0)))
        if "add_bgm_under_source" in remotion_payload:
            data["remotion"]["add_bgm_under_source"] = False

    brand_payload = payload.get("brand") or {}
    if "cta" in brand_payload:
        data["brand"]["default_cta"] = str(brand_payload.get("cta") or "").strip()
    for key in ("watermark", "cover", "endcard"):
        incoming = brand_payload.get(key)
        if not isinstance(incoming, dict):
            continue
        target = kit.setdefault(key, {})
        for field in ("mode", "image", "text", "position", "site", "title", "tagline"):
            if field in incoming:
                target[field] = str(incoming.get(field) or "").strip()
        for field in ("opacity",):
            if field in incoming:
                target[field] = max(0.0, min(1.0, float(incoming.get(field) or 0)))
        for field in ("width", "duration_sec"):
            if field in incoming:
                target[field] = max(1, int(incoming.get(field) or target.get(field) or 1))

    backup = config_path.with_suffix(".yaml.bak")
    backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    config.clear()
    config.update(data)
    config["_path"] = str(config_path)
    config["_root"] = str(config_path.parent.parent)
    return system_settings(config)


class DashboardApplication(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], config: dict[str, Any]):
        super().__init__(address, DashboardHandler)
        self.config = config
        self.tasks: dict[str, dict[str, Any]] = {}
        self.tasks_lock = threading.Lock()
        self.production_lock = threading.Lock()
        self.login_failures: dict[str, list[float]] = {}
        self.login_failures_lock = threading.Lock()
        recovered = recover_interrupted_productions(config)
        if recovered:
            print(f"dashboard recovered {recovered} interrupted production candidate(s)")

    def start_action(
        self,
        payload: dict[str, Any],
        *,
        actor: str = "dashboard",
        can_direct_approve: bool = False,
    ) -> str:
        action = str(payload.get("action") or "")
        source_import: dict[str, Any] | None = None
        if action == "ingest":
            source_import = create_source_import(
                self.config,
                platform=str(payload.get("platform") or ""),
                url=str(payload.get("url") or ""),
                source_category=payload.get("source_category"),
                target_area=payload.get("target_area"),
                operator_id=actor,
                can_direct_approve=can_direct_approve,
                idempotency_key=str(payload.get("idempotency_key") or ""),
            )
            payload = {
                **payload,
                "source_import_id": source_import["id"],
                "target_area": source_import["target_area"],
                "url": source_import["normalized_url"],
            }
        candidate_ids = payload.get("candidate_ids") or []
        if not isinstance(candidate_ids, list):
            raise ValueError("candidate_ids must be a list")
        single = str(payload.get("candidate_id") or "").strip()
        candidate_ids = [str(value).strip() for value in candidate_ids if str(value).strip()]
        if single and single not in candidate_ids:
            candidate_ids.append(single)
        candidate_ids = list(dict.fromkeys(candidate_ids))
        task_id = str(source_import["download_task_id"]) if source_import else uuid.uuid4().hex[:12]
        task = {
            "id": task_id, "action": action, "status": "RUNNING",
            "started_at": now_iso(), "result": None, "error": "", "progress": 0,
            "completed": 0, "total": len(candidate_ids) or 1, "current_candidate": "",
            "message": "任务已进入队列", "candidate_ids": candidate_ids,
            "source_import_id": str((source_import or {}).get("id") or ""),
            "target_area": str((source_import or {}).get("target_area") or ""),
            "target_area_label": str((source_import or {}).get("target_label") or ""),
        }
        with self.tasks_lock:
            if task_id in self.tasks and self.tasks[task_id].get("status") == "RUNNING":
                return task_id
            requested = set(candidate_ids)
            for active in self.tasks.values():
                if active.get("status") == "RUNNING" and requested.intersection(active.get("candidate_ids") or []):
                    raise ValueError("selected candidate is already running in another task")
            self.tasks[task_id] = task
        if source_import and source_import.get("actual_workflow_status") in {"DOWNLOADED", "APPROVED"}:
            self.update_task(
                task_id,
                status="COMPLETED",
                progress=100,
                completed=1,
                result={
                    "candidate_id": source_import.get("candidate_id"),
                    "source_import_id": source_import["id"],
                    "status": source_import["actual_workflow_status"],
                    "target_area": source_import["target_area"],
                    "reused": True,
                },
                message="已有相同导入记录",
                finished_at=now_iso(),
            )
            return task_id
        enriched = {**payload, "candidate_ids": candidate_ids}
        threading.Thread(target=self._run_action, args=(task_id, enriched), daemon=True).start()
        return task_id

    def update_task(self, task_id: str, **values: Any) -> None:
        with self.tasks_lock:
            self.tasks[task_id].update(values)

    def run_candidate_batch(
        self, task_id: str, action: str, candidate_ids: list[str], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not candidate_ids:
            raise ValueError("select at least one candidate")
        items = []
        failed = 0
        total = len(candidate_ids)
        for index, candidate in enumerate(candidate_ids):
            requested_candidate = candidate
            item_options = options or {}
            if action == "produce":
                candidate, item_options = resolve_production_candidate(self.config, requested_candidate, options)
            self.update_task(
                task_id, current_candidate=requested_candidate, completed=index,
                progress=max(2, int(index / total * 95)),
                message=f"正在{('下载' if action == 'download' else '制作' if action == 'produce' else '忽略')} {index + 1}/{total}",
            )
            if action == "download":
                result = download_top(self.config, 1, candidate)
                item_failed = int(result.get("failed", 0)) or int(result.get("selected", 0) == 0)
            elif action == "produce":
                row = connect_db(self.config).execute(
                    "SELECT status FROM candidates WHERE id=?", (candidate,)
                ).fetchone()
                if not row and isinstance(item_options.get("design"), dict) and materialize_review_candidate(self.config, candidate):
                    row = connect_db(self.config).execute(
                        "SELECT status FROM candidates WHERE id=?", (candidate,)
                    ).fetchone()
                if not row:
                    result = {
                        "selected": 0,
                        "produced": 0,
                        "failed": 1,
                        "error": f"candidate does not exist: {requested_candidate}",
                    }
                    failed += 1
                    items.append({"candidate_id": requested_candidate, "result": result, "failed": True})
                    self.update_task(task_id, completed=index + 1, progress=int((index + 1) / total * 95))
                    continue

                def production_progress(percent: int, message: str) -> None:
                    base = index / total * 95
                    share = 95 / total
                    self.update_task(
                        task_id, progress=int(base + percent / 100 * share),
                        message=f"{message} · {index + 1}/{total}",
                    )

                self.update_task(task_id, message=f"统一下载与制作 · {index + 1}/{total}")
                result = produce_top(
                    self.config,
                    1,
                    candidate,
                    progress_callback=production_progress,
                    options={**item_options, "trigger_source": "dashboard"},
                )
                item_failed = int(result.get("failed", 0)) or int(result.get("selected", 0) == 0)
            else:
                try:
                    result = skip_candidate(self.config, {"candidate_id": candidate})
                    item_failed = 0
                except ValueError as error:
                    result = {"error": str(error)}
                    item_failed = 1
            failed += int(bool(item_failed))
            items.append({"candidate_id": requested_candidate, "resolved_candidate_id": candidate, "result": result, "failed": bool(item_failed)})
            self.update_task(task_id, completed=index + 1, progress=int((index + 1) / total * 95))
        return {"selected": total, "completed": total - failed, "failed": failed, "items": items}

    def _run_action(self, task_id: str, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "")
        try:
            if action == "discover":
                self.update_task(task_id, progress=10, message=f"正在搜索 {payload.get('platform') or 'youtube'}")
                keywords = payload.get("keywords") or []
                result = discover(
                    self.config,
                    platforms=[str(payload.get("platform") or "youtube")],
                    limit=int_value(payload.get("limit"), 3),
                    keyword_overrides=keywords if isinstance(keywords, list) else [],
                )
            elif action == "ingest":
                import_id = str(payload.get("source_import_id") or "").strip()
                if not import_id:
                    created_import = create_source_import(
                        self.config,
                        platform=str(payload.get("platform") or ""),
                        url=str(payload.get("url") or ""),
                        source_category=payload.get("source_category"),
                        target_area=payload.get("target_area"),
                        operator_id="dashboard",
                        can_direct_approve=False,
                    )
                    import_id = str(created_import["id"])
                import_record = source_import_row(self.config, import_id)
                url = str(import_record.get("normalized_url") or "").strip()
                url = normalize_import_url(
                    url,
                    str(import_record.get("source_platform") or payload.get("platform") or ""),
                )
                self.update_task(task_id, progress=15, message="正在读取视频信息")
                candidate_id = inspect_url(
                    self.config,
                    url,
                    requested_platform=str(payload.get("platform") or ""),
                    allow_stub=True,
                )
                candidate = connect_db(self.config).execute(
                    "SELECT title,status FROM candidates WHERE id=?", (candidate_id,)
                ).fetchone()
                attach_source_import_candidate(
                    self.config,
                    import_id,
                    candidate_id,
                    original_title=str(candidate["title"] or "") if candidate else "",
                )
                self.update_task(
                    task_id,
                    progress=45,
                    current_candidate=candidate_id,
                    candidate_ids=[candidate_id],
                    message="正在下载到服务器",
                )
                row = connect_db(self.config).execute(
                    "SELECT status,title FROM candidates WHERE id=?", (candidate_id,)
                ).fetchone()
                media = candidate_source_media(self.config, candidate_id)
                if media is not None:
                    download_result = {"selected": 1, "downloaded": 1, "failed": 0, "already_downloaded": True}
                elif row and row["status"] == "TOO_LONG":
                    raise RuntimeError("URL 已导入，但视频超过 30 分钟，只能删除，不能进入待制作")
                else:
                    if row and row["status"] == "IMPORT_FAILED":
                        connection = connect_db(self.config)
                        connection.execute(
                            "UPDATE candidates SET status='DOWNLOAD_FAILED',updated_at=? WHERE id=?",
                            (now_iso(), candidate_id),
                        )
                        connection.commit()
                    download_result = download_top(self.config, 1, candidate_id)
                if int(download_result.get("failed", 0)) or int(download_result.get("downloaded", 0) == 0):
                    failure = connect_db(self.config).execute(
                        """
                        SELECT payload_json FROM events
                        WHERE candidate_id=? AND event_type='DOWNLOAD_FAILED'
                        ORDER BY id DESC LIMIT 1
                        """,
                        (candidate_id,),
                    ).fetchone()
                    detail = ""
                    if failure:
                        try:
                            payload_json = json.loads(failure["payload_json"] or "{}")
                            detail = str(payload_json.get("stderr") or payload_json.get("error") or "").strip()
                        except json.JSONDecodeError:
                            detail = str(failure["payload_json"] or "").strip()
                    suffix = f"：{detail[-500:]}" if detail else ""
                    raise RuntimeError(f"URL 已导入，但服务器下载失败，请在下载失败列表重试或检查登录态{suffix}")
                media = candidate_source_media(self.config, candidate_id)
                if media is None:
                    raise RuntimeError("URL 已下载，但服务器没有找到受管理的源视频文件")
                completed_import = complete_source_import(
                    self.config,
                    import_id,
                    candidate_id=candidate_id,
                    media_path=media,
                    original_title=str(row["title"] or "") if row else "",
                )
                result = {
                    "candidate_id": candidate_id,
                    "source_import_id": import_id,
                    "download": download_result,
                    "status": completed_import["actual_workflow_status"],
                    "target_area": completed_import["target_area"],
                    "target_area_label": completed_import["target_label"],
                }
            elif action in {"download", "produce", "skip"}:
                result = self.run_candidate_batch(
                    task_id, action, payload.get("candidate_ids") or [], payload.get("options") or {}
                )
            elif action == "review":
                result = {"index": str(generate_review_index(self.config))}
            else:
                raise ValueError(f"unsupported action: {action}")
            failed = int(result.get("failed", 0)) if isinstance(result, dict) else 0
            message = f"任务完成，失败 {failed} 条" if failed else "任务完成"
            state = {"status": "COMPLETED", "result": result, "error": "", "progress": 100,
                     "completed": self.tasks[task_id].get("total", 1), "current_candidate": "",
                     "message": message, "finished_at": now_iso()}
        except Exception as error:
            import_id = str(payload.get("source_import_id") or "").strip()
            if action == "ingest" and import_id:
                try:
                    current = source_import_row(self.config, import_id)
                    if current["actual_workflow_status"] != "IMPORT_FAILED":
                        fail_source_import(
                            self.config,
                            import_id,
                            category="DOWNLOAD_FAILED",
                            summary=str(error),
                            candidate_id=str(current.get("candidate_id") or ""),
                        )
                except Exception:
                    pass
            state = {"status": "FAILED", "result": None, "error": str(error), "message": str(error),
                     "progress": 100, "current_candidate": "", "finished_at": now_iso()}
        with self.tasks_lock:
            self.tasks[task_id].update(state)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardApplication

    def log_message(self, format: str, *args: Any) -> None:
        print(f"dashboard {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/login":
                if self.authorized_for_admin():
                    return self.redirect("/")
                return self.send_static("/login.html")
            if parsed.path == "/api/auth/status":
                return self.send_json({"authenticated": self.authorized_for_admin()})
            if (
                parsed.path == "/"
                and self.admin_required_path(parsed.path)
                and not self.authorized_for_admin()
            ):
                return self.redirect("/login")
            posters_enabled = bool((self.server.config.get("features") or {}).get("posters", True))
            if not posters_enabled and parsed.path == "/api/posters/counts":
                return self.send_json({"ALL": 0, "PENDING_SCREENING": 0, "PENDING_REVIEW": 0, "APPROVED": 0})
            if not posters_enabled and parsed.path.startswith("/api/posters"):
                return self.send_json({"error": "poster workflow is retired"}, HTTPStatus.GONE)
            if parsed.path == "/oauth/youtube/callback":
                return self.send_youtube_oauth_callback(query)
            if parsed.path == "/oauth/x/callback":
                return self.send_x_oauth_callback(query)
            if parsed.path == "/oauth/youtube/start":
                account = str((query.get("account") or ["consumer_football"])[0]).strip() or "consumer_football"
                if not self.authorized_for_admin(parsed) and not youtube_auth_link_is_valid(query):
                    return self.send_admin_unauthorized(parsed.path)
                try:
                    return self.redirect(youtube_oauth_start_url(self.server.config, account))
                except Exception as error:
                    return self.send_html(
                        oauth_result_html(
                            "YouTube 授权尚未配置",
                            [
                                str(error),
                                "请在服务器 .env 中配置 Google OAuth Client ID、Client Secret 和 token 加密密钥。",
                            ],
                            ok=False,
                        ),
                        HTTPStatus.BAD_REQUEST,
                    )
            if parsed.path == "/oauth/x/start":
                if not self.authorized_for_admin(parsed):
                    return self.send_admin_unauthorized(parsed.path)
                account = str((query.get("account") or ["consumer_main"])[0]).strip() or "consumer_main"
                try:
                    return self.redirect(x_oauth_start_url(self.server.config, account))
                except Exception as error:
                    return self.send_html(
                        oauth_result_html(
                            "X 授权尚未配置",
                            [
                                str(error),
                                "请在服务器 .env 中配置 JAGUARTV_X_CLIENT_ID、JAGUARTV_OAUTH_TOKEN_KEY 和回调地址。",
                            ],
                            ok=False,
                        ),
                        HTTPStatus.BAD_REQUEST,
                    )
            if self.admin_required_path(parsed.path) and not self.authorized_for_admin(parsed):
                return self.send_admin_unauthorized(parsed.path)
            if parsed.path == "/api/overview":
                return self.send_json(dashboard_overview(self.server.config))
            if parsed.path == "/api/candidates":
                status = query.get("status", [None])[0]
                limit = int_value(query.get("limit", [100])[0], 100)
                if str((query.get("paginated") or [""])[0]).lower() in {"1", "true", "yes"}:
                    return self.send_json(candidate_page(
                        self.server.config,
                        status=status,
                        source_type=str((query.get("source_type") or [""])[0]),
                        platform=str((query.get("platform") or [""])[0]),
                        category=str((query.get("category") or [""])[0]),
                        search=str((query.get("search") or [""])[0]),
                        sort=str((query.get("sort") or ["time"])[0]),
                        page=validated_positive_int((query.get("page") or [1])[0], "page", 1),
                        page_size=validated_positive_int(
                            (query.get("page_size") or [50])[0], "page_size", 50
                        ),
                    ))
                return self.send_json(candidate_rows(self.server.config, status, limit))
            if parsed.path == "/api/import-capabilities":
                return self.send_json({
                    "default_target_area": "pending_production",
                    "allowed_target_areas": ["pending_production", "approved"],
                    "source_category_labels": list(SOURCE_IMPORT_CATEGORY_LABELS),
                    "allow_upload_approved": True,
                    "can_direct_approve": self.authorized_for_admin(parsed),
                })
            if parsed.path == "/api/originals":
                return self.send_json(list_original_items(
                    self.server.config,
                    status=(query.get("status") or ["PENDING_REVIEW"])[0],
                    category=(query.get("category") or [""])[0],
                    page=(query.get("page") or [1])[0],
                    page_size=(query.get("page_size") or [24])[0],
                ))
            if parsed.path == "/api/originals/counts":
                return self.send_json(original_counts(self.server.config))
            if parsed.path == "/api/originals/import/limits":
                return self.send_json({
                    **original_upload_limits(self.server.config),
                    "formats": ["MP4"],
                    "mime_types": ["video/mp4"],
                })
            original_parts = parsed.path.strip("/").split("/")
            if len(original_parts) == 3 and original_parts[:2] == ["api", "originals"]:
                return self.send_json(original_detail(self.server.config, unquote(original_parts[2])))
            if len(original_parts) == 4 and original_parts[:2] == ["api", "originals"]:
                item_id = unquote(original_parts[2])
                if original_parts[3] == "preview":
                    return self.send_original_asset(item_id, download=False)
                if original_parts[3] == "thumbnail":
                    return self.send_original_asset(item_id, download=False, thumbnail=True)
                if original_parts[3] == "file":
                    return self.send_original_asset(item_id, download=True)
            if parsed.path == "/api/posters":
                return self.send_json(list_posters(
                    self.server.config,
                    status=(query.get("status") or [""])[0],
                    category=(query.get("category") or [""])[0],
                    page=(query.get("page") or [1])[0],
                    page_size=(query.get("page_size") or [24])[0],
                ))
            if parsed.path == "/api/posters/counts":
                return self.send_json(poster_counts(
                    self.server.config,
                    category=(query.get("category") or [""])[0],
                ))
            if parsed.path == "/api/posters/import/limits":
                return self.send_json({
                    **poster_upload_limits(self.server.config),
                    "formats": ["JPEG", "PNG", "WebP"],
                })
            poster_parts = parsed.path.strip("/").split("/")
            if len(poster_parts) == 3 and poster_parts[:2] == ["api", "posters"]:
                return self.send_json(poster_detail(self.server.config, unquote(poster_parts[2])))
            if len(poster_parts) == 4 and poster_parts[:2] == ["api", "posters"]:
                poster_id = unquote(poster_parts[2])
                if poster_parts[3] == "preview":
                    return self.send_poster_asset(poster_id, download=False)
                if poster_parts[3] == "thumbnail":
                    return self.send_poster_asset(poster_id, download=False, thumbnail=True)
                if poster_parts[3] == "download":
                    return self.send_poster_asset(poster_id, download=True)
            if (
                len(poster_parts) == 6
                and poster_parts[:2] == ["api", "posters"]
                and poster_parts[3] == "attachments"
                and poster_parts[5] == "preview"
            ):
                return self.send_poster_attachment(
                    unquote(poster_parts[2]), unquote(poster_parts[4])
                )
            if parsed.path == "/api/publications":
                return self.send_json(publication_rows(self.server.config))
            if parsed.path == "/api/youtube-analytics/summary":
                return self.send_json(analytics_summary(
                    self.server.config,
                    range_name=str((query.get("range") or ["30d"])[0]),
                    start_date=str((query.get("start_date") or [""])[0]),
                    end_date=str((query.get("end_date") or [""])[0]),
                    account_id=str((query.get("account_id") or [""])[0]),
                ))
            if parsed.path == "/api/youtube-analytics/ranking":
                return self.send_json(analytics_ranking(
                    self.server.config,
                    range_name=str((query.get("range") or ["30d"])[0]),
                    start_date=str((query.get("start_date") or [""])[0]),
                    end_date=str((query.get("end_date") or [""])[0]),
                    account_id=str((query.get("account_id") or [""])[0]),
                    metric=str((query.get("metric") or ["views"])[0]),
                    page=validated_positive_int((query.get("page") or [1])[0], "page", 1),
                    page_size=validated_positive_int((query.get("page_size") or [20])[0], "page_size", 20),
                ))
            if parsed.path == "/api/youtube-analytics/accounts":
                return self.send_json(analytics_accounts(self.server.config))
            if parsed.path == "/api/youtube-analytics/channel-import/status":
                return self.send_json(channel_import_status(self.server.config))
            analytics_parts = parsed.path.strip("/").split("/")
            if len(analytics_parts) in {4, 5} and analytics_parts[:3] == ["api", "youtube-analytics", "publications"]:
                publication_id = validated_positive_int(analytics_parts[3], "publication_id", 0)
                if len(analytics_parts) == 5 and analytics_parts[4] == "history":
                    return self.send_json(analytics_history(
                        self.server.config,
                        publication_id,
                        limit=validated_positive_int((query.get("limit") or [100])[0], "limit", 100),
                    ))
                if len(analytics_parts) == 4:
                    detail = publication_latest(self.server.config, publication_id)
                    if detail is None:
                        return self.send_json({"error": "publication not found"}, HTTPStatus.NOT_FOUND)
                    return self.send_json(detail)
            if parsed.path == "/api/publish/capabilities":
                return self.send_json(platform_capabilities())
            if parsed.path == "/api/publish/accounts":
                platform = (query.get("platform") or [""])[0].strip()
                return self.send_json(list_publish_accounts(self.server.config, platform))
            if parsed.path == "/api/x-auths":
                return self.send_json(x_auth_rows(self.server.config))
            if parsed.path == "/api/workers":
                return self.send_json(worker_rows(self.server.config))
            if parsed.path == "/api/render-jobs":
                limit = int_value(query.get("limit", [100])[0], 100)
                return self.send_json(render_job_rows(self.server.config, limit))
            if parsed.path == "/api/feedback":
                return self.send_json(feedback_rows(self.server.config))
            if parsed.path == "/api/download-claims":
                limit = int_value(query.get("limit", [200])[0], 200)
                return self.send_json(download_claim_rows(self.server.config, limit))
            if parsed.path == "/api/keywords":
                return self.send_json(load_keyword_groups(self.server.config))
            if parsed.path == "/api/hot-keywords":
                date_value = (query.get("date") or [""])[0].strip()
                if date_value == "today":
                    date_value = ""
                return self.send_json(list_hot_keywords(self.server.config, date_value or None))
            if parsed.path == "/api/category-keywords":
                date_value = (query.get("date") or [""])[0].strip()
                return self.send_json(category_keyword_rows(self.server.config, date_value or None))
            if parsed.path == "/api/trends/status":
                return self.send_json(trends_status(self.server.config))
            if parsed.path == "/api/trends/run":
                return self.send_json({"error": "use POST /api/trends/run"}, HTTPStatus.METHOD_NOT_ALLOWED)
            if parsed.path == "/api/settings":
                return self.send_json(system_settings(self.server.config))
            if parsed.path == "/api/sessions":
                return self.send_json(list_sessions(self.server.config))
            if parsed.path == "/api/health":
                return self.send_json(system_health(self.server.config))
            if parsed.path == "/api/uploads":
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                return self.send_json(upload_rows(self.server.config))
            if parsed.path == "/api/cta":
                return self.send_json(list_cta_assets(self.server.config))
            if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/link"):
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                upload_id = parsed.path.strip("/").split("/")[2]
                find_upload(self.server.config, upload_id)
                return self.send_json({"download_url": signed_upload_url(upload_id)})
            if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/download"):
                return self.send_private_upload(parsed, head_only=False)
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/design"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 4:
                    return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return self.send_json(candidate_design_info(self.server.config, unquote(parts[2])))
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/source"):
                return self.send_candidate_source(parsed, head_only=False)
            if parsed.path == "/api/attribution":
                candidate = (query.get("candidate_id") or [""])[0].strip()
                if not candidate:
                    return self.send_json({"error": "candidate_id is required"}, HTTPStatus.BAD_REQUEST)
                return self.send_json(attribution_report(self.server.config, candidate))
            if parsed.path == "/api/tasks":
                with self.server.tasks_lock:
                    tasks = list(self.server.tasks.values())[-50:]
                return self.send_json(tasks)
            if parsed.path.startswith("/media/"):
                download = str((query.get("download") or [""])[0]).lower() in {"1", "true", "yes"}
                return self.send_media(parsed.path.removeprefix("/media/"), download=download)
            return self.send_static(parsed.path)
        except (PosterError, OriginalFactoryError) as error:
            self.send_json({"error": str(error)}, HTTPStatus(error.status))
        except PermissionError as error:
            self.send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.log_error("dashboard GET failed: %r", error)
            self.send_json({"error": "internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not bool((self.server.config.get("features") or {}).get("posters", True)) and parsed.path.startswith("/api/posters"):
            return self.send_json({"error": "poster workflow is retired"}, HTTPStatus.GONE)
        if self.admin_required_path(parsed.path) and not self.authorized_for_admin(parsed):
            return self.send_admin_unauthorized(parsed.path)
        if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/download"):
            return self.send_private_upload(parsed, head_only=True)
        if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/source"):
            return self.send_candidate_source(parsed, head_only=True)
        poster_parts = parsed.path.strip("/").split("/")
        if len(poster_parts) == 4 and poster_parts[:2] == ["api", "posters"]:
            poster_id = unquote(poster_parts[2])
            try:
                if poster_parts[3] == "preview":
                    return self.send_poster_asset(poster_id, download=False, head_only=True)
                if poster_parts[3] == "thumbnail":
                    return self.send_poster_asset(
                        poster_id, download=False, thumbnail=True, head_only=True
                    )
                if poster_parts[3] == "download":
                    return self.send_poster_asset(poster_id, download=True, head_only=True)
            except PosterError as error:
                return self.send_json({"error": str(error)}, HTTPStatus(error.status))
        original_parts = parsed.path.strip("/").split("/")
        if len(original_parts) == 4 and original_parts[:2] == ["api", "originals"]:
            item_id = unquote(original_parts[2])
            try:
                if original_parts[3] == "preview":
                    return self.send_original_asset(item_id, download=False, head_only=True)
                if original_parts[3] == "thumbnail":
                    return self.send_original_asset(
                        item_id, download=False, thumbnail=True, head_only=True
                    )
                if original_parts[3] == "file":
                    return self.send_original_asset(item_id, download=True, head_only=True)
            except OriginalFactoryError as error:
                return self.send_json({"error": str(error)}, HTTPStatus(error.status))
        if parsed.path.startswith("/media/"):
            download = str((query.get("download") or [""])[0]).lower() in {"1", "true", "yes"}
            return self.send_media(parsed.path.removeprefix("/media/"), download=download)
        return self.send_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/auth/login":
                return self.login_dashboard()
            if parsed.path == "/api/auth/logout":
                return self.send_json(
                    {"authenticated": False},
                    cookie=self.clear_admin_cookie_header(),
                )
            if not bool((self.server.config.get("features") or {}).get("posters", True)) and parsed.path.startswith("/api/posters"):
                return self.send_json({"error": "poster workflow is retired"}, HTTPStatus.GONE)
            if parsed.path == "/api/originals/import":
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                encoded = self.headers.get("X-Original-Metadata", "").strip()
                if not encoded or len(encoded) > 128_000:
                    raise ValueError("X-Original-Metadata is required or too large")
                try:
                    padding = "=" * (-len(encoded) % 4)
                    metadata = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("X-Original-Metadata must be URL-safe base64 JSON") from error
                if not isinstance(metadata, dict):
                    raise ValueError("X-Original-Metadata must contain a JSON object")
                query = parse_qs(parsed.query)
                return self.send_json(
                    import_original_video(
                        self.server.config,
                        self.rfile,
                        filename=str((query.get("filename") or [""])[0]),
                        mime_type=self.headers.get("Content-Type", ""),
                        content_length=int(self.headers.get("Content-Length") or 0),
                        category=metadata.get("category"),
                        match_name=metadata.get("match_name"),
                        match_date=metadata.get("match_date"),
                        match_time_sao_paulo=metadata.get("match_time_sao_paulo"),
                        channels=metadata.get("channels"),
                        match_info=metadata.get("match_info"),
                        social_sources=metadata.get("social_sources"),
                        generated_at=metadata.get("generated_at"),
                        metadata=metadata.get("metadata"),
                        actor=self.headers.get("X-Operator", "") or metadata.get("actor") or "original-worker",
                        request_id=self.headers.get("X-Request-ID", "") or metadata.get("request_id") or "",
                        batch_size=self.headers.get("X-Original-Batch-Size", "1"),
                    ),
                    HTTPStatus.CREATED,
                )
            analytics_mutation = (
                parsed.path == "/api/youtube-analytics/backfill"
                or parsed.path == "/api/youtube-analytics/channel-import"
                or parsed.path.startswith("/api/youtube-analytics/backfill/")
                or (
                    parsed.path.startswith("/api/youtube-analytics/publications/")
                    and parsed.path.endswith("/retry")
                )
            )
            if analytics_mutation and not self.authorized_for_admin(parsed):
                return self.send_admin_unauthorized(parsed.path)
            if self.admin_required_path(parsed.path) and not self.authorized_for_admin(parsed):
                return self.send_admin_unauthorized(parsed.path)
            if parsed.path == "/api/posters/import":
                query = parse_qs(parsed.query)
                return self.send_json(
                    import_poster(
                        self.server.config,
                        self.rfile,
                        filename=str((query.get("filename") or [""])[0]),
                        mime_type=self.headers.get("Content-Type", ""),
                        content_length=int(self.headers.get("Content-Length") or 0),
                        category=str((query.get("category") or [""])[0]),
                        actor=self.headers.get("X-Operator", ""),
                        batch_size=int(self.headers.get("X-Poster-Batch-Size") or 1),
                    ),
                    HTTPStatus.CREATED,
                )
            if parsed.path == "/api/cta/import":
                query = parse_qs(parsed.query)
                return self.send_json(
                    import_cta_stream(
                        self.server.config,
                        self.rfile,
                        filename=str((query.get("filename") or [""])[0]),
                        content_length=int(self.headers.get("Content-Length") or 0),
                        actor=self.headers.get("X-Operator", "") or "dashboard",
                    ),
                    HTTPStatus.CREATED,
                )
            poster_parts = parsed.path.strip("/").split("/")
            if len(poster_parts) == 4 and poster_parts[:2] == ["api", "posters"] and poster_parts[3] == "attachments":
                query = parse_qs(parsed.query)
                return self.send_json(
                    add_poster_attachment(
                        self.server.config,
                        unquote(poster_parts[2]),
                        self.rfile,
                        filename=str((query.get("filename") or [""])[0]),
                        mime_type=self.headers.get("Content-Type", ""),
                        content_length=int(self.headers.get("Content-Length") or 0),
                        actor=self.headers.get("X-Operator", ""),
                    ),
                    HTTPStatus.CREATED,
                )
            if (
                len(poster_parts) == 6
                and poster_parts[:2] == ["api", "posters"]
                and poster_parts[3] == "attachments"
                and poster_parts[5] == "replace"
            ):
                query = parse_qs(parsed.query)
                return self.send_json(
                    replace_poster_attachment(
                        self.server.config,
                        unquote(poster_parts[2]),
                        unquote(poster_parts[4]),
                        self.rfile,
                        filename=str((query.get("filename") or [""])[0]),
                        mime_type=self.headers.get("Content-Type", ""),
                        content_length=int(self.headers.get("Content-Length") or 0),
                        actor=self.headers.get("X-Operator", ""),
                    ),
                    HTTPStatus.OK,
                )
            if parsed.path == "/api/uploads/init":
                payload = self.read_json()
                kind = str(payload.get("kind") or "").lower()
                if not self.authorized_for_upload_kind(kind):
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                result = init_chunked_upload(
                    self.server.config,
                    filename=str(payload.get("filename") or ""),
                    kind=kind,
                    content_length=int(payload.get("size") or 0),
                )
                return self.send_json(result, HTTPStatus.CREATED)
            if parsed.path == "/api/uploads/chunk":
                query = parse_qs(parsed.query)
                upload_id = str((query.get("upload_id") or [""])[0])
                if not self.authorized_for_upload_kind(self.pending_upload_kind(upload_id)):
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                index = int((query.get("index") or ["-1"])[0])
                length = int(self.headers.get("Content-Length") or 0)
                result = save_upload_chunk(self.server.config, upload_id, index, self.rfile, length)
                return self.send_json(result, HTTPStatus.CREATED)
            if parsed.path == "/api/uploads/complete":
                payload = self.read_json()
                upload_id = str(payload.get("upload_id") or "")
                if not self.authorized_for_upload_kind(self.pending_upload_kind(upload_id)):
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                result = complete_chunked_upload(self.server.config, upload_id)
                if result["kind"] == "source":
                    if bool(payload.get("source_import")):
                        target_area = normalize_target_area(payload.get("target_area"))
                        source_category = normalize_source_category(payload.get("source_category"))
                        source_import = create_uploaded_source_import(
                            self.server.config,
                            upload_id=upload_id,
                            source_platform=str(payload.get("source_platform") or "original"),
                            source_category=source_category,
                            target_area=target_area,
                            operator_id=str(
                                payload.get("operator_id")
                                or self.headers.get("X-Operator", "")
                                or "dashboard"
                            ),
                            idempotency_key=str(payload.get("idempotency_key") or ""),
                        )
                        candidate_id = ingest_uploaded_media(
                            self.server.config,
                            result,
                            source_platform=str(source_import.get("source_platform") or "original"),
                        )
                        attach_source_import_candidate(
                            self.server.config,
                            source_import["id"],
                            candidate_id,
                            original_title=str(result.get("original_filename") or ""),
                        )
                        media = candidate_source_media(self.server.config, candidate_id)
                        if media is None:
                            fail_source_import(
                                self.server.config,
                                source_import["id"],
                                category="UPLOAD_INCOMPLETE",
                                summary="uploaded source candidate has no managed media file",
                                candidate_id=candidate_id,
                            )
                            raise ValueError("uploaded source candidate has no managed media file")
                        completed_import = complete_source_import(
                            self.server.config,
                            source_import["id"],
                            candidate_id=candidate_id,
                            media_path=media,
                            original_title=str(result.get("original_filename") or ""),
                        )
                        result.update({
                            "candidate_id": candidate_id,
                            "source_import_id": source_import["id"],
                            "target_area": completed_import["target_area"],
                            "target_area_label": completed_import["target_label"],
                            "actual_workflow_status": completed_import["actual_workflow_status"],
                            "source_platform": completed_import["source_platform"],
                            "publication": completed_import.get("publication", {}),
                        })
                        result.pop("path", None)
                    else:
                        result["candidate_id"] = ingest_uploaded_media(self.server.config, result)
                result["download_url"] = signed_upload_url(result["id"])
                return self.send_json(result, HTTPStatus.CREATED)
            if parsed.path == "/api/uploads":
                if not self.authorized_for_uploads():
                    return self.send_json(
                        {"error": "missing or invalid upload token"}, HTTPStatus.UNAUTHORIZED
                    )
                query = parse_qs(parsed.query)
                filename = str((query.get("filename") or [""])[0]).strip()
                kind = str((query.get("kind") or [""])[0]).strip().lower()
                length = int(self.headers.get("Content-Length") or 0)
                result = save_upload(
                    self.server.config,
                    self.rfile,
                    filename=filename,
                    kind=kind,
                    content_length=length,
                )
                if result["kind"] == "source":
                    result["candidate_id"] = ingest_uploaded_media(self.server.config, result)
                result["download_url"] = signed_upload_url(result["id"])
                return self.send_json(result, HTTPStatus.CREATED)
            payload = self.read_json()
            if parsed.path == "/api/originals/bulk-approve":
                return self.send_json(
                    approve_originals(
                        self.server.config,
                        payload.get("item_ids"),
                        actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                        request_id=payload.get("request_id") or self.headers.get("X-Request-ID", ""),
                    )
                )
            if parsed.path == "/api/originals/bulk-delete":
                return self.send_json(
                    delete_originals(
                        self.server.config,
                        payload.get("item_ids"),
                        actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                        request_id=payload.get("request_id") or self.headers.get("X-Request-ID", ""),
                    )
                )
            original_parts = parsed.path.strip("/").split("/")
            if len(original_parts) == 4 and original_parts[:2] == ["api", "originals"]:
                item_id = unquote(original_parts[2])
                if original_parts[3] == "approve":
                    return self.send_json(
                        approve_originals(
                            self.server.config,
                            [item_id],
                            actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                            request_id=payload.get("request_id") or self.headers.get("X-Request-ID", ""),
                        )
                    )
                if original_parts[3] == "delete":
                    return self.send_json(
                        delete_originals(
                            self.server.config,
                            [item_id],
                            actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                            request_id=payload.get("request_id") or self.headers.get("X-Request-ID", ""),
                        )
                    )
                if original_parts[3] == "download":
                    return self.send_json(
                        register_original_download(
                            self.server.config,
                            item_id,
                            actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                            request_id=payload.get("request_id") or self.headers.get("X-Request-ID", ""),
                        )
                    )
            publication_parts = parsed.path.strip("/").split("/")
            if (
                len(publication_parts) == 4
                and publication_parts[:2] == ["api", "publications"]
                and publication_parts[3] == "cancel"
            ):
                try:
                    result = cancel_publication(
                        self.server.config,
                        validated_positive_int(publication_parts[2], "publication_id", 0),
                        actor=str(payload.get("actor") or self.headers.get("X-Operator", "") or "dashboard"),
                    )
                except RuntimeError as error:
                    return self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
                return self.send_json(result, HTTPStatus.OK)
            analytics_parts = parsed.path.strip("/").split("/")
            if len(analytics_parts) == 5 and analytics_parts[:3] == ["api", "youtube-analytics", "publications"] and analytics_parts[4] == "retry":
                return self.send_json(
                    retry_publication_sync(
                        self.server.config,
                        validated_positive_int(analytics_parts[3], "publication_id", 0),
                    ),
                    HTTPStatus.OK,
                )
            if parsed.path == "/api/youtube-analytics/backfill":
                dry_run_value = payload.get("dry_run", True)
                if not isinstance(dry_run_value, bool):
                    raise ValueError("dry_run must be a boolean")
                return self.send_json(backfill_report(
                    self.server.config,
                    dry_run=dry_run_value,
                    rate_limit_per_minute=validated_positive_int(
                        payload.get("rate_limit_per_minute"), "rate_limit_per_minute", 6
                    ),
                ), HTTPStatus.OK if dry_run_value else HTTPStatus.ACCEPTED)
            if parsed.path == "/api/youtube-analytics/channel-import":
                dry_run_value = payload.get("dry_run", True)
                if not isinstance(dry_run_value, bool):
                    raise ValueError("dry_run must be a boolean")
                max_pages_value = payload.get("max_pages")
                max_pages = None if max_pages_value is None or max_pages_value == "" else validated_positive_int(
                    max_pages_value, "max_pages", 0
                )
                return self.send_json(channel_import_report(
                    self.server.config,
                    account_id=str(payload.get("account_id") or ""),
                    dry_run=dry_run_value,
                    max_pages=max_pages,
                ), HTTPStatus.OK if dry_run_value else HTTPStatus.ACCEPTED)
            if len(analytics_parts) == 5 and analytics_parts[:3] == ["api", "youtube-analytics", "backfill"] and analytics_parts[4] == "status":
                return self.send_json(set_backfill_status(
                    self.server.config,
                    validated_positive_int(analytics_parts[3], "run_id", 0),
                    str(payload.get("action") or ""),
                ), HTTPStatus.OK)
            if parsed.path == "/api/actions":
                payload["idempotency_key"] = str(
                    payload.get("idempotency_key")
                    or self.headers.get("X-Idempotency-Key", "")
                )
                can_direct_approve = self.authorized_for_admin(parsed)
                requested_target = (
                    normalize_target_area(payload.get("target_area"))
                    if str(payload.get("action") or "") == "ingest"
                    else ""
                )
                task_id = self.server.start_action(
                    payload,
                    actor=str(
                        "dashboard_admin"
                        if requested_target == TARGET_APPROVED and can_direct_approve
                        else payload.get("operator_id")
                        or self.headers.get("X-Operator", "")
                        or "dashboard"
                    ),
                    can_direct_approve=can_direct_approve,
                )
                with self.server.tasks_lock:
                    task_status = str(self.server.tasks.get(task_id, {}).get("status") or "RUNNING")
                return self.send_json(
                    {"task_id": task_id, "status": task_status},
                    HTTPStatus.OK if task_status == "COMPLETED" else HTTPStatus.ACCEPTED,
                )
            if parsed.path == "/api/copywriter/generate":
                payload = self.read_json()
                try:
                    return self.send_json(
                        {"result": generate_copywriter_with_ai(payload, self.server.config)},
                        HTTPStatus.OK,
                    )
                except RuntimeError as error:
                    return self.send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
            if parsed.path == "/api/candidates/delete":
                return self.send_json(delete_candidates(self.server.config, payload), HTTPStatus.OK)
            cta_parts = parsed.path.strip("/").split("/")
            if len(cta_parts) == 4 and cta_parts[:2] == ["api", "cta"] and cta_parts[3] == "delete":
                return self.send_json(
                    delete_cta_asset(
                        self.server.config,
                        unquote(cta_parts[2]),
                        actor=str(payload.get("actor") or self.headers.get("X-Operator", "") or "dashboard"),
                    ),
                    HTTPStatus.OK,
                )
            if parsed.path == "/api/review/manual-cut":
                return self.send_json(
                    manual_cut_review_output(
                        self.server.config,
                        str(payload.get("asset_id") or ""),
                        float(payload.get("cut_start_sec")),
                        float(payload.get("cut_end_sec")),
                        actor=str(payload.get("actor") or self.headers.get("X-Operator", "") or "dashboard"),
                    ),
                    HTTPStatus.OK,
                )
            if parsed.path == "/api/review/design-replace":
                layers = payload.get("layers")
                if not isinstance(layers, list):
                    raise ValueError("layers must be a list")
                return self.send_json(
                    replace_review_output_design(
                        self.server.config,
                        str(payload.get("asset_id") or ""),
                        layers,
                        actor=str(payload.get("actor") or self.headers.get("X-Operator", "") or "dashboard"),
                    ),
                    HTTPStatus.OK,
                )
            poster_parts = parsed.path.strip("/").split("/")
            if len(poster_parts) == 4 and poster_parts[:2] == ["api", "posters"]:
                poster_id = unquote(poster_parts[2])
                audit = {
                    "actor": payload.get("actor") or self.headers.get("X-Operator", ""),
                    "request_id": payload.get("request_id") or self.headers.get("X-Request-ID", ""),
                }
                if poster_parts[3] == "approve":
                    return self.send_json(
                        approve_poster(
                            self.server.config,
                            poster_id,
                            expected_status=payload.get("expected_status") or "",
                            **audit,
                        ),
                        HTTPStatus.OK,
                    )
                if poster_parts[3] == "delete":
                    return self.send_json(
                        delete_poster(self.server.config, poster_id, **audit), HTTPStatus.OK
                    )
                if poster_parts[3] == "content":
                    return self.send_json(
                        save_poster_content(
                            self.server.config,
                            poster_id,
                            title=payload.get("title"),
                            copy_text=payload.get("copy"),
                            tags=payload.get("tags"),
                            actor=audit["actor"],
                        ),
                        HTTPStatus.OK,
                    )
            if (
                len(poster_parts) == 5
                and poster_parts[:2] == ["api", "posters"]
                and poster_parts[3:] == ["attachments", "reorder"]
            ):
                return self.send_json({
                    "attachments": reorder_poster_attachments(
                        self.server.config,
                        unquote(poster_parts[2]),
                        payload.get("attachment_ids"),
                        actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                    )
                })
            if (
                len(poster_parts) == 6
                and poster_parts[:2] == ["api", "posters"]
                and poster_parts[3] == "attachments"
                and poster_parts[5] == "delete"
            ):
                return self.send_json(
                    delete_poster_attachment(
                        self.server.config,
                        unquote(poster_parts[2]),
                        unquote(poster_parts[4]),
                        actor=payload.get("actor") or self.headers.get("X-Operator", ""),
                    )
                )
            if parsed.path == "/api/publish/copy":
                try:
                    return self.send_json(generate_publish_copy_preview(self.server.config, payload), HTTPStatus.OK)
                except RuntimeError as error:
                    return self.send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
            if parsed.path == "/api/publications":
                if payload.get("asset_id") or payload.get("title") or payload.get("operation_type"):
                    return self.send_json(create_publish_operation(self.server.config, payload), HTTPStatus.CREATED)
                return self.send_json({"id": save_publication(self.server.config, payload)}, HTTPStatus.CREATED)
            if parsed.path == "/api/publications/status":
                return self.send_json(update_publication_status(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/x-auths":
                return self.send_json(update_x_auth(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/download-claims":
                return self.send_json(save_download_claim(self.server.config, payload), HTTPStatus.CREATED)
            if parsed.path == "/api/download-claims/metrics":
                return self.send_json(update_download_claim_metrics(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/callback":
                if not self.authorized_for_callback():
                    return self.send_json(
                        {"error": "missing or invalid callback token"},
                        HTTPStatus.UNAUTHORIZED,
                    )
                return self.send_json(save_callback(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/metrics":
                return self.send_json({"id": save_metrics(self.server.config, payload)}, HTTPStatus.CREATED)
            if parsed.path == "/api/trends/run":
                return self.send_json(run_trends_job(self.server.config), HTTPStatus.OK)
            if parsed.path == "/api/move-to-review":
                return self.send_json(
                    move_candidate_to_review(
                        self.server.config,
                        str(payload.get("candidate_id") or payload.get("id") or ""),
                    ),
                    HTTPStatus.OK,
                )
            if parsed.path == "/api/review":
                return self.send_json(save_review(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/skip":
                return self.send_json(skip_candidate(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/keywords":
                return self.send_json(save_keyword_group(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/settings":
                return self.send_json(save_system_settings(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/sessions":
                if payload.get("delete"):
                    return self.send_json(
                        delete_session(
                            self.server.config,
                            str(payload.get("platform") or ""),
                            str(payload.get("account") or ""),
                        ),
                        HTTPStatus.OK,
                    )
                if payload.get("check"):
                    return self.send_json(
                        check_session(
                            self.server.config,
                            str(payload.get("platform") or ""),
                            str(payload.get("account") or ""),
                        ),
                        HTTPStatus.OK,
                    )
                return self.send_json(save_session(self.server.config, payload), HTTPStatus.OK)
            if parsed.path == "/api/events":
                if not self.authorized_for_events():
                    return self.send_json(
                        {"error": "missing or invalid bearer token (set JAGUARTV_EVENTS_TOKEN)"},
                        HTTPStatus.UNAUTHORIZED,
                    )
                return self.send_json(save_events(self.server.config, payload), HTTPStatus.CREATED)
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (PosterError, OriginalFactoryError) as error:
            self.send_json({"error": str(error)}, HTTPStatus(error.status))
        except PermissionError as error:
            self.send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.log_error("dashboard POST failed: %r", error)
            self.send_json({"error": "internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def admin_required_path(self, path: str) -> bool:
        if path in {"/login", "/login.html", "/api/auth/login", "/api/auth/logout", "/api/auth/status"}:
            return False
        if path == "/api/health":
            return False
        if path == "/oauth/youtube/callback":
            return False
        if path == "/oauth/x/callback":
            return False
        if path in {"/api/events", "/api/callback"}:
            return False
        if path == "/api/uploads" or path.startswith("/api/uploads/"):
            return False
        if path.startswith("/media/") or path.startswith("/assets/brand/"):
            return False
        public_read_only = os.environ.get("JAGUARTV_DASHBOARD_PUBLIC", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if public_read_only and self.command in {"GET", "HEAD"}:
            return False
        return True

    def admin_token(self) -> str:
        return os.environ.get("JAGUARTV_DASHBOARD_TOKEN", "").strip()

    def cookie_session(self) -> str:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == ADMIN_COOKIE_NAME:
                return unquote(value)
        return ""

    def authorized_for_admin(self, parsed: Any | None = None) -> bool:
        token = self.admin_token()
        if not token:
            return loopback_client(self.client_address[0])
        provided = [
            self.headers.get("X-Dashboard-Token", "").strip(),
            self.headers.get("Authorization", "").removeprefix("Bearer ").strip(),
        ]
        if any(secrets.compare_digest(value, token) for value in provided if value):
            return True
        return admin_session_is_valid(token, self.cookie_session())

    def authorized_by_dashboard_access(self) -> bool:
        """Return true only for a configured and authenticated dashboard password."""
        return bool(self.admin_token()) and self.authorized_for_admin()

    def admin_session_cookie_header(self) -> str:
        token = self.admin_token()
        if not token:
            return ""
        expires_at = int(time.time()) + ADMIN_SESSION_SECONDS
        session = sign_admin_session(token, expires_at)
        return (
            f"{ADMIN_COOKIE_NAME}={quote(session)}; Path=/; Max-Age={ADMIN_SESSION_SECONDS}; "
            "HttpOnly; SameSite=Strict; Secure"
        )

    def clear_admin_cookie_header(self) -> str:
        return f"{ADMIN_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict; Secure"

    def login_dashboard(self) -> None:
        client = self.client_address[0]
        now = time.monotonic()
        with self.server.login_failures_lock:
            failures = [value for value in self.server.login_failures.get(client, []) if now - value < 300]
            self.server.login_failures[client] = failures
        if len(failures) >= 5:
            return self.send_json(
                {"error": "尝试次数过多，请等待 5 分钟后再试"},
                HTTPStatus.TOO_MANY_REQUESTS,
            )
        password = str(self.read_json().get("password") or "")
        token = self.admin_token()
        if not token:
            return self.send_json(
                {"error": "服务器尚未设置 Dashboard 访问密码"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if len(password) > 4096 or not secrets.compare_digest(password, token):
            with self.server.login_failures_lock:
                self.server.login_failures.setdefault(client, []).append(now)
            return self.send_json({"error": "访问密码不正确"}, HTTPStatus.UNAUTHORIZED)
        with self.server.login_failures_lock:
            self.server.login_failures.pop(client, None)
        return self.send_json(
            {"authenticated": True},
            cookie=self.admin_session_cookie_header(),
        )

    def send_admin_unauthorized(self, path: str) -> None:
        return self.send_json(
            {
                "error": "dashboard authentication required",
                "hint": "open /login in a browser or send X-Dashboard-Token/Authorization",
            },
            HTTPStatus.UNAUTHORIZED,
        )

    def authorized_for_events(self) -> bool:
        """Allow a logged-in operator or a machine postback bearer token.

        The token comes from the JAGUARTV_EVENTS_TOKEN environment variable.
        If it is unset, only loopback clients are accepted (local testing).
        """
        if self.authorized_by_dashboard_access():
            return True
        token = os.environ.get("JAGUARTV_EVENTS_TOKEN", "").strip()
        if not token:
            return self.client_address[0] in {"127.0.0.1", "::1"}
        header = self.headers.get("Authorization", "")
        return header.removeprefix("Bearer ").strip() == token

    def authorized_for_callback(self) -> bool:
        if self.authorized_by_dashboard_access():
            return True
        token = (
            os.environ.get("JAGUARTV_CALLBACK_TOKEN", "").strip()
            or os.environ.get("JAGUARTV_EVENTS_TOKEN", "").strip()
        )
        if not token:
            return loopback_client(self.client_address[0])
        header = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        provided = self.headers.get("X-Callback-Token", "").strip() or header
        return secrets.compare_digest(provided, token)

    def authorized_for_uploads(self) -> bool:
        if self.authorized_by_dashboard_access():
            return True
        token = os.environ.get("JAGUARTV_UPLOAD_TOKEN", "").strip()
        if not token:
            return loopback_client(self.client_address[0])
        provided = (
            self.headers.get("X-Upload-Token", "").strip()
            or self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        )
        return bool(provided) and secrets.compare_digest(provided, token)

    def authorized_for_upload_kind(self, kind: str) -> bool:
        if not upload_kind_requires_token(kind):
            return True
        return self.authorized_for_uploads()

    def pending_upload_kind(self, upload_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", str(upload_id or "")):
            return ""
        try:
            base = storage_root(self.server.config) / "uploads" / ".pending" / upload_id
            manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
            return str(manifest.get("kind") or "").lower()
        except (OSError, ValueError, json.JSONDecodeError):
            return ""

    def valid_upload_signature(self, upload_id: str, query: dict[str, list[str]]) -> bool:
        secret = os.environ.get("JAGUARTV_UPLOAD_SIGNING_SECRET", "").strip() or os.environ.get(
            "JAGUARTV_UPLOAD_TOKEN", ""
        ).strip()
        if not secret or not re.fullmatch(r"[a-f0-9]{32}", str(upload_id or "")):
            return False
        try:
            expires = int(str((query.get("expires") or [""])[0]))
        except (TypeError, ValueError):
            return False
        now = int(time.time())
        if expires < now or expires > now + 7 * 24 * 3600:
            return False
        provided = str((query.get("signature") or [""])[0]).strip()
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{upload_id}:{expires}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return bool(provided) and secrets.compare_digest(provided, expected)

    def send_private_upload(self, parsed: Any, *, head_only: bool) -> None:
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4:
            return self.send_error(HTTPStatus.NOT_FOUND)
        upload_id = parts[2]
        query = parse_qs(parsed.query)
        if not self.authorized_for_uploads() and not self.valid_upload_signature(upload_id, query):
            return self.send_json({"error": "private download link is invalid or expired"}, HTTPStatus.UNAUTHORIZED)
        try:
            item = find_upload(self.server.config, upload_id)
        except ValueError:
            return self.send_error(HTTPStatus.NOT_FOUND)
        original = str(item.get("original_filename") or Path(str(item["path"])).name)
        self.send_file(
            Path(str(item["path"])),
            cache="private, no-store",
            disposition=f"attachment; filename=media{Path(original).suffix}; filename*=UTF-8''{quote(original)}",
            head_only=head_only,
        )

    def send_candidate_source(self, parsed: Any, *, head_only: bool) -> None:
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4:
            return self.send_error(HTTPStatus.NOT_FOUND)
        source = candidate_source_media(self.server.config, parts[2])
        if source is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_file(source, cache="private, no-store", head_only=head_only)

    def send_poster_asset(
        self,
        poster_id: str,
        *,
        download: bool,
        thumbnail: bool = False,
        head_only: bool = False,
    ) -> None:
        path = resolve_poster_file(
            self.server.config,
            poster_id,
            require_approved=download,
            thumbnail=thumbnail,
        )
        disposition = "inline"
        if download:
            filename = poster_download_name(self.server.config, poster_id)
            fallback = f"poster{path.suffix.lower()}"
            disposition = f"attachment; filename={fallback}; filename*=UTF-8''{quote(filename)}"
        self.send_file(
            path,
            cache="private, no-store",
            disposition=disposition,
            head_only=head_only,
        )

    def send_original_asset(
        self,
        item_id: str,
        *,
        download: bool,
        thumbnail: bool = False,
        head_only: bool = False,
    ) -> None:
        path = resolve_original_file(
            self.server.config,
            item_id,
            require_approved=download,
            thumbnail=thumbnail,
        )
        disposition = "inline"
        if download:
            filename = original_download_name(self.server.config, item_id)
            disposition = f"attachment; filename=original-video.mp4; filename*=UTF-8''{quote(filename)}"
        self.send_file(
            path,
            cache="private, no-store",
            disposition=disposition,
            head_only=head_only,
        )

    def send_poster_attachment(self, poster_id: str, attachment_id: str) -> None:
        path = resolve_poster_attachment(self.server.config, poster_id, attachment_id)
        self.send_file(
            path,
            cache="private, no-store",
            disposition="inline",
            head_only=False,
        )

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def send_json(
        self,
        value: Any,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        cookie: str = "",
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def send_youtube_oauth_callback(self, query: dict[str, list[str]]) -> None:
        try:
            result = save_youtube_oauth_callback(self.server.config, query)
        except Exception as error:
            return self.send_html(
                oauth_result_html(
                    "YouTube 授权未完成",
                    [
                        str(error),
                        "请从 /oauth/youtube/start?account=consumer_football 重新开始授权。",
                        "确认服务器 .env 已配置 JAGUARTV_GOOGLE_CLIENT_ID、JAGUARTV_GOOGLE_CLIENT_SECRET、JAGUARTV_OAUTH_TOKEN_KEY。",
                    ],
                    ok=False,
                ),
                HTTPStatus.BAD_REQUEST,
            )
        return self.send_html(
            oauth_result_html(
                "YouTube 授权成功",
                [
                    f"账号配置：{result['account']}",
                    f"频道：{result['channel_title']}",
                    f"Channel ID：{result['channel_id']}",
                    f"授权时间：{result['authorized_at']}",
                    "refresh token 已加密保存到服务器数据库。",
                ],
                ok=True,
            )
        )

    def send_x_oauth_callback(self, query: dict[str, list[str]]) -> None:
        try:
            result = save_x_oauth_callback(self.server.config, query)
        except Exception as error:
            return self.send_html(
                oauth_result_html(
                    "X 授权未完成",
                    [
                        str(error),
                        "请从 /oauth/x/start?account=consumer_main 重新开始授权。",
                        "确认服务器 .env 已配置 JAGUARTV_X_CLIENT_ID、JAGUARTV_OAUTH_TOKEN_KEY 和正确的 X 回调地址。",
                    ],
                    ok=False,
                ),
                HTTPStatus.BAD_REQUEST,
            )
        return self.send_html(
            oauth_result_html(
                "X 授权已更新" if result.get("duplicate") else "X 授权待确认",
                [
                    f"授权位：{X_ACCOUNT_SLOTS.index(result['account']) + 1}",
                    f"实际授权账号：@{result['username']}",
                    f"X User ID：{result['x_user_id']}",
                    f"授权时间：{result['authorized_at']}",
                    "access token 和 refresh token 已加密保存。",
                    (
                        "该 X 账号已经绑定，系统已更新原授权位的 Token，没有创建重复绑定。"
                        if result.get("duplicate")
                        else "请回到发布队列页核对账号后点击确认，确认后系统会立即检测视频发布权限。"
                    ),
                ],
                ok=True,
            )
        )

    def send_static(self, requested: str) -> None:
        if requested.startswith("/assets/brand/"):
            path = public_brand_asset_path(requested)
            if path is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_file(path, cache="public, max-age=3600")
        relative = "index.html" if requested in {"", "/"} else requested.lstrip("/")
        path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in path.parents or not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_file(path, cache="no-cache")

    def send_media(self, relative: str, download: bool = False) -> None:
        relative = unquote(relative)
        if relative.startswith(("review/", "cta/")):
            root = storage_root(self.server.config)
            path = (root / relative).resolve()
        else:
            root = (workspace_dir(self.server.config) / "ready_for_review").resolve()
            path = (root / relative).resolve()
        if root not in path.parents or not path.is_file() or "uploads" in path.parts:
            return self.send_error(HTTPStatus.NOT_FOUND)
        disposition = ""
        if download:
            filename = path.name if path.name != "video.mp4" else f"{path.parent.name}{path.suffix}"
            disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        self.send_file(path, cache="private, max-age=60", disposition=disposition)

    def send_file(
        self,
        path: Path,
        cache: str,
        disposition: str = "",
        head_only: bool = False,
    ) -> None:
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        partial = False
        if range_header and range_header.startswith("bytes="):
            requested = range_header.removeprefix("bytes=").split(",", 1)[0]
            start_text, end_text = requested.split("-", 1)
            start = int(start_text) if start_text else 0
            end = min(size - 1, int(end_text)) if end_text else size - 1
            if start > end or start >= size:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            partial = True
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or head_only:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining and (chunk := handle.read(min(256 * 1024, remaining))):
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


def serve_dashboard(config: dict[str, Any], host: str = "127.0.0.1", port: int = 8787) -> None:
    register_coordinator(config, port)
    start_trends_scheduler(config)
    server = DashboardApplication((host, port), config)
    print(f"JaguarTV Content OS: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
