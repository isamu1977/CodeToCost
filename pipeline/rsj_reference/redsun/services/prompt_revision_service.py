"""Prompt Revision Service for Human-in-the-Loop visual prompt adjustments.

Provides targeted surgical AI revisions of individual reference or scene tasks,
or bulk revisions across all tasks in a project's gemini-assistant configuration.
Enforces strict anti-pollution sanitation and preserves the Master Style Bible.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from llm_client import LLMConfig, _call_openrouter, extract_json_safely
from redsun.services.style_constants import (
    format_prompt_with_style_bible,
    sanitize_visual_prompt,
    MASTER_STYLE_BIBLE,
)
from redsun.services.visual_prompt_linter import VisualPromptLinter
from redsun.workspace import ProjectWorkspace

logger = logging.getLogger("redsun.prompt_revision")


SYSTEM_PROMPT_REVISION = """You are an expert Visual Art Director and Prompt Engineer for historical Japanese cinema.
Your job is to REVISE visual prompts for diffusion models (Midjourney, Flux, Imagen 3, Gemini) based strictly on user feedback.

CRITICAL RULES:
1. NEVER include project titles, episode names, or metadata in the prompt (e.g. do NOT mention 'Kappa', 'Episode 1', or project names as labels).
2. Start immediately with the camera shot type and concrete physical subjects, authentic textures, and cinematic Edo-period lighting.
3. Maintain strict historical and folklore fidelity (Edo period, authentic attire, genuine Japanese mythology features rather than generic fantasy).
4. If modifying a folklore creature (such as a Kappa), describe its genuine physical characteristics: dish-like indentation (sara) holding water on the crown of the head, green-brown scaly reptilian or amphibious skin, turtle-like shell on back, webbed clawed fingers, beak-like mouth, and riverbank marsh environment.
5. If the current prompt contains a Master Style Bible or STYLE EXCLUSIONS block at the end, you may focus your edits on the descriptive scene/character section, but ensure the final prompt adheres to authentic Edo cinematic guidelines.
6. Return ONLY a valid JSON object matching this schema:
{
  "title": "Concise descriptive task title",
  "prompt": "Complete revised prompt string"
}
"""


class PromptRevisionService:
    """Service to surgically revise reference or scene prompts with AI."""

    @staticmethod
    def get_project_json_path(slug: str) -> Path:
        """Default scene project.json path."""
        ws = ProjectWorkspace.from_slug(slug)
        return ws.gemini_project_json

    @staticmethod
    def get_ref_project_json_path(slug: str) -> Path:
        """Reference job project.json path."""
        ws = ProjectWorkspace.from_slug(slug)
        return ws.gemini_ref_job_project_json

    @classmethod
    def _call_llm_json(cls, system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
        """Executes LLM call using utility model (Gemini 2.5 Flash / Claude) via OpenRouter."""
        cfg = LLMConfig.from_env().for_utility()
        try:
            result = _call_openrouter(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                cfg=cfg,
                json_mode=True,
                temperature=0.4,
                max_tokens=3000,
            )
            parsed = extract_json_safely(result.text)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                return parsed[0]
            logger.warning("LLM response did not parse as JSON dict: %s", result.text[:200])
            return None
        except Exception as exc:
            logger.error("Error calling LLM for prompt revision: %s", exc)
            raise RuntimeError(f"Falha na comunicação com LLM: {exc}") from exc

    @classmethod
    def revise_prompts(
        cls,
        slug: str,
        instruction: str,
        target_task_id: Optional[str] = None,
        scope: Optional[str] = None,
        project_title: str = "",
    ) -> dict[str, Any]:
        """Surgically revise a single task or a collection of tasks based on user instruction.

        Args:
            slug: Project slug or ID.
            instruction: Natural language instruction (e.g. "corrigir ref-003 para o Kappa folclórico em vez de Yūrei").
            target_task_id: Specific task id (e.g. "ref-003", "shot-005"). If None, revises all tasks in target scope.
            scope: "references" | "scenes" | None. Automatically inferred if target_task_id is given.
            project_title: Project display title to prevent metadata pollution.
        """
        if not instruction or not instruction.strip():
            raise ValueError("Instrução de revisão não pode ser vazia.")

        ws = ProjectWorkspace.from_slug(slug)
        clean_title = project_title or slug.replace("-", " ").title()

        # Determine target file(s)
        # 1. If target_task_id is provided, locate where it exists
        is_ref_target = False
        target_file: Optional[Path] = None
        target_mirror_file: Optional[Path] = None

        if target_task_id:
            task_id_clean = target_task_id.strip()
            # Check references first if starts with 'ref-'
            if task_id_clean.startswith("ref-") or scope in ("references", "phase1"):
                ref_paths = [ws.gemini_ref_job_project_json, ws.references_json_path]
                for p in ref_paths:
                    if p.exists():
                        try:
                            d = json.loads(p.read_text(encoding="utf-8"))
                            if any(t.get("id") == task_id_clean for t in d.get("tasks", [])):
                                target_file = ws.gemini_ref_job_project_json
                                target_mirror_file = ws.references_json_path
                                is_ref_target = True
                                break
                        except Exception:
                            pass

            if not target_file:
                # Check scene project.json
                scene_paths = [ws.gemini_project_json, ws.final_scenes_json_path]
                for p in scene_paths:
                    if p.exists():
                        try:
                            d = json.loads(p.read_text(encoding="utf-8"))
                            if any(t.get("id") == task_id_clean for t in d.get("tasks", [])):
                                target_file = ws.gemini_project_json
                                target_mirror_file = ws.final_scenes_json_path
                                is_ref_target = False
                                break
                        except Exception:
                            pass

            # If still not found, check ref-job as fallback
            if not target_file and ws.gemini_ref_job_project_json.exists():
                try:
                    d = json.loads(ws.gemini_ref_job_project_json.read_text(encoding="utf-8"))
                    if any(t.get("id") == task_id_clean for t in d.get("tasks", [])):
                        target_file = ws.gemini_ref_job_project_json
                        target_mirror_file = ws.references_json_path
                        is_ref_target = True
                except Exception:
                    pass

            if not target_file:
                raise ValueError(f"Task '{target_task_id}' não encontrada nas referências nem nas cenas de {slug}")

        else:
            # Bulk revision: scope decides
            if scope in ("references", "phase1"):
                target_file = ws.gemini_ref_job_project_json if ws.gemini_ref_job_project_json.exists() else ws.references_json_path
                target_mirror_file = ws.references_json_path
                is_ref_target = True
            else:
                target_file = ws.gemini_project_json if ws.gemini_project_json.exists() else ws.final_scenes_json_path
                target_mirror_file = ws.final_scenes_json_path
                is_ref_target = False

        if not target_file or not target_file.exists():
            raise FileNotFoundError(f"Arquivo de contrato não encontrado para {slug}: {target_file}")

        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        tasks = data.get("tasks", [])
        if not tasks:
            raise ValueError(f"Nenhuma task encontrada em {target_file.name}")

        if target_task_id:
            tasks_to_revise = [t for t in tasks if t.get("id") == target_task_id.strip()]
        else:
            tasks_to_revise = tasks

        if not tasks_to_revise:
            raise ValueError(f"Nenhuma tarefa elegível para revisão com id '{target_task_id}'")

        revised_tasks_log = []

        for task in tasks_to_revise:
            task_id = task.get("id", "task")
            current_title = task.get("title", "")
            current_prompt = task.get("prompt", "")
            current_ar = task.get("aspectRatio", "16:9")

            logger.info("Revising prompt for task %s (%s)...", task_id, current_title)

            user_prompt = (
                f"TASK ID: {task_id}\n"
                f"CURRENT TASK TITLE: {current_title}\n"
                f"CURRENT ASPECT RATIO: {current_ar}\n\n"
                f"CURRENT PROMPT:\n{current_prompt}\n\n"
                f"USER REVISION INSTRUCTION:\n{instruction.strip()}\n\n"
                "Rewrite the visual description of this task to faithfully address the user instruction.\n"
                "DO NOT include titles or project metadata.\n"
                "Ensure the prompt ends with standard style exclusions.\n"
                "Return JSON with: {\"title\": \"string\", \"prompt\": \"string\"}"
            )

            response = cls._call_llm_json(system_prompt=SYSTEM_PROMPT_REVISION, user_prompt=user_prompt)

            if response and "prompt" in response and response["prompt"]:
                raw_revised_prompt = response["prompt"]
                new_title = response.get("title")

                # Sanitize against metadata pollution (title, slug, episode terms)
                sanitized_prompt = sanitize_visual_prompt(
                    raw_revised_prompt,
                    project_title=clean_title,
                    slug=slug,
                )

                # Ensure Master Style Bible and STYLE EXCLUSIONS are properly retained
                if "STYLE EXCLUSIONS" not in sanitized_prompt:
                    sanitized_prompt = format_prompt_with_style_bible(
                        sanitized_prompt,
                        aspect_ratio=current_ar,
                        project_title=clean_title,
                        slug=slug,
                    )

                task["prompt"] = sanitized_prompt
                if new_title and str(new_title).strip():
                    task["title"] = str(new_title).strip()

                revised_tasks_log.append({
                    "id": task_id,
                    "title": task.get("title"),
                    "prompt": task.get("prompt"),
                    "aspectRatio": current_ar,
                })
            else:
                logger.warning("Failed to obtain revised prompt for task %s", task_id)

        if not revised_tasks_log:
            raise RuntimeError("A LLM não retornou revisões válidas para as tarefas selecionadas.")

        # Persist updated project.json and mirror
        file_content = json.dumps(data, indent=2, ensure_ascii=False)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(file_content, encoding="utf-8")

        if target_mirror_file and target_mirror_file != target_file:
            try:
                target_mirror_file.parent.mkdir(parents=True, exist_ok=True)
                target_mirror_file.write_text(file_content, encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not sync mirror file %s: %s", target_mirror_file, exc)

        # If it was references, also sync to ws.gemini_references_project_json or ws.gemini_ref_gen_project_json if present
        if is_ref_target:
            for extra_path in (ws.gemini_ref_gen_project_json, ws.gemini_references_project_json):
                if extra_path and extra_path.exists():
                    try:
                        extra_path.write_text(file_content, encoding="utf-8")
                    except Exception:
                        pass

        logger.info(
            "Successfully revised %d tasks for project %s (target_file=%s)",
            len(revised_tasks_log),
            slug,
            target_file.name,
        )

        return {
            "success": True,
            "target_task_id": target_task_id,
            "scope": "references" if is_ref_target else "scenes",
            "revised_tasks_count": len(revised_tasks_log),
            "updated_tasks": revised_tasks_log,
            "target_file": str(target_file),
        }
