---
name: jaguartv-copywriter
description: Maintain and extend the JaguarTV Brazilian Portuguese copywriter web tool. Use when working on copywriter.html/copywriter.js/copywriter.css, the /api/copywriter/generate AI proxy, Chinese-to-pt-BR marketing/content generation, GPT/DeepSeek model configuration, public preview tunnels, or tests around the copy generator.
---

# JaguarTV Copywriter

Maintain the Chinese-to-Brazilian-Portuguese copy generator for JaguarTV.

The tool has two modes:

1. `tv`: JaguarTV product promotion for Brazilian users.
2. `generic`: content copy based only on the user's Chinese keywords, with no TV product mentions.

## Files

- Frontend: `src/jaguartv_factory/web/copywriter.html`
- UI styles: `src/jaguartv_factory/web/copywriter.css`
- Client generator and fallback templates: `src/jaguartv_factory/web/copywriter.js`
- Server API and AI proxy: `src/jaguartv_factory/dashboard.py`
- Tests: `tests/test_dashboard.py`
- Product/factory operating skill: `.agents/skills/jaguartv-content-factory/SKILL.md`

Read `references/tool-spec.md` when changing behavior, AI integration, deployment, or public access.

## Guardrails

- Never hardcode API keys or any secret. Use environment variables only.
- Keep browser code free of API keys; GPT and DeepSeek calls must go through `/api/copywriter/generate`.
- Preserve the `generic` mode boundary: it must not output `JaguarTV`, `Jarg.top`, `TV ao vivo`, Android TV, or product/download-site claims.
- In `tv` mode, avoid unverifiable promises about copyrighted channels, prices, free access, guaranteed availability, or regional coverage.
- Keep the Chinese audit translation below the pt-BR output and include it in copy-all text.
- Treat localtunnel URLs as temporary previews, not production deployment.

## Workflow

1. Inspect the current implementation with `rg -n "copywriter|gpt|openai|deepseek|generate_copywriter"`.
2. For generation behavior changes, update the AI prompt/normalization in `dashboard.py` and local fallback templates in `copywriter.js`.
3. For UI changes, keep controls accessible, responsive, and consistent with the existing dense tool layout.
4. Verify with:

```bash
python3 -m py_compile src/jaguartv_factory/dashboard.py tests/test_dashboard.py
PYTHONPATH=src /Users/jaguar/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_dashboard.py -q
node --check src/jaguartv_factory/web/copywriter.js
git diff --check -- src/jaguartv_factory/dashboard.py src/jaguartv_factory/web/copywriter.js src/jaguartv_factory/web/copywriter.css tests/test_dashboard.py
```

5. For browser verification, start the dashboard and test both modes:

```bash
cd "/Users/jaguar/Documents/ChatGPT/jaguar视频二创"
PYTHONPATH="$PWD/src" /Users/jaguar/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m jaguartv_factory.cli ui --host 127.0.0.1 --port 8788
```

6. If the user needs temporary public access, tunnel the running dashboard, not a static server:

```bash
npx --yes localtunnel --port 8788 --local-host 127.0.0.1 --subdomain jaguartv-copywriter-ai
```

## AI Defaults

Use these environment variables when the user wants real model generation:

```bash
export JAGUARTV_OPENAI_API_KEY="..."
export JAGUARTV_OPENAI_MODEL="gpt-5.2"
export JAGUARTV_DEEPSEEK_API_KEY="..."
export JAGUARTV_DEEPSEEK_MODEL="deepseek-v4-flash"
```

The backend tries `gpt-5.2` first. If GPT is not callable, fails, or returns invalid JSON, it retries the same prompt with `deepseek-v4-flash`.
