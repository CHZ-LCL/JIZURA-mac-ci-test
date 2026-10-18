# Third-party notices

## mp4-muxer 5.2.2 (bundled)

`vendor/mp4-muxer.min.js` is embedded in `index.html` and is used to write MP4 files.
Source: https://github.com/Vanilagy/mp4-muxer — licensed under the MIT License:

```
MIT License

Copyright (c) 2023 Vanilagy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## FFmpeg (bundled binary, GPL)

`plugins/song-lyrics/ffmpeg-tools/ffmpeg/ffmpeg.exe` is an unmodified standalone build of
FFmpeg 6.1.1 ("essentials" build by gyan.dev, distributed via the ffmpeg-static npm package /
npmmirror). FFmpeg is free software licensed under the GNU General Public License (GPL);
it is included here as a separate executable program and invoked as a subprocess. License
text: see `plugins/song-lyrics/ffmpeg-tools/ffmpeg/win32-x64.LICENSE`. Source code and
build information: https://ffmpeg.org/ and https://www.gyan.dev/ffmpeg/builds/

`plugins/song-lyrics/ffmpeg-tools/ffmpeg/ffmpeg` is the macOS (Apple Silicon, arm64)
counterpart: an unmodified standalone FFmpeg 6.1.1 build from the same ffmpeg-static b6.1.1
release, likewise distributed under the GPL as a separate executable program. License text:
see `plugins/song-lyrics/ffmpeg-tools/ffmpeg/darwin-arm64.LICENSE`.

## pywebview (bundled in the EXE builds)

The JIZURA desktop-window builds embed pywebview (https://pywebview.flowrl.com/, BSD 3-Clause
License, Copyright (c) 2014-2024 Roman Sirokov and contributors), together with
pythonnet (MIT License, https://github.com/pythonnet/pythonnet) and clr-loader
(MIT License, https://github.com/pythonnet/clr-loader), to host the app window with the
Microsoft Edge WebView2 runtime. Full license texts are included in the respective
Python packages under `exe-build/build-tools/wheels/`.

The macOS builds (JIZURA.app) use pywebview's Cocoa backend with pyobjc (MIT License,
https://github.com/ronaldoussoren/pyobjc) and the system WKWebView instead of WebView2.
License texts are included with the wheel files under `exe-build/build-tools/wheels-mac-arm64/`.

## Fonts (not bundled)

The web app loads the following typefaces at runtime from Google Fonts (https://fonts.google.com/); they are not
included in this repository. They are distributed by their authors under the SIL Open Font License 1.1:
Noto Sans JP, Noto Serif JP, Dela Gothic One, Zen Kaku Gothic New, Zen Old Mincho, Kaisei Tokumin,
M PLUS Rounded 1c, Mochiy Pop One, DotGothic16, Yuji Syuku, IBM Plex Mono, IBM Plex Sans JP.
