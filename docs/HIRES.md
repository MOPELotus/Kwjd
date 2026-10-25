# Bilibili Hi-Res / final audio profile

本工程以**节目混音**为最终来源，而不是原片的原声；Windows 达芬奇 Fairlight 的 BGM -14 dB、节目 +10 dB 是用户当前的轨道调整，**不是**平台响度目标。最终 Linux 音频编码不会再擅自拉高增益，应检查削波、True Peak 与音画同步。

## Default preset

`config/project.json`:

```json
{
  "audio": {
    "preset": "bilibili_hires",
    "codec": "flac",
    "container": "mkv",
    "minimum_sample_rate": 48000,
    "minimum_bits_per_sample": 24,
    "preserve_native_rate_if_at_least": 96000
  }
}
```

Linux worker 将来自达芬奇母版的**已混音音轨**导出为 FLAC / 24-bit / 48 kHz，若母版原生采样率已达到 96 kHz 则尽量保留该原生采样率。使用 `s32` 表示 FFmpeg 内部承载 24-bit 样本的格式，同时设置 `-bits_per_raw_sample 24`；后续必须用 ffprobe 验证**实际** `bits_per_raw_sample=24`。

音频随最后一次带 ASS 字幕的 HEVC 视频一起编码：

```bash
ffmpeg -i enhanced.mkv -i clean-master.mp4 \
  -map 0:v:0 -map 1:a:0 \
  -vf "ass=filename=/ABS/PATH/S01E49.ass:fontsdir=/ABS/PATH/fonts" \
  -c:v hevc_nvenc -preset p6 -rc vbr -cq 18 -b:v 0 -pix_fmt yuv420p10le \
  -c:a flac -sample_fmt s32 -bits_per_raw_sample 24 -ar 48000 \
  -shortest S01E49.final-1080p60.mkv
```

以上是**示意**，实际压制由 `scripts/server_worker.py` 和服务器端已实测的 AI 后端负责。不要直接将这个命令代替整个补帧/超分流程。

## Required validation

```bash
ffprobe -v error -select_streams a:0 \
  -show_entries stream=codec_name,sample_rate,channels,sample_fmt,bits_per_raw_sample \
  -of json S01E49.final-1080p60.mkv
```

期望 `codec_name=flac`、`sample_rate>=48000`、`bits_per_raw_sample>=24`。如果这个条件不满足，Worker 必须停止并标记为失败，而非交付成品。

## B 站投稿限制与诚实说明

B 站公开的 Hi-Res 投稿说明：音频必须使用支持的**无损编码**（其中包括 FLAC、ALAC 和 PCM），**至少 48 kHz / 24-bit**；普通 AAC 不算无损音频。早期官方规则还提及 96 kHz 输出档位。2026 年 3 月的投稿用户实践记录也支持这一规格。

- 官方页面：https://www.bilibili.com/blackboard/activity-m1vS5T9l2t.html
- 2026 实践记录：https://www.bilibili.com/opus/769750507240554498

**重要区别**：

- 将低分辨率音频升采样或升位深**不会凭空提升音质**，更不会创造真正的 24-bit / 96 kHz 原生录音细节。本工程只确保输出格式合规、后续不再额外有损压缩。
- B 站是否允许本次投稿启用 Hi-Res、是否出现对应选项和标识，取决于平台当时规则、账号功能与实际投稿处理，仓库不能保证。
- 常规 HEVC+AAC 的 MP4 文件不能自动取得 Hi-Res 资格；本工作流使用 MKV+FLAC。若当前投稿入口不接受 MKV，请**先以一个短样片测试**平台，再考虑 MOV+ALAC 等同等无损格式，不要擅自转换回 AAC。
- Hi-Res 为可选的**投稿规格目标**。不要为追求标识擅自增大音量或改变既有混音。
