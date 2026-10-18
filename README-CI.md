# JIZURA macOS 云端构建仓

这个仓库是**自动生成的瘦身构建仓**（由主仓库 `exe-build/make-ci-repo.py` 生成），
只用于在 GitHub Actions 的免费真机（`macos-14` = Apple Silicon / arm64）上
构建 **JIZURA.app**。

## 一、建仓与推送

建议在 GitHub 新建**公开（public）**仓库：构建内容本来就来自公开的 JIZURA 项目，
且只有 public 仓库的 Release 才能用加速镜像下载（见第三节）。
仓库名随意，例如 `jizura-mac-ci`。

推送按下面顺序尝试（大仓库推送常中途断流，附加设置顺手做一下）：

    git config --global http.version HTTP/1.1
    git config --global http.postBuffer 524288000

### 方式 A：直接推（网络能通时最省事）

    git remote add origin https://github.com/<你的账号>/<仓库名>.git
    git push -u origin main

### 方式 B：有代理软件时

    git config --global http.proxy http://127.0.0.1:7890   # 端口换成你自己的
    git push -u origin main
    git config --global --unset http.proxy

### 方式 C：SSH 走 443 端口（不挂代理也常能通）

    ssh-keygen -t ed25519 -C "ci@local"          # 生成密钥
    #   把 %USERPROFILE%\.ssh\id_ed25519.pub 的内容加到
    #   GitHub 网页 Settings → SSH and GPG keys
    #   在 %USERPROFILE%\.ssh\config 写入：
    #   Host github.com
    #     HostName ssh.github.com
    #     Port 443
    #     User git
    ssh -T -p 443 git@ssh.github.com             # 测试，应显示 Hi <账号>!
    git remote set-url origin git@github.com:<你的账号>/<仓库名>.git
    git push -u origin main

### 方式 D：Gitee 中转（GitHub 主动来拉，网络受限时最稳）

    # 1) 在 gitee.com 新建公开仓库，然后推过去（国内速度很快）：
    git remote add gitee https://gitee.com/<你的Gitee账号>/<仓库名>.git
    git push gitee main
    # 2) 浏览器打开 https://github.com/new/import
    #    Source repository URL 填 Gitee 仓库地址，点 Import（GitHub 服务器自己拉取）
    # 3) 导入完成后：Actions → Build JIZURA.app (macOS arm64) → Run workflow

## 二、首次检查（要发 Release 就必须做）

仓库 → Settings → Actions → General → Workflow permissions：
选 **Read and write permissions** 并保存。
（只下载 Artifacts 的话可以跳过；但网络受限时 Release + 镜像才是最好用的下载方式。）

## 三、拿产物

等着 Actions 里 `Build JIZURA.app (macOS arm64)` 跑完（约 5~10 分钟），然后：

- **网络受限（推荐）**：构建成功会发布一个 Release，用加速镜像直接下载：

      https://gh-proxy.com/https://github.com/<你的账号>/<仓库名>/releases/download/mac-build-<运行序号>/JIZURA-mac.zip

  （把 gh-proxy.com 换成其他可用的 GitHub 加速服务也行。）

- **能直连 GitHub**：该次运行页面底部 Artifacts → `JIZURA-mac-arm64.zip`。

解压得到 `JIZURA-mac-<日期>.zip` —— 完整 macOS 发行包
（JIZURA.app + models/ + ffmpeg/ + 使用说明.txt）。

## 四、说明

- 构建过程自带冒烟测试：把构建出的 app 以 `--no-ui` 模式启动并访问一次页面；
  冒烟失败不会发 Release。
- 产物是**原生 arm64**、ad-hoc 签名（未公证）；首次打开需右键 - 打开。
- 想改构建逻辑，请回主仓库修改，然后重新运行 `python exe-build/make-ci-repo.py`
  并在本目录重新提交/推送（本目录会被整体覆盖）。
- Actions 分钟数：private 每月免费 2000 分钟，macOS 按 10 倍计；
  public 仓库免费无限且无额度顾虑。
