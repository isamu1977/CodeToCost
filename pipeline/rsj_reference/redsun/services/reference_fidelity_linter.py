"""Reference Fidelity Linter.

Deterministic validation executed before writing gemini-assistant/project.json
for Phase 1 (Handoff Gate: Visual References).
Enforces:
1. Anti-Contaminação: Rejects prompts containing project titles, slugs, or administrative leftovers.
2. Anti-Anacronismo: Rejects modern or anachronistic medical/industrial terms.
3. Anatomical & Semantic Fidelity: Validates physical folklore traits and required instruments
   (e.g., blind protagonist for Miminashi/Blind tales, biwa lute, amphibious shell & sara for Kappa).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("redsun.reference_fidelity_linter")


class PromptContaminationError(ValueError):
    """Raised when prompt contains forbidden metadata, project title, slug, or administrative tags."""
    pass


class ReferenceFidelityError(ValueError):
    """Raised when visual reference fails semantic, anatomical, or folklore fidelity checks."""
    pass


# Forbidden anachronistic or modern terms in historical Japanese Edo/feudal contexts
FORBIDDEN_ANACHRONISMS = [
    "needle",        # modern medical needles (unless specifically sewing needle, but forbidden in reference anchors)
    "hypodermic",
    "syringe",
    "modern",
    "plastic",
    "cellphone",
    "smartphone",
    "polyester",
    "nylon",
    "zipper",
    "sunglasses",
    "photograph",
    "camera",
]


class ReferenceFidelityLinter:
    """Deterministic validator for visual reference integrity."""

    @staticmethod
    def extract_visual_text(raw_prompt: str) -> str:
        """Isolate visual scene/character description from the canonical Style Bible and Exclusions."""
        text = str(raw_prompt or "").strip()
        if "STYLE EXCLUSIONS:" in text:
            text = text.split("STYLE EXCLUSIONS:")[0].strip()
        bible_lead = "Cinematic semi-realistic"
        if bible_lead in text:
            text = text.split(bible_lead)[0].strip()
        return text

    @classmethod
    def lint_prompt_contamination(
        cls,
        task_id: str,
        prompt_text: str,
        project_title: str = "",
        slug: str = "",
    ) -> List[str]:
        """Check for project title, slug, or administrative residue in the visual text."""
        errors: List[str] = []
        if not prompt_text:
            errors.append(f"[{task_id}] Prompt text is empty.")
            return errors

        visual_text = cls.extract_visual_text(prompt_text)

        # 1. Exact slug match
        if slug and len(slug) >= 5:
            slug_clean = slug.strip().lower()
            if slug_clean in visual_text.lower():
                errors.append(
                    f"[{task_id}] PromptContamination: slug '{slug}' found inside prompt."
                )

        # 2. Full title contamination (e.g. 'Hoichi the Earless — The Blind Biwa Priest of Akamagaseki')
        if project_title and len(project_title.strip()) >= 6:
            clean_title = project_title.strip()
            # Check full title
            if clean_title.lower() in visual_text.lower():
                errors.append(
                    f"[{task_id}] PromptContamination: full project title '{clean_title}' found inside prompt."
                )
            
            # Check major title clauses separated by dashes/colons if long
            for part in re.split(r"[-—:–]+", clean_title):
                p_str = part.strip()
                if len(p_str) >= 20 and p_str.lower() in visual_text.lower():
                    errors.append(
                        f"[{task_id}] PromptContamination: title clause '{p_str}' found inside prompt."
                    )

        # 3. Administrative / meta pollution patterns
        admin_patterns = [
            (r"\bfor\s+Project\b", "Administrative phrase 'for Project'"),
            (r"\bfor\s+project\s+[\w\-]+", "Administrative pattern 'for project <name>'"),
            (r"\bMaster References?\b", "Administrative tag 'Master Reference'"),
            (r"—\s*Master References?", "Administrative dash tag '— Master Reference'"),
            (r"\bEpisode\s+\d+\b", "Television metadata 'Episode X'"),
        ]
        for pat, desc in admin_patterns:
            if re.search(pat, visual_text, re.IGNORECASE):
                errors.append(f"[{task_id}] PromptContamination: {desc} detected in prompt.")

        return errors

    @classmethod
    def lint_anachronisms(cls, task_id: str, prompt_text: str) -> List[str]:
        """Check for forbidden modern or medical anachronistic terms in the visual description."""
        errors: List[str] = []
        visual_text = cls.extract_visual_text(prompt_text)
        text_lower = visual_text.lower()

        for term in FORBIDDEN_ANACHRONISMS:
            # Use word boundary search
            pattern = rf"\b{re.escape(term)}\b"
            if re.search(pattern, text_lower):
                errors.append(
                    f"[{task_id}] AnachronismDetected: forbidden modern/medical term '{term}' found in visual description."
                )
        return errors

    @classmethod
    def lint_anatomical_and_semantic(
        cls,
        tasks_map: Dict[str, str],
        project_title: str = "",
        slug: str = "",
        script_text: str = "",
    ) -> List[str]:
        """Check required physical and anatomical features based on narrative context."""
        errors: List[str] = []
        context = f"{project_title} {slug} {script_text}".lower()

        # Rule A: Blind / Miminashi narrative check on ref-002
        is_blind_story = "blind" in context or "miminashi" in context
        if is_blind_story:
            ref_002_prompt = tasks_map.get("ref-002", "").lower()
            if not ref_002_prompt:
                errors.append("[ref-002] AnatomicalMissing: ref-002 task is missing for blind story.")
            elif "blind" not in ref_002_prompt:
                errors.append(
                    "[ref-002] AnatomicalMismatch: narrative mentions 'Blind' / 'Miminashi', "
                    "but ref-002 does not describe the protagonist as 'blind'."
                )

        # Rule B: Biwa story check on ref-002
        is_biwa_story = "biwa" in context
        if is_biwa_story:
            ref_002_prompt = tasks_map.get("ref-002", "").lower()
            if not ref_002_prompt:
                errors.append("[ref-002] InstrumentMissing: ref-002 task is missing for biwa story.")
            elif "biwa" not in ref_002_prompt:
                errors.append(
                    "[ref-002] InstrumentMismatch: narrative mentions 'Biwa', "
                    "but ref-002 does not mention the 'biwa' instrument."
                )

        # Rule C: Kappa folklore check on ref-003
        is_kappa_story = "kappa" in context
        if is_kappa_story:
            ref_003_prompt = tasks_map.get("ref-003", "").lower()
            if not ref_003_prompt:
                errors.append("[ref-003] EntityMissing: ref-003 task is missing for Kappa folklore story.")
            else:
                # 1. Amphibious traits
                has_amphibious = any(k in ref_003_prompt for k in ("amphibious", "amphibian", "reptilian", "scaly"))
                if not has_amphibious:
                    errors.append(
                        "[ref-003] AnatomicalMismatch: Kappa entity must describe amphibious/scaly traits."
                    )
                # 2. Shell (kora)
                has_shell = any(k in ref_003_prompt for k in ("shell", "kora", "carapace", "tortoise"))
                if not has_shell:
                    errors.append(
                        "[ref-003] AnatomicalMismatch: Kappa entity must describe a shell (kora/tortoise-like shell)."
                    )
                # 3. Water plate (sara)
                has_sara = any(k in ref_003_prompt for k in ("sara", "dish", "plate", "depression", "cavity"))
                if not has_sara:
                    errors.append(
                        "[ref-003] AnatomicalMismatch: Kappa entity must describe the water dish/plate (sara) on the head."
                    )

        return errors

    @classmethod
    def validate_or_raise(
        cls,
        project_data: Dict[str, Any],
        project_title: str = "",
        slug: str = "",
        script_text: str = "",
    ) -> None:
        """Runs all linter rules against the project tasks.
        
        Raises PromptContaminationError or ReferenceFidelityError on failure.
        """
        tasks = project_data.get("tasks", [])
        if not tasks:
            raise ReferenceFidelityError("No tasks found in project data to validate.")

        tasks_map: Dict[str, str] = {}
        all_contamination_errors: List[str] = []
        all_fidelity_errors: List[str] = []

        for task in tasks:
            t_id = str(task.get("id", ""))
            prompt = str(task.get("prompt", ""))
            tasks_map[t_id] = prompt

            # 1. Anti-contamination
            contam_errs = cls.lint_prompt_contamination(
                task_id=t_id,
                prompt_text=prompt,
                project_title=project_title,
                slug=slug,
            )
            all_contamination_errors.extend(contam_errs)

            # 2. Anti-anachronism
            anachronism_errs = cls.lint_anachronisms(task_id=t_id, prompt_text=prompt)
            all_fidelity_errors.extend(anachronism_errs)

        # 3. Anatomical & semantic fidelity
        anatomical_errs = cls.lint_anatomical_and_semantic(
            tasks_map=tasks_map,
            project_title=project_title,
            slug=slug,
            script_text=script_text,
        )
        all_fidelity_errors.extend(anatomical_errs)

        if all_contamination_errors:
            msg = " | ".join(all_contamination_errors)
            logger.error("ReferenceFidelityLinter failed with PromptContaminationError: %s", msg)
            raise PromptContaminationError(f"Prompt contamination detected: {msg}")

        if all_fidelity_errors:
            msg = " | ".join(all_fidelity_errors)
            logger.error("ReferenceFidelityLinter failed with ReferenceFidelityError: %s", msg)
            raise ReferenceFidelityError(f"Reference fidelity integrity error: {msg}")

        logger.info(
            "ReferenceFidelityLinter passed with 0 errors for %s (%d tasks verified)",
            slug or project_title,
            len(tasks),
        )
