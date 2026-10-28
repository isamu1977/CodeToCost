"""Editorial Quality Linter for Red Sun Japan Blog Articles.

Enforces deterministic quality gates on generated blog posts before export:
1. Rigid frontmatter audit (description length 100-180 chars, terminal punctuation, tags).
2. Web scannability audit (at least 4 H2 headings, no run-on paragraphs >120 words).
3. Narrative & historical conclusion audit (1,400 to 2,500 words, formal legacy section).
4. Automated LLM self-correction loop when issues are found.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import yaml

logger = logging.getLogger("redsun.blog_linter")


class BlogQualityLinter:
    """Deterministic Quality Assurance linter for blog Markdown articles."""

    MIN_DESCRIPTION_LEN = 100
    MAX_DESCRIPTION_LEN = 180
    MIN_H2_COUNT = 4
    MAX_PARAGRAPH_WORDS = 120
    MIN_WORD_COUNT = 1400
    MAX_WORD_COUNT = 2500

    HISTORICAL_LEGACY_PATTERNS = [
        r"historical\s+legacy",
        r"archival",
        r"historical\s+aftermath",
        r"aftermath",
        r"legacy",
        r"literary\s+origins",
        r"sources\s+&\s+further\s+reading",
        r"sources\s+&\s+archival",
        r"epilogue",
    ]

    @classmethod
    def parse_document(cls, markdown_text: str) -> tuple[dict[str, Any], str, list[str]]:
        """Split document into frontmatter dict and markdown body."""
        issues: list[str] = []
        if not markdown_text.strip().startswith("---"):
            return {}, markdown_text, ["Document is missing opening '---' YAML frontmatter fence."]

        parts = markdown_text.split("---", 2)
        if len(parts) < 3:
            return {}, markdown_text, ["Document is missing closing '---' YAML frontmatter fence."]

        fm_raw = parts[1].strip()
        body = parts[2].strip()

        try:
            fm_data = yaml.safe_load(fm_raw) or {}
            if not isinstance(fm_data, dict):
                return {}, body, [f"Frontmatter is not a valid YAML mapping (got {type(fm_data).__name__})."]
        except Exception as exc:
            return {}, body, [f"YAML frontmatter parsing failed: {exc}"]

        return fm_data, body, []

    @classmethod
    def audit(cls, markdown_text: str) -> dict[str, Any]:
        """Perform comprehensive audit on the markdown document."""
        issues: list[str] = []
        fm, body, parse_issues = cls.parse_document(markdown_text)
        issues.extend(parse_issues)

        # 1. Frontmatter Audit
        desc = ""
        if fm:
            desc = str(fm.get("description", "")).strip()
            if not desc:
                issues.append("Frontmatter is missing required 'description' field.")
            else:
                desc_len = len(desc)
                if desc_len < cls.MIN_DESCRIPTION_LEN:
                    issues.append(
                        f"Frontmatter 'description' is too short ({desc_len} chars). "
                        f"Expected between {cls.MIN_DESCRIPTION_LEN} and {cls.MAX_DESCRIPTION_LEN} chars."
                    )
                elif desc_len > cls.MAX_DESCRIPTION_LEN:
                    issues.append(
                        f"Frontmatter 'description' is too long ({desc_len} chars). "
                        f"Expected between {cls.MIN_DESCRIPTION_LEN} and {cls.MAX_DESCRIPTION_LEN} chars."
                    )
                if not desc.endswith((".", "!", "?")):
                    issues.append(
                        f"Frontmatter 'description' must end with terminal punctuation (. ! ?). "
                        f"Found: '{desc[-15:]}' (likely truncated)."
                    )

            title = str(fm.get("title", "")).strip()
            if not title:
                issues.append("Frontmatter is missing required 'title' field.")

            tags = fm.get("tags")
            if not tags or not isinstance(tags, list) or len(tags) < 3:
                issues.append("Frontmatter 'tags' must contain at least 3 relevant lowercase tags.")

        # 2. Scannability Audit
        h2_headings = re.findall(r"^##\s+(.+)$", body, flags=re.MULTILINE)
        h2_count = len(h2_headings)
        if h2_count < cls.MIN_H2_COUNT:
            issues.append(
                f"Web scannability failure: Document contains only {h2_count} H2 (##) headings. "
                f"Expected at least {cls.MIN_H2_COUNT} structured sections."
            )

        # Paragraph length check (exclude headers, lists, code fences, quotes, images)
        raw_paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        max_p_words = 0
        long_paragraphs: list[str] = []

        for p in raw_paragraphs:
            if p.startswith(("#", "!", ">", "-", "*", "```", "|")):
                continue
            words = len(p.split())
            if words > max_p_words:
                max_p_words = words
            if words > cls.MAX_PARAGRAPH_WORDS:
                snippet = p[:60].replace("\n", " ") + "..."
                long_paragraphs.append(f"{words} words: '{snippet}'")

        if long_paragraphs:
            issues.append(
                f"Web scannability failure: {len(long_paragraphs)} paragraph(s) exceed {cls.MAX_PARAGRAPH_WORDS} words "
                f"without a break (e.g. {long_paragraphs[0]})."
            )

        # 3. Conclusion & Word Count Audit
        # Calculate clean word count of body
        body_words = len(re.findall(r"\b\w+\b", body))
        if body_words < cls.MIN_WORD_COUNT:
            issues.append(
                f"Article is too brief ({body_words} words). "
                f"Expected between {cls.MIN_WORD_COUNT} and {cls.MAX_WORD_COUNT} words for full editorial depth."
            )
        elif body_words > cls.MAX_WORD_COUNT:
            issues.append(
                f"Article exceeds maximum word count ({body_words} words). "
                f"Expected between {cls.MIN_WORD_COUNT} and {cls.MAX_WORD_COUNT} words."
            )

        # Check for historical legacy / archival resolution
        has_conclusion = False
        all_headings = re.findall(r"^#{2,3}\s+(.+)$", body, flags=re.MULTILINE)
        for h in all_headings:
            for pat in cls.HISTORICAL_LEGACY_PATTERNS:
                if re.search(pat, h, flags=re.IGNORECASE):
                    has_conclusion = True
                    break
            if has_conclusion:
                break

        if not has_conclusion:
            issues.append(
                "Missing formal closing section: The article must include a concluding section "
                "(e.g., '## Historical Legacy & Archival Footnotes') analyzing the tale's literary provenance and historical impact."
            )

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "frontmatter": fm,
            "stats": {
                "description_len": len(desc),
                "h2_count": h2_count,
                "word_count": body_words,
                "max_paragraph_words": max_p_words,
                "has_conclusion": has_conclusion,
            },
        }

    @classmethod
    def validate(cls, markdown_text: str) -> tuple[bool, list[str]]:
        """Validate markdown text and return (is_valid, issues)."""
        result = cls.audit(markdown_text)
        return result["valid"], result["issues"]

    @classmethod
    def format_diagnostic_prompt(cls, markdown_text: str, issues: list[str]) -> str:
        """Create a targeted prompt instructing LLM to fix specific quality violations."""
        issues_bulleted = "\n".join(f"- {issue}" for issue in issues)
        return f"""The companion blog article you generated failed our strict editorial quality audit.
Please restructure and rewrite the article strictly correcting every issue listed below, while preserving the rich historical prose, Japanese terminology, and atmosphere.

### Audit Violations to Fix:
{issues_bulleted}

### Strict Requirements:
1. Frontmatter:
   - `description`: MUST be exactly 130 to 160 characters long, concise, compelling, and MUST end with terminal punctuation ('.', '!', '?').
   - `heroImage` and `ogImage`: keep as '{{HERO_IMAGE_PATH}}'.
   - `tags`: 3 to 5 lowercase tags.
2. Architecture:
   - Total word count MUST be between 1,500 and 2,200 words.
   - Must contain at least 4 to 5 major sections initiated with `## ` (H2).
   - Break any paragraph longer than 100-120 words into shorter, scannable paragraphs.
   - Do NOT include empty introductory phrases ("In this article..."). Start immediately with the narrative or historical setting.
   - Conclude with a formal `## Historical Legacy & Archival Footnotes` section contextualizing the literary compilers (Asai Ryōi, Ueda Akinari, etc.) and surviving archives.

### Draft to Correct:
```markdown
{markdown_text}
```

Return ONLY the corrected, complete Markdown document starting with `---`.
"""

    @classmethod
    def run_correction_loop(
        cls,
        markdown_text: str,
        script: str = "",
        topic: str = "",
        max_attempts: int = 2,
    ) -> str:
        """Audit and iteratively correct markdown text with the LLM until valid."""
        from llm_client import LLMConfig, _call_openrouter, _load_prompt_pair

        current_text = markdown_text

        for attempt in range(1, max_attempts + 1):
            is_valid, issues = cls.validate(current_text)
            if is_valid:
                logger.info("BlogQualityLinter: Article passed audit on attempt %d.", attempt)
                return current_text

            logger.warning(
                "BlogQualityLinter: Audit failed on attempt %d (%d issues): %s",
                attempt,
                len(issues),
                "; ".join(issues),
            )

            diagnostic_prompt = cls.format_diagnostic_prompt(current_text, issues)
            sys_p, _ = _load_prompt_pair("blog")
            cfg = LLMConfig.from_env().for_literary()

            try:
                logger.info("BlogQualityLinter: Requesting LLM correction (attempt %d)...", attempt)
                res = _call_openrouter(
                    sys_p,
                    diagnostic_prompt,
                    cfg=cfg,
                    max_tokens=10000,
                    temperature=0.4,
                )
                fixed_text = res.text.strip()
                # Strip markdown wrapping if model enclosed everything in ```markdown ... ```
                if fixed_text.startswith("```markdown"):
                    fixed_text = fixed_text[len("```markdown"):].strip()
                elif fixed_text.startswith("```"):
                    fixed_text = fixed_text[3:].strip()
                if fixed_text.endswith("```"):
                    fixed_text = fixed_text[:-3].strip()

                current_text = fixed_text
            except Exception as exc:
                logger.error("BlogQualityLinter: Auto-correction LLM call failed: %s", exc)
                break

        # Final audit
        final_valid, final_issues = cls.validate(current_text)
        if not final_valid:
            logger.warning("BlogQualityLinter: Article has residual issues after correction: %s", final_issues)
        return current_text
