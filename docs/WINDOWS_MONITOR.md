# Windows 云端进度监测窗

用户需要可见监测窗时，以已有 Python 的 pythonw.exe 启动
`scripts/jigong_monitor_window.py --root D:/qingqingwu --config D:/qingqingwu/.kwjd/managed-receiver-config.json`。
只读SSH抓取每20秒一次，不启动模型、Worker、OCR、清理或额度查询；关闭窗口不影响云端和独立接收器。
所有控制台子进程使用CREATE_NO_WINDOW，不为轮询弹出终端。可见监测窗本身是用户请求的界面。

显示首回后期状态、匹配当前母版的分段回执百分比和当前顺序处理段、可用的实际帧速/剩余时间；
校准片段和其他母版不混入进度。缺少帧计数显示未知，不按耗时估算完成率。
字幕分别显示当前回文字/边界条目比例以及13回SRT封存和ASS回执数，不能将文字核对100%等同封存。
上传/下载按真实接收记录和字节数，实际质量验收仍使用原有门禁。

GPU、各媒体区域可用空间和最近巡检额度仅作为状态显示。断网保留上次成功数据并明确标注，
不显示成新快照，不触发任务恢复或自动重启。
本机最新快照与错误日志分别保存在`.kwjd/jigong-monitor.json`、`.kwjd/jigong-monitor.stderr.log`，不进Git。
