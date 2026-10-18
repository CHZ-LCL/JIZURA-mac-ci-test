本目录由 plugins/song-lyrics/get-models.py 下载生成：

  Xenova/…   Whisper 模型（「AI 识曲 → 歌词」插件的本地离线转写用）
  ort/       ONNX Runtime 的 WASM 运行时（transformers.js 需要，避免依赖 CDN）

可以安全删除；删除后重新运行 python plugins/song-lyrics/get-models.py 可再次下载。
请不要提交到 git（体积大；仓库 .gitignore 已忽略 models/）。
