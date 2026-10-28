#!/usr/bin/env python3
"""
retrofit_kofi_youtube.py
Guarantees that all long-form YouTube descriptions and distribution artifacts have the Ko-fi support link:
1. In all local project distribution files (social_distribution_kit.json, youtube_kit.txt, youtube_description.txt)
2. In all live long videos currently published on the YouTube channel
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

try:
    from redsun.workspace import RUNTIME_ROOT
except ImportError:
    RUNTIME_ROOT = Path("/Users/isamumatsuyama/Documents/red-sun-japan-runtime/projects")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("retrofit_kofi_youtube")

KOFI_URL = "https://ko-fi.com/redsunjapan"
KOFI_SOCIAL_BULLET = "- Support the Archive (Buy a Tea): https://ko-fi.com/redsunjapan"
KOFI_FEATURE_LINE = "🍵 Support our independent translations on Ko-fi: https://ko-fi.com/redsunjapan"
KOFI_PINNED_LINE = "• Buy the Archive a Tea: https://ko-fi.com/redsunjapan"


def inject_kofi_into_description(desc: str) -> str:
    """Injects Ko-fi link into the official links section and as a featured callout."""
    if not desc:
        return desc

    # 1. Add bullet to Official Links section if present
    if "Official Links & Art Shop:" in desc and KOFI_URL not in desc:
        lines = desc.splitlines()
        new_lines = []
        injected = False
        for line in lines:
            new_lines.append(line)
            if not injected and ("Official Chronicles (Blog):" in line or "Official Art Prints" in line):
                new_lines.append(KOFI_SOCIAL_BULLET)
                injected = True
        desc = "\n".join(new_lines)

    # 2. Add featured callout line before audio attribution or hashtags
    if KOFI_FEATURE_LINE not in desc and KOFI_URL not in desc:
        if "Soundtrack & Audio Attribution" in desc:
            idx = desc.find("Soundtrack & Audio Attribution")
            desc = desc[:idx].rstrip() + f"\n\n{KOFI_FEATURE_LINE}\n\n" + desc[idx:].lstrip()
        elif "\n#" in desc:
            idx = desc.rfind("\n#")
            desc = desc[:idx].rstrip() + f"\n\n{KOFI_FEATURE_LINE}\n\n" + desc[idx:].lstrip()
        else:
            desc = desc.rstrip() + f"\n\n{KOFI_FEATURE_LINE}\n"
    elif KOFI_FEATURE_LINE not in desc and KOFI_SOCIAL_BULLET in desc:
        # If only the bullet exists, also ensure the prominent callout is present
        if "Soundtrack & Audio Attribution" in desc:
            idx = desc.find("Soundtrack & Audio Attribution")
            desc = desc[:idx].rstrip() + f"\n\n{KOFI_FEATURE_LINE}\n\n" + desc[idx:].lstrip()
        elif "\n#" in desc:
            idx = desc.rfind("\n#")
            desc = desc[:idx].rstrip() + f"\n\n{KOFI_FEATURE_LINE}\n\n" + desc[idx:].lstrip()

    return desc


def inject_kofi_into_pinned_comment(comment: str, slug: str = "", title: str = "") -> str:
    """Ensures pinned comment has Ko-fi tea support reminder."""
    if not comment or len(comment.strip()) < 10:
        blog_url = f"https://redsunjapan.com/posts/{slug}" if slug else "https://redsunjapan.com"
        t_label = f"the {title} chronicle" if title else "this chronicle"
        return (
            f"⛩️ Which moment of {t_label} chilled you the most?\n\n"
            f"• Read the archival essay & historical footnotes: {blog_url}\n"
            f"• Buy the Archive a Tea: {KOFI_URL}\n\n"
            f"Subscribe to Red Sun Japan for weekly archival folklore & kaidan documentaries."
        )

    if KOFI_URL in comment:
        return comment

    lines = comment.splitlines()
    new_lines = []
    injected = False
    for line in lines:
        new_lines.append(line)
        if not injected and ("Read the archival essay" in line or "historical footnotes" in line):
            new_lines.append(f"• Buy the Archive a Tea: {KOFI_URL}")
            injected = True

    if not injected:
        new_lines.append(f"\n• Buy the Archive a Tea: {KOFI_URL}")

    return "\n".join(new_lines)


def update_youtube_kit_txt(kit_path: Path, new_desc: str, new_pinned: str) -> None:
    """Updates the [DESCRIPTION] and [SUGGESTED PINNED COMMENT] sections of youtube_kit.txt."""
    if not kit_path.exists():
        return

    content = kit_path.read_text(encoding="utf-8")
    
    # 1. Update [DESCRIPTION] section
    desc_header = "[DESCRIPTION]\n"
    next_header_candidates = ["[CHAPTERS / TIMESTAMPS]", "[SUGGESTED PINNED COMMENT]", "[SOUNDTRACK / BGM CREDITS]"]
    
    if desc_header in content:
        start_idx = content.find(desc_header) + len(desc_header)
        end_idx = -1
        for cand in next_header_candidates:
            cand_idx = content.find(cand, start_idx)
            if cand_idx != -1:
                if end_idx == -1 or cand_idx < end_idx:
                    # Look backwards for the separator line dashes
                    sep_idx = content.rfind("--------------------------------------------------------------------------------", start_idx, cand_idx)
                    end_idx = sep_idx if sep_idx != -1 else cand_idx
        
        if end_idx != -1:
            content = content[:start_idx] + new_desc + "\n\n" + content[end_idx:]

    # 2. Update [SUGGESTED PINNED COMMENT] section
    pinned_header = "[SUGGESTED PINNED COMMENT]\n"
    next_pinned_candidates = ["[SOUNDTRACK / BGM CREDITS]", "[TAGS]", "[YOUTUBE SHORTS (3 CLIPS)]"]
    if pinned_header in content:
        start_idx = content.find(pinned_header) + len(pinned_header)
        end_idx = -1
        for cand in next_pinned_candidates:
            cand_idx = content.find(cand, start_idx)
            if cand_idx != -1:
                if end_idx == -1 or cand_idx < end_idx:
                    sep_idx = content.rfind("--------------------------------------------------------------------------------", start_idx, cand_idx)
                    end_idx = sep_idx if sep_idx != -1 else cand_idx
        
        if end_idx != -1:
            content = content[:start_idx] + new_pinned + "\n\n" + content[end_idx:]

    kit_path.write_text(content, encoding="utf-8")


def retrofit_local_projects() -> list[str]:
    """Scans all runtime projects and updates distribution deliverables with Ko-fi."""
    updated_projects = []

    for p in sorted(RUNTIME_ROOT.iterdir()):
        if not p.is_dir() or p.name.startswith(".") or p.name.startswith("_") or p.is_symlink():
            continue

        dist_dir = p / "distribution"
        kit_json_path = dist_dir / "social_distribution_kit.json"
        yt_kit_path = dist_dir / "youtube_kit.txt"
        yt_desc_path = dist_dir / "youtube_description.txt"

        if not kit_json_path.exists():
            continue

        try:
            kit_data = json.loads(kit_json_path.read_text(encoding="utf-8"))
            slug = kit_data.get("slug", p.name)
            title = kit_data.get("title", slug)

            # 1. Update social_links in kit_data
            social_links = kit_data.get("social_links", {})
            if "kofi" not in social_links:
                social_links["kofi"] = {
                    "label": "Support the Archive (Buy a Tea)",
                    "url": KOFI_URL,
                }
                kit_data["social_links"] = social_links

            # 2. Update YouTube Description
            yt = kit_data.get("youtube", {})
            current_desc = yt.get("description", "")
            updated_desc = inject_kofi_into_description(current_desc)
            yt["description"] = updated_desc

            # 3. Update Pinned Comment
            current_pinned = yt.get("pinned_comment", "")
            updated_pinned = inject_kofi_into_pinned_comment(current_pinned, slug=slug, title=title)
            yt["pinned_comment"] = updated_pinned
            kit_data["youtube"] = yt

            # 4. Update Twitter Thread (Tweet 6)
            tweets = kit_data.get("twitter_thread", [])
            for i, t in enumerate(tweets, start=1):
                num = t.get("tweet_number", i)
                text = t.get("text", "")
                if (num == 6 or (i == len(tweets) and len(tweets) >= 5)) and KOFI_URL not in text:
                    text = f"{text}\n🍵 Support our independent translations: {KOFI_URL}"
                    t["text"] = text
            kit_data["twitter_thread"] = tweets

            # Save updated social_distribution_kit.json
            kit_json_path.write_text(json.dumps(kit_data, indent=2, ensure_ascii=False), encoding="utf-8")

            # Save updated youtube_description.txt
            yt_desc_path.write_text(updated_desc, encoding="utf-8")

            # Save updated youtube_kit.txt
            update_youtube_kit_txt(yt_kit_path, updated_desc, updated_pinned)

            # Update twitter_thread.txt / .md if they exist
            tt_txt = dist_dir / "twitter_thread.txt"
            tt_md = dist_dir / "twitter_thread.md"
            if tt_txt.exists():
                lines = []
                for i, t in enumerate(tweets, start=1):
                    num = t.get("tweet_number", i)
                    lines.extend([f"--- Tweet {num}/{len(tweets)} ---", t.get("text", ""), ""])
                tt_txt.write_text("\n".join(lines), encoding="utf-8")
            if tt_md.exists():
                md_lines = [f"# X/Twitter Thread: {title}\n"]
                for i, t in enumerate(tweets, start=1):
                    num = t.get("tweet_number", i)
                    md_lines.append(f"---\n🧵 ({num}/{len(tweets)})\n{t.get('text', '')}\n")
                tt_md.write_text("\n".join(md_lines), encoding="utf-8")

            updated_projects.append(p.name)
            logger.info("Updated distribution files with Ko-fi for: %s", p.name)

        except Exception as exc:
            logger.error("Failed to update project %s: %s", p.name, exc)

    return updated_projects


def retrofit_legacy_post_kits():
    """Updates legacy post_kits folders if present in repo."""
    post_kits_dir = PARENT_DIR / "post_kits"
    if not post_kits_dir.exists():
        return

    for desc_file in post_kits_dir.glob("**/youtube_description.md"):
        content = desc_file.read_text(encoding="utf-8")
        if KOFI_URL not in content:
            updated = inject_kofi_into_description(content)
            desc_file.write_text(updated, encoding="utf-8")
            logger.info("Updated legacy post_kit: %s", desc_file)

    for pin_file in post_kits_dir.glob("**/youtube_pinned_comment.md"):
        content = pin_file.read_text(encoding="utf-8")
        if KOFI_URL not in content:
            updated = inject_kofi_into_pinned_comment(content)
            pin_file.write_text(updated, encoding="utf-8")
            logger.info("Updated legacy pinned comment: %s", pin_file)


def retrofit_live_youtube_videos() -> list[dict[str, Any]]:
    """Updates long-form videos published on the YouTube channel via YouTube Data API v3."""
    results = []
    token_p = PARENT_DIR / "token.json"
    if not token_p.exists():
        logger.warning("token.json not found. Skipping live YouTube Data API update.")
        return results

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(
            str(token_p),
            ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
        )
        yt_service = build("youtube", "v3", credentials=creds)

        # Get uploaded videos
        channels_res = yt_service.channels().list(mine=True, part="contentDetails").execute()
        uploads_id = channels_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        p_res = yt_service.playlistItems().list(playlistId=uploads_id, part="snippet", maxResults=50).execute()

        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in p_res.get("items", [])]
        if not video_ids:
            return results

        v_res = yt_service.videos().list(part="snippet,contentDetails", id=",".join(video_ids)).execute()

        for item in v_res.get("items", []):
            vid_id = item["id"]
            snippet = item["snippet"]
            title = snippet.get("title", "")
            desc = snippet.get("description", "")
            duration = item.get("contentDetails", {}).get("duration", "")

            # Filter for long-form videos: duration > 1 min or not tagged with #shorts
            is_short = "#shorts" in title.lower() or "#shorts" in desc.lower() or ("PT" in duration and "M" not in duration)
            if is_short:
                logger.info("Skipping short video on YouTube: [%s] %s", vid_id, title)
                continue

            if KOFI_URL in desc:
                logger.info("YouTube video already contains Ko-fi: [%s] %s", vid_id, title)
                results.append({"id": vid_id, "title": title, "status": "ALREADY_PRESENT"})
                continue

            logger.info("Injecting Ko-fi link into live YouTube video: [%s] %s", vid_id, title)
            updated_desc = inject_kofi_into_description(desc)

            # Update YouTube snippet
            update_body = {
                "id": vid_id,
                "snippet": {
                    "title": snippet["title"],
                    "description": updated_desc,
                    "tags": snippet.get("tags", []),
                    "categoryId": snippet.get("categoryId", "27"),
                }
            }
            yt_service.videos().update(part="snippet", body=update_body).execute()
            logger.info("Successfully updated YouTube description for [%s] %s", vid_id, title)
            results.append({"id": vid_id, "title": title, "status": "UPDATED_ON_YOUTUBE"})

    except Exception as exc:
        logger.error("Failed to update live YouTube videos: %s", exc, exc_info=True)

    return results


def main():
    print("=" * 80)
    print("RED SUN JAPAN // KO-FI RETROFIT FOR LONG-FORM YOUTUBE DESCRIPTIONS")
    print("=" * 80)

    # 1. Local project files
    print("\n--- 1. Retrofitting Local Project Distribution Kits ---")
    updated_local = retrofit_local_projects()
    print(f"Total Local Projects Updated: {len(updated_local)}")
    for p in updated_local:
        print(f"  ✓ {p}")

    # 2. Legacy post_kits
    print("\n--- 2. Retrofitting Legacy post_kits ---")
    retrofit_legacy_post_kits()
    print("  ✓ Legacy post_kits synchronized.")

    # 3. Live YouTube Videos
    print("\n--- 3. Retrofitting Live YouTube Long Videos via Data API v3 ---")
    updated_yt = retrofit_live_youtube_videos()
    for item in updated_yt:
        print(f"  ✓ [{item['id']}] {item['title']} -> {item['status']}")

    print("\n" + "=" * 80)
    print("KO-FI YOUTUBE RETROFIT COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
