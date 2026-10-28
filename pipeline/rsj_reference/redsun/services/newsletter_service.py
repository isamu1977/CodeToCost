"""Weekly Newsletter Orchestrator ("The Midnight Archive") for Red Sun Japan.

Extracts metadata, distribution kits, and editorial essays from selected weekly
chronicles, composes an atmospheric dossier via LLM (Gemini 2.5 Flash / Claude),
and creates draft campaigns in Listmonk (https://newsletter.redsunjapan.com).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal, VideoProject
from llm_client import LLMConfig, _call_openrouter, extract_json_safely
from redsun.workspace import RUNTIME_ROOT, resolve_project_dir

logger = logging.getLogger("redsun.newsletter_service")


class NewsletterService:
    """Service to curate, compose and dispatch weekly newsletter campaigns to Listmonk."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_user: Optional[str] = None,
        api_password: Optional[str] = None,
        list_uuid: Optional[str] = None,
        template_id: Optional[int] = None,
    ):
        self.api_url = (api_url or os.environ.get("LISTMONK_API_URL", "https://newsletter.redsunjapan.com")).rstrip("/")
        self.api_user = api_user or os.environ.get("LISTMONK_API_USER", "")
        self.api_password = api_password or os.environ.get("LISTMONK_API_PASSWORD", "")
        self.list_uuid = list_uuid or os.environ.get("LISTMONK_LIST_UUID", "6a86b646-9e7d-47e6-ad0c-3c9185bfc881")
        try:
            self.template_id = int(template_id or os.environ.get("LISTMONK_TEMPLATE_ID", 1))
        except (ValueError, TypeError):
            self.template_id = 1

    def is_listmonk_configured(self) -> bool:
        """Check if real Listmonk API credentials are set in environment."""
        user = self.api_user.strip()
        pwd = self.api_password.strip()
        if not user or not pwd:
            return False
        if user == "seu_usuario_admin" or pwd == "sua_senha_admin":
            return False
        return True

    def extract_project_content(self, slug: str) -> Dict[str, Any]:
        """Extract all archival text, YouTube links, titles and hooks for a project."""
        clean_slug = re.sub(r"^\d{3}_", "", slug.strip())
        proj_dir = resolve_project_dir(slug)

        db = SessionLocal()
        db_proj = (
            db.query(VideoProject).filter(VideoProject.slug == clean_slug).first()
            or (db.query(VideoProject).filter(VideoProject.id == int(clean_slug)).first() if clean_slug.isdigit() else None)
        )
        db_title = db_proj.title if db_proj else clean_slug.replace("-", " ").title()
        db_title_ja = db_proj.title_ja if db_proj else ""
        db_yt_url = db_proj.youtube_url if db_proj else None
        db_yt_id = db_proj.youtube_video_id if db_proj else None
        db.close()

        # Read distribution kit
        kit_path = proj_dir / "distribution" / "social_distribution_kit.json"
        kit_data: Dict[str, Any] = {}
        if kit_path.exists():
            try:
                kit_data = json.loads(kit_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Could not read social distribution kit for %s: %s", slug, e)

        # YouTube URL resolution
        yt_video_id = db_yt_id or kit_data.get("youtube", {}).get("video_id")
        yt_url = (
            db_yt_url
            or kit_data.get("youtube", {}).get("video_url")
            or (f"https://www.youtube.com/watch?v={yt_video_id}" if yt_video_id else None)
            or f"https://www.youtube.com/@redsunjapan"
        )

        title = (
            (kit_data.get("youtube", {}).get("title_options") or [None])[0]
            or kit_data.get("title")
            or db_title
        )

        # Read script preview
        script_path = proj_dir / "text" / "script_en.md"
        script_excerpt = ""
        if script_path.exists():
            try:
                script_text = script_path.read_text(encoding="utf-8")
                # Clean headers and take first ~2,000 chars
                clean_script = re.sub(r"^#+.*$", "", script_text, flags=re.MULTILINE).strip()
                paragraphs = [p.strip() for p in clean_script.split("\n\n") if p.strip()]
                script_excerpt = "\n\n".join(paragraphs[:4])[:2000]
            except Exception as e:
                logger.debug("Could not read script_en.md for %s: %s", slug, e)

        # Read blog essay or article if present
        blog_path = proj_dir / "text" / "blog_article.md"
        if not blog_path.exists():
            blog_path = proj_dir / "blog" / f"{clean_slug}.md"
        blog_excerpt = ""
        if blog_path.exists():
            try:
                b_text = blog_path.read_text(encoding="utf-8")
                blog_excerpt = b_text[:2000]
            except Exception:
                pass

        # Canonical blog post URL
        canonical_blog_url = f"https://redsunjapan.com/posts/{clean_slug}"

        description = kit_data.get("youtube", {}).get("description", "")
        if description and len(description) > 500:
            description = description[:500] + "..."

        return {
            "slug": clean_slug,
            "folder": proj_dir.name if proj_dir else clean_slug,
            "title": title,
            "title_ja": db_title_ja,
            "youtube_url": yt_url,
            "blog_url": canonical_blog_url,
            "script_excerpt": script_excerpt,
            "blog_excerpt": blog_excerpt,
            "description": description,
            "timestamps": kit_data.get("timestamps", []),
        }

    def generate_dossier_markdown(
        self,
        projects_data: List[Dict[str, Any]],
        issue_number: int,
        custom_notes: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Synthesize weekly dossier markdown via LLM (Gemini 2.5 Flash / Claude)."""
        cfg = LLMConfig.from_env()

        # Build context for the LLM
        chronicles_context = []
        for idx, p in enumerate(projects_data, 1):
            day_label = "Tuesday" if idx == 1 else ("Thursday" if idx == 2 else f"Episode #{idx}")
            chronicles_context.append(
                f"### Chronicle {idx} ({day_label}):\n"
                f"- Canonical Title: {p['title']}\n"
                f"- Japanese Title: {p.get('title_ja', 'N/A')}\n"
                f"- YouTube Video URL: {p['youtube_url']}\n"
                f"- Companion Blog Article URL: {p['blog_url']}\n"
                f"- Story Synopsis & Atmosphere:\n{p['description'] or p['script_excerpt'][:800]}\n"
                f"- Script Opening Excerpt:\n{p['script_excerpt'][:1200]}\n"
            )

        context_str = "\n\n".join(chronicles_context)
        notes_str = f"\nAdditional Curator Notes: {custom_notes}\n" if custom_notes else ""

        system_prompt = (
            "You are JingJing, Chief Historical Curator and Archival Editor of Red Sun Japan.\n"
            "You write the prestigious weekly editorial dispatch: \"The Midnight Archive\" (published via Listmonk).\n"
            "Audience: Discerning connoisseurs of Japanese dark folklore, samurai chronicles, Kaidan legends, and feudal history.\n"
            "Tone: Solemn, evocative, atmospheric, deeply scholarly yet captivating without purple prose. "
            "Write in atmospheric, publication-ready English.\n\n"
            "MANDATORY DISPATCH STRUCTURE:\n"
            "1. Subject Line: A high-impact historical curiosity connecting the chronicles (e.g., 'Two Curses of Edo & The Peony Lantern — Dispatch #{issue_number}').\n"
            "2. Dispatch Header: Atmospheric opening paragraph welcoming readers to The Midnight Archive #{issue_number}.\n"
            "3. Chronicle Sections (e.g. '## I. [Title]' and '## II. [Title]'):\n"
            "   - Exactly 2 rich, immersive paragraphs exploring historical context, social setting (Edo, Heian, Meiji), and psychological suspense.\n"
            "   - DO NOT spoil the ending or supernatural climax.\n"
            "   - Explicit callouts at the end of each chronicle:\n"
            "     ▶ **[Watch the Restored Chronicle on YouTube](URL)**\n"
            "     📖 **[Read the Companion Archival Essay on our Blog](URL)**\n"
            "4. Archival Notes & Primary Sources ('### 📜 Archival Notes & Historical Footnotes'):\n"
            "   - 1 to 2 paragraphs citing authentic Edo woodblock prints, scrolls, or Kaidan anthologies (e.g. Asai Ryōi, Toriyama Sekien, Ueda Akinari, Lafcadio Hearn, National Diet Library).\n"
            "5. Footer:\n"
            "   - A calm, respectful invitation to support our independent restoration work on Ko-fi (https://ko-fi.com/redsunjapan).\n\n"
            "OUTPUT REQUIREMENT: Output strictly a valid JSON object with 'subject', 'preview_text', and 'body' (in Markdown)."
        )

        user_prompt = (
            f"Draft weekly dispatch #{issue_number} of 'The Midnight Archive'.\n\n"
            f"{context_str}\n"
            f"{notes_str}\n\n"
            "Provide the complete response in strict JSON format:\n"
            "{\n"
            f'  "subject": "The Midnight Archive #{issue_number} Subject Line",\n'
            '  "preview_text": "One sentence email preview text",\n'
            '  "body": "Complete markdown body of the newsletter with headers, paragraphs, and links."\n'
            "}"
        )

        try:
            # Prefer model_utility (Gemini 2.5 Flash) or literary model
            target_model = cfg.model_utility or "google/gemini-2.5-flash"
            llm_cfg = cfg.with_model(target_model)
            llm_result = _call_openrouter(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                cfg=llm_cfg,
                json_mode=True,
                temperature=0.7,
                max_tokens=4000,
            )

            parsed = extract_json_safely(llm_result.text)
            if isinstance(parsed, dict) and "subject" in parsed and "body" in parsed:
                return parsed["subject"].strip(), parsed["body"].strip()
            
            # Fallback if raw markdown was returned
            raw = llm_result.text.strip()
            subject_match = re.search(r"^#+\s*(?:Subject:)?\s*(.+)$", raw, flags=re.MULTILINE)
            subject = subject_match.group(1).strip() if subject_match else f"The Midnight Archive #{issue_number}: Feudal Chronicles"
            return subject, raw

        except Exception as exc:
            logger.error("LLM Generation failed for newsletter: %s", exc)
            # Deterministic fallback template
            return self._build_fallback_dossier(projects_data, issue_number)

    def _build_fallback_dossier(self, projects_data: List[Dict[str, Any]], issue_number: int) -> Tuple[str, str]:
        """Deterministic fallback newsletter body in case LLM is unreachable."""
        titles = [p["title"] for p in projects_data]
        subject = f"The Midnight Archive #{issue_number}: {titles[0] if titles else 'Feudal Chronicles'}"

        body_parts = [
            f"# The Midnight Archive — Dispatch #{issue_number}\n",
            "*Welcome to this week's archival dispatch from Red Sun Japan. In this volume, we examine two chronicles preserved across centuries of dark folklore, restored through our historical reconstruction pipeline.*\n",
            "---\n",
        ]

        roman_numerals = ["I", "II", "III", "IV", "V"]
        for idx, p in enumerate(projects_data):
            num = roman_numerals[idx] if idx < len(roman_numerals) else str(idx + 1)
            body_parts.extend([
                f"## {num}. {p['title']}\n",
                f"{p['description'] or p['script_excerpt'][:600]}\n",
                f"\n▶ **[Watch the Restored Chronicle on YouTube]({p['youtube_url']})**  ",
                f"📖 **[Read the Companion Archival Essay on our Blog]({p['blog_url']})**\n",
                "---\n",
            ])

        body_parts.extend([
            "### 📜 Archival Notes & Historical Footnotes\n",
            "These chronicles are derived from Edo-period woodblock prints and archival manuscripts preserved in classical collections such as *Otogi Bōko* and national folklore compendiums. Each account reflects the historical anxieties and spiritual cosmology of pre-modern Japan.\n",
            "---\n",
            "*If you value these historical restorations, you can support our independent archival work on [Ko-fi](https://ko-fi.com/redsunjapan).*\n",
        ])

        return subject, "\n".join(body_parts)

    def resolve_listmonk_numeric_id(self) -> int:
        """Query Listmonk API to resolve numeric list ID from list UUID."""
        if not self.is_listmonk_configured():
            return 1

        url = f"{self.api_url}/api/lists"
        try:
            resp = requests.get(
                url,
                auth=(self.api_user, self.api_password),
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                results = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for item in results:
                    if str(item.get("uuid", "")).lower() == str(self.list_uuid).lower():
                        return int(item["id"])
                for item in results:
                    name = str(item.get("name", "")).lower()
                    if "midnight" in name or "archive" in name:
                        return int(item["id"])
                if results and "id" in results[0]:
                    return int(results[0]["id"])
        except Exception as exc:
            logger.warning("Could not query Listmonk list numeric ID: %s", exc)

        return 1

    def create_listmonk_campaign(
        self,
        name: str,
        subject: str,
        body: str,
        list_ids: List[int],
    ) -> Dict[str, Any]:
        """Post new draft campaign to Listmonk via HTTP Basic Auth."""
        url = f"{self.api_url}/api/campaigns"
        payload = {
            "name": name,
            "subject": subject,
            "lists": list_ids,
            "type": "regular",
            "content_type": "markdown",
            "body": body,
            "template_id": self.template_id,
            "tags": ["weekly-dispatch", "editorial"],
        }

        resp = requests.post(
            url,
            auth=(self.api_user, self.api_password),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def compose_weekly_dispatch(
        self,
        project_slugs: List[str],
        issue_number: Optional[int] = None,
        custom_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract project content, write dossier with LLM, and register draft campaign in Listmonk."""
        if not project_slugs:
            raise ValueError("At least 1 project slug is required to compose a weekly dispatch.")

        if issue_number is None or issue_number <= 0:
            # Deterministic issue number fallback
            issue_number = 1

        # 1. Content Extraction
        projects_data = [self.extract_project_content(slug) for slug in project_slugs]

        # 2. LLM Dossier Generation
        subject, body_markdown = self.generate_dossier_markdown(
            projects_data=projects_data,
            issue_number=issue_number,
            custom_notes=custom_notes,
        )

        campaign_name = f"The Midnight Archive #{issue_number} — Draft"

        # 3. Listmonk Draft Registration
        if not self.is_listmonk_configured():
            logger.info("Listmonk credentials not fully configured; returning generated draft payload.")
            return {
                "success": True,
                "mock_draft": True,
                "campaign_id": 0,
                "issue_number": issue_number,
                "subject": subject,
                "name": campaign_name,
                "dashboard_url": f"{self.api_url}/admin/campaigns",
                "project_slugs": project_slugs,
                "body": body_markdown,
                "list_id": 1,
                "template_id": self.template_id,
                "warning": (
                    "Campanha gerada com sucesso via LLM! As credenciais do Listmonk em motor-python/.env "
                    "estão com valores de exemplo ('seu_usuario_admin'). Para criar o rascunho diretamente "
                    "no servidor do Listmonk, configure LISTMONK_API_USER e LISTMONK_API_PASSWORD no arquivo .env."
                ),
            }

        numeric_list_id = self.resolve_listmonk_numeric_id()

        try:
            campaign_data = self.create_listmonk_campaign(
                name=campaign_name,
                subject=subject,
                body=body_markdown,
                list_ids=[numeric_list_id],
            )
            campaign_id = campaign_data.get("id")
            dashboard_url = f"{self.api_url}/admin/campaigns/{campaign_id}"

            return {
                "success": True,
                "mock_draft": False,
                "campaign_id": campaign_id,
                "issue_number": issue_number,
                "subject": subject,
                "name": campaign_data.get("name", campaign_name),
                "dashboard_url": dashboard_url,
                "project_slugs": project_slugs,
                "body": body_markdown,
                "list_id": numeric_list_id,
                "template_id": self.template_id,
                "status": campaign_data.get("status", "draft"),
            }
        except requests.exceptions.HTTPError as http_err:
            status_code = http_err.response.status_code if http_err.response is not None else 500
            err_text = http_err.response.text if http_err.response is not None else str(http_err)
            logger.error("Listmonk API error (%s): %s", status_code, err_text)
            return {
                "success": False,
                "error": f"Erro na API do Listmonk (Status {status_code}): {err_text}",
                "issue_number": issue_number,
                "subject": subject,
                "name": campaign_name,
                "project_slugs": project_slugs,
                "body": body_markdown,
                "dashboard_url": f"{self.api_url}/admin/campaigns",
            }
        except Exception as exc:
            logger.error("Failed to connect to Listmonk: %s", exc)
            return {
                "success": False,
                "error": f"Falha de conexão com Listmonk: {exc}",
                "issue_number": issue_number,
                "subject": subject,
                "name": campaign_name,
                "project_slugs": project_slugs,
                "body": body_markdown,
                "dashboard_url": f"{self.api_url}/admin/campaigns",
            }


# Module-level convenience function
async def compose_weekly_dispatch(
    project_slugs: List[str],
    issue_number: Optional[int] = None,
    custom_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper function to compose and dispatch a weekly newsletter."""
    service = NewsletterService()
    return await service.compose_weekly_dispatch(
        project_slugs=project_slugs,
        issue_number=issue_number,
        custom_notes=custom_notes,
    )
