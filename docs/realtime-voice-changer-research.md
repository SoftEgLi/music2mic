# 开源实时音色转换调研

核实日期：2026-09-12。依据项目官方 README、源代码、模型仓库与音频驱动官网；本次未安装或实测推理、音色相似度及游戏延迟。

## 当前选型：MeanVC2，CUDA 模式

用户要求：用提供的短音频提取目标音色，把实时麦克风语音转换后送入 CS2；最终明确选择 GPU 模式，并希望控制显存占用。综合公开的参考音频转换、实时实现和 Windows 路由能力，建议先试 **MeanVC2 的 40ms 模型 + CUDA**。这是文档与代码层面的选型，尚未完成本机部署或效果验证。

官方提供源码、模型权重与 Windows CPU 可执行程序，README 及模型卡声明 Apache-2.0。模型支持无需针对目标说话人重新训练的音色转换。[官方项目](https://github.com/ASLP-lab/MeanVC2)、[官方模型卡](https://huggingface.co/ASLP-lab/MeanVC2)

### GPU 路径与资源边界

官方 Windows 下载页中的 EXE 是 **CPU-only**；用户选择 GPU 时应使用源码入口 `runtime/run_rt.py` 的 `--device cuda`。该参数将转换网络、声码器和音色编码器放到 CUDA，代码中的 ASR 编码器仍在 CPU，因此 CPU 也有负载。不能把 CPU EXE 改名当作 GPU 版。[Windows 分发说明](https://github.com/ASLP-lab/MeanVC2#-standalone-executables)、[运行代码](https://github.com/ASLP-lab/MeanVC2/blob/main/runtime/run_rt.py)

本机只读检测：GPU 为 **NVIDIA GeForce RTX 4060，8188 MiB 显存**，CPU 为 **Intel Core i5-13400F**。查询时 GPU 已用 1784 MiB、利用率 4%；这只是查询瞬间的状态，不能代表 CS2 对局时的余量。

官方未公布该 CUDA 入口的峰值显存，本次不能给出“只占 1GB/2GB”之类保证。论文所说 18M 参数不等于完整程序规模，此外还加载 WavLM 等组件。后续应在加载音色、持续变声、CS2 同时运行三个阶段分别测显存和帧率。[模型结构及资源说明](https://github.com/ASLP-lab/MeanVC2)、[音色编码器代码](https://github.com/ASLP-lab/MeanVC2/blob/main/runtime/src/speaker.py)

论文的约 **110 ms** 是 AMD EPYC 7542 单核单线程测得的整条模型流水线首包延迟，不能视为本机游戏通信的实测延迟。当前公开 Python 入口还固定使用 2560 个 16kHz 样本的音频块，即 160 ms；这与模型的 40 ms 分块概念不同，直接运行也不能保证达到论文数字。[论文实验设置及表 1](https://arxiv.org/html/2606.09050v1)、[音频回调代码](https://github.com/ASLP-lab/MeanVC2/blob/main/runtime/run_rt.py)

### 用户参考录音

本地文件：`E:\Code\MyProgram\music2mic\musics\好厉害啊哥哥.mp3`。用本机 ffprobe 读取：**1.724 秒、44.1kHz、单声道、128kbps、27584 字节**。本次只检查格式与时长，未试听、未提取音色，也未修改原文件。

这段样本很短，可先用于零样本转换的试验；官方未为 MeanVC2 给出可保证相似度的最短参考时长。若试音不够像，建议收集同一说话人更多清晰、无背景音乐的独立语音，例如合计 5–15 秒；这是试验建议，不是该项目的硬性门槛。简单重复这 1.724 秒不会增加新的音色信息。

安装依赖和权重后，在 MeanVC2 的 `runtime` 目录下可按实际文件路径使用以下源码入口；下面是经过参数核对的待验证命令，不表示已经执行。参考 MP3 可先转换成 16kHz 单声道 WAV，以匹配官方示例的格式。

```powershell
python run_rt.py --mode realtime --model 40ms --device cuda --target-spk 'E:\Code\MyProgram\music2mic\musics\reference.wav'
```

`reference.wav` 是说明用的目标路径，当前尚未创建。源码支持 `--target-spk`，但 CLI 没有公开输入/输出设备参数，`run_realtime` 使用默认设备；后续部署需设置正确的默认设备，或在启动包装中传入设备编号。[参数与设备选择代码](https://github.com/ASLP-lab/MeanVC2/blob/main/runtime/run_rt.py)、[参考音频读取代码](https://github.com/ASLP-lab/MeanVC2/blob/main/runtime/src/speaker.py)

### 接入当前 Music2Mic / CS2

当前仓库 README 记录已配置 VB-CABLE。拟用通路：**真实麦克风 → MeanVC2 → CABLE Input → CABLE Output → CS2 语音输入**。这是基于本项目记录和虚拟线缆机制制定的接线方案，尚未做 MeanVC2 与 CS2 的联合实测。[本地说明](../README.md)、[MeanVC2 音频路由说明](https://github.com/ASLP-lab/MeanVC2#-standalone-executables)、[VB-CABLE 官方说明](https://vb-audio.com/Cable/)

## 其他方案筛选

| 项目 | 对短样本与实时游戏的适用性 | 本次结论 |
| --- | --- | --- |
| [Seed-VC](https://github.com/Plachtaa/seed-vc) | 官方明确支持 1–30 秒参考音频免训练；有实时 GUI；示例延迟数百毫秒 | 可作音色效果对照与备选，已归档，详见下文 |
| [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) + [w-okada](https://github.com/w-okada/voice-changer) | 需先有训练完成的目标音色模型；RVC 官方建议至少 10 分钟低噪声语音 | 只有当前 1.724 秒录音时不优先选；w-okada 主客户端未列 Seed-VC 后端 |
| [RT-VC](https://github.com/Berkeley-Speech-Group/RT-VC) | 有研究代码和 CPU 低延迟论文，但未找到完整 Windows 发行包、预训练权重入口或明确根许可证 | 当前不优先；[权重请求 issue](https://github.com/Berkeley-Speech-Group/RT-VC/issues/5)仍开放 |

RVC 与 w-okada 仓库为 MIT，但其不同模型后端有各自许可。RVC 官方的约 170 ms、ASIO 约 90 ms 也是特定硬件/驱动条件下的报告，并未在本机复现。[RVC 官方说明](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)、[w-okada 后端与版本表](https://github.com/w-okada/voice-changer)

## Seed-VC 详细核查

Seed-VC 符合“给参考音频，再把麦克风声音实时变成该音色”的方向。官方称无需训练、参考语音可为 1–30 秒；需要更高的特定说话人相似度时可选微调。实时用途选择 v1 tiny。项目已于 **2025-11-21 归档**，应按可研究和试用的既有实现评估，不能依赖后续维护。[官方仓库及 README](https://github.com/Plachtaa/seed-vc)

### 版本、入口及参考音频

| 路径 | 适用范围 | 核实依据 |
| --- | --- | --- |
| `python real-time-gui.py` | 麦克风输入、连续音频输出；默认加载 v1 tiny 权重 `DiT_uvit_tat_xlsr_ema.pth` | [GUI 源码](https://github.com/Plachtaa/seed-vc/blob/main/real-time-gui.py) |
| `python app_vc.py` | 已提供源音频与参考音频的 Web 转换；分块输出不等于实时麦克风 | [v1 Web UI 源码](https://github.com/Plachtaa/seed-vc/blob/main/app_vc.py) |
| `python app_vc_v2.py` / `python inference_v2.py` | v2 音色、口音/情绪转换；官方现有实时 GUI 未接入此流程 | [v2 文件转换源码](https://github.com/Plachtaa/seed-vc/blob/main/inference_v2.py)、[实时 GUI](https://github.com/Plachtaa/seed-vc/blob/main/real-time-gui.py) |

GUI 可选择 WAV、MP3、FLAC 等参考文件。其代码实际按 `Max prompt length` 截取开头，默认 3 秒、滑块范围 1–20 秒；因此“支持 1–30 秒参考”不表示实时模式总会使用整个文件。建议先用开头即有清晰单人讲话的样本试听，再决定是否延长参考。GUI 可分别选输入、输出设备，并用 `Start Voice Conversion` 启动。[GUI 源码](https://github.com/Plachtaa/seed-vc/blob/main/real-time-gui.py)

### 硬件与延迟

官方强烈建议实时模式使用 GPU；RTX 3060 Laptop 的示例测试报告延迟 **430 ms**、每块推理 **150 ms**，使用 10 步、CFG 0.7、参考 3 秒、Block 0.18 秒、Crossfade 0.04 秒、左上下文 2.5 秒、右上下文 0.02 秒。官方要求推理时间小于 Block Time，并说明游戏等 GPU 负载可能拖慢推理。[官方实时说明及基准](https://github.com/Plachtaa/seed-vc#usage%EF%B8%8F)

README 同时概述约 300 ms 算法延迟加约 100 ms 设备延迟，并给出算法延迟近似式 `2 × Block Time + Extra context (right)`。这些是不同概述/测试口径，不能把 150 ms 的每块推理耗时当成用户听到的总延迟。按其表格参数和“约 100 ms”设备延迟计算为约 480 ms，与表格 430 ms 并非精确一致；因此只能按数百毫秒量级预期，实际还需测量音频驱动和游戏通信链路。[官方延迟说明](https://github.com/Plachtaa/seed-vc#usage%EF%B8%8F)

源码虽然有 CPU 设备回退，实时处理中的非 MPS 分支仍调用 CUDA Event 与 synchronize；据此不能承诺原版实时 GUI 在纯 CPU 上可用。README 未给明确最低显存门槛，本次也未实测显存。[实时 GUI 源码](https://github.com/Plachtaa/seed-vc/blob/main/real-time-gui.py)

### 接入 Windows 游戏

按官方推荐，可使用 VB-CABLE 路由到虚拟麦克风。具体接线为：

```text
真实麦克风 → Seed-VC 输入
Seed-VC 输出 → CABLE Input（播放设备）
CABLE Output（录音设备）→ 游戏的麦克风输入
```

VB-Audio 明确说明虚拟线缆会把其播放端输入转发到录音端输出；游戏需能选择该录音设备。VB-CABLE 是另一个以 donationware 方式提供的驱动，本笔记的“开源”指 Seed-VC 本身。[Seed-VC 推荐路由](https://github.com/Plachtaa/seed-vc#usage%EF%B8%8F)、[VB-CABLE 官方说明](https://vb-audio.com/Cable/)

### 安装与维护判断

官方建议 Python 3.10，Windows/Linux 使用 `pip install -r requirements.txt`，随后在仓库目录运行 `python real-time-gui.py`。初次启动需下载相关模型。当前官方 Hugging Face 文件目录仍列出 tiny 权重与配置，但本次未实际下载加载。[官方安装说明](https://github.com/Plachtaa/seed-vc#installation)、[官方模型文件](https://huggingface.co/Plachta/Seed-VC/tree/main)

现有 requirements 同时含 PyTorch nightly CUDA 12.6 索引与 `torch==2.4.0` 等固定版本。归档后直接安装能否完整解析依赖仍需在独立环境验证，不能据 README 命令承诺一键成功。[requirements.txt](https://github.com/Plachtaa/seed-vc/blob/main/requirements.txt)

代码 LICENSE 与官方模型卡均标为 **GPL-3.0**。[代码许可证](https://github.com/Plachtaa/seed-vc/blob/main/LICENSE)、[模型许可证](https://huggingface.co/Plachta/Seed-VC)

截至核实日，main 提交列表最新为 2025-04-18 的 `d334fb7`。本次在官方 README、提交记录与作者主页中未找到可确认的官方后继或迁移指向；这不等于不存在社区分支。[提交记录](https://github.com/Plachtaa/seed-vc/commits/main/)、[作者主页](https://github.com/Plachtaa)
