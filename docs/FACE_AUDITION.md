# 有界人脸恢复实测样片

用户于2026-10-28授权对几分钟样片逐个实测候选效果与速度。此实验不修改生产默认、不重跑整集、不替换正式E49。

## 对照合同

统一读取已交付的E49成片，SHA-256为 `f2bae90ba1b50bc7953867433c25b6a70be233828f14257fe646c975e8b4d3fc`。按60–120、1700–1760、2800–2860秒顺序拼接，共180秒、25fps、4500帧。原母片仅有局部短片段和留存PNG，无法覆盖这三段，因此所有视频候选都是成片上的增量处理。60fps降至25fps不等于找回原始母片；测速也不代表原母片生产ETA。

Windows负责baseline/CAS/crisp；Linux负责GFPGAN1.4、CodeFormer fidelity0.5和0.8。共同采用HEVC NVENC Main10/p6/hq/CQ16及原声FLAC24-bit/48kHz，不加增益，不再次补帧。RGB/full到YUV/Rec709/tv显式指定矩阵及色彩标签。模型输入和融合使用BGR8，10-bit输出格式不表示模型恢复了10-bit精度。后续对照应保留此限制。

仅人脸采用512×512恢复，背景不超分；共用facexlib0.3.0的RetinaFace ResNet50、resize640、眼间距阈值5和ParseNet融合。全部检测到的人脸均处理；每帧记录检测数量、成功恢复数量、改变像素数，无脸帧原样通过。遇到推理异常立即失败，不把失败帧静默退回原图后声称成功。GFPGAN固定噪声，CodeFormer使用adain；这些参数全部记入报告。

另外对留存原母片 `source_60.png`、`source_1700.png`、`source_2800.png`各处理三种模型，共9张PNG，明确标注原母片静帧；它们不是三分钟视频的基准来源。

## 来源与独立环境

- [GFPGAN官方源码](https://github.com/TencentARC/GFPGAN)，权重为官方release的GFPGANv1.4。
- [CodeFormer官方源码和权重](https://github.com/sczhou/CodeFormer)，包括检测和解析权重。
- 本地报告保存源码commit、模型SHA、下载URL、环境版本和准备时间；权重、专有字体、视频、PNG、机器状态均不提交Git。
- 独立 `post/face-venv` 只读引用现有Torch2.6.0+cu126运行库，另装匹配的torchvision0.21.0+cu126和自身依赖。不改系统CUDA、驱动、生产或OCR环境。
- BasicSR1.4.2与新版torchvision的兼容仅通过导入时将`torchvision.transforms.functional_tensor`映射到官方`functional`解决，供其`rgb_to_grayscale`使用；不修改模型权重或生产包。

## 执行与审计

`scripts/render_face_samples.py`限制最多3段、每段不超过60秒、总长不超过180秒；拒绝覆盖旧目录或SHA不匹配的输入，独占自身实验锁，流式传输全帧，不落盘整段PNG。执行前保留25GiB磁盘底线并预算临时空间；CUDA分配预算还保留至少3GiB共享显存，不修改其他进程。只允许真实CUDA，不退回CPU模型推理。

先以同一短片比较T10和A4000的实际FP32吞吐，并可在实测支持的A4000上比较TF32或仅恢复/解析网络的FP16 autocast（检测器保持FP32）；未经实际验证不据设备名推测性能，不直接添加全长精度变体。避开Windows滤镜实验，正式实测串行执行。温控阈值取实读最大工作温度减2°C并限制最高95°C，恢复阈值再低4°C；两秒检查一次并记录暂停。实读A4000最大工作98°C、T10为87°C，不改驱动或硬件阈值。

示例（具体Python路径、工具目录和输出目录由本机独立环境提供）：

```bash
post/face-venv/bin/python repo/scripts/render_face_samples.py \
  --input post/final/S01E49.final-1080p60.mkv \
  --input-sha256 f2bae90ba1b50bc7953867433c25b6a70be233828f14257fe646c975e8b4d3fc \
  --tools post/tools/face-candidates --gpu 1 --precision fp32 \
  --output post/reports/face_audition_new \
  --original-stills-dir post/cache/master-validation
```

正式帧数和实际成功恢复帧数分别报告；远景无脸检测率必须披露。准备、导入/CUDA检查、模型加载、检测、对齐、恢复、融合、流式编码总墙钟、拼接、QC均分别记录。编码进程寿命包括等待推理并与之重叠；pipe写入等待不等于独立NVENC计算耗时，不能相加编造加速比。

每段及最终样片均实际严格解码并检查帧数、帧率、时长、Main10/Rec709/tv及FLAC规格；最终样片解码PCM必须等于原输入三段依次拼接的PCM。文件SHA和原输入不变检查均保留。连续播放及嘴型、身份和闪烁仍需实际抽检，并由用户逐个观看选择；结构通过不代表观感通过。最终片保留规则不因本实验改变。

## 已完成的有界验证

同一60秒处2秒短片，所有三模型每种配置均50帧实际检测并恢复，实际编码/严格解码、颜色标签与音频PCM等价通过。A4000 FP16 autocast加float32 mask在此短测中连模型加载的有效速度为GFPGAN2.66fps、CodeFormer0.5为2.51fps、0.8为2.57fps；T10 FP32约0.93–1.49fps（其中0.8含26秒温控暂停）。短测含冷启动及首帧比较开销，不是长片ETA。

float32 mask仅在单个helper实例里编译官方函数的一处dtype替换，不改site-packages；保持同解析网络、同blur、同warp与类别表。首帧和三张原母片静帧与官方float64融合对照，当前12个真实样本最大差1个8-bit灰阶。A4000 FP32与AMP候选同一视频帧的全图最大差2–3灰阶、平均约0.005灰阶；这是差异度量，不是质量分。根代理实际查看三组原静帧及近景精度对照，尚不能证明整段时域稳定性。对应图和报告在数据根目录，不提交Git。
