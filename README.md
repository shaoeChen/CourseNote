# CourseNote

`publish-hackmd` 會先建立文章，再逐張上傳簡報圖片，最後寫回圖片連結。終端與課程目錄的 `hackmd_publish.log` 顯示文章連結、圖片進度、等待、限流重試與完成狀態；等待時每 10 秒更新。`hackmd_publish.json` 立即保存文章 ID 及每張成功上傳圖片的雜湊與連結；同一文章重跑可續傳並沿用成功圖片，更新失敗不會另建重複文章。已完成且檔案未變動時，重跑直接回傳結果，不覆寫遠端文章。建立文章的結果不確定時會停止，避免重複建立。單次退避等待超過 60 秒時會保存狀態並停止，稍後執行相同命令即可續傳。若本地文章已修改，程式會要求先保留或重新命名舊發布狀態，避免把不同版本當作同一次續傳。

### 簡報辨識模式（hybrid）

對於教室鏡頭與全螢幕簡報交替的影片，可以啟用 `--scene-mode hybrid`。原本的 `algorithm` 模式仍是預設。

Hybrid 以演算法時間線為基礎，模型每段一次審查字幕、段首／中間／段尾三張畫面、相鄰字幕與既有筆記。可用時附上一段已確認可讀的簡報作為比較。審查會指出文不對題、同頁鏡頭切換、段內多頁及畫面先於講述換頁等問題。只合併高信心的同一頁鏡頭切換；同議題的不同頁仍保留。

圖片擷取需要 OpenCV，可用 `pip install -e ".[vision]"` 或 `uv sync --extra vision` 安裝。模型服務需另外啟動；這項安裝不會下載視覺模型。

在 `.env` 用 `COURSE_NOTE_VISION_PROVIDER` 指向已配置好的供應商，程式會直接沿用該供應商的模型、API 位址與金鑰。該模型與服務需支援多張圖片輸入。

```dotenv
COURSE_NOTE_VISION_PROVIDER=vllm
```

例如指定 `vllm`，就使用既有的 `COURSE_NOTE_VLLM_MODEL`、`COURSE_NOTE_VLLM_API_BASE`（或 HOST／PORT）與 `COURSE_NOTE_VLLM_API_KEY`；指定 `ollama` 則沿用 Ollama 的設定。無須再設定 `COURSE_NOTE_VISION_MODEL`、`COURSE_NOTE_VISION_API_BASE` 或 `COURSE_NOTE_VISION_API_KEY`。未指定視覺供應商時，沿用 `COURSE_NOTE_PROVIDER`；模型與連線的 fallback 規則也和寫筆記相同。

逾時沿用 `COURSE_NOTE_LLM_TIMEOUT_SECONDS`，需要單次覆寫時才傳 `--vision-timeout-seconds`。`--vision-backend` 也僅作為單次供應商覆寫，平常不需傳入。

對已下載影片與字幕的課程，先執行預覽。會讀取 `subtitles.vtt`（或 `transcript.json`）及既有 `segments.json` 的筆記，沿用 `scenes.json` 的時間線；沒有時間線時才用演算法建立。預覽不會覆寫 `article.md`、`segments.json`、原本的 `frames/`：

```powershell
course-note detect-slides `
  --course-name "Stanford CS329A Self-Improving AI Agents | Part 1 | Course Overview" `
  --scene-threshold 15 `
  --scene-edge-weight 0.3 `
  --min-segment-seconds 20 `
  --workdir workdir
```

預覽輸出為 `workdir/<課程名稱>/slide_preview/preview.md` 及 `frames/`。判斷依據、低信心結果與待檢查範圍保存在 `slide_analysis/slide_review.json`；候選切點與統計也保留在該目錄。

審查會即時顯示目前區段、時間範圍、圖片數、快取命中、累計模型請求、追加分析次數、耗時與結果；等待模型時每 10 秒更新一次。帶時間戳的日誌會追加到 `slide_analysis/review.log`，不記錄金鑰或完整提示。`requests` 是本次啟動的請求數，包含可能仍在等待的請求，並非費用估算。按 Ctrl+C 會把報告標記為中止並保留成功快取；已送出的請求仍可能計費。

模型回覆格式不符時，停止分析並把原始回覆保存到 `slide_analysis/invalid_responses/`，報告標示失敗區段與檔案路徑。若已有分段成果，會另外輸出 `slide_preview/partial/partial_preview.md` 和截圖；部分預覽標示已涵蓋的範圍，不代表整部課程已完成。這個部分預覽只使用本機影片與既有成果，不會重送模型請求。

使用 DeepSeek 官方 API 的圖片審查會關閉 thinking、要求 JSON 回覆，並限制輸出為 2048 tokens。這避免短審查沿用服務端較大的預設推理額度；文字筆記與其他供應商維持既有請求設定。既有合法審查快取仍可沿用。成功回覆有提供 token 用量時，日誌會印出 `[USAGE]`；最終答案為空時，保存完整 API 回覆並顯示結束原因，不自動重試，也不把 `reasoning_content` 當作審查結果。

檢查預覽後，用原本的 `generate` 命令加入：

```powershell
  --scene-mode reviewed
```

`reviewed` 直接套用已完成的 `slide_analysis/slide_review.json`，保留修正後的時間線與已選擇截圖時間；不呼叫視覺模型，也不重新跑場景演算法。報告必須完整、分段連續且覆蓋原影片時間線。接著重新產生文字筆記與可選的關鍵詞，這些文字模型請求仍會計費，產出會覆寫課程目錄的 `article.md`、`segments.json`、`scenes.json` 和 `frames/`。`hybrid` 則用於需要重新切段及審查的情境。

`generate` 與 `rewrite-notes` 的文字生成會逐段顯示 `[TEXT_SEGMENT]`、請求數、耗時、完成結果與 API 提供的 token 用量；等待回覆時每 10 秒顯示 `[TEXT_WAIT]`。課程總結、關鍵詞與錯誤也會記錄，日誌同步追加到課程目錄的 `text_generation.log`。日誌不包含完整字幕、提示、筆記內容或金鑰，不會自動重試失敗請求。已經啟動的舊程序不會載入後來修改的日誌程式。

`generate` 的 Hybrid 保留 `--scene-threshold`、`--scene-edge-weight`、`--min-segment-seconds` 的演算法分段，再按段審查。`detect-slides` 有現成時間線時不重新切段，這些參數僅用於缺少時間線時。`--sample-interval` 用於沒有確認可讀簡報的區段；其餘採用模型選出的截圖時間。截圖檔名包含區段結束時間的毫秒值，避免短區段覆蓋同名圖片。恰好在分界開始的字幕歸下一段。

首次基礎審查最多每段一次請求，例如 79 段最多 79 次。僅有問題且需要局部分析的段落會追加一次五張圖的分析，**全片最多 10 次追加請求**；因此最多 89 次。這是請求數上限，實際費用仍依圖片與文字用量計算。已移除 `--vision-scan-interval`。`vision_cache.json` 按模型、API 位址、圖片、字幕、鄰段與既有筆記識別結果，成功後立即保存；相同輸入重跑可免重複請求，筆記或時間線變更則重新判斷。

局部分析只有在高信心、證據有效、分段完整連續且各截圖位於對應段內時才套用。模型無法確認、追加額度用完或簡報被遮擋時，保留原邊界並標記待確認；不會因為同議題就合併。取樣間短暫出現的頁面仍可能漏掉，模型品質需以實際課程預覽檢查。API 失敗或回傳結構錯誤會停止分析，保留部分報告與成功快取。

CourseNote 是一個本地端 CLI 工具，用來把 YouTube 課程影片整理成結構化 Markdown 筆記。

它會下載字幕與影片、偵測場景切換、擷取每段代表畫面，再把逐段內容交給 OpenAI-compatible LLM 改寫成可閱讀的課程筆記。

## 功能

- 下載 YouTube 影片、字幕與中繼資料
- 可單獨下載字幕並輸出逐字稿 JSON
- 優先使用原生字幕，次選自動字幕；可用 `--transcribe-fallback` 在沒有符合語言的字幕時，從音軌自動辨識原語言並轉錄
- 使用 PySceneDetect 做 scene detection
- 依場景切段，並擷取每段代表截圖
- 將逐段字幕改寫成結構化 Markdown 筆記
- 可額外產生課程摘要與關鍵詞
- 可將產出的 Markdown 發布到 HackMD

## 安裝

```bash
pip install -e .
```

額外需要：

- `yt-dlp`
- `ffmpeg`
- 一個可用的 OpenAI-compatible API，例如 Ollama、llama.cpp server、vLLM、DeepSeek、ClinePass

### 指定 ffmpeg 位置

程式會從目前 shell 的 `PATH` 尋找 `ffmpeg`、`ffprobe` 與 `ffplay`。如果把執行檔放在專案的 `ffmpeg/` 資料夾，請先將該資料夾加入 `PATH`，再執行 `course-note`。

Windows PowerShell：

```powershell
$env:Path = "${PWD}\ffmpeg;$env:Path"
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" --course-name "FastAPI 入門"
```

Linux/macOS：

```bash
export PATH="$(pwd)/ffmpeg:$PATH"
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" --course-name "FastAPI 入門"
```

也可以直接把 ffmpeg 所在資料夾永久加入作業系統的 `PATH`。專案內的 `ffmpeg/` 只提供資料夾位置，不包含執行檔。

建議先複製一份環境設定：

```bash
cp .env.example .env
```

如果你在 Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

## LLM 設定

模型名稱現在統一由 `.env` 控制，不再提供 `--model` CLI 參數。你可以在 `.env` 中指定預設 provider、主機、port 與 model；設定好之後，`generate` 與 `rewrite-notes` 會直接使用這些值。

例如使用 `llama.cpp`：

```env
COURSE_NOTE_PROVIDER=llamacpp
COURSE_NOTE_LLAMACPP_HOST=127.0.0.1
COURSE_NOTE_LLAMACPP_PORT=8080
COURSE_NOTE_LLAMACPP_MODEL=Qwen/Qwen2.5-7B-Instruct
```

例如使用 `vllm`：

```env
COURSE_NOTE_PROVIDER=vllm
COURSE_NOTE_VLLM_HOST=192.168.1.20
COURSE_NOTE_VLLM_PORT=9000
COURSE_NOTE_VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

例如使用 `deepseek`（預設模型為 Flash）：

```env
COURSE_NOTE_PROVIDER=deepseek
COURSE_NOTE_DEEPSEEK_API_KEY=your_api_key
COURSE_NOTE_DEEPSEEK_MODEL=deepseek-v4-flash
```

例如使用 `clinepass`：

```env
COURSE_NOTE_PROVIDER=clinepass
COURSE_NOTE_CLINEPASS_API_KEY=your_api_key
COURSE_NOTE_CLINEPASS_MODEL=cline-pass/deepseek-v4-flash
```

如果你用 Docker Compose、反向代理，或路徑不是標準 `/v1`，也可以直接覆蓋完整 endpoint：

```env
COURSE_NOTE_LLAMACPP_API_BASE=http://127.0.0.1:8080/v1
```

## CLI

以下範例使用 `.env` 內設定的預設 provider 與 model。

### `generate`

`generate` 會固定輸出到 `workdir/<course-name>/article.md`；目前沒有 `--output` 參數。若要改變輸出根目錄，請使用 `--workdir`。

`bash`：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --course-name "FastAPI 入門" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --min-segment-seconds 20 \
  --note-style summary \
  --include-transcript \
  --with-course-summary \
  --with-key-terms
```

`PowerShell`：

```powershell
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" `
  --course-name "FastAPI 入門" `
  --subtitle-lang zh-TW `
  --sample-interval 1 `
  --min-segment-seconds 20 `
  --note-style summary `
  --include-transcript `
  --with-course-summary `
  --with-key-terms
```

如果 `.env` 已經設定 DeepSeek Flash，也可以直接這樣執行：

`bash`：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --course-name "DeepSeek 課程筆記" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --min-segment-seconds 20 \
  --note-style summary \
  --with-course-summary \
  --with-key-terms
```

`PowerShell`：

```powershell
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" `
  --course-name "DeepSeek 課程筆記" `
  --subtitle-lang zh-TW `
  --sample-interval 1 `
  --min-segment-seconds 20 `
  --note-style summary `
  --with-course-summary `
  --with-key-terms
```

如果只想單次覆蓋 provider，仍可使用 `--llm-backend`。模型請改成先更新 `.env` 中對應 backend 的 `COURSE_NOTE_*_MODEL`。

`bash`：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --course-name "FastAPI 入門" \
  --llm-backend vllm
```

`PowerShell`：

```powershell
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" `
  --course-name "FastAPI 入門" `
  --llm-backend vllm
```

### `download-subtitles`

如果只想先取得字幕，不下載影片、不偵測場景、也不呼叫 LLM，可以使用 `download-subtitles`。它會優先下載原生字幕，沒有時改抓自動字幕，並輸出到同一個課程工作區：

- `workdir/<course-name>/subtitles.vtt`
- `workdir/<course-name>/transcript.json`
- `workdir/<course-name>/course_metadata.json`

`bash`：

```bash
course-note download-subtitles "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --course-name "FastAPI 入門" \
  --subtitle-lang zh-TW \
  --workdir workdir
```

`PowerShell`：

```powershell
course-note download-subtitles "https://www.youtube.com/watch?v=2rcJdFuNbZQ" `
  --course-name "FastAPI 入門" `
  --subtitle-lang zh-TW `
  --workdir workdir
```

#### 沒有字幕時從音軌轉錄

`generate` 與 `download-subtitles` 都支援 `--transcribe-fallback`（需安裝 `pip install -e ".[whisper]"`）。當沒有符合 `--subtitle-lang` 的原生或自動字幕時，會下載音軌並用 faster-whisper **自動偵測音訊語言，保留原語言逐字稿**。這也符合 [faster-whisper 的自動語言偵測用法](https://github.com/SYSTRAN/faster-whisper#usage)。

- `--subtitle-lang en`：優先取得英文字幕；只影響下載字軌，不會強迫音軌使用英文或中文辨識，也不控制筆記輸出語言。
- `--transcribe-fallback`：找不到指定語言的字幕時才轉錄音軌；未指定此旗標則失敗。
- `--whisper-model small`、`--whisper-device auto`：分別設定轉錄模型與執行裝置，這兩個值也是預設值。
- `subtitles.vtt` 保留原語言，`transcript.json` 的 `lang` 和中繼資料的 `subtitle_language` 記錄偵測語言；`subtitle_requested_language` 則記錄下載字軌的偏好。後續 LLM 仍會依筆記風格整理成繁體中文。

英文課程範例（PowerShell）：

```powershell
course-note download-subtitles "https://www.youtube.com/watch?v=-Ggc37xLj_Y" `
  --course-name "Stanford CS329A Self-Improving AI Agents | Part 2 | Test-Time Compute Scaling" `
  --subtitle-lang en `
  --transcribe-fallback `
  --workdir workdir
```

舊版標示為 `transcribed`、但沒有 `subtitle_requested_language` 的字幕快取，會在下次 `generate` 或 `download-subtitles` 時重新取得，避免繼續沿用錯誤的語言設定，不需手動刪檔。仍須加上 `--transcribe-fallback` 才能在沒有字幕時轉錄。只重跑 `download-subtitles` 會更新字幕、逐字稿與中繼資料；已有的 `segments.json` 和 `article.md` 需再跑 `generate` 才會使用新逐字稿重建，`rewrite-notes` 仍使用原本的分段逐字稿。

### `rewrite-notes`

`bash`：

```bash
course-note rewrite-notes \
  --workdir workdir \
  --course-name "Python 資料分析" \
  --llm-backend ollama \
  --note-style near_transcript \
  --with-course-summary \
  --with-key-terms
```

`PowerShell`：

```powershell
course-note rewrite-notes `
  --workdir workdir `
  --course-name "Python 資料分析" `
  --llm-backend ollama `
  --note-style near_transcript `
  --with-course-summary `
  --with-key-terms
```

### `publish-hackmd`

若要把已產生的 Markdown 筆記發布到 HackMD，並自動把 `簡報：000243.jpg` 這類段落對應的本機圖片上傳後插入到該段落下方，可使用：

`bash`：
```bash
course-note publish-hackmd --course-name "Python 資料分析"
```

`PowerShell`：
```powershell
course-note publish-hackmd --course-name "Python 資料分析"
```

如果你已經在瀏覽器登入 HackMD，這個指令仍然需要 `.env` 或環境變數中的 `HACKMD_API` token 才能成功發布。

預設會從 `workdir/<course-name>/frames` 尋找圖片。若圖片在別的位置，可改用：

`bash`：
```bash
course-note publish-hackmd --course-name "Python 資料分析" --image-dir path/to/frames
```

`PowerShell`：
```powershell
course-note publish-hackmd --course-name "Python 資料分析" --image-dir path/to/frames
```

請先在 `.env` 或環境變數中設定：

```env
HACKMD_API=your_hackmd_token
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
它會被轉成 PySceneDetect 的 `min_scene_len`。如果兩個切點距離太近、短於這個值，系統就會傾向把它們合併，而不是切成兩個獨立片段。

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

`bash`：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --course-name "FastAPI 入門" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --scene-threshold 27 \
  --scene-edge-weight 0.2 \
  --min-segment-seconds 20 \
  --workdir workdir \
  --llm-backend ollama \
  --note-style near_transcript \
  --with-key-terms
```

`PowerShell`：

```powershell
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" `
  --course-name "FastAPI 入門" `
  --subtitle-lang zh-TW `
  --sample-interval 1 `
  --scene-threshold 27 `
  --scene-edge-weight 0.2 `
  --min-segment-seconds 20 `
  --workdir workdir `
  --llm-backend ollama `
  --note-style near_transcript `
  --with-key-terms
```

如果問題是「中間還是有片段切段失敗，而且看起來偏向切太碎、誤切太多」，建議先試這一版：

`bash`：

```bash
course-note generate "https://www.youtube.com/watch?v=2rcJdFuNbZQ" \
  --course-name "FastAPI 入門" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --scene-threshold 30 \
  --scene-edge-weight 0.0 \
  --min-segment-seconds 30 \
  --workdir workdir \
  --llm-backend ollama \
  --note-style near_transcript \
  --with-key-terms
```

`PowerShell`：

```powershell
course-note generate "https://www.youtube.com/watch?v=vXb2QYOUzl4" `
  --course-name "加快語言模型生成速度 (1/2)：Flash Attention" `
  --subtitle-lang zh-TW `
  --sample-interval 1 `
  --scene-threshold 30 `
  --scene-edge-weight 0.0 `
  --min-segment-seconds 30 `
  --workdir workdir `
  --llm-backend llamacpp `
  --note-style near_transcript `
  --with-key-terms
```

這樣改的原因是：

- `--min-segment-seconds 20 -> 30`
  先減少太短的誤切片段。
- `--scene-threshold 27 -> 30`
  讓系統不要對小幅度畫面變化太敏感。
- `--scene-edge-weight 0.2 -> 0.0`
  避免因為框線、表格、版型邊緣變動而被誤判成新場景。

### 哪些參數不影響切段

- `--sample-interval`
  只影響代表截圖是在段尾前幾秒擷取，不影響切段數量。
- `--note-style`
  只影響 LLM 改寫筆記的風格，不影響切段。
- `.env` 中的 `COURSE_NOTE_<PROVIDER>_MODEL`
  只影響摘要內容，不影響切段。

### 實務建議

如果你要幫使用者排查切段問題，最有用的做法是：

1. 先看 `segments.json` 和 `frames/`，確認到底是「切太多」還是「切太少」。
2. 如果是切太多，優先增加 `--min-segment-seconds`。
3. 如果是切點太敏感，再提高 `--scene-threshold`。
4. 如果是版面線條害你誤切，再降低 `--scene-edge-weight`。

## Workdir 結構

`generate` 會把 `--workdir` 當成課程工作區根目錄，每個 `--course-name` 會對應到一個固定子資料夾。相同 course name 會共用同一份 `video.mp4` 與 `subtitles.vtt` 快取，但 `frames/`、`scenes.json`、`segments.json`、`llm_outputs.json` 會在每次執行時重建。

同一個 `course-name` 如果拿去跑不同 URL，程式會直接報錯，避免誤用舊快取。

```text
workdir/
  Python 資料分析/
    course_metadata.json
    article.md
    video.mp4
    subtitles.vtt
    transcript.json
    frames/
    scenes.json
    segments.json
    llm_outputs.json
```

## 常用
李宏毅教授課程：
```
course-note generate "https://www.youtube.com/watch?v=fDQaadKysSA" `
  --course-name "加快語言模型生成速度 (2/2)：KV Cache" `
  --subtitle-lang zh-TW `
  --sample-interval 1 `
  --scene-threshold 15 `
  --scene-edge-weight 0.3 `
  --min-segment-seconds 20 `
  --workdir workdir `
  --note-style near_transcript `
  --with-key-terms
```
