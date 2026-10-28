# Image Prompt Generation — User Prompt Template

You are converting the following Red Sun Japan documentary narration script into a structured Gemini Assistant image prompt package.

## Project metadata

- **Episode**: Botan Dōrō — The Peony Lantern
- **Tone**: BBC/Attenborough-grade long-form documentary. Cold open cadence, restrained supernatural melancholy, sober closing.
- **Total narration duration**: ~8.9 minutes (~534 seconds)
- **Target image seconds**: 25 seconds per frame
- **Target frame count**: 18–22 frames. Aim for 20.
- **Style bible**: see system prompt — cinematic semi-realistic historical dark fantasy digital painting (NOT ukiyo-e, NOT anime).
- **Output**: JSON object matching the schema in the system prompt.

## Script

```text
{SCRIPT}
```

## Scene mapping guidance (paragraph → frames)

Below is the paragraph breakdown of the script (15 paragraphs, ~100 words each). Use this as your segmentation starting point — break a paragraph into multiple frames if it spans multiple distinct visuals; merge two paragraphs into one frame if the visual is continuous.

{SCENE_GUIDE}

## Hard requirements

1. Output JSON only. No markdown fences, no commentary.
2. Every prompt ends with `Single unified composition.` (this exact phrase, including the period).
3. Character descriptions must be **byte-for-byte identical** to the character ledger in the system prompt.
4. Total frame count: between 18 and 22 (target 20).
5. Validate your JSON before responding — make sure it parses.

## What I want you to do

Generate the complete `project.json` body now. Don't ask clarifying questions, don't add prose around it. Just the JSON object, perfectly valid, complete, ready to write to disk.
