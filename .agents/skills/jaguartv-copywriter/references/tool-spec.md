# JaguarTV Copywriter Tool Spec

## Purpose

The copywriter is a browser tool for Chinese-speaking operators who need Brazilian Portuguese copy and a Chinese review translation.

The current page is `/copywriter.html` served by the JaguarTV dashboard. It is not only a static page: AI generation requires the Python dashboard API.

## User Experience

Inputs:

- Mode: `tv` or `generic`
- Chinese keywords/topic
- Platform: YouTube Shorts, TikTok, Kwai, Facebook Reels, WhatsApp, Email, or SEO
- Tone: viral, trust, urgent, friendly
- CTA/action
- Variant count: 3, 5, or 8
- Viral intensity: 1-10

Outputs:

- pt-BR strategy summary
- pt-BR short-video titles/hooks
- pt-BR platform captions
- CTA and hashtags
- Email follow-up sequence
- SEO title/description/keywords
- pt-BR note
- Chinese audit translation beneath the pt-BR result

## Generation Rules

TV product mode:

- May mention JaguarTV, Jarg.top, live TV, sports, movies, series, Android phone, Android TV, TV box, and Brazil.
- Should drive toward download/testing via Jarg.top.
- Must avoid unsupported claims about copyrighted channels, free access, price, guarantees, and regional availability.

Generic content mode:

- Must generate publishable content directly about the user's keywords.
- Must not give meta-advice such as "how to market this topic" unless the user explicitly asks for strategy.
- Must not mention JaguarTV, Jarg.top, TV ao vivo, Android TV, or any TV product/download site.
- Example: `足球，巴西街头足球挑战` should become content about a Brazilian street-football challenge, not generic marketing advice.

Chinese audit:

- Translate or explain the pt-BR meaning for review.
- Keep it below the pt-BR sections.
- Include it in copy-all output.

## Backend API

Endpoint:

```text
POST /api/copywriter/generate
```

Request shape:

```json
{
  "input": "足球，巴西街头足球挑战",
  "mode": "generic",
  "platform": "tiktok",
  "tone": "viral",
  "heat": 7,
  "count": 3,
  "cta": "Saiba mais"
}
```

Response shape:

```json
{
  "result": {
    "mode": "generic",
    "strategy": "...",
    "titles": ["..."],
    "captions": ["..."],
    "cta": "...",
    "hashtags": "...",
    "emails": [{"name": "...", "subject": "...", "preview": "...", "body": "...", "cta": "..."}],
    "seo": {"title": "...", "description": "...", "keywords": ["..."]},
    "zhAudit": {
      "strategy": "...",
      "titles": ["..."],
      "captions": ["..."],
      "cta": "...",
      "hashtags": "...",
      "emails": [{"name": "...", "subject": "...", "preview": "...", "body": "...", "cta": "..."}],
      "seo": {"title": "...", "description": "...", "keywords": ["..."]}
    },
    "note": "...",
    "source": "openai",
    "model": "..."
  }
}
```

If the endpoint returns non-OK or times out in the browser, `copywriter.js` falls back to local templates and shows a fallback note.

## AI Integration

Server-side functions live in `src/jaguartv_factory/dashboard.py`:

- `copywriter_request`
- `copywriter_prompt`
- `extract_json_object`
- `normalize_ai_copywriter_result`
- `generate_copywriter_with_ai`
- `copywriter_ai_model_name`
- `copywriter_ai_model_candidates`

Model handling:

- Primary model: `gpt-5.2` through the OpenAI Responses endpoint.
- Fallback model: `deepseek-v4-flash` through a DeepSeek-compatible chat completions endpoint.
- Missing key, HTTP error, timeout, empty text, invalid JSON, or failed normalization counts as "not callable" and triggers the next provider.

## Local Runbook

Start the dashboard:

```bash
cd "/Users/jaguar/Documents/ChatGPT/jaguar视频二创"
export JAGUARTV_OPENAI_API_KEY="..."
export JAGUARTV_OPENAI_MODEL="gpt-5.2"
export JAGUARTV_DEEPSEEK_API_KEY="..."
export JAGUARTV_DEEPSEEK_MODEL="deepseek-v4-flash"
PYTHONPATH="$PWD/src" /Users/jaguar/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m jaguartv_factory.cli ui --host 127.0.0.1 --port 8788
```

Open:

```text
http://127.0.0.1:8788/copywriter.html
```

Health check:

```bash
curl -s http://127.0.0.1:8788/api/health | python3 -m json.tool
```

Generation check:

```bash
curl -s --max-time 75 -X POST http://127.0.0.1:8788/api/copywriter/generate \
  -H 'Content-Type: application/json' \
  --data '{"input":"足球，巴西街头足球挑战","mode":"generic","platform":"tiktok","tone":"viral","count":3,"heat":7,"cta":"Saiba mais"}'
```

Temporary tunnel:

```bash
npx --yes localtunnel --port 8788 --local-host 127.0.0.1 --subdomain jaguartv-copywriter-ai
```

Localtunnel may show a reminder/password page. The password is usually the current `x-localtunnel-agent-ips` value from the response headers.

## Production Notes

Localtunnel is not stable production hosting. For "any computer can open it long-term", deploy the dashboard behind a real server/domain and store `JAGUARTV_OPENAI_API_KEY` and `JAGUARTV_DEEPSEEK_API_KEY` in server environment variables or secret storage.

Do not deploy the tool as plain static HTML if AI generation is required; `/api/copywriter/generate` must be available.
