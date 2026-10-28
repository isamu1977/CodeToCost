"""CodeToCost — Dashboard local de operação (Streamlit).

Painel de controle para o operador do canal:

* **Sidebar** — seletor de projetos (varre ``projects/``) e criação de
  novos projetos com o scaffold padrão.
* **Aba 1 — Status & Pipeline Runner** — checklist dos artefatos do projeto
  e disparo do ``build_pipeline.py`` (TTS + Whisper) e do render Remotion,
  com console de log ao vivo.
* **Aba 2 — Audio Player & Roteiro** — leitura do ``video_spec.json``,
  cena a cena, com player de áudio para auditar a voz do StepFun.
* **Aba 3 — Preview do Vídeo** — player nativo do MP4 final.
* **Aba 4 — YouTube Publisher Pack** — título, descrição (com links do
  catálogo de afiliados e capítulos extraídos do ``timeline.json``) e tags
  de SEO, prontos para copiar.

Executar::

    ./run_dashboard.sh
    # ou
    pipeline/core/.venv/bin/streamlit run pipeline/dashboard.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Paths — resolvidos a partir deste arquivo, nunca do cwd do terminal
# ─────────────────────────────────────────────────────────────────────────────
PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent
CORE_DIR = PIPELINE_DIR / "core"
PROJECTS_DIR = REPO_ROOT / "projects"
BUILD_SCRIPT = PIPELINE_DIR / "build_pipeline.py"
AGENT_WORKSPACE = REPO_ROOT / "agent_workspace"
AFFILIATE_CATALOG = AGENT_WORKSPACE / "affiliate_catalog.json"

#: Scaffold criado para cada novo projeto.
SCAFFOLD_DIRS = (
    "01_research",
    "02_spec",
    "03_audio",
    "04_assets",
    "05_lead_magnet",
    "output",
)

#: Rótulos das abas.
TAB_STATUS = "1 · Status & Pipeline"
TAB_AUDIO = "2 · Áudio & Roteiro"
TAB_VIDEO = "3 · Preview do Vídeo"
TAB_PUBLISH = "4 · YouTube Pack"

#: Palavras-chave base do canal (complementadas pelo conteúdo do roteiro).
CHANNEL_BASE_TAGS = (
    "automation",
    "self hosted",
    "saas cost",
    "cost engineering",
    "devops",
    "docker",
    "workflow automation",
    "serverless",
)

#: Stopwords removidas das tags derivadas.
TAG_STOPWORDS = {
    "the", "and", "for", "you", "with", "that", "this", "vs", "at", "in",
    "of", "on", "to", "a", "an", "is", "are", "real", "full", "next",
    "episode", "point", "between", "breakdown", "teardown", "from",
    "do", "da", "de", "des", "du", "la", "le",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de filesystem
# ─────────────────────────────────────────────────────────────────────────────
def list_projects() -> list[str]:
    """Return the project folder names under ``projects/`` (sorted)."""
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in PROJECTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_"))
    )


def project_dir(name: str) -> Path:
    """Absolute path of a project folder."""
    return PROJECTS_DIR / name


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file, returning ``None`` when missing or malformed."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def spec_path(pdir: Path) -> Path:
    return pdir / "02_spec" / "video_spec.json"


def timeline_path(pdir: Path) -> Path:
    return pdir / "timeline.json"


def dossier_path(pdir: Path) -> Path | None:
    """Locate the research dossier (project root or ``01_research/``)."""
    for candidate in (
        pdir / "01_research_dossier.md",
        pdir / "01_research" / "01_research_dossier.md",
    ):
        if candidate.exists():
            return candidate
    return None


def scene_wav(pdir: Path, index: int) -> Path | None:
    """Return the WAV of a scene (1-based index), mirroring the pipeline."""
    candidate = pdir / "03_audio" / f"scene_{index:02d}.wav"
    return candidate if candidate.exists() else None


def latest_render(pdir: Path) -> Path | None:
    """Return the most recent MP4 inside ``output/``."""
    output_dir = pdir / "output"
    if not output_dir.is_dir():
        return None
    videos = sorted(
        output_dir.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return videos[0] if videos else None


# ─────────────────────────────────────────────────────────────────────────────
# Scaffold de novo projeto
# ─────────────────────────────────────────────────────────────────────────────
def starter_spec(slug: str) -> dict[str, Any]:
    """Minimal ``video_spec.json`` template for a fresh project."""
    return {
        "video_id": slug,
        "title": "TODO: título oficial do episódio",
        "language": "en-US",
        "brand": {
            "name": "CodeToCost",
            "tagline": "Clear Architecture, Real Math, Zero Fluff.",
        },
        "scenes": [
            {
                "scene_id": "scene-01-title",
                "spoken_text": "TODO: escreva a narração de abertura.",
                "display_subtitle": "TODO",
                "visual_component": "title",
                "component_props": {
                    "kind": "title",
                    "kicker": "CodeToCost · Cost Engineering",
                    "title": "TODO: título na tela",
                    "subtitle": "TODO: subtítulo na tela",
                },
            },
            {
                "scene_id": "scene-02-outro",
                "duration_sec": 5.0,
                "display_subtitle": "TODO",
                "visual_component": "outro",
                "component_props": {
                    "kind": "outro",
                    "title": "TODO: título de encerramento",
                    "subtitle": "TODO",
                    "cta": "TODO: chamada para ação",
                },
            },
        ],
    }


def create_project(slug: str) -> tuple[bool, str]:
    """Create the folder scaffold for a new project.

    Returns ``(ok, message)``.
    """
    slug = slug.strip()
    if not slug:
        return False, "Informe um nome de projeto."
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]*", slug):
        return False, (
            "Use apenas minúsculas, números, hífen e underscore "
            "(ex.: 002-make-vs-zapier-scale)."
        )

    target = project_dir(slug)
    if target.exists():
        return False, f"O projeto '{slug}' já existe."

    for folder in SCAFFOLD_DIRS:
        (target / folder).mkdir(parents=True, exist_ok=True)
        keep = target / folder / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

    spec = spec_path(target)
    spec.write_text(
        json.dumps(starter_spec(slug), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True, f"Projeto '{slug}' criado com o scaffold padrão."


# ─────────────────────────────────────────────────────────────────────────────
# Status dos artefatos
# ─────────────────────────────────────────────────────────────────────────────
def project_status(pdir: Path) -> list[dict[str, str]]:
    """Build the artifact checklist for the project."""
    rows: list[dict[str, str]] = []

    # 1. Research dossier
    dossier = dossier_path(pdir)
    rows.append(
        {
            "label": "01_research_dossier.md",
            "state": "Criado" if dossier else "Ausente",
            "icon": "✅" if dossier else "❌",
            "detail": str(dossier.relative_to(REPO_ROOT)) if dossier else "Pesquisa ainda não documentada",
        }
    )

    # 2. Video spec
    spec = read_json(spec_path(pdir))
    if spec is None:
        spec_state, spec_icon, spec_detail = "Ausente", "❌", "Sem 02_spec/video_spec.json"
    elif not spec.get("scenes"):
        spec_state, spec_icon, spec_detail = "Ausente", "❌", "JSON sem o array 'scenes'"
    else:
        spec_state, spec_icon = "Validado", "✅"
        spec_detail = f"{len(spec['scenes'])} cena(s) · lang={spec.get('language', 'en-US')}"

    rows.append(
        {
            "label": "02_spec/video_spec.json",
            "state": spec_state,
            "icon": spec_icon,
            "detail": spec_detail,
        }
    )

    # 3. Generated audio
    audio_dir = pdir / "03_audio"
    wavs = sorted(audio_dir.glob("*.wav")) if audio_dir.is_dir() else []
    rows.append(
        {
            "label": "03_audio/",
            "state": f"{len(wavs)} WAV(s)",
            "icon": "✅" if wavs else "⚠️",
            "detail": ", ".join(w.name for w in wavs[:4]) or "Nenhum áudio gerado",
        }
    )

    # 4. Timeline
    timeline = read_json(timeline_path(pdir))
    if timeline is None:
        tl_state, tl_icon, tl_detail = "Ausente", "❌", "Rode o pipeline para compilar"
    else:
        tl_state, tl_icon = "Compilado", "✅"
        frames = int(timeline.get("durationInFrames") or 0)
        fps = int(timeline.get("fps") or 30)
        tl_detail = f"{frames} frames · {frames / max(fps, 1):.2f}s · {len(timeline.get('scenes') or [])} cena(s)"

    rows.append(
        {"label": "timeline.json", "state": tl_state, "icon": tl_icon, "detail": tl_detail}
    )

    # 5. Render
    video = latest_render(pdir)
    rows.append(
        {
            "label": "output/*.mp4",
            "state": "Renderizado" if video else "Ausente",
            "icon": "✅" if video else "⚠️",
            "detail": f"{video.name} ({video.stat().st_size / 1_048_576:.1f} MB)"
            if video
            else "Nenhum MP4 em output/",
        }
    )

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Execução do pipeline (com log ao vivo)
# ─────────────────────────────────────────────────────────────────────────────
def pipeline_python() -> str:
    """Interpreter used to run the pipeline (the core venv when available)."""
    venv_python = CORE_DIR / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def run_pipeline(project: str, render: bool, console) -> int:
    """Run ``build_pipeline.py`` streaming its output into ``console``.

    Returns the process exit code. The child runs with ``PYTHONUNBUFFERED``
    so the log reaches the browser line by line instead of in blocks.
    """
    command = [
        pipeline_python(),
        str(BUILD_SCRIPT),
        "--project",
        project,
    ]
    if render:
        command.append("--render")

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    lines: list[str] = [f"$ {' '.join(command)}"]
    console.code("\n".join(lines), language="bash")

    process = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip()
        lines.append(line)
        console.code("\n".join(lines[-250:]), language="bash")
        st.session_state["last_log"] = "\n".join(lines)

    st.session_state["last_log"] = "\n".join(lines)
    return process.wait()


# ─────────────────────────────────────────────────────────────────────────────
# YouTube Publisher Pack
# ─────────────────────────────────────────────────────────────────────────────
def format_timestamp(seconds: float) -> str:
    """Format seconds as ``M:SS`` (or ``H:MM:SS`` past one hour)."""
    total = int(round(max(seconds, 0)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _scene_label(scene: dict[str, Any], fallback: str) -> str:
    """Human label for a chapter, taken from the scene props."""
    props = scene.get("props") or {}
    for key in ("title", "headline", "subtitle", "footerNote"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def build_chapters(timeline: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Extract ``(timestamp, label)`` chapters from the compiled timeline."""
    if not timeline:
        return []

    fps = float(timeline.get("fps") or 30)
    chapters: list[tuple[str, str]] = []
    for scene in timeline.get("scenes") or []:
        start = float(scene.get("startFrame") or 0) / max(fps, 1)
        label = _scene_label(scene, str(scene.get("id") or "Cena"))
        chapters.append((format_timestamp(start), label))
    return chapters


def mentioned_affiliates(
    spec: dict[str, Any], catalog: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split the affiliate catalog into (referenced, mentioned-but-pending).

    Only programs with a real URL are rendered as links — the catalog
    explicitly forbids inventing an affiliate URL while the status is
    ``pending``.
    """
    if not catalog:
        return [], []

    programs = catalog.get("programs") or {}
    keys = {
        str(spec.get("primary_affiliate") or ""),
        str(spec.get("secondary_affiliate") or ""),
    }

    haystack = " ".join(
        [
            str(spec.get("title") or ""),
            " ".join(
                str(scene.get("spoken_text") or "")
                for scene in (spec.get("scenes") or [])
            ),
            " ".join(
                str(scene.get("display_subtitle") or "")
                for scene in (spec.get("scenes") or [])
            ),
        ]
    ).lower()

    referenced: list[dict[str, Any]] = []
    pending: list[str] = []
    for key, program in programs.items():
        if not isinstance(program, dict):
            continue
        display = str(program.get("display_name") or key)
        mentioned = key in keys or display.lower() in haystack
        if not mentioned:
            continue
        if str(program.get("status") or "").lower() == "pending" or not program.get("url"):
            pending.append(display)
        else:
            referenced.append({"name": display, "url": str(program["url"])})
    return referenced, pending


def build_tags(spec: dict[str, Any]) -> list[str]:
    """Derive SEO tags from the episode title, scene copy and channel base.

    The title contributes its meaningful tokens; the scene subtitles only
    contribute *named* terms (capitalized or numeric) — product names such as
    ``Make.com`` or ``n8n`` — so generic filler never reaches the tag list.
    """
    tokens: list[str] = []

    title = str(spec.get("title") or "")
    # Regex Unicode-aware: preserva acentos ("título" não vira "tulo").
    for word in re.findall(r"[^\W_][\w,\.\+#]*", title, flags=re.UNICODE):
        word = word.strip(",.+#")
        if len(word) > 2 and word.lower() not in TAG_STOPWORDS:
            tokens.append(word.lower())

    for scene in spec.get("scenes") or []:
        subtitle = str(scene.get("display_subtitle") or "")
        # Named terms: capitalized words (Make.com, Postgres) and numbers.
        for word in re.findall(r"\b[A-ZÀ-Þ][\w\.]*\b", subtitle, flags=re.UNICODE):
            cleaned = word.strip(".")
            if len(cleaned) > 1 and cleaned.lower() not in TAG_STOPWORDS:
                tokens.append(cleaned.lower())
        for word in re.findall(r"\b\d[\d,\.]*\b", subtitle):
            if word not in TAG_STOPWORDS:
                tokens.append(word)

    tokens.extend(CHANNEL_BASE_TAGS)

    seen: set[str] = set()
    tags: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        tags.append(token)
    return tags[:15]


def build_publisher_pack(pdir: Path) -> dict[str, Any]:
    """Assemble the YouTube publisher pack for a project."""
    spec = read_json(spec_path(pdir)) or {}
    timeline = read_json(timeline_path(pdir))
    catalog = read_json(AFFILIATE_CATALOG)

    title = str(spec.get("title") or pdir.name)
    chapters = build_chapters(timeline)
    referenced, pending = mentioned_affiliates(spec, catalog)
    tags = build_tags(spec)

    # ── Description ──────────────────────────────────────────────────────
    lines: list[str] = []

    hook = ""
    for scene in spec.get("scenes") or []:
        subtitle = str(scene.get("display_subtitle") or "").strip()
        if subtitle:
            hook = subtitle
            break
    if hook:
        lines.append(hook)
        lines.append("")

    if chapters:
        lines.append("CHAPTERS")
        for stamp, label in chapters:
            lines.append(f"{stamp} {label}")
        lines.append("")

    if referenced:
        lines.append("TOOLS & LINKS")
        for item in referenced:
            lines.append(f"- {item['name']}: {item['url']}")
        lines.append("")

    if pending:
        lines.append("TOOLS MENTIONED")
        for name in pending:
            lines.append(f"- {name}")
        lines.append("")

    own_products = (catalog or {}).get("own_products") or {}
    resources = [
        product
        for product in own_products.values()
        if isinstance(product, dict) and str(product.get("status") or "").lower() != "pending"
    ]
    if resources:
        lines.append("RESOURCES")
        for product in resources:
            price = product.get("price_usd")
            price_text = f" (${price})" if price else ""
            url = str(product.get("url") or "")
            lines.append(
                f"- {product.get('display_name')}{price_text}" + (f": {url}" if url else "")
            )
        lines.append("")

    lines.append(
        "Clear architecture, real math, zero fluff. "
        "Subscribe for the full cost models."
    )

    description = "\n".join(lines).strip()

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "chapters": chapters,
        "referenced": referenced,
        "pending": pending,
        "has_spec": bool(spec),
    }


# ─────────────────────────────────────────────────────────────────────────────
# UI — copy button
# ─────────────────────────────────────────────────────────────────────────────
def embed_html(html: str, height: int = 70) -> None:
    """Embed raw HTML in an iframe.

    ``st.iframe`` is the current API (Streamlit >= 1.60); ``st.components.v1.html``
    is the fallback for older versions, kept because the dashboard declares
    ``streamlit>=1.35.0`` as its minimum.
    """
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # pragma: no cover - legacy Streamlit
        st.components.v1.html(html, height=height)


def copy_box(text: str, label: str = "Copiar") -> None:
    """Render a code box plus a copy-to-clipboard button."""
    st.code(text, language="markdown")

    payload = json.dumps(text)
    embed_html(
        f"""
        <button id="copy-btn"
                style="width:100%;padding:9px;border-radius:8px;border:1px solid #1F2937;
                       background:#111827;color:#06B6D4;font-weight:600;cursor:pointer;font-size:14px;">
            📋 {label}
        </button>
        <div id="copy-status" style="color:#10B981;font-size:12px;margin-top:6px;"></div>
        <script>
          const payload = {payload};
          const btn = document.getElementById('copy-btn');
          const status = document.getElementById('copy-status');
          btn.addEventListener('click', () => {{
            const done = () => {{ status.textContent = 'Copiado para a área de transferência ✓'; }};
            if (navigator.clipboard && window.isSecureContext) {{
              navigator.clipboard.writeText(payload).then(done).catch(fallback);
            }} else {{
              fallback();
            }}
            function fallback() {{
              const ta = document.createElement('textarea');
              ta.value = payload;
              ta.style.position = 'fixed';
              ta.style.opacity = '0';
              document.body.appendChild(ta);
              ta.select();
              try {{ document.execCommand('copy'); done(); }}
              catch (e) {{ status.textContent = 'Selecione o texto e copie manualmente (Ctrl+C).'; }}
              document.body.removeChild(ta);
            }}
          }});
        </script>
        """,
        height=70,
    )


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CodeToCost · Dashboard",
    page_icon="◧",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.1rem; padding-bottom: 3rem; }
      .ctc-title { font-size: 1.55rem; font-weight: 700; letter-spacing: -0.02em; color: #E5E7EB; }
      .ctc-sub   { color: #9CA3AF; font-size: 0.92rem; margin-top: -0.4rem; margin-bottom: 1.1rem; }
      .ctc-card  { background:#111827; border:1px solid #1F2937; border-radius:12px;
                   padding:14px 18px; margin-bottom:10px; }
      .ctc-row   { display:flex; justify-content:space-between; align-items:center; gap:14px; }
      .ctc-name  { font-weight:600; color:#E5E7EB; }
      .ctc-meta  { color:#6B7280; font-size:0.82rem; }
      .ctc-pill  { border-radius:999px; padding:3px 12px; font-size:0.78rem; font-weight:600;
                   background:#151D2C; border:1px solid #1F2937; color:#06B6D4; white-space:nowrap; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div class="ctc-title">◧ CodeToCost</div>'
        '<div class="ctc-sub">Local operator dashboard</div>',
        unsafe_allow_html=True,
    )

    projects = list_projects()
    st.markdown("#### Projetos")

    # Uma criação de projeto agendada no run anterior precisa ser aplicada
    # ANTES de instanciar o widget — modificar `st.session_state["project"]`
    # depois do selectbox levanta StreamlitWidgetAlreadyInstantiatedError.
    pending = st.session_state.pop("pending_project", None)
    if pending and pending in projects:
        st.session_state["project"] = pending

    if not projects:
        st.info("Nenhum projeto encontrado em `projects/`.")
        selected = None
    else:
        index = projects.index(st.session_state["project"]) if st.session_state.get("project") in projects else 0
        selected = st.selectbox(
            "Projeto ativo",
            projects,
            index=index,
            key="project",
            label_visibility="collapsed",
        )

    st.divider()
    st.markdown("#### Novo projeto")

    with st.form("new_project", clear_on_submit=True):
        new_slug = st.text_input(
            "Slug",
            placeholder="002-make-vs-zapier-scale",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("➕ Criar projeto", use_container_width=True)

    if submitted:
        ok, message = create_project(new_slug)
        if ok:
            # A troca de seleção acontece no próximo run (ver `pending_project`).
            st.session_state["pending_project"] = new_slug.strip()
            st.success(message)
            st.rerun()
        else:
            st.error(message)

    st.divider()
    st.caption("Pipeline local · StepFun TTS · Remotion")

# ── Cabeçalho ────────────────────────────────────────────────────────────
st.markdown(
    '<div class="ctc-title">CodeToCost · Production Dashboard</div>'
    '<div class="ctc-sub">Status do pipeline, auditoria de roteiro, preview e pacote de publicação.</div>',
    unsafe_allow_html=True,
)

if selected is None:
    st.warning("Crie um projeto na barra lateral para começar.")
    st.stop()

pdir = project_dir(selected)
status_rows = project_status(pdir)

tab_status, tab_audio, tab_video, tab_publish = st.tabs(
    [TAB_STATUS, TAB_AUDIO, TAB_VIDEO, TAB_PUBLISH]
)

# ═════════════════════════════════════════════════════════════════════════
# Aba 1 — Status & Pipeline Runner
# ═════════════════════════════════════════════════════════════════════════
with tab_status:
    left, right = st.columns([1.25, 1], gap="large")

    with left:
        st.markdown("#### Artefatos do projeto")
        for row in status_rows:
            st.markdown(
                f"""
                <div class="ctc-card">
                  <div class="ctc-row">
                    <div>
                      <div class="ctc-name">{row['icon']} {row['label']}</div>
                      <div class="ctc-meta">{row['detail']}</div>
                    </div>
                    <div class="ctc-pill">{row['state']}</div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.caption(f"Projeto: `projects/{selected}`")

        # O console vive na coluna esquerda: ele é criado ANTES dos botões
        # (que ficam na coluna direita), então o handler consegue escrever
        # nele em tempo real durante o rerun.
        st.markdown("#### Console")
        console = st.empty()
        with console.container():
            st.code(
                st.session_state.get("last_log")
                or "Clique em uma ação ao lado para ver o log do pipeline em tempo real.",
                language="bash",
            )

    with right:
        st.markdown("#### Ações do pipeline")

        st.markdown(
            "**Gerar Áudios + Whisper** — sintetiza a narração com a voz oficial "
            "do StepFun (`soft-spoken-gentleman`), normaliza o loudness e gera "
            "os timestamps de palavra."
        )
        if st.button(
            "🎙️ Gerar Áudios + Whisper",
            use_container_width=True,
            key="run_tts",
            disabled=read_json(spec_path(pdir)) is None,
        ):
            with st.spinner("Sintetizando narração…"):
                code = run_pipeline(selected, render=False, console=console)
            st.session_state["last_exit_code"] = code
            if code == 0:
                st.success("Áudios + timestamps gerados.")
            else:
                st.error(f"Pipeline falhou (exit code {code}).")

        st.markdown("---")

        st.markdown(
            "**Renderizar Vídeo Remotion** — compila o `timeline.json`, sincroniza "
            "os WAVs para `remotion-app/public/` e renderiza o MP4 final."
        )
        if st.button(
            "🎬 Renderizar Vídeo Remotion",
            use_container_width=True,
            key="run_render",
            disabled=read_json(spec_path(pdir)) is None,
        ):
            with st.spinner("Renderizando com Remotion…"):
                code = run_pipeline(selected, render=True, console=console)
            st.session_state["last_exit_code"] = code
            if code == 0:
                st.success("Render concluído.")
            else:
                st.error(f"Render falhou (exit code {code}).")

# ═════════════════════════════════════════════════════════════════════════
# Aba 2 — Audio Player & Roteiro
# ═════════════════════════════════════════════════════════════════════════
with tab_audio:
    spec = read_json(spec_path(pdir))

    if spec is None:
        st.warning("Sem `02_spec/video_spec.json` — rode o pipeline primeiro.")
    else:
        scenes = spec.get("scenes") or []

        # Voices actually used by the generated scenes (from the TTS meta).
        voices: list[str] = []
        for index in range(1, len(scenes) + 1):
            meta = read_json(pdir / "03_audio" / f"scene_{index:02d}.meta.json") or {}
            voice = str(meta.get("voice") or "").strip()
            if voice and voice not in voices:
                voices.append(voice)

        voice_label = ", ".join(f"`{v}`" for v in voices) if voices else "—"
        st.markdown(f"**{len(scenes)} cena(s)** · voz: {voice_label}")
        st.divider()

        for index, scene in enumerate(scenes, start=1):
            scene_id = str(scene.get("scene_id") or f"scene-{index}")
            spoken = str(scene.get("spoken_text") or "")
            component = str(scene.get("visual_component") or "—")
            subtitle = str(scene.get("display_subtitle") or "")

            header_left, header_right = st.columns([3, 1.2])
            with header_left:
                st.markdown(f"##### {index}. `{scene_id}` · `{component}`")
            with header_right:
                wav = scene_wav(pdir, index)
                if wav:
                    st.caption(f"🔊 {wav.name}")
                else:
                    st.caption("🔇 sem áudio")

            if subtitle:
                st.caption(subtitle)

            if spoken:
                st.text(spoken)
            else:
                st.caption("Cena visual-only (sem narração).")

            wav = scene_wav(pdir, index)
            if wav:
                st.audio(str(wav), format="audio/wav")
            st.divider()

# ═════════════════════════════════════════════════════════════════════════
# Aba 3 — Preview do Vídeo
# ═════════════════════════════════════════════════════════════════════════
with tab_video:
    video = latest_render(pdir)

    if video is None:
        st.info("Nenhum MP4 em `output/` — use a aba **Status & Pipeline** para renderizar.")
    else:
        st.video(str(video))
        size_mb = video.stat().st_size / 1_048_576
        st.caption(
            f"🎬 `{video.name}` · {size_mb:.1f} MB · modificado em "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(video.stat().st_mtime))}"
        )

        others = [p for p in sorted((pdir / 'output').glob('*.mp4')) if p != video]
        if others:
            st.markdown("#### Outros renders")
            for other in others:
                st.caption(f"• {other.name} ({other.stat().st_size / 1_048_576:.1f} MB)")

# ═════════════════════════════════════════════════════════════════════════
# Aba 4 — YouTube Publisher Pack
# ═════════════════════════════════════════════════════════════════════════
with tab_publish:
    pack = build_publisher_pack(pdir)

    if not pack["has_spec"]:
        st.warning("Sem `video_spec.json` — o pacote usa apenas o título do projeto.")
    elif not pack["chapters"]:
        st.info(
            "Sem `timeline.json` compilado — os capítulos serão gerados na próxima "
            "execução do pipeline."
        )

    st.markdown("#### Título")
    copy_box(pack["title"], "Copiar título")

    st.markdown("#### Descrição")
    copy_box(pack["description"], "Copiar descrição")

    st.markdown("#### Tags")
    copy_box(", ".join(pack["tags"]), "Copiar tags")

    if pack["pending"]:
        st.markdown(
            f"ℹ️ *{len(pack['pending'])} programa(s) de afiliado ainda sem URL aprovada "
            f"(`{', '.join(pack['pending'])}`) — nada foi inventado no texto.*"
        )
