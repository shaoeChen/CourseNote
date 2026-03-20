from __future__ import annotations

TEMPLATES = {
    "summary": "請根據以下字幕內容，整理成精簡的繁體中文課程重點。\n\n{text}",
    "study_notes": "請把以下字幕整理成適合複習的條列式筆記。\n\n{text}",
    "structured": "請輸出主題、重點、例子三個區塊，根據以下內容整理。\n\n{text}",
    "near_transcript": "請保留原意，將以下字幕整理成較易讀的逐字稿式筆記。\n\n{text}",
}

EXTRA_NOTES_PROMPT = "如需補充背景知識，請另外以『補充說明』區塊輸出，且不可混入主內容。"
