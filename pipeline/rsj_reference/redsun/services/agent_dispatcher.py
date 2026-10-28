"""Lifecycle Hooks and Hermes Agent Dispatcher for Red Sun Japan.

Enables autonomous triggers for:
- Gate 1.5: Pre-TTS Script & Phonetic Audit via Hermes Agent (JingJing).
- Node 3 Handoff: macOS Native Notification to operator (Isamu) for image generation.
- Stage 4 & 5: Post-Render Master/Shorts Quality Audit and Multi-Platform Dispatch.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("redsun.dispatcher")


def notify_macos(title: str, message: str, sound: str = "Submarine") -> bool:
    """Emite uma notificação nativa na Central de Notificações do macOS via osascript."""
    clean_title = title.replace('"', '\\"')
    clean_message = message.replace('"', '\\"')
    script = f'display notification "{clean_message}" with title "{clean_title}" sound name "{sound}"'
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return True
        logger.warning(f"Falha ao emitir notificação macOS (código {res.returncode}): {res.stderr}")
        return False
    except Exception as e:
        logger.warning(f"Falha ao emitir notificação macOS: {e}")
        return False


def find_hermes_binary() -> Optional[str]:
    """Resolves hermes CLI path across standard PATH and local user paths."""
    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        return hermes_bin

    candidates = [
        Path.home() / ".local" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
        Path("/opt/homebrew/bin/hermes"),
    ]
    for cand in candidates:
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


async def dispatch_hermes_agent(task_prompt: str, working_dir: Optional[Path] = None) -> bool:
    """Executa a JingJing de forma assíncrona via Hermes Agent CLI sem travar o worker do FastAPI."""
    hermes_bin = find_hermes_binary()
    if not hermes_bin:
        logger.error("Binário 'hermes' não encontrado no PATH do sistema.")
        return False

    cmd = [hermes_bin, "run", "--agent", "jingjing", "--prompt", task_prompt]
    logger.info(f"Disparando Hermes Agent: {' '.join(cmd[:4])}...")

    # Ensure hermes environment has UTF-8 and proper paths
    env = os.environ.copy()
    env["PATH"] = f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(working_dir) if working_dir else None,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("JingJing concluiu a tarefa com sucesso via Hermes CLI.")
            return True
        else:
            err_output = stderr.decode(errors="replace").strip()
            # If the command syntax is not supported (e.g. 'run' is not a hermes command in this build),
            # gracefully fall back to one-shot mode (-z)
            if "not a `hermes` command" in err_output or "'run' is not" in err_output:
                logger.info("Hermes 'run' não reconhecido. Tentando modo one-shot (-z)...")
                fallback_cmd = [hermes_bin, "-z", task_prompt]
                proc_fb = await asyncio.create_subprocess_exec(
                    *fallback_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(working_dir) if working_dir else None,
                    env=env,
                )
                stdout_fb, stderr_fb = await proc_fb.communicate()
                if proc_fb.returncode == 0:
                    logger.info("JingJing concluiu a tarefa com sucesso via Hermes CLI (-z).")
                    return True
                else:
                    err_output = stderr_fb.decode(errors="replace").strip()

            logger.error(f"Hermes CLI retornou código {proc.returncode}: {err_output[:400]}")
            return False
    except Exception as e:
        logger.error(f"Exceção ao disparar Hermes Agent: {e}")
        return False


def dispatch_background(coro) -> asyncio.Task | None:
    """Safely dispatches an async coroutine from either an async loop or a synchronous thread."""
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)
    except RuntimeError:
        # No running event loop in current thread: spawn a dedicated thread
        import threading
        t = threading.Thread(target=lambda: asyncio.run(coro), daemon=True)
        t.start()
        return None


def trigger_gate_1_5(slug: str, working_dir: Optional[Path] = None):
    """Nó 1 concluído (Status SCRIPT READY / Passo 5 prepare_narration).

    Dispara a JingJing para auditar o roteiro e gerar pronunciation_map.json.
    """
    prompt_gate_1_5 = (
        f"JingJing, o Nó 1 do projeto '{slug}' foi concluído com sucesso. "
        f"Execute o Gate 1.5 estritamente conforme o AGENTS.md: "
        f"1. Inspecione 'projects/{slug}/text/script_en.md' validando consistência e epílogo. "
        f"2. Extraia os termos japoneses e gere 'projects/{slug}/audio/pronunciation_map.json'. "
        f"Avise no chat quando o arquivo estiver gravado."
    )
    logger.info("Triggering Hook Gate 1.5 for %s...", slug)
    return dispatch_background(dispatch_hermes_agent(prompt_gate_1_5, working_dir=working_dir))


def trigger_handoff_gate(slug: str) -> bool:
    """Nó 3 pausado (Passo 9 / HANDOFF GATE).

    Notifica o operador no macOS para iniciar a geração de imagens no Gemini Assistant.
    """
    logger.info("Triggering Hook Handoff Gate (macOS Notification) for %s...", slug)
    return notify_macos(
        title="Red Sun Japan // Handoff Gate",
        message=f"Prompts gerados para '{slug}'. Pronto para geração de imagens no Gemini Assistant!",
        sound="Glass"
    )


def trigger_stage_4_5(slug: str, working_dir: Optional[Path] = None):
    """Nó 4 concluído (Passo 16 organize_output / MASTER FULL HD COMPLETED).

    Notifica no macOS e aciona a JingJing para auditoria final e despacho.
    """
    logger.info("Triggering Hook Stage 4 & 5 for %s...", slug)
    notify_macos(
        title="Red Sun Japan // Render Concluído",
        message=f"Vídeo Master de '{slug}' compilado. Acionando JingJing para auditoria e despacho.",
        sound="Submarine"
    )

    prompt_stage_4_5 = (
        f"JingJing, o pipeline concluiu com sucesso a renderização completa (16/16) do projeto '{slug}'. "
        f"Execute imediatamente o Stage 4 (Auditoria de Integridade do Master sem bumpers externos, "
        f"conferência de legendas contra o script e integridade dos 3 Shorts) "
        f"e o Stage 5 (Git push do blog Astro e upload privado no YouTube). "
        f"Emita o Release Audit Report final ao Isamu conforme o AGENTS.md."
    )
    return dispatch_background(dispatch_hermes_agent(prompt_stage_4_5, working_dir=working_dir))
