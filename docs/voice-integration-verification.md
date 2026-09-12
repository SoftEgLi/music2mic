# 预设音频与实时变声集成验证

本机验证日期：2026-09-12。Windows、RTX 4060、USB2.0 麦克风、VB-CABLE。

`launcher.bat` 仍运行 `main.py`，主窗口新增实时变声页。播放器通过 `VoiceController` 控制独立 CUDA 子进程；预设必须等到静音确认后才开始播放和发送自动 PTT。静音确认包含实际零输出回调和声卡排空等待。恢复会清空队列、重置转换缓存并丢弃设备里旧的输入。

## 已执行的检查

| 检查 | 结果与范围 |
| --- | --- |
| 主程序自检 | 通过，模块导入、配置、热键、设备枚举和音频初始化 |
| 根目录回归测试 | 54 项通过，包括 PTT、转盘焦点、启动静音、预设互斥、快速切换、设备切换和故障退出 |
| VoiceGate 波形测试 | 4 项通过，包括部分输出缓存、待处理队列、正在计算的旧结果和模型缓存重置 |
| 实际桌面 PTT 测试 | 临时目标窗口收到 C 的 down/up，停止后按键不再按住 |
| 真实 VB-CABLE 录音 | 8 项通过；真实 pygame 播放预设，变声端用可区分的合成音验证隔离，包含预设开始边界；未发现旧信号混入预设或恢复后补播 |
| 实际 CUDA 子进程 | 初始静音→恢复→静音→恢复→停止，全部确认，退出码 0 |
| 完整 Tk 界面与实际 CUDA 后端 | 13 项检查通过，覆盖自然结束、跨曲长循环、手动停止、连续 8 次取消/重选、关闭变声释放子进程 |
| 界面布局 | 两个标签页均已截图检查，设备和参考音频正常显示 |
| 启动脚本 | `launcher.bat` 的 CRLF 保留 |

根目录测试先执行 52 项完整测试，新增设备同名/短名歧义回归后，又执行包含这两项的 19 项界面集成测试，共 54 个不同测试通过。测试使用临时配置，未改动用户的插槽、F8、`;`、C 和输出设置。

## 实际测量与限制

- CUDA 控制测试处理 25 个音频块：平均 102.4 ms，最大 134.7 ms，块时长 160 ms；无音频欠载、丢帧或异常。切换时确实拒绝了一个仍在计算的旧结果。
- 实际双工 WASAPI 报告输入延迟 0.32 s、输出延迟 0.48 s；静音确认实测约 0.83 s。恢复时额外丢弃约 0.5 s 的旧输入。自然结束检测还需要两次 400 ms 轮询，随后才恢复；模型和音频管线也有延迟。因此这次实现保证隔离，有短暂切换等待。
- CUDA 测量的 PyTorch 已分配张量约 117 MiB、保留约 134 MiB；这不是整个进程的显存占用，启动加载峰值也更高。
- 录音仅保存合成测试音。完整界面测试没有发送游戏说话键；PTT 在独立临时接收窗口验证。本次没有重新进入 CS2 验证游戏端语音或网络延迟。

## 复现

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe main.py --selftest
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\voice-changer\.venv\Scripts\python.exe voice-changer/tools/test_voice_gate.py
.\voice-changer\.venv\Scripts\python.exe voice-changer/tools/verify_audio_integration.py cable
.\voice-changer\.venv\Scripts\python.exe voice-changer/tools/verify_audio_integration.py cuda
.\.venv\Scripts\python.exe tests/gui_ptt_smoke.py
.\.venv\Scripts\python.exe tests/gui_voice_smoke.py
```

设备测试会打开本机音频流，桌面测试会临时打开测试窗口。运行前关闭独立变声窗口；勿与用户正在进行的游戏语音测试同时运行。

详细证据：

- [虚拟声卡隔离报告](../voice-changer/results/integration-cable-report.json)
- [实际 CUDA 控制报告](../voice-changer/results/integration-cuda-ipc-report.json)
- [CUDA 实时统计](../voice-changer/results/integration-cuda-live.json)
- [完整界面集成报告](../voice-changer/results/integration-gui-report.json)

快速取消/重选产生的过期目标状态会合并，每次只等待一个静音/恢复命令，避免设备排空等待积压触发误超时。音频设备启动时按名称和 Host API 重新解析，避免热插拔后数字索引指向其他设备；输出精确名称优先，兼容短名匹配必须唯一。
