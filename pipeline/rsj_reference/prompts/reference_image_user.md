# Reference Image Generation — User Prompt

Generate the 9 reference image prompts described in the system prompt as a single JSON object.

Output ONLY the JSON — no commentary, no markdown fence, no code block wrapping.

Required structure:
- `schemaVersion`: 3
- `project`: { id: "botan-doro-references", name: "Botan Dōrō — Reference Anchors (Gemini)", description: "9 reference images (1 style master + 6 characters + 2 environments) to stabilize visual consistency across 20 final scene frames." }
- `generation`: {
    `masterPrompt`: <paste the entire master prompt block verbatim from the system prompt — including the STYLE EXCLUSIONS section>,
    `aspectRatio`: "3:4",
    `sceneSeparator`: "\n\n---\n\n"
  }
- `assets`: {}
- `tasks`: array of exactly 9 tasks in this exact order:
    1. style-master (16:9 environment/palette anchor)
    2. character-shinzaburo (3:4 portrait)
    3. character-otsuyu (3:4 portrait)
    4. character-yone (3:4 portrait)
    5. character-tomozo (3:4 portrait)
    6. character-omine (3:4 portrait)
    7. character-priest (3:4 portrait)
    8. environment-yanaka-lane (16:9 location)
    9. environment-temple (16:9 location)

Each task:
- `id`: kebab-case slug exactly as listed above
- `title`: short human label
- `prompt`: full cinematic prompt (English, ≤220 words, ends with literal sentence "Single unified composition.")
- `references`: [] (empty — no in-project references; these ARE the references)
- `output`: { `basename`: same as `id` }

Begin now. Return raw JSON only.
