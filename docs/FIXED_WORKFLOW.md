# 固定后期工作流

仓库固定环境、模型、参数、设备分工和阶段顺序。正常清理后的下一次制作，
输入新的已接收母片和已审定ASS；缺少环境或引擎时恢复同一套工作栈。
改变模型、精度、双卡分工、调度或编码属于工作流修订，需要独立验证，不能作为清理恢复。

## 两份锁

- `config/workflows/native-face-v1.json`：GFPGAN1.4、RIFE4.6强类型TensorRT配方、
  模型/配方SHA、A4000修脸与T10补帧/编码、显存预算、overlap1、镜头切换、
  CAS参数、1080p60、ASS后烧、单次HEVC编码和原混音FLAC。
- `config/workflows/native-face-v1.requirements.txt`：完整Python环境的逐包精确版本，
  包括Torch/CUDA依赖、TensorRT、ONNX、OpenCV及修脸依赖。恢复后重新核对版本，
  不让无约束的传递依赖把环境升级成另一套。

驱动和物理显卡仍实际核查；不安装替代驱动或软件渲染器来绕过设备门禁。
FFmpeg实际命令、编码参数和解码日志保留在每次处理证据中，结果重新做媒体检查。

## 日常入口

当前济公传从原生25fps母版制作60fps成片：每250个原生帧生成600帧，分段时长为精确的
600/60＝10秒；整回输出数量为ceil(源帧数×60/25)，RIFE采样位置为输出索引×25/60，
不能套用旧30fps批次的“每帧翻倍”。通用库保留其他项目的30fps能力，济公传入口只准25fps。
拼接清单必须以真实输出帧数
写入duration，不能累计MKV毫秒舍入后的容器时长。ASS前的绝对时间偏移使用60fps整数PTS刻度，
避免把分数秒再除以浮点TB造成单帧跳变；模型、编码质量和原混音参数保持锁定。
已完成分段的旧任务可用`repair_concat_clock.py`在原队列排他锁下无损重拼/修正时间戳，
但必须核验连续分段身份、完整帧数/PTS、整回严格解码及压缩视频码流SHA相同，保留旧文件和来源。
此工具不重新跑神经模型、不做有损编码，也不批准最终质量。
旧30fps批次的源码、runtime及准入证明仅作历史记录。新25fps母版需新SHA、实际切镜邻帧
检查、字幕时钟派生与当前整链短测；母版未到前仅准备未激活的25fps模板，不复用旧批准。

在仓库中运行，路径指向数据根内已完成交接的母片及已封存字幕生成的ASS：

```bash
python3 scripts/run_fixed_post.py \
  --master ~/qingqingwu/post/incoming/S03E02.clean-master.mov \
  --ass ~/qingqingwu/output/ass/S03E02.ass
```

入口按母片内部编号选择已明确建立的季profile，不改默认S01配置，不暴露重新选择模型、
GPU、精度或质量参数的选项。当前S03正式生产范围仍只有第一回；其它回须先有真实交接
和相应托管准入，不因13回字幕任务存在就自行启动13回后期。

入口核对母片收据及新鲜SHA、完整字幕审校与封存、ASS绑定、当前母片样式批准和视频时钟。
源时基ASS的母片偏移只从真实已审时钟证据派生，原SRT不改。未完成时返回明确WAIT，
不会用文件存在代替通过。`--check-only`检查输入门禁，不安装依赖或启动Worker。

随后按同一锁恢复依赖、权重和TensorRT资产；已存在且身份吻合的资产直接复用。
真实整链技术/画面准入仍必需，首次清理恢复后由托管主代理补齐，不要求用户重新挑方案。
正式任务写入唯一队列项，通过用户systemd服务启动现有Worker，独立于聊天和Windows在线状态。
每回冻结自己的样式和母片批准副本；下一回的预览/批准不能改写正在处理上一回的绑定。
重复提交相同输入复用已有任务；输入身份不同、既有失败或成品均保留检查，不静默覆盖。
服务提交成功只代表已提交，实际处理进度看Worker状态和帧/分段记录。

## 清理后的恢复

`bootstrap_face_runtime.py`从版本锁恢复独立环境及原权重。
`bootstrap_rife_onnx.py`恢复哈希固定的官方源模型，作为同一TensorRT配方的输入和数值参考。
`bootstrap_fixed_trt.py`把原转换/构建配方原字节复制到新的隔离目录，使用ONNX1.20.1和
TensorRT10.13.3.9，在物理T10上编译。转换图必须复现既有SHA；FP16只用于既定神经层，
坐标、光流、网格、warp、blend和I/O保持FP32。

重新编译的引擎二进制SHA可以不同。新schema2证明绑定本次真实引擎、原配方、实际母片帧对、
数值误差和计时；旧schema1继续只接受原引擎SHA。不能伪造旧SHA、继承旧成品质量结论，
或因为缺少TRT而改用ONNX生产。数值短测之后，仍对当前母片跑真实整链短片并完整解码，
检查帧时钟、镜头切换、字体、实际画面及原混音PCM，再准入整回。

`verify_clean_recovery.py`已实际验证从空虚拟环境安装完整80包版本锁、`pip check`以及
物理A4000/T10上的两帧GFPGAN和TensorRT RIFE推理。完整证据在数据根
`post/reports/fixed-clean-recovery/`。下载遇到临时TLS错误时保留已完成环境、权重和失败日志，
按同一所有权标记续跑，未通过SHA的下载不发布；通过后只释放自己的隔离自检目录。
这项冷安装检查不替代当前母片整链和最终片质量检查。

## 托管、交付与证据

生产期间约每30分钟由纯工具巡检，必要时接续同一主对话；不持续占用模型来轮询。
云端真实额度低于2%时保存并暂停模型审校/对话，实际后台媒体处理继续；不回退点数或重置卡。
最终片需要实际质量证据以及原Windows制作端lotuspc的同SHA/媒体/PCM收据才能完成交接清理。
来源、工程、封存字幕、字体和必要审计证据按既定生命周期保留。

收到母版后，如需排查切镜串帧，可用 `scripts/inspect_master_cut_samples.py` 做有限范围取证：
显式提供原片、接收母版及其SHA、输出目录和时钟偏移假设，默认最多检查原片前240秒的6个
场景候选点，各保存母版前后9帧及其真实整数PTS，生成待实际查看的联系图。偏移假设不是
时钟批准；候选点及图片生成也不是质量通过或全回无问题的证明。该CPU诊断不修改母版、
不启动生产Worker、不改变固定双卡配方。验收仍遵循既有范围：关注郭德纲的人脸、身体和
应保留的桌子；观众等其他人物的效果不作为返工条件。

切镜修复须保留当前帧的动态背景和右下角进度条。整帧沿用邻帧即使通过帧数、PTS和
无损像素检查，也会冻结背景/进度条，不能据此准入生产。`probe_cut_frame_hold.py`仅为
隔离诊断试验，不是生产修复入口。实际修复还须验证人物/桌子局部区域、修补边缘以及
保护区域逐帧保持，再经过既定双卡链与实际最终质量检查。

2026-11-23用户将仓库统一磁盘保底调整为5GiB，各季生产、字幕取证、传输和恢复入口沿用。
先逐文件系统核算生产及临时文件峰值，再额外保留5GiB；不合并不同磁盘余量，也不省略峰值预算。
历史文档与证据中的25GiB为旧规则。已经运行且绑定源码哈希的任务保留其启动快照，完成后同步新规则。

## Jigong external delegation, 2026-11-27

The user delegated current cloud work, including actual chapter3 (S03E04) and
subsequent final QC, to the existing isolated OMP Responses provider
`jigong-relay`, `https://api.zhehentiaohe.cn/v1/responses`, model
`gpt-6.1-sol`, medium reasoning, using the existing private key. This is explicit
authorization for external model requests. No App OAuth or reset credits.
Record the true OMP UUID, response IDs and immutable actual-view evidence; never
label an external review as Codex. The quality verifier accepts this identity
only for S03E04–S03E14. All four actual QC sections, asset hashes, original PCM
equality and matching lotuspc receipt/cleanup gates remain mandatory. Update the
Windows receiver checkout before accepting these records. Unchanged media recipe,
serial Worker, per-master clock/style/calibration, source preservation and bounded
mask exceptions still apply. Private ownership: `.kwjd/production-executor.json`.
While external ownership is active the hourly tool timer records status without
queuing App model wakeups. The external controller handles production/QC/publish,
matching-receipt cleanup and subsequent chapters; no native review agent restart.

For a masked native25 camera cut missed by the bounded visual detector, a repair
may supply `render_native_segment_repair.py` with an actually reviewed cut record
bound to the episode, exact master SHA, native frame indices and reviewer session.
Only the declared 250-frame segment is rendered with the existing fixed recipe;
its receipt records the reviewed cut evidence. Model/device/encode settings and
heuristic thresholds stay unchanged. `stage_native_segment_repair.py` must prove
unchanged compressed packets outside the patch, complete PCM identity and final
decode; actual candidate cut/join review still precedes any publication.
