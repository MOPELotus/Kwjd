# Kwjd — 坑王驾到半自动视频制作流水线

Windows DaVinci Resolve + Linux GPU + Codex CLI 的双端协作工程。当前配置覆盖《坑王驾到》**第一季 S01**（片头、片尾白帧、OCR 规则共用），剧集编号从素材动态发现；**S01E49–E55 是已经启动的首次接管实例，不是节目范围上限**。

> **状态**：仓库含已验证的本地无损裁剪脚本和可接管现有素材的协调工具；Linux AI 增强推理后端需要由服务器端 Codex 根据实际 GPU 环境安装、实测、选择，不会伪装成已完成的功能。

## 分工

- **Windows**：从原片检测白帧并无损裁切（已经完成的文件直接接管）；DaVinci Studio Magic Mask、风景+BGM 合成、Fairlight 混音；Codex 制作 MiSans ASS 并用 PotPlayer 实测（Aegisub 可选）；上传合成母版、取回成品。
- **Linux**：保留现有 VideOCR/Codex OCR 任务；直接复用 `~/qingqingwu/input/S01E49.mp4` ～ `S01E55.mp4` 和已生成的 `output/*.zh.srt`；负责后续字幕审校、超分、补帧、ASS 渲染、HEVC/FLAC Hi-Res 规格最终编码。
- **交接**：GitHub 存放共享代码，SSH/SCP 传输素材，磁盘上的 JSON 清单是持久化状态；用户已授权双端 Codex 直接对话，规则变更即时写回仓库并 push/pull 同步。

## 快速开始

```powershell
# Windows PowerShell 7
cd D:\qingqingwu
git clone https://github.com/MOPELotus/Kwjd.git repo
cd repo
python kwjd.py adopt --role windows
python kwjd.py status --role windows
```

```bash
# Linux SSH shell；请勿停止正在运行的 OCR
cd ~/qingqingwu
git clone https://github.com/MOPELotus/Kwjd.git repo
cd repo
python3 kwjd.py adopt --role linux
python3 kwjd.py status --role linux
```

分别告诉两端 Codex：

- **本地**：「你是 Windows 端。先读 `AGENTS.md`、`docs/WORKFLOW.md`，执行 adopt/status，接管现有文件，不重跑已经验证的裁剪。」
- **服务器**：「你是 Linux 端。先读 `AGENTS.md`、`docs/WORKFLOW.md`，执行 adopt/status。现有 OCR 正在运行，不要干扰；在独立 `post/` 工作区实现与测试 AI 后期。」

具体命令见 [工作流](docs/WORKFLOW.md)、[接管说明](docs/ADOPTION.md)、[后端约定](docs/BACKENDS.md)。

## 绝对约束

1. **只在去片头片尾时**使用 `ffmpeg -c copy`；Magic Mask 合成、AI 增强和烧录字幕必须允许重新编码。
2. OCR 基于已经裁剪的视频，不再次扣除 30 秒；横向尽量全宽，只收紧纵向，兼容长、短及双行字幕。
3. 保持原字幕：不自动添加标点；恢复原有空格；观众衣服 Logo 不算字幕。
4. 完成达芬奇合成后再补帧/超分；**ASS 字幕最后渲染**，避免 AI 改坏字幕。
5. 不重新跑现有任务；不覆盖原片或封存 OCR；用户重新导出的同名母片以新哈希区分，不复用旧检查点；不在 GitHub 提交视频、字幕素材、字体文件、密钥或缓存。
6. 下载校验前绝不删除远程唯一成品；磁盘预留默认至少 25 GiB，并按单集控制并发。

## 仓库结构

```text
AGENTS.md                  双端 Codex 约定
config/project.json        S01 动态剧集发现、后期输出及音频规格
config/seasons/S01.json    第一季共用片头、片尾白帧与 OCR 配置
kwjd.py                    接管、状态、字幕校验与样式生成
scripts/
  cut/qqw-local-cut-upload-v2.ps1   已确认可用的裁剪/传输脚本
  qqw.ps1                 Windows SSH 交接入口
docs/
  WORKFLOW.md             完整阶段与分工
  ADOPTION.md             原地迁移、不重复工作
  BACKENDS.md             模型/FFmpeg 推理与验收契约
tests/                    不依赖 GPU 的回归测试
```

本仓库不上传任何大型媒体。真实工作数据保留在 `D:\qingqingwu` 和 `~/qingqingwu`。

## 第一季配置与 Hi-Res 投稿音频

- **动态发现**：`config/project.json` 的 `season=S01`；`config/seasons/S01.json` 固定本季片头 30 秒、片尾取首个纯白帧、OCR 横向全宽/纵向自检。不再限制 S01E49–55。新素材用 `S01E##`（可扩展 3 位数字）识别，实际文件名仍可包含节目标题。
- **已有进度接管**：`bootstrap_episodes` 里的 E49–55 仅为了首次显示当前任务，不意味着必须重跑或必须处理全部 E01–E58。执行 adopt/status 不会重新裁剪/识别。
- **最后输出音频**：默认 HEVC 1080p60 视频 + **FLAC 24-bit / 48 kHz（或母版已具备的 ≥96 kHz 原生采样率）** 封装为 MKV；不是 AAC 码率升级。B 站官方 Hi-Res 投稿规格要求无损编码、≥48 kHz、≥24-bit，投稿时还需要账号/投稿入口提供相应功能并勾选 Hi-Res，不能由本工具保证平台显示标识。
- **真实性**：从普通 AAC 或 16-bit 重新量化为 24-bit 并不会恢复原本不存在的高频/动态细节；只是保证最终混音采用无损编码并满足已公开的上传音频参数。

详情：[第一季配置](config/seasons/S01.json) · [音频导出与 B 站投稿](docs/HIRES.md) · [动态命名与迁移](docs/ADOPTION.md)。

## 当前工作约定

以 [决策记录](docs/DECISIONS.md)、[工作流](docs/WORKFLOW.md)、[回传与单集清理](docs/LIFECYCLE.md) 为准。默认直接从 DaVinci 导出高质量 HEVC Main10 + PCM 的 MOV；保留已接受的字幕残片和混音，不设置 AI 样片人工审批门槛。增强先跑，样式确认后再烧录。最终以 `第X回 回目标题.mkv` 回传，按阶段校验交接后清理不再需要的已登记中间物；母片上传SHA校验后即释放本地母片和CacheClip，Windows可准备下一集。云端后处理全托管，服务器最终片仍等Windows回传验收收据才删。
