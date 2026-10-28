from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

AssetType = Literal["character", "environment", "style", "object", "other"]

from redsun.services.style_constants import (
    MASTER_STYLE_BIBLE,
    STYLE_TERMINATOR,
    format_prompt_with_style_bible,
)

STYLE_CUE_TERMINATOR = (
    f"16:9 aspect ratio. {STYLE_TERMINATOR}\n\n{MASTER_STYLE_BIBLE}"
)


class GeminiProjectMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: Optional[str] = None


class GeminiAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    type: AssetType
    file: str
    prompt: Optional[str] = None


class GeminiTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basename: str


class GeminiReferenceTask(BaseModel):
    """Fase A task (reference-generation).
    Contém EXCLUSIVAMENTE: id, title, prompt, references, output.
    `references` deve ser sempre uma lista vazia ([]).
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    prompt: str
    references: List[str] = Field(default_factory=list)
    output: GeminiTaskOutput

    @model_validator(mode="after")
    def validate_references_empty(self) -> "GeminiReferenceTask":
        if self.references:
            raise ValueError(
                f"Fase A reference task '{self.id}' cannot have references, "
                f"got: {self.references}. In Phase A, assets is {{}} and references must be []."
            )
        return self


class GeminiReferenceProject(BaseModel):
    """Contrato canônico de Fase A (`gemini-assistant/reference-generation/project.json`).
    - schemaVersion: 2 (inteiro)
    - project: {"id": f"{slug}-references", "name": f"{title} — Master References", "description": "..."}
    - assets: {} (sempre vazio)
    - tasks: [GeminiReferenceTask]
    """
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[2] = 2
    project: GeminiProjectMeta
    assets: Dict[str, GeminiAsset] = Field(default_factory=dict)
    tasks: List[GeminiReferenceTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_phase_a_invariants(self) -> "GeminiReferenceProject":
        if self.assets:
            raise ValueError(
                f"Fase A project must have empty assets {{}}, got {len(self.assets)} assets."
            )
        for t in self.tasks:
            if t.references:
                raise ValueError(
                    f"Fase A task '{t.id}' has non-empty references: {t.references}"
                )
        return self


class GeminiSceneTask(BaseModel):
    """Fase B task (scene-generation).
    Contém EXCLUSIVAMENTE: id, title, prompt, references, output.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    prompt: str
    references: List[str] = Field(default_factory=list)
    output: GeminiTaskOutput


class GeminiSceneProject(BaseModel):
    """Contrato canônico de Fase B (`gemini-assistant/02-scenes/project.json` ou raiz da fixture).
    - schemaVersion: 2 (inteiro)
    - project: {"id": slug, "name": title, "description": "..."}
    - assets: dict com assets e caminhos relativos
    - tasks: [GeminiSceneTask]
    """
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[2] = 2
    project: GeminiProjectMeta
    assets: Dict[str, GeminiAsset] = Field(default_factory=dict)
    tasks: List[GeminiSceneTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cross_references(self) -> "GeminiSceneProject":
        valid_keys = set(self.assets.keys())
        for t in self.tasks:
            for ref in t.references:
                if ref not in valid_keys:
                    raise ValueError(
                        f"Fase B task '{t.id}' references unknown asset '{ref}'. "
                        f"Declared assets: {sorted(valid_keys)}"
                    )
        return self
