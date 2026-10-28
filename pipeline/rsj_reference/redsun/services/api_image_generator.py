"""Autonomous Google Imagen API image generator for Red Sun Japan.

Provides 100% autonomous 2-phase generation (Phase 1: References, Phase 2: Scenes)
using the official Google GenAI SDK (google-genai / Imagen 3).
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from PIL import Image

from redsun.services.style_constants import (
    attenuate_content_for_safety,
    format_prompt_with_style_bible,
)
from redsun.workspace import ProjectWorkspace

logger = logging.getLogger("redsun.api_image_generator")

# Directive to reinforce 16:9 panoramic horizontal composition at the start of scene prompt
WIDESCREEN_SCENE_DIRECTIVE = "Cinematic widescreen composition, 16:9 aspect ratio, panoramic horizontal framing."

# Explicitly load .env from motor-python and workspace root
_MOTOR_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
if _MOTOR_ENV.exists():
    load_dotenv(dotenv_path=_MOTOR_ENV, override=False)
load_dotenv()

DEFAULT_IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "muse-image-1.0")
DEFAULT_IMAGEN_MODEL = DEFAULT_IMAGE_MODEL

META_API_KEY = os.environ.get("META_API_KEY")
META_BASE_URL = os.environ.get("META_API_BASE_URL", "https://api.meta.ai/v1")

# Global in-memory tracking of generation status and cancellation requests
_STATUS_STORE: Dict[str, Dict[str, Any]] = {
    "rokurokubi-the-long-necked-woman-of-kai": {
        "status": "idle",
        "phase": "scenes",
        "current_index": 0,
        "total_images": 72,
        "total_items": 72,
        "current_filename": None,
        "current_item_title": None,
        "error": None,
        "error_message": None,
        "warning": None,
        "failed_tasks": [],
        "message": "Idle",
    }
}
_CANCEL_FLAGS: Dict[str, bool] = {}


def crop_to_16_9(img: Image.Image) -> Image.Image:
    """Center-crops an image to exact 16:9 widescreen aspect ratio (e.g. 1920x1280 -> 1920x1080)."""
    w, h = img.size
    if h == 0 or w == 0:
        return img
    target_ratio = 16.0 / 9.0
    current_ratio = w / h
    if abs(current_ratio - target_ratio) < 0.02:
        return img
    if current_ratio > target_ratio:
        # Too wide: crop horizontal sides
        target_w = int(round(h * target_ratio))
        left = max(0, (w - target_w) // 2)
        right = left + target_w
        return img.crop((left, 0, right, h))
    else:
        # Too tall: crop top and bottom
        target_h = int(round(w / target_ratio))
        top = max(0, (h - target_h) // 2)
        bottom = top + target_h
        return img.crop((0, top, w, bottom))


def convert_webp_to_target(
    webp_bytes: bytes,
    target_path: Path,
    output_format: str = "PNG",
    is_scene: bool = False,
) -> None:
    """Conversão obrigatória de formato WEBP nativo para PNG ou JPEG, garantindo proporção 16:9 para cenas."""
    with Image.open(io.BytesIO(webp_bytes)) as img:
        rgb_img = img.convert("RGB")
        if is_scene:
            w, h = rgb_img.size
            ratio = w / h if h > 0 else 0.0
            if ratio >= 1.20 and abs(ratio - (16.0 / 9.0)) >= 0.02:
                rgb_img = crop_to_16_9(rgb_img)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        if output_format.upper() in ["JPG", "JPEG"]:
            rgb_img.save(target_path, "JPEG", quality=95, optimize=True)
        else:
            rgb_img.save(target_path, "PNG", optimize=True)


def validate_scene_aspect_ratio(
    image_path: Path | str,
    is_scene: bool = True,
) -> tuple[int, int, float]:
    """Validação física obrigatória imediatamente após gravar a imagem em disco.
    
    1. Abre o arquivo com PIL.Image.open(output_path) e lê w, h = img.size.
    2. Calcula o ratio: ratio = w / h if h > 0 else 0.0.
    3. Para cenas de vídeo (is_scene=True), se a imagem for landscape (ratio >= 1.20)
       mas não for exatamente 16:9 (ex: 1920x1280 ratio 1.50), executa auto-crop
       centralizado para 16:9 exato (1920x1080).
    4. Se após o ajuste o ratio for < 1.60, remove o arquivo e lança ValueError
       para indicar proporção incompatível com o formato widescreen.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado para validação física de proporção: {path}")

    with Image.open(path) as img:
        w, h = img.size
        ratio = w / h if h > 0 else 0.0

    if h == 0:
        path.unlink(missing_ok=True)
        raise ValueError(f"Dimensões físicas inválidas detectadas no arquivo gerado: {w}x{h}")

    if is_scene:
        if ratio >= 1.20 and ratio < 1.60:
            with Image.open(path) as img:
                cropped = crop_to_16_9(img)
                out_fmt = "JPEG" if path.suffix.lower() in [".jpg", ".jpeg"] else "PNG"
                if out_fmt == "JPEG":
                    cropped.convert("RGB").save(path, "JPEG", quality=95, optimize=True)
                else:
                    cropped.save(path, "PNG", optimize=True)
                w, h = cropped.size
                ratio = w / h if h > 0 else 0.0
                logger.info(
                    "validate_scene_aspect_ratio: Auto-cropped %s to 16:9 (%dx%d, ratio %.2f)",
                    path.name,
                    w,
                    h,
                    ratio,
                )

        if ratio < 1.60:
            path.unlink(missing_ok=True)
            raise ValueError(
                f"ERRO DE PROPORÇÃO (Meta Muse): Imagem {w}x{h} (ratio {ratio:.2f}) não é widescreen 16:9. "
                f"Abortando lote para evitar consumo desnecessário."
            )

    return w, h, ratio


async def generate_with_meta_muse(
    prompt: str,
    is_scene: bool = True,
    model: str = "muse-image-1.0",
    timeout: float = 90.0,
) -> bytes:
    """Dispara a geração de imagem via API Meta AI (POST /v1/responses) retornando buffer WEBP."""
    api_key = os.environ.get("META_API_KEY") or META_API_KEY
    if not api_key or not api_key.strip():
        error_msg = "META_API_KEY não configurada no ambiente (.env)."
        logger.error(error_msg)
        print(f"\n[ERROR] [API_IMAGE_GENERATOR] {error_msg}\n", flush=True)
        raise ValueError(error_msg)

    base_url = (os.environ.get("META_API_BASE_URL") or META_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/responses"

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }

    prompt_to_send = attenuate_content_for_safety(prompt)
    if is_scene and "16:9" not in prompt_to_send:
        prompt_to_send = f"{WIDESCREEN_SCENE_DIRECTIVE}\n\n{prompt_to_send}"

    payload = {
        "model": model,
        "input": prompt_to_send,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        if response.status_code == 400:
            error_details = response.text
            try:
                err_json = response.json()
                if "error" in err_json and "message" in err_json["error"]:
                    error_details = f"{err_json['error'].get('code')}: {err_json['error'].get('message')}"
            except Exception:
                pass
            raise RuntimeError(f"Meta AI 400 Bad Request ({error_details})")
        response.raise_for_status()
        data = response.json()

    # Extrai o buffer WEBP da imagem gerada
    for item in data.get("output", []):
        if item.get("type") == "image_generation_call":
            res = item.get("result")
            if res:
                if res.startswith("http://") or res.startswith("https://"):
                    async with httpx.AsyncClient(timeout=30.0) as dl_client:
                        dl_resp = await dl_client.get(res)
                        dl_resp.raise_for_status()
                        return dl_resp.content
                return base64.b64decode(res)
        elif item.get("type") == "image":
            res = item.get("image", {}).get("b64_json") or item.get("b64_json") or item.get("result")
            if res:
                return base64.b64decode(res)
            url = item.get("image", {}).get("url") or item.get("url")
            if url:
                async with httpx.AsyncClient(timeout=30.0) as dl_client:
                    dl_resp = await dl_client.get(url)
                    dl_resp.raise_for_status()
                    return dl_resp.content

    if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
        d0 = data["data"][0]
        if "b64_json" in d0 and d0["b64_json"]:
            return base64.b64decode(d0["b64_json"])
        if "url" in d0 and d0["url"]:
            async with httpx.AsyncClient(timeout=30.0) as dl_client:
                dl_resp = await dl_client.get(d0["url"])
                dl_resp.raise_for_status()
                return dl_resp.content

    raise RuntimeError(f"Nenhum dado de imagem retornado pela Meta AI para {model}.")


def generate_with_meta_muse_sync(
    prompt: str,
    is_scene: bool = True,
    model: str = "muse-image-1.0",
    timeout: float = 90.0,
) -> bytes:
    """Wrapper síncrono para generate_with_meta_muse, seguro em loops de eventos e threads."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, generate_with_meta_muse(prompt, is_scene, model, timeout)).result()
    else:
        return asyncio.run(generate_with_meta_muse(prompt, is_scene, model, timeout))


def generate_scene_frame(
    prompt: str,
    is_scene: bool = True,
    client: Any = None,
    model: str = "muse-image-1.0",
) -> bytes:
    """Gera frame de cena usando muse-image-1.0 e retorna os bytes em formato convertido (JPEG)."""
    raw_webp = generate_with_meta_muse_sync(prompt=prompt, is_scene=is_scene, model=model)
    buf = io.BytesIO()
    with Image.open(io.BytesIO(raw_webp)) as img:
        rgb_img = img.convert("RGB")
        if is_scene:
            w, h = rgb_img.size
            ratio = w / h if h > 0 else 0.0
            if ratio >= 1.20 and abs(ratio - (16.0 / 9.0)) >= 0.02:
                rgb_img = crop_to_16_9(rgb_img)
        rgb_img.save(buf, "JPEG", quality=95, optimize=True)
    return buf.getvalue()


def get_api_client(force_vertexai: Optional[bool] = None, location_override: Optional[str] = None) -> Any:
    """Compatibilidade legada para get_api_client."""
    return None


SAFETY_TERM_REPLACEMENTS = [
    # Termos de manuseio e transporte folclórico de cabeças Yōkai
    (r"\bholds\s+the\s+severed\s+head\s+by\s+its\s+hair\b", "carries the wrapped spirit bundle revealing the pale supernatural Yōkai countenance"),
    (r"\bsevered\s+head\s+by\s+its\s+hair\b", "wrapped supernatural Yōkai countenance"),
    (r"\bholds\s+the\s+severed\s+head\s+aloft\b", "holds aloft the wrapped supernatural spirit bundle"),
    (r"\bstill\s+carrying\s+the\s+head\b", "carrying the wrapped spirit bundle"),
    (r"\bcarrying\s+the\s+head\b", "carrying the wrapped spirit bundle"),
    (r"\bstares\s+at\s+the\s+severed\s+head\b", "stares in awe at the supernatural Yōkai countenance"),
    (r"\bthe\s+severed\s+head\s+still\s+dangling\s+from\s+its\s+sleeve\b", "the wrapped Yōkai spirit relic resting in the sleeve"),
    (r"\bsevered\s+head\s+still\s+dangling\b", "wrapped Yōkai spirit relic resting in the sleeve"),
    (r"\bthe\s+head\s+still\s+dangles\b", "the wrapped spirit relic rests in hand"),
    (r"\bmacabre\s+trophy\b", "supernatural curiosity"),
    (r"\bcarefully\s+burying\s+the\s+severed\s+head\b", "carefully interring the sacred Rokurokubi spirit relic"),
    (r"\bburying\s+the\s+severed\s+head\b", "reverently interring the sacred Rokurokubi spirit relic"),
    (r"\blifts\s+a\s+headless\s+body\b", "lifts the slumbering kimono-clad form"),
    (r"\bcasts\s+the\s+headless\s+body\b", "conceals the slumbering kimono form"),
    (r"\bblood-fouled\s+head\b", "wrapped spirit bundle containing the eerie Yōkai countenance"),
    (r"\betched\s+into\s+the\s+pale\s+flesh\b", "naturally marked as ancient talismanic script along the skin"),

    # Termos de corte de pescoço e separação anatômica
    (r"\bthe\s+neck\s+ends\s+abruptly\s+in\s+a\s+(?:clean,\s+)?unsettling\s+cut\b", "the kimono collar recedes into deep indigo shadows and cold ethereal mist"),
    (r"\bneck\s+ends\s+abruptly\s+in\s+a\s+(?:clean,\s+)?(?:unsettling\s+)?cut\b", "kimono collar recedes into deep indigo shadows and cold ethereal mist"),

    # Termos de ausência de cabeça / separação folclórica
    (r"\bthe\s+headless\s+bodies\b", "figures on sleeping mats with collars veiled in deep shadow"),
    (r"\bheadless\s+bodies\b", "figures on sleeping mats with collars veiled in deep shadow"),
    (r"\bthe\s+headless\s+body\b", "prone figure in robes with collar receding into dark mist"),
    (r"\bheadless\s+body\b", "prone figure in robes with collar receding into dark mist"),
    (r"\bheadless\b", "shadow-shrouded"),
    (r"\bdisembodied\s+heads\b", "floating ethereal spirit faces"),
    (r"\bdisembodied\s+head\b", "spectral face drifting in mid-air"),
    (r"\bdisembodied\b", "spectral drifting"),
    (r"\bthe\s+severed\s+head(?:'s)?\b", "detached spiritual countenance floating in shadows"),
    (r"\bsevered\s+head(?:'s)?\b", "detached spiritual countenance floating in shadows"),
    (r"\bsevered\s+necks?\b", "spiritual mist-veiled collar"),
    (r"\bsevered\s+arms?\b", "torn floating kimono sleeve"),
    (r"\bsevered\s+(?:limbs?|legs?|hands?|feet)\b", "submerged frayed silk garments"),
    (r"\bsevered\b", "ethereally detached"),
    (r"\bdecapitated\b", "spiritually separated"),
    (r"\bbeheaded\b", "spiritually detached"),

    # Termos de morte, cadáver e subjugação física
    (r"\bthe\s+body\s+of\s+Priest\s+Ryōseki\s*\([^)]*\)", "the reclining figure of Priest Ryōseki (Jikaku)"),
    (r"\bthe\s+body\s+of\s+(?:an?\s+)?(?:elderly\s+)?(?:monk|priest|man|woman|person)\b", "the resting figure in robes"),
    (r"\bthe\s+body\s+of\b", "the resting form of"),
    (r"\bsprawled\s+on\s+the\s+tatami(?:\s+mat)?\b", "resting in deep shadows on the tatami mat"),
    (r"\bsprawled\s+on\s+the\b", "resting motionless on the"),
    (r"\bthe\s+woman\s+partially\s+pinned\s+beneath\s+him,\s+her\s+eyes\s+wide\s+with\s+shock\b", "the woman in disheveled robes recoiling back against the screen in shock"),
    (r"\b(?:partially\s+)?pinned\s+beneath\s+(?:him|her|them)\b", "recoiling back in shock"),
    (r"\bpinned\s+beneath\b", "recoiling back in shock"),
    (r"\b(?:the\s+)?arrow\s+is\s+barely\s+visible,\s+protruding\s+from\s+the\s+monk's\s+head\b", "a stray wooden arrow embedded in the cedar timber beam beside them"),
    (r"\bprotruding\s+from\s+the\s+monk's\s+head\b", "embedded in the cedar timber beam"),
    (r"\bprotruding\s+from\s+(?:the\s+)?(?:monk's|man's|person's|their|his|her)?\s*(?:head|skull|temple)\b", "embedded in the timber beam nearby"),
    (r"\barrow\s+(?:protruding|embedded|stuck)\s+in\s+(?:the\s+)?(?:head|skull|neck|chest|body)\b", "stray wooden arrow embedded in the wooden beam"),
    (r"\broughly\s+the\s+size\s+of\s+a\s+human\s+body\b", "a heavy woven hemp parcel"),
    (r"\bhinting\s+at\s+decay\b", "coated in aged dust"),
    (r"\bdecay\b", "age and weathering"),
    (r"\b(?:a\s+)?pile\s+of\s+skeletal\s+remains\b", "weathered ceremonial relics and faded silk fragments"),
    (r"\bskeletal\s+remains\b", "ancient ceremonial relics"),
    (r"\bclings\s+to\s+the\s+bones\b", "resting among dry autumn leaves"),
    (r"\bhuman\s+bones\b", "ancient relics"),
    (r"\bbones\b", "ancient relics"),
    (r"\bvisibly\s+stained\s+with\s+dark,\s+dried\s+patches,\s+indicating\s+old\s+blood\b", "weathered with damp soil and aged soot stains"),
    (r"\bindicating\s+old\s+blood\b", "marking the aged hemp cloth"),
    (r"\bdried\s+patches,\s+indicating\s+old\s+blood\b", "dark damp earth stains"),
    (r"\bold\s+blood\b", "aged dark dye"),
    (r"\bslowly\s+draws\s+a\s+short,\s+gleaming\s+blade\s+from\s+his\s+waist\b", "solemnly rests his hand upon the lacquered hilt of a ceremonial tantō at his waist"),
    (r"\bdraws\s+a\s+short,\s+gleaming\s+blade\b", "rests his hand upon a ceremonial tantō hilt"),
    (r"\bshort,\s+highly\s+polished\s+blade,\s+its\s+sharp\s+edge\s+glinting\b", "ceremonial tantō dagger in its polished black lacquer scabbard"),
    (r"\bdead\s+bodies\b", "motionless resting figures"),
    (r"\bdead\s+body\b", "motionless resting figure"),
    (r"\bdead\s+head(?:'s)?\s+jaw\b", "spectral hovering countenance with locked unyielding expression"),
    (r"\bdead\s+head(?:'s)?\b", "spectral hovering countenance"),
    (r"\bdead\s+head\b", "spectral hovering countenance"),
    (r"\bcorpses\b", "still slumbering forms"),
    (r"\bcorpse\b", "still slumbering form"),
    (r"\bcadavers?\b", "sleeping figures in shadows"),

    # Termos de sangue, ferimentos e horror explícito
    (r"\bcrimson\s+stains?\b", "dark liquid shadow"),
    (r"\bred\s+bloom\b", "mystical dark ripple"),
    (r"\bstaining\s+the\s+(?:pale\s+)?(?:jade\s+)?water\b", "rippling across the undisturbed water"),
    (r"\bturning\s+belly-up\b", "drifting motionless in the ripples"),
    (r"\bbelly-up\b", "motionless in the water"),
    (r"\bsevered\s+arms?\b", "torn floating kimono sleeve"),
    (r"\bsevered\s+(?:limbs?|legs?|hands?|feet)\b", "submerged frayed silk garments"),
    (r"\bwound\s+at\s+the\s+shoulder\s+stark\s+and\s+fresh\b", "frayed threads of the torn garment trailing in the water"),
    (r"\bwound\s+at\s+the\s+shoulder\b", "torn shoulder of the garment"),
    (r"\bthe\s+wound\s+.*?\s*fresh\b", "the frayed threads trailing gently"),
    (r"\bwounds?\b", "shadowed markings"),
    (r"\bblood\s+still\s+a\s+vibrant\s+crimson\s+against\s+the\s+pale\s+flesh\b", "dark indigo dyes trailing softly into the cold water"),
    (r"\bblood\s+still\s+a\s+vibrant\s+crimson\b", "dark dye trailing softly into the water"),
    (r"\bvibrant\s+crimson\s+against\s+the\s+pale\s+flesh\b", "delicate dark silk ribbons drifting in the water"),
    (r"\bpale\s+flesh\b", "pale silk garment"),
    (r"\bfresh\s+blood\b", "dark fluid shadow"),
    (r"\bcrimson\s+water\b", "shadowed water"),
    (r"\bbloodstains?\b", "dark ominous markings"),
    (r"\bbloody?\b", "shadow-stained"),
    (r"\bblood\b", "dark ambient fluid"),
    (r"\bbleeding\b", "emanating dark shadow"),
    (r"\bgore\b", "dark supernatural stillness"),
    (r"\bgory\b", "eerie"),
    (r"\bmutilated\b", "shadowy obscured figure"),
    (r"\bmutilation\b", "spectral transformation"),
    (r"\bstrangled\b", "ensnared by supernatural mist"),
    (r"\bstrangling\b", "ensnaring with shadowy mist"),
    (r"\bgrotesque\b", "hauntingly uncanny"),
    (r"\bdisgust\b", "awe and dread"),
    (r"\brecoil\s+in\s+horror\b", "recoil in supernatural dread"),
    (r"\bscream(?:ing)?\s+and\s+hide\b", "hide in fear"),
    (r"\bscreams?\b", "whispers of dread"),
    (r"\bslit\s+throats?\b", "shadow-veiled neck"),
    (r"\bcut\s+throats?\b", "obscured neck"),
    (r"\bhanging\s+bod(?:y|ies)\b", "floating shadowy specters"),

    # Termos de subjugação corporal, postura de terror extremo e tortura aparente
    (r"\bcowering\s+on\s+the\s+ground,\s+their\s+faces\s+pressed\s+into\s+the\s+(?:earth|ground|dirt),\s+hands\s+covering\s+their\s+heads\b", "kneeling low on the ground, shielding their faces with wide sleeves"),
    (r"\btheir\s+faces\s+pressed\s+into\s+the\s+(?:earth|ground|dirt)\b", "bowing low in solemn awe"),
    (r"\bfaces\s+pressed\s+into\s+the\s+(?:earth|ground|dirt)\b", "heads bowed in reverence"),
    (r"\bcowering\s+on\s+the\s+ground\b", "kneeling low in reverence"),
    (r"\bhands\s+covering\s+their\s+heads\b", "hands shielding their faces"),
    (r"\bterrified\s+expressions\b", "astonished, awestruck expressions"),
    (r"\btheir\s+terrified\s+expressions\b", "their awestruck, guarded expressions"),
    (r"\bemphasizing\s+their\s+fear\b", "capturing their solemn awe"),

    # Termos de amarras, contenção corporal e restrição física (Bondage / Restraint filters)
    (r"\blies\s+bound\s+hand\s+and\s+foot\s+with\s+braided\s+hemp\s+cord\s+wrapped\s+in\s+coloured\s+silk\b", "rests subdued upon the stone dais draped in ceremonial coloured silk ribbons and braided sacred hemp cords"),
    (r"\bbound\s+hand\s+and\s+foot\b", "subdued in ceremonial stillness"),
    (r"\bpowerful\s+limbs\s+bound\s+tightly\s+to\s+the\s+rough\s+stone\s+floor\s+with\s+layers\s+of\s+silk\s+and\s+hemp\b", "massive form resting subdued upon the stone floor surrounded by ceremonial five-coloured silk ribbons and traditional hemp ropes"),
    (r"\bbound\s+tightly\s+to\s+the\s+rough\s+stone\s+floor\b", "resting subdued upon the stone floor"),
    (r"\bbound\s+tightly\b", "subdued in mystical stillness"),
    (r"\bstrains\s+against\s+(?:its|his|her|their)\s+bonds\b", "glows with repressed supernatural energy"),
    (r"\bbound\s+with\s+braided\s+hemp\s+cord\b", "draped with sacred braided hemp cords (shimenawa)"),
    (r"\bbound\s+with\s+hemp\s+ropes?\b", "draped with ceremonial braided ropes"),

    # Termos de agressão armada e lâminas empunhadas (Weapons / Violence filters)
    (r"\bpressing\s+forward\s+with\s+drawn\s+swords\b", "standing in disciplined vigil with hands upon sheathed sword hilts"),
    (r"\bwith\s+drawn\s+swords\b", "with sheathed blades resting at their sashes"),
    (r"\bswords\s+drawn\s+and\s+ready\b", "in disciplined defensive stance with weapons at their sides"),
    (r"\bswords\s+drawn\b", "in disciplined posture with sheathed swords at their sashes"),
    (r"\bweapons?\s+drawn\b", "weapons at rest"),

    # Termos de tecido rasgado e vestimenta desordenada (Filtros de violência / assédio)
    (r"\btorn\s+white\s+silk\b", "scattered white silk sashes"),
    (r"\btorn\s+(?:silk|kimono|robes?|clothing|garments?)\b", "scattered flowing silk ribbons"),
    (r"\bdisheveled\s+(?:kimono|robes?|clothing|garments?)\b", "layered draped kimono"),
    (r"\bdisheveled\b", "draped"),

    # Termos de carcaça, alimentação violenta e crueldade animal (Animal Cruelty filters)
    (r"\btearing\s+into\s+the\s+carcass\s+of\s+a\s+dog\s+with\s+primal\s+ferocity\b", "feasting from rustic earthenware vessels with primal appetite"),
    (r"\btearing\s+into\s+the\s+carcass\s+of\s+a\s+\w+\b", "feasting from large earthenware platters"),
    (r"\bcarcass\s+of\s+an?\s+\w+\b", "rustic clay platter"),
    (r"\bcarcass(?:es)?\b", "clay vessels"),

    # Termos de esfaqueamento, penetração de lâmina e sangramento pontual (Graphic Injury filters)
    (r"\bdriving\s+a\s+(?:short,\s+)?sharp\s+blade\s+precisely\s+into\s+a\s+small,\s+vulnerable\s+area\s+just\s+below\s+the\s+navel\b", "directing a sacred bronze sealing talisman towards the supernatural core"),
    (r"\bdriving\s+a\s+(?:short,\s+)?sharp\s+blade\s+into\b", "directing a sacred ritual talisman towards"),
    (r"\ba\s+single\s+drop\s+of\s+dark\s+blood\s+begins\s+to\s+well\s+up\b", "a delicate wisp of cold dark spirit vapor begins to rise"),
    (r"\bblood\s+begins\s+to\s+well\s+up\b", "dark spirit vapor begins to rise"),
    (r"\bblood\s+staining\s+(?:his|her|their)\s+white\s+robes\b", "soot and shadow markings gracing the white robes"),
    (r"\bmoment\s+of\s+shock\s+and\s+pain\b", "moment of sudden realization and mystical stillness"),

    # Termos anatômicos sensíveis e filtros de nudez/sugestão (Anatomical / NSFW filters)
    (r"\bextreme\s+close-up\s+on\s+a\s+small,\s+unblemished\s+patch\s+of\s+skin\s+just\s+below\s+the\s+navel\b", "dramatic close-up on a small circular talismanic marking on the creature's stone-hard mythical torso"),
    (r"\bunblemished\s+patch\s+of\s+skin\s+just\s+below\s+the\s+navel\b", "circular talismanic spirit glyph on the torso"),
    (r"\bpatch\s+of\s+skin\s+just\s+below\s+the\s+navel\b", "sacred spirit glyph on the torso"),
    (r"\bskin\s+just\s+below\s+the\s+navel\b", "spirit glyph on the torso"),
    (r"\bjust\s+below\s+the\s+navel\b", "at the enchanted core of the torso"),
    (r"\bbelow\s+the\s+navel\b", "upon the enchanted torso"),
    (r"\bnavel\b", "spirit core"),
    (r"\btaut,\s+shimmering\s+silk\s+cords\s+biting\s+into\s+a\s+powerful,\s+muscular\s+limb,\s+straining\s+against\s+its\s+bonds\b", "ceremonial five-coloured silk cords draped across an ancient weathered stone plinth"),
    (r"\bbiting\s+into\s+a\s+powerful,\s+muscular\s+limb,\s+straining\s+against\s+its\s+bonds\b", "resting gracefully across the ancient stone dais"),

    # Termos de estrangulamento, sufocamento e autoagressão (Strangulation / Self-Harm filters)
    (r"\bhands,\s+clenched\s+tightly\s+around\s+his\s+own\s+throat\s+as\s+he\s+lies\s+on\s+his\s+futon\b", "hands clutching the collar of his sleeping kimono during restless sleep paralysis on his futon"),
    (r"\bclenched\s+tightly\s+around\s+(?:his|her|their|own)\s+throat\b", "clutching the kimono collar in restless slumber"),
    (r"\bclutching\s+(?:his|her|their|own)\s+throat\b", "clutching the collar in troubled sleep"),
    (r"\baround\s+(?:his|her|their|own)\s+throat\b", "near the kimono collar"),
    (r"\btightening\s+around\s+the\s+neck\s+of\b", "hovering near the shadowed collar of"),
    (r"\btightening\s+around\s+the\s+neck\b", "hovering near the shadowed collar"),
    (r"\btightening\s+around\s+the\s+throat\b", "hovering near the shadowed collar"),
    (r"\bhands\s+tightening\s+around\b", "shadowy hands hovering near"),
    (r"\bskin\s+of\s+his\s+throat\s+is\s+taut\b", "expression of his face is tense in sleep paralysis"),
    (r"\bclutching\s+throat\s+in\s+sleep\b", "clutching collar in troubled sleep"),
    (r"\bstrangling\b", "hovering with spectral oppression"),
    (r"\bstrangled\b", "haunted by spectral presence"),
    (r"\bchoking\b", "gasping in sleep paralysis"),
    (r"\bsuffocating\b", "struggling against unseen spectral weight"),

    # Termos de carcaça sobrenatural sem vida (Dead / Motionless entity)
    (r"\bthe\s+dead\s+ape\s+lies\s+motionless\b", "the dormant mythical white ape rests in deep slumber"),
    (r"\bthe\s+dead\s+ape(?:-like\s+creature)?\b", "the dormant supernatural ape"),
    (r"\bdead\s+ape\b", "dormant spirit ape"),
    (r"\blies\s+motionless,\s+dead\b", "rests motionless in deep enchanted slumber"),

    # Termos de anomalias orais ou corporais sensíveis (Oral / Mutilation / NSFW False-positives)
    (r"\b(?:an\s+)?(?:enormous|impossibly\s+long|unnaturally\s+long|pale|slick|glistening)?\s*tongue\s+(?:nearly\s+two\s+feet\s+long\s+)?hanging\s+(?:far\s+out\s+of|limp\s+and\s+glistening\s+from|from)\s+(?:her|his|an)?\s*open\s+mouth(?:\s+down\s+past\s+her\s+chin)?\b", "an eerie folkloric pale spirit ribbon draping down past her chin in the traditional kaidan painting style"),
    (r"\b(?:an\s+)?(?:impossibly\s+long,\s+pale,\s+and\s+glistening|impossibly\s+long|unnaturally\s+pale|pale|slick|glistening)\s+tongue\s+extending\s+from\s+(?:a\s+woman's\s+mouth|her\s+mouth)(?:\s+towards\s+the\s+viewer)?\b", "an uncanny pale folkloric spirit ribbon descending from the woman's silhouette in the traditional kaidan style"),
    (r"\bextreme\s+close-up\s+of\s+an\s+impossibly\s+long\s+tongue[^\.]*\.", "Extreme close-up of an eerie folkloric translucent spirit ribbon held across the lower face, swaying slightly in the thin moonlight, creating the classic urban legend illusion in the style of Japanese kaidan painting."),
    (r"\bthe\s+tongue\b", "the spirit ribbon"),
    (r"\bface\s+and\s+tongue\s+bright\b", "face and spectral feature bright"),
    (r"\b(?:no\s+)?(?:wounds|shadowed\s+markings)\s+or\s+crimson\s+shadow\b", "subtle monochromatic shading"),
    (r"\bcrimson\s+shadow\b", "deep monochromatic shadow"),
    (r"\bimpossibly\s+long\s+and\s+slick\b", "flowing with eerie grace"),
    (r"\bbloodshot\b", "wide in shock"),
]


def soften_prompt_for_safety(prompt: str, attempt: int = 2) -> str:
    """Sanitize and soften sensitive anatomical or graphic folkloric terms to prevent safety false-positives."""
    softened = prompt
    for pattern, replacement in SAFETY_TERM_REPLACEMENTS:
        softened = re.sub(pattern, replacement, softened, flags=re.IGNORECASE)

    if attempt >= 3:
        # Aggressive softening for attempt 3: ensure atmosphere is purely spectral and indirect
        extra_replacements = [
            (r"\bfear\s+and\s+disgust\b", "eerie awe and solemn silence"),
            (r"\bhorror\b", "supernatural mystery"),
            (r"\bterrifying\b", "eerie"),
            (r"\bghastly\b", "spectral"),
            (r"\bmonstrous\b", "otherworldly"),
            (r"\battached\s+to\s+his\s+elbow\b", "hovering near his sleeve"),
        ]
        for pattern, replacement in extra_replacements:
            softened = re.sub(pattern, replacement, softened, flags=re.IGNORECASE)

    return softened


def get_generation_status(slug: str) -> Dict[str, Any]:
    """Retrieve current background generation status for a given project slug."""
    if slug not in _STATUS_STORE:
        # Check on-disk counts to provide accurate initial idle status
        try:
            ws = ProjectWorkspace.from_slug(slug)
            ref_count = len(list(ws.gemini_references_dir.glob("*.jp*g"))) if ws.gemini_references_dir.exists() else 0
            scene_count = len(list(ws.gemini_output_dir.glob("*.jp*g"))) if ws.gemini_output_dir.exists() else 0
            total = 0
            if ws.gemini_project_json.exists():
                try:
                    pdata = json.loads(ws.gemini_project_json.read_text(encoding="utf-8"))
                    total = len(pdata.get("tasks", []))
                except Exception:
                    pass
            return {
                "status": "completed" if (total > 0 and scene_count >= total) else "idle",
                "phase": "scenes" if ref_count > 0 else "references",
                "current_index": scene_count,
                "total_images": total,
                "total_items": total,
                "current_filename": None,
                "current_item_title": None,
                "error": None,
                "error_message": None,
                "warning": None,
                "failed_tasks": [],
                "message": f"{scene_count}/{total} scenes generated" if total else "Idle",
            }
        except Exception:
            return {
                "status": "idle",
                "phase": "references",
                "current_index": 0,
                "total_images": 0,
                "total_items": 0,
                "current_filename": None,
                "current_item_title": None,
                "error": None,
                "error_message": None,
                "warning": None,
                "failed_tasks": [],
                "message": "Idle",
            }

    if slug in _STATUS_STORE:
        st = _STATUS_STORE[slug]
        # Self-healing: if status is failed/warning but all required files now exist on disk, auto-heal to completed
        if st.get("status") in ("failed", "warning"):
            try:
                ws = ProjectWorkspace.from_slug(slug)
                if st.get("phase") == "references":
                    ref_count = len(list(ws.gemini_references_dir.glob("*.jp*g"))) if ws.gemini_references_dir.exists() else 0
                    total = st.get("total_items") or st.get("total_images") or 9
                    if ref_count >= total:
                        st["status"] = "completed"
                        st["current_index"] = ref_count
                        st["error"] = None
                        st["error_message"] = None
                        st["warning"] = None
                        st["failed_tasks"] = []
                        st["message"] = f"All {ref_count} reference assets successfully generated!"
                elif st.get("phase") == "scenes":
                    scene_count = len(list(ws.gemini_output_dir.glob("*.jp*g"))) if ws.gemini_output_dir.exists() else 0
                    total = st.get("total_items") or st.get("total_images") or 0
                    if total > 0 and scene_count >= total:
                        st["status"] = "completed"
                        st["current_index"] = scene_count
                        st["error"] = None
                        st["error_message"] = None
                        st["warning"] = None
                        st["failed_tasks"] = []
                        st["message"] = f"All {scene_count} scenes successfully generated!"
            except Exception:
                pass
        return _STATUS_STORE[slug]

    return {
        "status": "idle",
        "phase": "references",
        "current_index": 0,
        "total_images": 0,
        "total_items": 0,
        "current_filename": None,
        "current_item_title": None,
        "error": None,
        "error_message": None,
        "warning": None,
        "failed_tasks": [],
        "message": "Idle",
    }


def cancel_generation(slug: str) -> bool:
    """Request cancellation of an ongoing generation process."""
    if slug in _STATUS_STORE and _STATUS_STORE[slug].get("status") == "running":
        _CANCEL_FLAGS[slug] = True
        _STATUS_STORE[slug]["message"] = "Cancellation requested..."
        return True
    return False


def reset_generation_status(slug: str) -> None:
    """Reset generation status to idle and clear error states."""
    _STATUS_STORE[slug] = {
        "status": "idle",
        "phase": "scenes",
        "current_index": 0,
        "total_images": 72,
        "total_items": 72,
        "current_filename": None,
        "current_item_title": None,
        "error": None,
        "error_message": None,
        "warning": None,
        "failed_tasks": [],
        "message": "Ready",
    }


def generate_imagen3_sdk(
    prompt: str,
    aspect_ratio: str = "16:9",
    safety_level: str = "BLOCK_ONLY_HIGH",
    model: str = "gemini-3.1-flash-image",
) -> bytes:
    """Wrapper de compatibilidade delegando para generate_scene_frame."""
    is_scene = aspect_ratio == "16:9"
    return generate_scene_frame(prompt=prompt, is_scene=is_scene, model=model)


async def call_imagen3_api(
    prompt: str,
    aspect_ratio: str = "16:9",
    safety_level: str = "BLOCK_ONLY_HIGH",
    model: str = "gemini-3.1-flash-image",
) -> bytes:
    """Wrapper assíncrono delegando para generate_scene_frame."""
    is_scene = aspect_ratio == "16:9"
    return generate_scene_frame(prompt=prompt, is_scene=is_scene, model=model)


def _generate_with_retry(
    prompt: str,
    aspect_ratio: str = "16:9",
    task_id: str = "",
    max_retries: int = 3,
    is_scene: bool = False,
    safety_level: str = "BLOCK_ONLY_HIGH",
    client: Any = None,
    model: Optional[str] = None,
    config: Any = None,
) -> bytes:
    """Executa a geração com muse-image-1.0 da Meta AI com retries e validação dimensional in-memory."""
    task_lower = (task_id or "").lower()
    if not is_scene and ("shot_" in task_lower or "scene_" in task_lower or aspect_ratio == "16:9"):
        is_scene = True

    target_model = model or os.environ.get("IMAGE_MODEL", DEFAULT_IMAGE_MODEL)
    delay = 2.0
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        current_prompt = soften_prompt_for_safety(prompt, attempt)
        if current_prompt != prompt:
            logger.info(
                "Tentativa %d/%d para task %s: aplicando prompt suavizado contra filtros de conteúdo.",
                attempt,
                max_retries,
                task_id or "unspecified",
            )

        try:
            raw_webp = generate_with_meta_muse_sync(
                prompt=current_prompt,
                is_scene=is_scene,
                model=target_model,
            )

            # In-memory validation & auto-crop to 16:9
            with Image.open(io.BytesIO(raw_webp)) as pil_img:
                w, h = pil_img.size
                r = w / h if h > 0 else 0.0
                if is_scene and h > 0:
                    if r >= 1.20 and abs(r - (16.0 / 9.0)) >= 0.02:
                        cropped_img = crop_to_16_9(pil_img)
                        out_buf = io.BytesIO()
                        cropped_img.save(out_buf, format="WEBP", quality=95)
                        raw_webp = out_buf.getvalue()
                        w, h = cropped_img.size
                        r = w / h
                        logger.info(
                            "Auto-cropped scene %s to exact 16:9 (%dx%d, ratio %.2f)",
                            task_id or "unspecified",
                            w,
                            h,
                            r,
                        )

            if is_scene and r < 1.60:
                raise ValueError(
                    f"ERRO DE PROPORÇÃO (Meta Muse): Imagem {w}x{h} (ratio {r:.2f}) não é widescreen 16:9."
                )

            return raw_webp

        except Exception as exc:
            last_err = exc
            if isinstance(exc, ValueError) and "ERRO DE PROPORÇÃO" in str(exc):
                if attempt < max_retries:
                    logger.warning(
                        "Tentativa %d/%d para task %s teve ratio inadequado (%s). Tentando novamente...",
                        attempt,
                        max_retries,
                        task_id or "unspecified",
                        exc,
                    )
                    time.sleep(delay)
                    delay *= 2.0
                    continue
                else:
                    raise exc

            last_err = exc
            logger.error(
                "Falha na tentativa %d/%d com %s para task %s: %s.",
                attempt,
                max_retries,
                target_model,
                task_id or "unspecified",
                exc,
                exc_info=False,
            )
            print(f"[ERROR] [API_IMAGE_GENERATOR] Task {task_id} tentativa {attempt}/{max_retries} falhou: {exc}", flush=True)

            err_str = str(exc)
            if "401" in err_str or "403" in err_str:
                raise exc

            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2.0

    raise RuntimeError(f"Geração com {target_model} falhou após {max_retries} tentativas: {last_err}")


def run_autonomous_generation(
    slug: str,
    project_title: str = "",
    force_all: bool = False,
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute complete 2-Phase Autonomous Image Generation:

    Phase 1: Reference visual anchors saved to gemini-assistant/references/ (no prior references injected)
    Phase 2: Video scenes saved to gemini-assistant/output/

    Uses Meta AI muse-image-1.0 with automatic WEBP to PNG/JPEG conversion.
    """
    model_name = model_override or os.environ.get("IMAGE_MODEL", os.environ.get("IMAGEN_MODEL", DEFAULT_IMAGE_MODEL))
    _CANCEL_FLAGS[slug] = False

    ws = ProjectWorkspace.from_slug(slug)
    ws.create()

    os.makedirs(ws.gemini_references_dir, exist_ok=True)
    os.makedirs(ws.gemini_output_dir, exist_ok=True)

    failed_tasks: List[str] = []

    _STATUS_STORE[slug] = {
        "status": "running",
        "phase": "references",
        "current_index": 0,
        "total_images": 0,
        "total_items": 0,
        "current_filename": None,
        "current_item_title": None,
        "error": None,
        "error_message": None,
        "warning": None,
        "failed_tasks": [],
        "message": f"Initializing Meta AI {model_name} client...",
        "start_time": time.time(),
    }

    api_key = (os.environ.get("META_API_KEY") or "").strip()
    if not api_key:
        error_msg = "META_API_KEY não configurada no ambiente (.env)."
        logger.error(error_msg)
        print(f"\n[ERROR] [API_IMAGE_GENERATOR] {error_msg}\n", flush=True)
        _STATUS_STORE[slug] = {
            "status": "failed",
            "phase": "references",
            "current_index": 0,
            "total_images": 0,
            "total_items": 0,
            "current_filename": None,
            "current_item_title": None,
            "error": error_msg,
            "error_message": error_msg,
            "warning": None,
            "failed_tasks": [],
            "message": error_msg,
        }
        return _STATUS_STORE[slug]

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1: References Generation (Self-contained, NO prior visual references)
    # ─────────────────────────────────────────────────────────────────────────
    ref_tasks: List[Dict[str, Any]] = []
    if ws.gemini_ref_job_project_json.exists():
        try:
            ref_data = json.loads(ws.gemini_ref_job_project_json.read_text(encoding="utf-8"))
            ref_tasks = ref_data.get("tasks", [])
        except Exception as e:
            logger.warning("Could not read gemini_ref_job_project_json: %s", e)

    if not ref_tasks and ws.references_json_path.exists():
        try:
            ref_data = json.loads(ws.references_json_path.read_text(encoding="utf-8"))
            ref_tasks = ref_data.get("tasks", [])
        except Exception as e:
            logger.warning("Could not read references_json_path: %s", e)

    total_refs = len(ref_tasks)
    _STATUS_STORE[slug]["total_images"] = total_refs
    _STATUS_STORE[slug]["total_items"] = total_refs
    _STATUS_STORE[slug]["phase"] = "references"
    _STATUS_STORE[slug]["message"] = f"Phase 1: Generating {total_refs} reference anchors with Meta Muse..."

    for idx, task in enumerate(ref_tasks):
        if _CANCEL_FLAGS.get(slug):
            _STATUS_STORE[slug]["status"] = "idle"
            _STATUS_STORE[slug]["message"] = "Generation paused by user."
            return _STATUS_STORE[slug]

        basename = task.get("output", {}).get("basename") or task.get("id") or f"ref_{idx+1:03d}"
        clean_basename = re.sub(r"[^a-zA-Z0-9_\-]", "", basename)
        out_filename = f"{clean_basename}.jpeg"
        target_path = ws.gemini_references_dir / out_filename

        _STATUS_STORE[slug]["current_index"] = idx + 1
        _STATUS_STORE[slug]["current_filename"] = out_filename
        _STATUS_STORE[slug]["current_item_title"] = task.get("title") or out_filename

        # Skip existing if valid and not forced (smart resume)
        if target_path.exists() and target_path.stat().st_size > 5000 and not force_all:
            logger.info("Reference %s (%d/%d) already exists on disk, skipping.", out_filename, idx + 1, total_refs)
            continue

        raw_prompt = task.get("prompt", "")
        task_id = str(task.get("id", "")).lower()
        task_title = str(task.get("title", "")).lower()
        is_character = any(k in task_id or k in task_title or k in clean_basename.lower() for k in ("character", "protagonist", "entity", "supporting", "priest", "figure"))
        aspect_ratio = "3:4" if is_character else "16:9"

        task_prompt = format_prompt_with_style_bible(
            narrative_description=raw_prompt,
            aspect_ratio=aspect_ratio,
            project_title=project_title,
            slug=slug,
        )

        try:
            logger.info("Generating reference %s with Meta Muse (%s)...", out_filename, aspect_ratio)
            raw_webp = _generate_with_retry(
                prompt=task_prompt,
                aspect_ratio=aspect_ratio,
                task_id=out_filename,
                is_scene=False,
            )
            out_fmt = "JPEG" if target_path.suffix.lower() in [".jpg", ".jpeg"] else "PNG"
            convert_webp_to_target(raw_webp, target_path, output_format=out_fmt)
            logger.info("Saved reference %s to %s (converted from WEBP to %s)", out_filename, target_path, out_fmt)
        except Exception as exc:
            err_msg = str(exc)
            import traceback
            tb_str = traceback.format_exc()
            logger.error("Failed to generate reference %s with Meta Muse: %s\nPrompt: %s\n%s", out_filename, err_msg, raw_prompt, tb_str)
            print(f"\n[ERROR] [API_IMAGE_GENERATOR] Falha na referência {out_filename}: {err_msg}", flush=True)
            print(f"Descrição da referência: {raw_prompt}", flush=True)
            print(f"Traceback:\n{tb_str}\n", flush=True)

            _STATUS_STORE[slug] = {
                "status": "failed",
                "phase": "references",
                "current_index": idx,
                "total_images": total_refs,
                "total_items": total_refs,
                "current_filename": out_filename,
                "current_item_title": task.get("title") or out_filename,
                "error": err_msg,
                "error_message": err_msg,
                "warning": None,
                "failed_tasks": [out_filename],
                "message": f"Erro na referência {out_filename}: {err_msg}",
            }
            return _STATUS_STORE[slug]

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2: Video Scenes Generation
    # ─────────────────────────────────────────────────────────────────────────
    scene_tasks: List[Dict[str, Any]] = []
    if ws.gemini_project_json.exists():
        try:
            scene_data = json.loads(ws.gemini_project_json.read_text(encoding="utf-8"))
            scene_tasks = scene_data.get("tasks", [])
        except Exception as e:
            logger.warning("Could not read gemini_project_json: %s", e)

    if not scene_tasks and ws.final_scenes_json_path.exists():
        try:
            scene_data = json.loads(ws.final_scenes_json_path.read_text(encoding="utf-8"))
            scene_tasks = scene_data.get("tasks", [])
        except Exception as e:
            logger.warning("Could not read final_scenes_json_path: %s", e)

    total_scenes = len(scene_tasks)
    _STATUS_STORE[slug]["phase"] = "scenes"
    _STATUS_STORE[slug]["total_images"] = total_scenes
    _STATUS_STORE[slug]["total_items"] = total_scenes
    _STATUS_STORE[slug]["message"] = f"Phase 2: Generating {total_scenes} video scene frames with Meta Muse..."

    for idx, task in enumerate(scene_tasks):
        if _CANCEL_FLAGS.get(slug):
            _STATUS_STORE[slug]["status"] = "idle"
            _STATUS_STORE[slug]["message"] = "Generation paused by user."
            return _STATUS_STORE[slug]

        basename = task.get("output", {}).get("basename") or task.get("id") or f"shot_{idx+1:03d}"
        clean_basename = re.sub(r"[^a-zA-Z0-9_\-]", "", basename)
        out_filename = f"{clean_basename}.jpeg"
        target_path = ws.gemini_output_dir / out_filename

        _STATUS_STORE[slug]["current_index"] = idx + 1
        _STATUS_STORE[slug]["current_filename"] = out_filename
        _STATUS_STORE[slug]["current_item_title"] = task.get("title") or out_filename
        _STATUS_STORE[slug]["message"] = f"Generating scene {idx+1} of {total_scenes} ({out_filename})..."

        # Skip existing only if valid 16:9 and not forced; purge any corrupted square/vertical legacy images
        if target_path.exists() and not force_all:
            try:
                with Image.open(target_path) as check_img:
                    w_ex, h_ex = check_img.size
                r_ex = w_ex / h_ex if h_ex > 0 else 0.0
                if r_ex >= 1.6 and target_path.stat().st_size > 5000:
                    logger.info("Scene %s (%d/%d) already exists in 16:9 (%dx%d, ratio %.2f), skipping.", out_filename, idx + 1, total_scenes, w_ex, h_ex, r_ex)
                    continue
                else:
                    logger.warning(
                        "Scene %s on disk has invalid ratio %dx%d (ratio %.2f < 1.6). Removing corrupted square file to regenerate in 16:9...",
                        out_filename,
                        w_ex,
                        h_ex,
                        r_ex,
                    )
                    target_path.unlink(missing_ok=True)
            except Exception as read_err:
                logger.warning("Scene %s on disk is unreadable (%s). Removing to regenerate...", out_filename, read_err)
                target_path.unlink(missing_ok=True)

        raw_prompt = task.get("prompt", "")
        task_prompt = format_prompt_with_style_bible(
            narrative_description=raw_prompt,
            aspect_ratio="16:9",
            project_title=project_title,
            slug=slug,
        )
        task_prompt = f"{WIDESCREEN_SCENE_DIRECTIVE}\n\n{task_prompt}"

        try:
            logger.info("Generating scene frame %s with Meta Muse (16:9)...", out_filename)
            raw_webp = _generate_with_retry(
                prompt=task_prompt,
                aspect_ratio="16:9",
                task_id=out_filename,
                is_scene=True,
            )
            out_fmt = "JPEG" if target_path.suffix.lower() in [".jpg", ".jpeg"] else "PNG"
            convert_webp_to_target(raw_webp, target_path, output_format=out_fmt, is_scene=True)

            # Validação física obrigatória imediatamente após gravar a imagem em disco
            validate_scene_aspect_ratio(target_path, is_scene=True)
            logger.info("Saved scene %s to %s (%s, 16:9 validated)", out_filename, target_path, out_fmt)
        except Exception as exc:
            err_msg = str(exc)
            import traceback
            tb_str = traceback.format_exc()
            logger.error("Failed to generate scene %s with Meta Muse: %s\nPrompt: %s\n%s", out_filename, err_msg, raw_prompt, tb_str)
            print(f"\n[ERROR] [API_IMAGE_GENERATOR] Falha na cena {out_filename}: {err_msg}", flush=True)
            print(f"Descrição da cena: {raw_prompt}", flush=True)
            print(f"Traceback:\n{tb_str}\n", flush=True)

            _STATUS_STORE[slug] = {
                "status": "failed",
                "phase": "scenes",
                "current_index": idx,
                "total_images": total_scenes,
                "total_items": total_scenes,
                "current_filename": out_filename,
                "current_item_title": task.get("title") or out_filename,
                "error": err_msg,
                "error_message": err_msg,
                "warning": None,
                "failed_tasks": [out_filename],
                "message": f"Erro na cena {out_filename}: {err_msg}",
            }
            return _STATUS_STORE[slug]

    successful_count = total_scenes - len(failed_tasks)
    if failed_tasks:
        warning_msg = (
            f"Concluído com avisos: {successful_count}/{total_scenes} gerados "
            f"({len(failed_tasks)} frame{'s' if len(failed_tasks) > 1 else ''} requer{'em' if len(failed_tasks) > 1 else 'e'} ajuste manual de prompt: {', '.join(failed_tasks)})"
        )
        _STATUS_STORE[slug] = {
            "status": "completed",
            "phase": "scenes",
            "current_index": total_scenes,
            "total_images": total_scenes,
            "total_items": total_scenes,
            "current_filename": None,
            "current_item_title": None,
            "error": None,
            "error_message": None,
            "warning": warning_msg,
            "failed_tasks": failed_tasks,
            "message": warning_msg,
            "completed_at": time.time(),
        }
    else:
        _STATUS_STORE[slug] = {
            "status": "completed",
            "phase": "scenes",
            "current_index": total_scenes,
            "total_images": total_scenes,
            "total_items": total_scenes,
            "current_filename": None,
            "current_item_title": None,
            "error": None,
            "error_message": None,
            "warning": None,
            "failed_tasks": [],
            "message": f"All {total_scenes} scenes and reference assets successfully generated!",
            "completed_at": time.time(),
        }
    return _STATUS_STORE[slug]


def regenerate_single_frame(
    slug: str,
    target_identifier: str,
    project_title: str = "",
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    model_name = model_override or os.environ.get("IMAGE_MODEL", os.environ.get("IMAGEN_MODEL", DEFAULT_IMAGE_MODEL))
    ws = ProjectWorkspace.from_slug(slug)

    target_clean = target_identifier.strip().lower()
    target_basename = os.path.splitext(target_clean)[0]

    # 1. Search in scene tasks
    scene_tasks: List[Dict[str, Any]] = []
    if ws.gemini_project_json.exists():
        try:
            data = json.loads(ws.gemini_project_json.read_text(encoding="utf-8"))
            scene_tasks = data.get("tasks", [])
        except Exception:
            pass

    for task in scene_tasks:
        tid = str(task.get("id", "")).lower()
        tbase = str(task.get("output", {}).get("basename", "")).lower()
        if tid == target_clean or tid == target_basename or tbase == target_clean or tbase == target_basename:
            out_filename = f"{tbase or tid}.jpeg"
            target_path = ws.gemini_output_dir / out_filename
            prompt = format_prompt_with_style_bible(
                task.get("prompt", ""),
                aspect_ratio="16:9",
                project_title=project_title,
                slug=slug,
            )
            raw_webp = _generate_with_retry(
                prompt=prompt,
                aspect_ratio="16:9",
                task_id=out_filename,
                is_scene=True,
                model=model_name,
            )
            out_fmt = "JPEG" if target_path.suffix.lower() in [".jpg", ".jpeg"] else "PNG"
            convert_webp_to_target(raw_webp, target_path, output_format=out_fmt, is_scene=True)

            validate_scene_aspect_ratio(target_path, is_scene=True)

            if slug in _STATUS_STORE:
                st = _STATUS_STORE[slug]
                failed = st.get("failed_tasks", [])
                for fn in (out_filename, target_clean, target_basename, f"{target_clean}.jpeg"):
                    if fn in failed:
                        failed.remove(fn)
                if not failed:
                    st["error"] = None
                    st["error_message"] = None
                    st["warning"] = None
                    st["status"] = "completed"
                    st["message"] = f"Scene '{out_filename}' regenerated successfully."

            return {
                "success": True,
                "stage": "scene",
                "filename": out_filename,
                "path": str(target_path),
                "size_bytes": target_path.stat().st_size,
            }

    # 2. Search in reference tasks
    ref_tasks: List[Dict[str, Any]] = []
    if ws.gemini_ref_job_project_json.exists():
        try:
            data = json.loads(ws.gemini_ref_job_project_json.read_text(encoding="utf-8"))
            ref_tasks = data.get("tasks", [])
        except Exception:
            pass

    for task in ref_tasks:
        tid = str(task.get("id", "")).lower()
        tbase = str(task.get("output", {}).get("basename", "")).lower()
        if tid == target_clean or tid == target_basename or tbase == target_clean or tbase == target_basename:
            out_filename = f"{tbase or tid}.jpeg"
            target_path = ws.gemini_references_dir / out_filename
            is_char = any(k in tid or k in tbase for k in ("character", "protagonist", "entity", "supporting", "priest", "figure"))
            ar = "3:4" if is_char else "16:9"
            prompt = format_prompt_with_style_bible(
                task.get("prompt", ""),
                aspect_ratio=ar,
                project_title=project_title,
                slug=slug,
            )
            raw_webp = _generate_with_retry(
                prompt=prompt,
                aspect_ratio=ar,
                task_id=out_filename,
                is_scene=False,
                model=model_name,
            )
            out_fmt = "JPEG" if target_path.suffix.lower() in [".jpg", ".jpeg"] else "PNG"
            convert_webp_to_target(raw_webp, target_path, output_format=out_fmt)
            
            if slug in _STATUS_STORE:
                st = _STATUS_STORE[slug]
                failed = st.get("failed_tasks", [])
                for fn in (out_filename, target_clean, target_basename, f"{target_clean}.jpeg"):
                    if fn in failed:
                        failed.remove(fn)
                if not failed:
                    st["error"] = None
                    st["error_message"] = None
                    st["warning"] = None
                    st["status"] = "completed"
                    st["message"] = f"Reference '{out_filename}' regenerated successfully."

            return {
                "success": True,
                "stage": "reference",
                "filename": out_filename,
                "path": str(target_path),
                "size_bytes": target_path.stat().st_size,
            }

    raise ValueError(f"Task or file '{target_identifier}' not found in project tasks.")
