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
course-note generate "https://www.youtube.com/watch?v=urwDLyNa9FU&t"   --subtitle-lang zh-TW   --sample-interval 1   --scene-threshold 27   --scene-edge-weight 0.2   --min-segment-seconds 20   --llm-backend ollama   --model qwen3:8b   --note-style near_transcript   --include-transcript   --with-course-summary   --with-key-terms   --output output2.md
```

若要把已產生的 Markdown 筆記發布到 HackMD，並自動把 `簡報：000243.jpg` 這類段落對應的本機圖片上傳後插入到該段落下方，可使用：

```bash
course-note publish-hackmd output.md
```

預設會從 `workdir/frames` 尋找圖片。若圖片在別的位置，可改用：

```bash
course-note publish-hackmd output.md --image-dir path/to/frames
```

請先在 `.env` 或環境變數中設定：

```bash
HACKMD_API=your_hackmd_token
```

```shell
course-note rewrite-notes   --workdir workdir   --source-note output.md   --llm-backend ollama   --model qwen3:8b   --note-style near_transcript   --with-course-summary   --with-key-terms   --output output.md


```

## 切段調參指南

`course-note generate` 的切段效果，主要由這三個參數控制：

- `--min-segment-seconds`
- `--scene-threshold`
- `--scene-edge-weight`

如果使用者覺得「切段失敗」，通常不是同一種失敗，所以要先看症狀再調參數。

### 先記住這三個參數各自負責什麼

- `--min-segment-seconds`
  控制每段允許的最短秒數。值越大，系統越不容易切出很多很短的片段。
- `--scene-threshold`
  控制 PySceneDetect 對畫面變化有多敏感。值越低，越容易切段；值越高，越保守。
- `--scene-edge-weight`
  控制切段時要不要更重視畫面邊緣、框線、版型輪廓的變化。值越高，越容易把版面結構變化當成切點。

可以把它們簡單理解成：

- `--min-segment-seconds`：限制不要切太碎
- `--scene-threshold`：決定多大變化才算真的換場景
- `--scene-edge-weight`：決定邊框和版型變化要不要算很重要

### `--min-segment-seconds` 是什麼

`--min-segment-seconds 20` 的意思是「場景切分後，每一段最短至少要接近 20 秒」。  
它會被轉成 PySceneDetect 的 `min_scene_len`，如果兩個切點距離太近、短於這個值，系統就會傾向把它們合併，而不是切成兩個獨立片段。

所以這個參數控制的是：

- 切段下限
- 是否要抑制過度頻繁的切段

它不控制：

- 字幕長度
- 每段筆記字數
- LLM 摘要風格

### 常見症狀與建議調法

- 症狀：中間被切出很多很短的小段落
  先調高 `--min-segment-seconds`，例如從 `20` 提高到 `30` 或 `40`。
- 症狀：投影片只改了一點字、游標晃一下、畫面小閃動就被切段
  調高 `--scene-threshold`，例如從 `27` 提高到 `30` 或 `32`。
- 症狀：表格線、框線、版面輪廓一變就被切段
  調低 `--scene-edge-weight`，例如從 `0.2` 降到 `0.1` 或 `0.0`。
- 症狀：明明換到新投影片了卻沒有切開
  調低 `--scene-threshold`，例如從 `27` 降到 `24` 或 `22`。
- 症狀：新舊投影片顏色差不多，但版型輪廓明顯不同，系統卻抓不到
  調高 `--scene-edge-weight`，例如從 `0.0` 調到 `0.2` 或 `0.3`。

### 建議調整順序

如果不知道從哪裡開始，建議照這個順序：

1. 先調 `--min-segment-seconds`
   這是最直接控制「切太碎」的參數。
2. 再調 `--scene-threshold`
   這是整體切段敏感度的主開關。
3. 最後調 `--scene-edge-weight`
   這通常是針對簡報版型、表格、框線變化做微調。

### 以這組指令為例，怎麼改比較合理

你目前使用的是：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --scene-threshold 27 \
  --scene-edge-weight 0.2 \
  --min-segment-seconds 20 \
  --llm-backend ollama \
  --model qwen3:8b \
  --note-style near_transcript \
  --with-key-terms \
  --output output.md
```

如果問題是「中間還是有片段切段失敗，而且看起來偏向切太碎、誤切太多」，建議先試這一版：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --scene-threshold 30 \
  --scene-edge-weight 0.0 \
  --min-segment-seconds 30 \
  --llm-backend ollama \
  --model qwen3:8b \
  --note-style near_transcript \
  --with-key-terms \
  --output output.md
```

這樣改的原因是：

- `--min-segment-seconds 20 -> 30`
  先減少太短的誤切片段
- `--scene-threshold 27 -> 30`
  讓系統不要對小幅度畫面變化太敏感
- `--scene-edge-weight 0.2 -> 0.0`
  避免因為框線、表格、版型邊緣變動而被誤判成新場景

### 哪些參數不影響切段

- `--sample-interval`
  只影響代表截圖是在段尾前幾秒擷取，不影響切段數量。
- `--note-style`
  只影響 LLM 改寫筆記的風格，不影響切段。
- `--model`
  只影響摘要內容，不影響切段。

### 實務建議

如果你要幫使用者排查切段問題，最有用的做法是：

1. 先看 `segments.json` 和 `frames/`，確認到底是「切太多」還是「切太少」。
2. 如果是切太多，優先增加 `--min-segment-seconds`。
3. 如果是切點太敏感，再提高 `--scene-threshold`。
4. 如果是版面線條害你誤切，再降低 `--scene-edge-weight`。

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
