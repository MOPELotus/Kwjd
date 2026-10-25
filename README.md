# Kwjd — 坑王驾到半自动视频制作流水线

Windows DaVinci Resolve + Linux GPU + Codex CLI 的双端协作工程。当前实例是《坑王驾到·卿卿误我》S01E49–E55；架构允许通过项目配置复用到其他故事。

> **状态**：仓库含已验证的本地无损裁剪脚本和可接管现有素材的协调工具；Linux AI 增强推理后端需要由服务器端 Codex 根据实际 GPU 环境安装、实测、选择，不会伪装成已完成的功能。

## 分工

- **Windows**：从原片检测白帧并无损裁切（已经完成的文件直接接管）；DaVinci Studio Magic Mask、风景+BGM 合成、Fairlight 混音；Aegisub 设计 ASS 样式；上传合成母版、取回成品。
- **Linux**：保留现有 VideOCR/Codex OCR 任务；直接复用 `~/qingqingwu/input/S01E49.mp4` ～ `S01E55.mp4` 和已生成的 `output/*.zh.srt`；负责后续字幕审校、超分、补帧、ASS 渲染和最终编码。
- **交接**：GitHub 存放共享代码，SSH/SCP 传输素材，磁盘上的 JSON 清单是持久化状态；**两个 Codex 不需要长期互相聊天**。

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
5. 不重新跑现有任务；不覆盖原片、OCR 结果、母版；不在 GitHub 提交视频、字幕素材、字体文件、密钥或缓存。
6. 下载校验前绝不删除远程唯一成品；磁盘预留默认至少 25 GiB，并按单集控制并发。

## 仓库结构

```text
AGENTS.md                  双端 Codex 约定
config/project.json        可复用实例配置（E49–E55）
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
