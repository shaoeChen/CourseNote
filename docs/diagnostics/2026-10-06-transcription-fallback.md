# CS329A 字幕轉錄失敗診斷

- 日期：2026-10-06。
- 執行位置：`D:\python\CourseNote`；對話起始位置為 `D:\python\TalkPath`，指令套件實際以 editable install 指向 CourseNote。
- 範圍：查明失敗與備援流程，未修改程式或既有工作區；既有未提交變更保留。
- 驗證：使用系統預設 `course-note`，原指令參數僅將 workdir 改為自動清理的暫存目錄，退出碼 1；進入 `_transcribe_and_write_vtt` 後，因 `ModuleNotFoundError: No module named 'faster_whisper'` 轉為 RuntimeError。
- 環境：系統 CLI 位於 Python313 Scripts，該 Python 未安裝 faster-whisper／ctranslate2。CourseNote `.venv` 具備兩者，實際 import 成功（ctranslate2 4.8.1）。未取得使用者原執行終端錯誤，無法確認原終端是否使用相同環境。
- 外部來源驗證：yt-dlp 查詢影片 `6YnLB0XbTnI`，標題吻合；原生字幕包含 `en-j3PyPqV-e1s`，自動字幕含 en、不含 zh-TW。字幕下載探針確實抵達轉錄備援；音訊格式模擬下載可選到格式 140，未實際下載音訊或執行模型轉錄。
- 程式行為：先處理字幕，再下载影片。轉錄備援另用 yt-dlp 下載音訊，沒有從本地 video.mp4 擷取音訊。字幕 subprocess 拋出例外時沒有 catch，故只有字幕命令成功且找不到 VTT 才會進入備援。
- 語言限制：`language.split('-')[0]` 使 zh-TW 強制成 Whisper 的 zh；英文課程應使用英文辨識，現有 CLI 未分開設定來源語音語言與筆記輸出語言。
- 本次可重現失敗的處理方式：明確使用 `D:\python\CourseNote\.venv\Scripts\course-note.exe`。此處只驗證依賴載入與備援到達，未宣告整份筆記生成成功。

## 使用虛擬環境後的影片下載失敗

- 使用者提供的新 traceback 已通過字幕處理，停在 `VideoDownloader.download`；現有課程工作區有 subtitles.vtt（595024 bytes）與 transcript.json（825188 bytes）。
- 使用相同影片下載指令重現退出碼 1，實際 stderr 是 `ERROR: unable to download video data: HTTP Error 403: Forbidden`。
- 裸 `yt-dlp` 經 PATH 指向使用者 `.local/bin/yt-dlp.EXE`（2026.07.04），並非 CourseNote `.venv` 內的版本（2026.03.17）。即使明確指定 course-note.exe，子程序仍受 PATH 影響。
- 格式模擬下載成功選到 18，但實際媒體下载 403；啟用 Node 與 ejs:github 後同樣 403，不能宣告單獨啟用 JavaScript runtime 能解決。
- 嘗試以 `uv tool run --from "yt-dlp[default]"` 使用隔離下載器，套件下載未完成即停止（退出碼 1）；未改動 CourseNote 依賴，也未驗證新版下載成功。後續可直接以前述方式加 `--refresh` 測試新版，下載完成後重跑 generate 即可復用影片與字幕。
