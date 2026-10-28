"""LLM client for the Red Sun Japan pipeline.

Phase 3: real OpenRouter integration.

- Four named functions: script, blog, shorts, translate.
- Each function loads its prompt pair from ``prompts/<name>_{system,user}.md``,
  so copy can be iterated without touching Python.
- All calls funnel through ``_call_openrouter`` so OpenRouter-specific quirks
  (HTTP-Referer, X-Title, system vs. user messages, JSON mode) live in one place.

Design choices baked in:

- Chat Completions endpoint (not the newer ``/messages`` style Anthropic-style
  API). OpenRouter normalizes both, but ``/chat/completions`` is the universal
  entry point across all routed models.
- JSON mode is requested **only** for ``identify_short_clips``, where structured
  output is required. ``generate_blog_article_from_script`` could request it
  too but the frontmatter parsing is forgiving enough that plain prose works.
- We never include the source text both in the system prompt and the user
  prompt. Long context is one-shot: it all goes in user.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("redsun.llm")

PROMPTS_DIR = Path(__file__).parent / "prompts"

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class LLMConfig:
    """Resolved OpenRouter config. Read once at startup; reused per call."""

    api_key: str | None
    base_url: str
    model: str
    model_literary: str = "anthropic/claude-sonnet-5"
    model_utility: str = "google/gemini-2.5-flash"
    # Extended-thinking control. Claude Sonnet 5 defaults to a high reasoning
    # budget which silently consumes ~5,500 tokens before writing anything
    # (verified: script call returned reasoning_tokens=5549, completion_tokens=450).
    # For documentary-script tasks the reasoning does not measurably improve
    # the prose (the prompt already constrains cadence, vocabulary, and structure),
    # so we default to "disabled" and let the operator opt-in via env var.
    # Valid values:
    #   "disabled"           - no reasoning block; cheapest, fastest
    #   "enabled"            - open-ended reasoning (Claude decides budget)
    #   {"effort": "low|medium|high"} - cap reasoning budget
    #   {"max_tokens": N}    - hard cap on reasoning tokens
    # See https://openrouter.ai/docs/features/reasoning
    reasoning: str | dict = "disabled"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        raw_reasoning = os.environ.get("OPENROUTER_REASONING", "disabled")
        parsed_reasoning: str | dict
        if raw_reasoning.strip().startswith("{"):
            try:
                parsed_reasoning = json.loads(raw_reasoning)
            except json.JSONDecodeError:
                logger.warning(
                    "OPENROUTER_REASONING='%s' is not valid JSON; falling back to 'disabled'",
                    raw_reasoning,
                )
                parsed_reasoning = "disabled"
        else:
            parsed_reasoning = raw_reasoning.strip() or "disabled"

        model_literary = (
            os.environ.get("MODEL_LITERARY")
            or os.environ.get("OPENROUTER_MODEL")
            or "anthropic/claude-sonnet-5"
        )
        model_utility = (
            os.environ.get("MODEL_UTILITY")
            or "google/gemini-2.5-flash"
        )
        default_model = os.environ.get("OPENROUTER_MODEL") or model_literary

        return cls(
            api_key=os.environ.get("OPENROUTER_API_KEY") or None,
            base_url=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            model=default_model,
            model_literary=model_literary,
            model_utility=model_utility,
            reasoning=parsed_reasoning,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def with_model(self, model_name: str) -> "LLMConfig":
        """Return a copy of this config configured with a specific model slug."""
        return LLMConfig(
            api_key=self.api_key,
            base_url=self.base_url,
            model=model_name,
            model_literary=self.model_literary,
            model_utility=self.model_utility,
            reasoning=self.reasoning,
        )

    def for_literary(self) -> "LLMConfig":
        """Route to literary model (e.g. Claude Sonnet 5 for script and blog)."""
        return self.with_model(self.model_literary)

    def for_utility(self) -> "LLMConfig":
        """Route to utility model (e.g. Gemini 2.5 Flash for review, shorts, images)."""
        return self.with_model(self.model_utility)


@dataclass
class LLMResult:
    """What each public function returns. ``raw_text`` keeps the model output
    untouched for debugging; ``parsed`` is whatever shape-specific parser
    extracted (e.g. JSON dict for shorts, plain text elsewhere).

    Cost fields (cost_usd, generation_id, native_usage) are populated by
    ``_call_openrouter`` from the ``X-OpenRouter-Cost`` response header and
    the ``usage`` block. Anthropic-native fields (cache_read_input_tokens
    etc.) are surfaced via ``native_usage`` for future per-episode reporting.
    """

    text: str
    parsed: object = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_sec: float = 0.0
    finish_reason: str = ""    # OpenRouter's stop / length / content_filter
    generation_id: str = ""    # OpenRouter gen-XXXX id (for /api/v1/generation lookup)
    native_usage: dict | None = None  # raw usage dict (cache tokens, etc.)


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────
CONTENT_ATTENUATION_GUIDELINES = """## MANDATORY CONTENT ATTENUATION & DRAMATIC TRANSPOSITION (PG-13 PRESTIGE CRITERIA)
Classical Japanese texts often depict visceral cruelty, dismemberment, and explicit carnal themes. You must NEVER omit historical narrative arcs or moral consequences, but you must strictly transpose visceral and explicit elements into refined, atmospheric psychological suspense suitable for a prestige historical documentary:

1. EROTICISM, LASCIVIOUSNESS & SEDUCTION:
   - NEVER depict explicit nudity, vulgar carnal acts, or crude sexual descriptions.
   - TRANSPOSE carnal interactions into psychological allure, hypnotic enchantment, tragic obsession, or spiritual temptation.
   - Focus on atmospheric sensory intimacy: dim paper lantern glow, the quiet whisper of silk robes, lingering gaze, an intoxicating unnatural fragrance, and the protagonist's gradual loss of willpower. Keep it dignified, eerie, and suggestive.

2. VISCERAL VIOLENCE, GORE & MUTILATION:
   - NEVER describe butchery, dismemberment, open wounds, spurting blood, or torture mechanics.
   - TRANSPOSE visceral gore into psychological dread, sound design, lingering shadows, and emotional aftermath.
   - Utilize cinematic suggestion: describe the sudden extinguished flame, the sharp intake of breath, a muffled strike behind a paper shoji screen, the trembling hands of a horrified witness, and the grim stillness left in the room. Blood becomes "dark stillness" or "a tragedy sealed in shadows".

3. PRESERVATION OF FOLKLORIC & HISTORICAL WEIGHT:
   - Do NOT sanitize the consequence of the story. If a betrayal leads to death or curse, the death and curse MUST happen. Only the physical depiction of cruelty is elevated from grotesque splatter to haunting cinematic tension.
"""


def _compile_content_attenuation(system_prompt: str, content_rating: str = "pg13_prestige") -> str:
    """Ensure content attenuation guidelines are compiled into the system prompt."""
    if "MANDATORY CONTENT ATTENUATION & DRAMATIC TRANSPOSITION" in system_prompt:
        return system_prompt
    return f"{system_prompt}\n\n{CONTENT_ATTENUATION_GUIDELINES}"


def _load_prompt_pair(name: str) -> tuple[str, str]:
    """Load ``prompts/<name>_system.md`` and ``<name>_user.md``.

    Both files must exist. Raises FileNotFoundError with a clear message
    so a missing prompt surfaces immediately, not as a generic 500.
    """
    sys_path = PROMPTS_DIR / f"{name}_system.md"
    usr_path = PROMPTS_DIR / f"{name}_user.md"
    if not sys_path.exists():
        raise FileNotFoundError(
            f"prompt file missing: {sys_path}. "
            f"Phase 3 expects prompts/<name>_system.md for every public function."
        )
    if not usr_path.exists():
        raise FileNotFoundError(f"prompt file missing: {usr_path}")
    return sys_path.read_text(encoding="utf-8"), usr_path.read_text(encoding="utf-8")


def _fill_user_template(user_template: str, **vars) -> str:
    """Substitute ``{var}`` placeholders. Missing keys raise KeyError."""
    # We do this manually rather than ``str.format`` so the rest of the prompt
    # body (which itself may contain ``{`` braces in JSON examples) doesn't
    # have to escape every one.
    out = user_template
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _strip_markdown_fences(text: str) -> str:
    """Strip leading/trailing ```json ... ``` fences that some models add."""
    text = text.strip()
    # Match ```json / ```JSON / ``` at the very start, optionally with lang.
    m = re.match(r"^```(?:json|JSON)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def extract_json_safely(raw_text: str) -> dict | list | None:
    """Resiliently extract a JSON object or array from LLM response text.

    Handles markdown code fences (```json ... ```), surrounding prose,
    and trailing commas before brackets.
    """
    if not raw_text or not str(raw_text).strip():
        return None

    cleaned = _strip_markdown_fences(raw_text.strip())

    # Fast path: direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Strategy 1: regex search for markdown fence contents anywhere in text
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw_text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        cand = fence_match.group(1).strip()
        try:
            return json.loads(cand)
        except Exception:
            cleaned = cand

    # Strategy 2: determine whether outermost structure is an array or an object
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")

    parse_order = []
    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        parse_order = [("[", "]"), ("{", "}")]
    else:
        parse_order = [("{", "}"), ("[", "]")]

    for open_char, close_char in parse_order:
        start = cleaned.find(open_char)
        end = cleaned.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start : end + 1].strip()
            try:
                return json.loads(candidate)
            except Exception:
                # Try cleaning trailing commas: ,} -> } or ,] -> ]
                cand_no_commas = re.sub(r",\s*([\}\]])", r"\1", candidate)
                try:
                    return json.loads(cand_no_commas)
                except Exception:
                    pass

    return None


def _call_openrouter(
    system_prompt: str,
    user_prompt: str,
    *,
    cfg: LLMConfig,
    json_mode: bool = False,
    max_retries: int = 3,
    temperature: float = 0.7,
    max_tokens: int = 4000,
) -> LLMResult:
    """Single OpenRouter Chat Completions call with retry on transient errors.

    Raises ``PermissionError`` if the key is missing or invalid.
    Raises ``RuntimeError`` if all retries fail.
    """
    if not cfg.is_configured:
        raise PermissionError(
            "OPENROUTER_API_KEY is not configured. "
            "Add it to motor-python/.env before running Phase 3."
        )

    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        # OpenRouter attribution headers — required for proper app ranking
        # and analytics on their side. They are public; they identify the
        # caller app, not the user.
        "HTTP-Referer": os.environ.get("BRAND_DOMAIN", "https://redsunjapan.com"),
        "X-Title": os.environ.get("BRAND_NAME", "Red Sun Japan"),
    }

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # OpenRouter ``reasoning`` parameter (https://openrouter.ai/docs/features/reasoning).
    # For Claude Sonnet 5 the prompt's structure already enforces cadence and
    # vocabulary; reasoning adds latency + cost without measurable prose gain.
    # Opt-in via OPENROUTER_REASONING env var or by passing ``cfg.reasoning``.
    if cfg.reasoning and cfg.reasoning != "disabled":
        payload["reasoning"] = cfg.reasoning

    url = f"{cfg.base_url.rstrip('/')}/chat/completions"

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            elapsed = time.perf_counter() - t0

            if resp.status_code == 401 or resp.status_code == 403:
                raise PermissionError(
                    f"OpenRouter auth failed ({resp.status_code}): {resp.text[:300]}"
                )
            if resp.status_code == 429:
                # Rate-limited. Wait briefly and retry.
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "OpenRouter 429: backing off %ds (attempt %d/%d)",
                        wait, attempt, max_retries,
                    )
                    time.sleep(wait)
                    continue
            if resp.status_code >= 500:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "OpenRouter %d: backing off %ds (attempt %d/%d)",
                        resp.status_code, wait, attempt, max_retries,
                    )
                    time.sleep(wait)
                    continue
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "OpenRouter request error: %s. Retry in %ds (%d/%d)",
                    exc, wait, attempt, max_retries,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"OpenRouter call failed after {max_retries} attempts: {exc}"
            ) from exc

        # Parse successful response.
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}

        # ── Cost capture ─────────────────────────────────────────────────
        # OpenRouter returns the per-request cost in *one of two places*
        # depending on the upstream provider and pricing transparency:
        #   1. ``data["usage"]["cost"]`` — populated when OpenRouter has
        #      negotiated upstream pricing (Anthropic Claude, OpenAI, etc.).
        #      This is the authoritative number when present.
        #   2. ``X-OpenRouter-Cost`` response header — older convention;
        #      emitted by some providers but not Anthropic.
        # We prefer (1), fall back to (2), and never overwrite a real value
        # with 0.0 if the other source has data.
        cost_usd = 0.0
        # Source 1: usage.cost (Anthropic Claude path).
        try:
            usage_cost = usage.get("cost")
            if usage_cost is not None:
                cost_usd = float(usage_cost)
        except (TypeError, ValueError):
            pass
        # Source 2: header fallback (legacy / non-Anthropic providers).
        if cost_usd == 0.0:
            try:
                cost_header = resp.headers.get("X-OpenRouter-Cost") or resp.headers.get(
                    "x-openrouter-cost"
                )
                if cost_header:
                    cost_usd = float(cost_header)
            except (TypeError, ValueError):
                pass
        generation_id = data.get("id") or ""

        # Log so the operator can see cost + latency in real time, even
        # before the cost_tracking table is populated.
        logger.info(
            "OpenRouter: model=%s id=%s prompt_tokens=%d completion_tokens=%d "
            "cost_usd=$%.6f elapsed=%.2fs finish=%s",
            data.get("model", cfg.model),
            generation_id or "-",
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            cost_usd,
            elapsed,
            choice.get("finish_reason", ""),
        )

        return LLMResult(
            text=text,
            parsed=None,    # populated by caller if applicable
            model=data.get("model", cfg.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost_usd=cost_usd,
            elapsed_sec=elapsed,
            finish_reason=choice.get("finish_reason", "") or "",
            generation_id=generation_id,
            native_usage=dict(usage) if usage else None,
        )

    raise RuntimeError(
        f"OpenRouter call failed after {max_retries} attempts: {last_exc}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cost tracking (Option B)
# ─────────────────────────────────────────────────────────────────────────────
def record_cost(
    result: LLMResult,
    *,
    operation: str,
    project_id: int | None = None,
    translation_variant_id: int | None = None,
) -> int | None:
    """Persist an OpenRouter call's cost into the ``cost_tracking`` table.

    Returns the new ``CostTracking.id`` on success, ``None`` if recording was
    skipped (e.g. ``generation_id`` already exists — idempotent on retries).

    Failures are logged but never raised — cost tracking must not break a
    generation run. The ``cost_tracking.generation_id`` unique constraint
    makes the call safe to retry.
    """
    # Local import to keep the module importable in environments where the
    # database is offline (e.g. dry-run smoke tests on a fresh checkout).
    try:
        from database import SessionLocal, CostTracking, init_schema
        init_schema()  # idempotent; no-op if tables already exist
    except Exception as exc:
        logger.warning("cost_tracking: database unavailable, skipping record: %s", exc)
        return None

    session = SessionLocal()
    try:
        # Idempotency: if this generation_id is already recorded, skip.
        if result.generation_id:
            existing = (
                session.query(CostTracking)
                .filter(CostTracking.generation_id == result.generation_id)
                .one_or_none()
            )
            if existing is not None:
                logger.info(
                    "cost_tracking: generation_id=%s already recorded (#%d), skip",
                    result.generation_id, existing.id,
                )
                return existing.id

        row = CostTracking(
            project_id=project_id,
            translation_variant_id=translation_variant_id,
            operation=operation,
            model=result.model,
            generation_id=result.generation_id or None,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
            cost_usd=result.cost_usd,
            elapsed_sec=result.elapsed_sec,
            finish_reason=result.finish_reason or None,
            native_usage=result.native_usage,
        )
        session.add(row)
        session.commit()
        logger.info(
            "cost_tracking: recorded #%d op=%s model=%s cost=$%.6f "
            "prompt=%d completion=%d elapsed=%.2fs gen=%s",
            row.id, operation, result.model, result.cost_usd,
            result.prompt_tokens, result.completion_tokens,
            result.elapsed_sec, result.generation_id or "-",
        )
        return row.id
    except Exception as exc:
        session.rollback()
        logger.warning("cost_tracking: failed to record cost row: %s", exc)
        return None
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Public API: scripts
# ─────────────────────────────────────────────────────────────────────────────
def review_script_from_sources(
    english_script: str,
    *,
    topic: str = "Untitled topic",
    source_type: str = "classical_kaidan",
    atmosphere: str = "lantern-light-and-fog",
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Review an English narration script for readiness, quality, and TTS safety."""
    sys_p, usr_p = _load_prompt_pair("script_review")
    cfg = (cfg or LLMConfig.from_env()).for_utility()
    user = _fill_user_template(
        usr_p,
        topic=topic,
        source_type=source_type,
        atmosphere=atmosphere,
        script=english_script.strip(),
    )
    logger.info("review_script: model=%s, script_chars=%d", cfg.model, len(english_script))
    result = _call_openrouter(
        sys_p, user, cfg=cfg,
        max_tokens=6000,
        temperature=0.4,
    )
    result.parsed = extract_json_safely(result.text)
    return result


def generate_script_from_sources(
    source_text_ja: str,
    *,
    topic: str = "Untitled topic",
    target_keywords: Iterable[str] | None = None,
    content_rating: str = "pg13_prestige",
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Translate + adapt Japanese source into an English documentary script.

    The caller is responsible for persisting ``result.text`` on a
    ``TranslationVariant`` row.
    """
    sys_p, usr_p = _load_prompt_pair("script")
    sys_p = _compile_content_attenuation(sys_p, content_rating=content_rating)
    cfg = (cfg or LLMConfig.from_env()).for_literary()
    keywords_csv = ", ".join(target_keywords or []) or "(none)"
    user = _fill_user_template(
        usr_p,
        topic=topic,
        target_keywords=keywords_csv,
        source_text_ja=source_text_ja.strip(),
    )
    logger.info("generate_script: model=%s, source_chars=%d, rating=%s", cfg.model, len(source_text_ja), content_rating)
    # Claude-tuned: temperature 0.65 (solemn, documentary cadence; low enough
    # to suppress anachronistic language and date hallucination); max_tokens
    # 10000 so a full 1,800–2,200 word script never truncates mid-scene
    # (verified: at 6000 the script capped at ~744 words; 10000 leaves
    # ~30% headroom for the closing reflection without exceeding the
    # OpenRouter per-request cap for Claude Sonnet 5).
    result = _call_openrouter(
        sys_p, user, cfg=cfg,
        max_tokens=10000,
        temperature=0.65,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API: blog article
# ─────────────────────────────────────────────────────────────────────────────
def generate_blog_article_from_script(
    english_script: str,
    *,
    topic: str = "Untitled topic",
    target_keywords: Iterable[str] | None = None,
    content_rating: str = "pg13_prestige",
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Convert an English video script into an SEO-optimized Markdown article.

    The output is designed to be saved directly to
    ``blog_articles/{slug}.en.md`` and ingested by the Astroplate blog.
    """
    sys_p, usr_p = _load_prompt_pair("blog")
    sys_p = _compile_content_attenuation(sys_p, content_rating=content_rating)
    cfg = (cfg or LLMConfig.from_env()).for_literary()
    keywords_csv = ", ".join(target_keywords or []) or "(none)"
    user = _fill_user_template(
        usr_p,
        topic=topic,
        target_keywords=keywords_csv,
        script=english_script.strip(),
    )
    logger.info("generate_blog_article: model=%s, script_chars=%d, rating=%s", cfg.model, len(english_script), content_rating)
    # Claude-tuned: temperature 0.65 (consistent Markdown H2/H3 structure,
    # restrained tone, no flourish that breaks the docile reading rhythm);
    # max_tokens 10000 to cover long frontmatter + 8–14 body paragraphs +
    # Sources & Further Reading without truncation (verified: at 6000 the
    # generation was finishing around 1,392 words / 8,776 chars; 10000
    # gives ~30% headroom for the closing reflective paragraph).
    result = _call_openrouter(
        sys_p, user, cfg=cfg,
        max_tokens=10000,
        temperature=0.65,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API: shorts identification
# ─────────────────────────────────────────────────────────────────────────────
def identify_short_clips(
    english_script: str,
    *,
    topic: str = "Untitled topic",
    max_clips: int = 5,
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Identify self-contained 15–60s clip candidates from a long-form script.

    Returns an ``LLMResult`` whose ``.parsed`` is a Python dict matching the
    schema in ``prompts/shorts_system.md``:

        {"clips": [{"hook": str, "summary": str, "key_phrase": str,
                    "reason": str}, ...],
         "pacing_notes": str}

    Raises ``ValueError`` if the model output is not parseable JSON after
    code-fence stripping.
    """
    sys_p, usr_p = _load_prompt_pair("shorts")
    cfg = (cfg or LLMConfig.from_env()).for_utility()
    user = _fill_user_template(
        usr_p,
        topic=topic,
        script=english_script.strip(),
    )
    logger.info("identify_short_clips: model=%s, script_chars=%d", cfg.model, len(english_script))
    # Note: we do NOT use ``response_format=json_object`` here. OpenRouter
    # accepts it on some models but silently returns an empty body on others
    # (notably DeepSeek-v4-pro under heavy load). The system prompt already
    # tells the model to emit *only* a JSON object; we parse it ourselves
    # with tolerant fence-stripping.
    result = _call_openrouter(
        sys_p, user, cfg=cfg,
        json_mode=False,
        max_tokens=5000,    # bumped from 3500 — observed truncation mid-clips
                             # even on 4-clip requests; DeepSeek-v4 may count
                             # tokens differently than expected.
        temperature=0.5,    # lower => more deterministic JSON, fewer prose accidents
    )

    # Always log the response shape so future truncation bugs are diagnosable
    # from the server log alone, without rerunning the request.
    logger.info(
        "identify_short_clips: finish_reason=%r text_len=%d completion_tokens=%d",
        result.finish_reason, len(result.text), result.completion_tokens,
    )

    # Resilient JSON parsing using extract_json_safely
    parsed = extract_json_safely(result.text)
    if not isinstance(parsed, dict) or "clips" not in parsed:
        snippet = result.text[:2000]
        logger.error(
            "shorts JSON parse failed. finish_reason=%r text_len=%d raw[0:2000]=%s",
            result.finish_reason, len(result.text), snippet,
        )
        raise ValueError(
            f"shorts response was not valid JSON with 'clips' key.\n"
            f"raw (first 2000 chars): {snippet!r}"
        )

    # Clamp to max_clips.
    clips = parsed.get("clips") or []
    if len(clips) > max_clips:
        parsed["clips"] = clips[:max_clips]

    result.parsed = parsed
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API: translation
# ─────────────────────────────────────────────────────────────────────────────
def translate_script(
    english_script: str,
    target_language: str,
    *,
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Translate an English narration script into ``target_language``.

    Phase 6 wires this into a TranslationVariant row. Phase 3 keeps it
    callable so callers can experiment.
    """
    sys_p, usr_p = _load_prompt_pair("translate")
    cfg = cfg or LLMConfig.from_env()
    user = _fill_user_template(
        usr_p,
        target_language=target_language,
        english_script=english_script.strip(),
    )
    logger.info(
        "translate_script: model=%s, target=%s, script_chars=%d",
        cfg.model, target_language, len(english_script),
    )
    result = _call_openrouter(
        sys_p, user, cfg=cfg,
        max_tokens=4500,
        temperature=0.7,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Module-level smoke
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    cfg = LLMConfig.from_env()
    if not cfg.is_configured:
        print("OPENROUTER_API_KEY not set. Skipping smoke test.")
        raise SystemExit(1)
    print(f"smoke test with model={cfg.model}")
    # Trivial ping: a tiny script generation.
    res = generate_script_from_sources(
        "赤穂浪士の一夜。元禄十五年十二月十四日の夜、四十七士は本所松坂町の吉良邸に押し入った。",
        topic="The night of the 47 ronin",
        target_keywords=["47 ronin", "Ako roshi", "Chushingura"],
    )
    print(f"got {len(res.text)} chars in {res.elapsed_sec:.1f}s")
    print("---first 300 chars---")
    print(res.text[:300])


# ─────────────────────────────────────────────────────────────────────────────
# Content moderation — soften graphic violence for YouTube/social media safety
# ─────────────────────────────────────────────────────────────────────────────

def moderate_script_content(
    script: str,
    *,
    cfg: LLMConfig | None = None,
) -> LLMResult:
    """Review and soften graphic violence/blood/gore in a narration script.

    Japanese kaidan stories often contain graphic violence (ear-cutting,
    mutilation, blood descriptions) that violates YouTube/Instagram content
    policies. This function rewrites violent passages to maintain the story's
    emotional impact while removing graphic descriptions.

    Returns a LLMResult with the moderated script text.
    """
    cfg = (cfg or LLMConfig.from_env()).for_utility()

    system_prompt = (
        "You are a content moderation editor for a YouTube horror/history channel.\n\n"
        "Your task: review the narration script and SOFTEN any graphic violence, "
        "blood, gore, or mutilation descriptions that could violate YouTube's "
        "content policies.\n\n"
        "RULES:\n"
        "1. KEEP the story's emotional impact and atmosphere — do NOT make it bland.\n"
        "2. REPLACE graphic descriptions with atmospheric/emotional equivalents:\n"
        "   - BAD: 'blood streaming down his neck'\n"
        "   - GOOD: 'a sharp sting, then cold silence'\n"
        "   - BAD: 'torn flesh where his ears had been'\n"
        "   - GOOD: 'where his ears had been, there was only silence'\n"
        "   - BAD: 'blood pooling in his lap'\n"
        "   - GOOD: 'he sat motionless, the weight of what had happened settling over him'\n"
        "3. NEVER describe: blood, flesh wounds, torn body parts, mutilation, gore.\n"
        "4. You MAY describe: pain, cold, shock, fear, aftermath emotions.\n"
        "5. Keep the story's meaning clear — the audience should understand what "
        "happened without seeing it described graphically.\n"
        "6. Preserve the original writing style and tone.\n"
        "7. Do NOT add new content or change the plot.\n"
        "8. Do NOT add any commentary or explanation — return ONLY the moderated script.\n\n"
        "IMPORTANT: Return the COMPLETE moderated script, not just the changed parts. "
        "The output must be the same length and structure as the input, with only "
        "the violent passages rewritten."
    )

    user_prompt = (
        "Moderate the following narration script for YouTube content safety.\n"
        "Remove graphic violence while keeping the emotional story:\n\n"
        f"{script}"
    )

    logger.info("moderate_script: model=%s, input=%d chars", cfg.model, len(script))
    result = _call_openrouter(
        system_prompt, user_prompt, cfg=cfg,
        max_tokens=6000,
        temperature=0.5,  # lower temp for more consistent moderation
    )
    logger.info("moderate_script: output=%d chars", len(result.text))
    return result


def moderate_image_prompts(
    prompts: list[dict],
    *,
    cfg: LLMConfig | None = None,
) -> list[dict]:
    """Review and soften violent content in image generation prompts.

    Takes a list of prompt dicts (scene_index, prompt, scene_type) and
    returns the same structure with violent imagery replaced by atmospheric
    equivalents.

    Returns the moderated prompts list.
    """
    cfg = (cfg or LLMConfig.from_env()).for_utility()

    system_prompt = (
        "You are a content moderation editor for image generation prompts.\n\n"
        "Your task: review each prompt and REMOVE any graphic violence, blood, "
        "gore, wounds, or mutilation that could generate disturbing images.\n\n"
        "RULES:\n"
        "1. KEEP the atmosphere and mood — dark, eerie, suspenseful.\n"
        "2. REPLACE violent visual elements with atmospheric equivalents:\n"
        "   - 'blood streaming' → 'dark shadows, cold mist'\n"
        "   - 'torn flesh' → 'bandaged, wrapped in cloth'\n"
        "   - 'wounds visible' → 'solemn expression, head bowed'\n"
        "   - 'gore, mutilation' → 'dark silhouette, dramatic shadows'\n"
        "3. NEVER include: blood, wounds, torn body parts, gore, mutilation, "
        "visible injuries, graphic violence.\n"
        "4. You MAY include: dark atmosphere, shadows, mist, moonlight, "
        "solemn expressions, dramatic lighting.\n"
        "5. Preserve the visual composition and scene type.\n"
        "6. Return the COMPLETE moderated JSON array — same structure, same count.\n\n"
        "Return ONLY the JSON array. No markdown, no explanation."
    )

    # Build the input JSON
    prompts_json = json.dumps(prompts, ensure_ascii=False)

    user_prompt = (
        "Moderate these image prompts for content safety:\n\n"
        f"{prompts_json}"
    )

    logger.info("moderate_image_prompts: model=%s, %d prompts", cfg.model, len(prompts))
    result = _call_openrouter(
        system_prompt, user_prompt, cfg=cfg,
        max_tokens=4000,
        temperature=0.4,
    )

    # Parse the moderated prompts with resilient JSON extraction
    parsed = extract_json_safely(result.text)
    if isinstance(parsed, list) and len(parsed) == len(prompts):
        logger.info("moderate_image_prompts: %d prompts moderated successfully", len(parsed))
        return parsed

    logger.warning("moderate_image_prompts: failed to parse valid array, returning original")
    return prompts
