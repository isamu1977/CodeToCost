"""Automated YouTube Data API v3 uploader for Red Sun Japan master videos and shorts."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.http import MediaFileUpload
from sqlalchemy.orm import Session

from database import SessionLocal, VideoProject, VideoStatus
from redsun.services.youtube_auth import get_youtube_client
from redsun.workspace import ProjectWorkspace, RUNTIME_ROOT

logger = logging.getLogger("redsun.youtube_uploader")


def _resolve_project(slug_input: str, db: Session) -> Tuple[Optional[VideoProject], str]:
    """Resolve a slug or ID to canonical VideoProject and canonical slug."""
    slug_str = slug_input.strip()

    # Direct match on slug
    proj = db.query(VideoProject).filter(VideoProject.slug == slug_str).first()
    if proj:
        return proj, proj.slug

    # Integer ID match
    if slug_str.isdigit():
        proj = db.query(VideoProject).filter(VideoProject.id == int(slug_str)).first()
        if proj:
            return proj, proj.slug or f"project-{proj.id}"

    # Fuzzy match by keywords
    all_projects = db.query(VideoProject).all()
    keywords = [w.lower() for w in slug_str.replace("-", " ").replace("_", " ").split() if len(w) > 3]

    best_proj = None
    best_score = 0
    for p in all_projects:
        cand_slug = (p.slug or "").lower()
        cand_title = (p.title or "").lower()
        score = sum(1 for kw in keywords if kw in cand_slug or kw in cand_title)
        if score > best_score:
            best_score = score
            best_proj = p

    if best_proj and best_score > 0:
        return best_proj, best_proj.slug

    return None, slug_str


def _find_master_video(ws: ProjectWorkspace, slug: str) -> Optional[Path]:
    """Locate master video across known naming conventions."""
    candidates = [
        ws.root / "video" / f"{slug}_master.mp4",
        ws.root / "video" / "output_master.mp4",
        ws.root / "render" / "output_master.mp4",
        ws.root / f"{slug}_master.mp4",
        ws.root / "output_master.mp4",
    ]

    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 1024 * 1024:
            return cand

    # Pattern search in video_dir
    if ws.video_dir.exists():
        for f in ws.video_dir.glob("*_master.mp4"):
            if f.is_file() and f.stat().st_size > 1024 * 1024:
                return f

    return None


def _find_thumbnail(ws: ProjectWorkspace, slug: str) -> Optional[Path]:
    """Locate custom 1280x720 thumbnail PNG."""
    dist_dir = getattr(ws, "dist_dir", ws.root / "distribution")
    candidates = [
        dist_dir / "thumbnail.png",
        ws.root / "distribution" / "thumbnail.png",
        ws.root / "thumbnail.png",
        dist_dir / f"{slug}_thumbnail.png",
    ]
    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 1000:
            return cand
    return None


def _load_distribution_metadata(ws: ProjectWorkspace) -> Dict[str, Any]:
    """Load social_distribution_kit.json or fallback structure."""
    dist_dir = getattr(ws, "dist_dir", ws.root / "distribution")
    candidates = [
        dist_dir / "social_distribution_kit.json",
        ws.root / "distribution" / "social_distribution_kit.json",
        dist_dir / "reels_tiktok_kit.json",
        ws.root / "distribution" / "reels_tiktok_kit.json",
        dist_dir / "post_kit.json",
        ws.root / "distribution" / "post_kit.json",
    ]

    for kit_path in candidates:
        if kit_path.exists():
            try:
                data = json.loads(kit_path.read_text(encoding="utf-8"))
                if data:
                    return data
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", kit_path.name, exc)

    return {}


def upload_project_master_video(
    slug: str,
    title_index: int = 0,
    privacy_status: str = "private",
    category_id: str = "27",
) -> Dict[str, Any]:
    """Upload master 4K/HD video to YouTube as a private or unlisted draft.
    
    MANDATORY SAFETY PROTOCOL:
    Public status is strictly forbidden during automated dispatch.
    Only 'private' or 'unlisted' are accepted.
    """
    db = SessionLocal()
    try:
        project, canonical_slug = _resolve_project(slug, db)
        ws = ProjectWorkspace.from_slug(canonical_slug, project_id=project.id if project else None)

        video_path = _find_master_video(ws, canonical_slug)
        if not video_path:
            raise FileNotFoundError(
                f"Master video file not found in {ws.root}/video/. "
                "Ensure Remotion rendering (Node 4) is complete before uploading."
            )

        thumbnail_path = _find_thumbnail(ws, canonical_slug)
        kit_data = _load_distribution_metadata(ws)
        youtube_kit = kit_data.get("youtube", {})

        # 1. Resolve Title
        title_options = youtube_kit.get("title_options", [])
        if title_options and 0 <= title_index < len(title_options):
            final_title = title_options[title_index]
        elif title_options:
            final_title = title_options[0]
        elif project and project.title:
            final_title = project.title
        else:
            final_title = canonical_slug.replace("-", " ").title()

        # Sanitize YouTube max 100 chars
        if len(final_title) > 100:
            final_title = final_title[:97] + "..."

        # 2. Resolve Description
        final_description = youtube_kit.get("description")
        if not final_description:
            final_description = (
                f"The historical chronicle of {final_title}.\n\n"
                f"Archival folklore from Japan curated by Red Sun Japan.\n"
                f"Explore companion essays and historical sources at https://redsunjapan.com/posts/{canonical_slug}"
            )

        # 3. Resolve Tags
        tags = youtube_kit.get("tags") or []
        if not tags:
            tags = [
                "Red Sun Japan",
                "Japanese Folklore",
                "Kaidan",
                "Samurai History",
                "Edo Period",
                "Historical Documentary",
            ]
        # Clean tags (max 500 chars total for YouTube)
        clean_tags: List[str] = []
        cur_chars = 0
        for t in tags:
            if cur_chars + len(t) + 1 <= 480:
                clean_tags.append(t)
                cur_chars += len(t) + 1

        # 4. Strict Privacy Check (JingJing Operating Charter Safety Rule)
        sanitized_privacy = (privacy_status or "private").lower().strip()
        if sanitized_privacy not in ("private", "unlisted"):
            logger.warning("Requested privacy '%s' not permitted by safety rules. Enforcing 'private'.", privacy_status)
            sanitized_privacy = "private"

        # 5. Build Media Upload Request
        youtube = get_youtube_client(interactive=False)

        body = {
            "snippet": {
                "title": final_title,
                "description": final_description,
                "tags": clean_tags,
                "categoryId": str(category_id),
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": sanitized_privacy,
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
            },
        }

        logger.info(
            "Uploading master video '%s' (%s, %.1f MB) to YouTube as '%s'...",
            video_path.name,
            final_title,
            video_path.stat().st_size / (1024 * 1024),
            sanitized_privacy,
        )

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=4 * 1024 * 1024,
            resumable=True,
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            upload_status, response = request.next_chunk()
            if upload_status:
                logger.info("Upload progress: %d%%", int(upload_status.progress() * 100))

        video_id = response.get("id")
        if not video_id:
            raise RuntimeError(f"YouTube API returned no video ID: {response}")

        video_url = f"https://youtu.be/{video_id}"
        studio_url = f"https://studio.youtube.com/video/{video_id}/edit"
        logger.info("Master video uploaded successfully: %s", video_url)

        # 6. Upload Custom Thumbnail if present
        thumbnail_uploaded = False
        if thumbnail_path and thumbnail_path.exists():
            try:
                logger.info("Uploading custom thumbnail: %s", thumbnail_path)
                thumb_media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
                youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
                thumbnail_uploaded = True
                logger.info("Custom thumbnail successfully assigned to %s", video_id)
            except Exception as thumb_exc:
                logger.warning("Custom thumbnail upload failed for %s: %s", video_id, thumb_exc)

        # 7. Update Database
        if project:
            project.youtube_video_id = video_id
            project.youtube_url = video_url
            project.youtube_status = sanitized_privacy
            if project.status == VideoStatus.RENDERED:
                project.status = VideoStatus.PUBLISHED
            db.commit()

        return {
            "success": True,
            "video_id": video_id,
            "url": video_url,
            "studio_url": studio_url,
            "status": sanitized_privacy,
            "title": final_title,
            "thumbnail_uploaded": thumbnail_uploaded,
            "slug": canonical_slug,
        }

    finally:
        db.close()


def upload_project_short(
    slug: str,
    short_index: int = 1,
    privacy_status: str = "private",
    category_id: str = "27",
) -> Dict[str, Any]:
    """Upload vertical short clip (9:16) to YouTube as private/unlisted Short."""
    db = SessionLocal()
    try:
        project, canonical_slug = _resolve_project(slug, db)
        ws = ProjectWorkspace.from_slug(canonical_slug, project_id=project.id if project else None)

        shorts_dir = ws.video_dir / "shorts"
        if not shorts_dir.exists():
            shorts_dir = ws.root / "video" / "shorts"

        short_candidates = [
            shorts_dir / f"short_{short_index:02d}.mp4",
            shorts_dir / f"{canonical_slug}_short_{short_index:02d}.mp4",
            shorts_dir / f"{slug}_short_{short_index:02d}.mp4",
            shorts_dir / f"short_{short_index}.mp4",
            ws.root / "shorts" / f"short_{short_index:02d}.mp4",
            ws.root / "shorts" / f"{canonical_slug}_short_{short_index:02d}.mp4",
            ws.root / "shorts" / f"short_{short_index}.mp4",
        ]

        short_path: Optional[Path] = None
        for cand in short_candidates:
            if cand.exists() and cand.stat().st_size > 500 * 1024:
                short_path = cand
                break

        if not short_path:
            existing_files = [f.name for f in shorts_dir.glob("*.mp4")] if shorts_dir.exists() else []
            raise FileNotFoundError(
                f"Short video #{short_index} não encontrado em {shorts_dir}. "
                f"Arquivos .mp4 existentes: {existing_files if existing_files else 'nenhum arquivo .mp4 encontrado'}. "
                "Certifique-se de que a renderização dos Shorts foi concluída no Remotion."
            )

        kit_data = _load_distribution_metadata(ws)
        shorts_list = kit_data.get("shorts") or kit_data.get("youtube_shorts") or kit_data.get("reels_tiktok") or []

        # Find matching short metadata (supporting 1-based and 0-based clip_index)
        short_meta: Dict[str, Any] = {}
        for s in shorts_list:
            c_idx = s.get("clip_index")
            if c_idx is not None and int(c_idx) == short_index:
                short_meta = s
                break
        if not short_meta and 0 <= short_index - 1 < len(shorts_list):
            short_meta = shorts_list[short_index - 1]

        # Check shorts_metadata.json from video/shorts/ as well if available
        if not short_meta:
            sm_file = shorts_dir / "shorts_metadata.json"
            if sm_file.exists():
                try:
                    sm_data = json.loads(sm_file.read_text(encoding="utf-8"))
                    clips = sm_data.get("clips", [])
                    if 0 <= short_index - 1 < len(clips):
                        c = clips[short_index - 1]
                        short_meta = {
                            "title": c.get("hookText") or c.get("highlight"),
                            "caption": c.get("hookText") or c.get("highlight"),
                        }
                except Exception:
                    pass

        raw_title = short_meta.get("title") or (project.title if project else canonical_slug)
        # Format title (up to 95 chars with #Shorts)
        base_title = re.sub(r"\s*#Shorts\b", "", raw_title, flags=re.IGNORECASE).strip()
        if len(base_title) > 85:
            base_title = base_title[:82].rstrip() + "..."
        short_title = f"{base_title} #Shorts"

        caption = short_meta.get("caption") or short_meta.get("description") or raw_title
        hashtags = short_meta.get("hashtags") or ["#Shorts", "#JapaneseFolklore", "#Kaidan"]
        hashtags_str = " ".join(hashtags if isinstance(hashtags, list) else [str(hashtags)])
        if "#Shorts" not in hashtags_str:
            hashtags_str += " #Shorts"

        full_video_cta = ""
        if project and project.youtube_url:
            full_video_cta = f"\n\nWatch the full documentary: {project.youtube_url}"
        elif project:
            full_video_cta = f"\n\nExplore full companion chronicles at https://redsunjapan.com/posts/{canonical_slug}"

        description = f"{caption}\n\n{hashtags_str}{full_video_cta}\n\nHistorical Japanese folklore curated by Red Sun Japan."

        sanitized_privacy = (privacy_status or "private").lower().strip()
        if sanitized_privacy not in ("private", "unlisted"):
            sanitized_privacy = "private"

        youtube = get_youtube_client(interactive=False)

        body = {
            "snippet": {
                "title": short_title,
                "description": description,
                "tags": ["Shorts", "YouTubeShorts", "JapaneseFolklore", "Kaidan", "Red Sun Japan"],
                "categoryId": str(category_id),
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": sanitized_privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        logger.info(
            "Uploading Short clip #%d '%s' (%.1f MB) to YouTube as '%s'...",
            short_index,
            short_path.name,
            short_path.stat().st_size / (1024 * 1024),
            sanitized_privacy,
        )

        media = MediaFileUpload(
            str(short_path),
            mimetype="video/mp4",
            chunksize=2 * 1024 * 1024,
            resumable=True,
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            upload_status, response = request.next_chunk()
            if upload_status:
                logger.info("Short #%d upload progress: %d%%", short_index, int(upload_status.progress() * 100))

        video_id = response.get("id")
        video_url = f"https://youtube.com/shorts/{video_id}"
        studio_url = f"https://studio.youtube.com/video/{video_id}/edit"

        return {
            "success": True,
            "video_id": video_id,
            "url": video_url,
            "studio_url": studio_url,
            "status": sanitized_privacy,
            "title": short_title,
            "clip_index": short_index,
            "slug": canonical_slug,
        }

    finally:
        db.close()
