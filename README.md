# CourseNote

CourseNote 是一個本地端 CLI 工具，用來將 YouTube 課程影片整理成結構化 Markdown 筆記。

## 功能

- 下載 YouTube 影片資訊、影片檔與字幕
- 優先使用原生字幕，次選自動字幕；無字幕直接失敗
- 以 ffmpeg 抽幀並保留中間產物
- 使用 PySceneDetect 做 scene detection
- 將字幕依段落對齊後交給 OpenAI-compatible LLM 摘要
- 輸出單一 Markdown 筆記檔

## 安裝

```bash
pip install -e .
```

額外需要：

- `yt-dlp`
- `ffmpeg`
- 可選的 OpenAI-compatible API / Ollama 服務

## CLI

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --min-segment-seconds 20 \
  --llm-backend ollama \
  --model qwen3:8b \
  --note-style summary \
  --include-transcript \
  --with-course-summary \
  --with-key-terms \
  --output output.md
```

```shell
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ"   --subtitle-lang zh-TW   --sample-interval 1   --scene-threshold 27   --scene-edge-weight 0.2   --min-segment-seconds 20   --llm-backend ollama   --model qwen3:8b   --note-style summary   --include-transcript   --with-course-summary   --with-key-terms   --output output.md
```

## Workdir 結構

```text
workdir/
  video.mp4
  subtitles.vtt
  transcript.json
  frames/
  scenes.json
  segments.json
  llm_outputs.json
```
