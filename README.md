# Music2Mic — 游戏预设音频与实时变声

在游戏中通过“虚拟麦克风”播放音乐：

- 按快捷键（默认 **F8**）弹出 **10 槽位转盘** 选择音乐；
- 播放时**自动按住游戏说话键（PTT，默认 V）**，让队友听到音乐；
- 按停止键（默认 **`;`**）立即停止；
- 图形界面可编辑插槽（含每个插槽的显示名称，如“鼓励”“安慰”）、快捷键、输出设备、音量。
- “实时变声”页可开启 GPU 音色转换；播放预设期间自动静音变声，结束后恢复。

## 原理

1. 安装免费虚拟声卡 **VB-CABLE Virtual Audio Cable**：https://vb-audio.com/Cable/
2. 本软件把音乐输出到 **CABLE Input (VB-Audio Virtual Cable)**；
3. 在 Windows 声音设置里把默认“录音设备/麦克风”设为 **CABLE Output**；
4. 游戏中把语音输入选为 CABLE Output；播放音乐时软件自动按住说话键（V），队友即可听到音乐。

## 安装与运行

需要 Windows、Python 3.9+（本机使用 3.11.5 验证）和 VB-CABLE。安装 Python 时勾选 “Add python.exe to PATH”，并保留 tkinter 支持。

### 从 GitHub 获取完整音频与模型

本仓库包含 `musics/` 的全部预设音频以及当前 40 ms MeanVC2 模型，音频和权重由 **Git LFS** 管理。先安装 Git for Windows（包含 Git LFS），再使用 Git 克隆此仓库，并在仓库目录执行：

```powershell
git lfs install
git lfs pull
git lfs fsck
```

模型约 2.71 GiB，预设 MP3 共 21 个。下载 ZIP 时可能只有 LFS 指针，建议使用 Git 克隆；实际模型应为 MB/GB 级文件，不能是几行文字。GitHub 的 LFS 下载和存储使用仓库所有者的配额，见 [官方计费说明](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)。

首次运行会从 `config.example.json` 加载预设，音频路径相对于仓库目录解析；自己的设置保存在不上传的 `config.json` 中。原有配置优先。

### 播放器环境

首次安装时，在项目目录打开 PowerShell，使用 uv 创建独立虚拟环境并安装依赖：

```powershell
python -m pip install uv --index-url https://pypi.tuna.tsinghua.edu.cn/simple
uv venv --python python .venv
uv pip install --python .\.venv\Scripts\python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

已有可用的 `.venv` 时无需重建。双击 `launcher.bat` 启动；脚本优先使用 `.venv` 中的 Python，并申请管理员权限，出现 UAC 提示时选择“是”。也可以在 PowerShell 中直接运行，查看完整报错：

```powershell
.\.venv\Scripts\python.exe main.py
```

### GPU 变声环境（新电脑需单独安装）

需要支持 CUDA 的 NVIDIA 显卡和驱动，使用 **Python 3.11**。在项目根目录执行以下命令；本机已有该环境，无需重装：

```powershell
py -3.11 -m venv voice-changer/.venv
.\voice-changer\.venv\Scripts\python.exe -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
.\voice-changer\.venv\Scripts\python.exe -m pip install -r voice-changer/requirements-runtime.txt
.\voice-changer\.venv\Scripts\python.exe -m pip install s3prl==0.4.18 --no-deps
.\voice-changer\.venv\Scripts\python.exe -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

确认 Git LFS 已拉取模型后，按下文在主界面启用实时变声。虚拟环境、依赖下载缓存、运行日志和桌面截图不随仓库发布。上游来源和许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 使用

- **转盘键（默认 F8）**：打开/关闭转盘。转盘中按 **1-9、0** 或点击扇形选择音乐；点中央“停止”或按 **Esc** 关闭。
- **停止键（默认 `;`）**：停止播放并松开说话键。
- **说话键（默认 V）**：播放音乐时自动按住、停止时自动松开；可在界面中修改。
- 界面“输出设备”选 **Cable Input**；音量滑条实时生效；“自动按住说话”可单独关闭。
- 游戏中打开转盘时，程序会激活转盘窗口，使它接收鼠标和数字键输入；无法取得焦点时会关闭转盘并提示原因。选歌后先恢复游戏焦点，确认切回后再播放并按住说话键。如果 0.5 秒内仍未切回，会取消此次选歌，并在主界面状态栏提示原因。
- **CS2 使用 C 说话键时**：把本程序的“游戏说话键 (PTT)”也设为 **C**，勾选“自动按住说话”，然后回到 CS2 内按 F8 选歌。
- PTT 使用 Windows 扫描码发送按键，每次重新选歌都会重新按下说话键；停止时松开实际按下的键，即使期间修改过按键配置也不会松错键。实现依据：[KEYBDINPUT 扫描码说明](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput)。

## 实时变声与预设互斥播放

本机已安装 MeanVC2 和独立 CUDA 环境。关闭旧的 Music2Mic 和独立变声窗口后，双击 `launcher.bat`，在同一个窗口操作两种声音。不要同时运行 `start-voice-changer.bat`，独立窗口的声音不受本程序的预设切换控制。

1. 在“预设音频与快捷键”页把输出设备设为 **CABLE Input**。
2. 打开“实时变声”页，选择实体麦克风。目标音色默认使用 `musics/好厉害啊哥哥.mp3`，可另选参考音频。
3. 勾选 **启用实时变声**，等待显示“正在变声”。该选择、参考音频和麦克风名称会保存，下次启动自动恢复。关闭勾选会停止模型进程并释放显存。
4. 游戏语音输入使用 **CABLE Output**。平时通过游戏自己的说话键发送变声；预设仍使用原有自动 PTT 设置。

| 操作 | 传入虚拟麦克风的声音 |
| --- | --- |
| 平时、只打开或关闭 F8 转盘 | 变声后的麦克风 |
| 在转盘选中预设、点击插槽播放 | 先静音并排空变声输出，再播放预设 |
| 切歌、单曲循环 | 持续只播放预设 |
| 预设自然结束、按停止键或转盘中央停止 | 恢复新的麦克风变声 |

预设期间的麦克风声音会丢弃，正在计算的旧结果和缓存也会清空，因此不会在结束后补播。暂停期间模型保持加载，恢复不需要重载。切换前会等待声卡缓冲排空，会有短暂等待；恢复也需要清掉旧麦克风缓冲并重新积累实时音频，不是无缝切换。更换输出设备会停止当前预设并重启变声，使两种声音继续共用同一个设备。

试听请戴耳机，避免扬声器的声音被麦克风再次收进来。变声日志显示在页面下方，运行报告保存于 `voice-changer/reports/launcher-*/live-report.json`。普通播放器仍使用根目录 `.venv`；GPU 模型运行于 `voice-changer/.venv`，不需要向播放器环境安装 PyTorch。

本次集成的测试结果、切换延迟和复现命令见 [集成验证记录](docs/voice-integration-verification.md)。

## 自检

```powershell
.\.venv\Scripts\python.exe main.py --selftest
```

自检覆盖模块导入、配置、快捷键和音频初始化，不包含实际窗口创建。

转盘焦点、PTT 按键发送与失败提示的回归测试（无需安装额外的测试依赖）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

实际桌面按键收发测试：

```powershell
.\.venv\Scripts\python.exe tests/gui_ptt_smoke.py
```

桌面测试会临时打开并聚焦测试窗口，用静音 WAV 验证转盘选歌后目标窗口收到 C 的按下、持续按住和松开。如果期间切到其他应用，会单独标明焦点变化并检查 Windows 中的按键是否已释放。测试使用临时配置，结束后自动关闭测试窗口；它不替代 CS2 内的语音验证。

2026-09-12 已在本机 Steam CS2 的本地地图实测：F8 后鼠标选歌、数字选歌、重复选择同一首歌均能激活游戏语音标识，停止键能释放 C；通过 `launcher.bat` 管理员启动后也已复测。详细记录见 [VERIFICATION.txt](VERIFICATION.txt)。

## 常见问题

- **双击启动出现“不是内部或外部命令”或路径语法错误**：`launcher.bat` 必须保存为 **CRLF（Windows）换行**。本机已复现：含中文注释的脚本保存为 LF 时，CMD 会将部分文本截断并误当成命令执行；仅改回 CRLF 即可恢复。编辑启动脚本时请保留 CRLF。
- **音频设备初始化失败**：确认已安装 VB-Cable，并在界面中重新选择输出设备。
- **游戏内不显示转盘**：若游戏是“独占全屏”（DirectX 直接渲染，任何普通窗口都无法悬浮其上），请把游戏改为“无边框窗口化 / 窗口化全屏”；转盘打开期间本程序会周期性强制置顶，以对抗个别游戏抢回焦点。
- **快捷键无效**：若游戏以管理员身份运行（常见于带反作弊的游戏），普通权限程序收不到全局按键，请右键 `launcher.bat` → “以管理员身份运行”。
- **转盘选歌后没有触发游戏说话键**：本机 CS2 实测发现，旧版转盘虽然可见，但前台窗口仍是游戏，鼠标被锁在屏幕中心，点击实际命中了转盘中央“停止”，数字键也没有交给转盘，因此没有执行播放和按住 C。现在会在打开时真正激活转盘，选歌后再恢复游戏焦点。更新后请关闭旧的 Music2Mic 再重新启动。窗口激活实现参见 [SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow) 和 [AttachThreadInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-attachthreadinput) 文档。如果游戏语音标识已亮起但仍无音乐，再检查游戏语音输入是否选为 CABLE Output。
- **如何判断按键是否发送**：状态栏“说话键按下请求已发送”表示 Windows 接受了发送请求，不能单凭它判断游戏内语音已开启。发送被拒绝或按键无效时，会显示“自动说话失败”及原因。程序会在项目目录写入 `ptt.log`，仅记录自身发送的 PTT 事件、目标前台进程 ID 和发送结果，不记录其他键盘输入；日志达到 256 KiB 后轮换并保留一份备份。`sent=1` 表示请求被接受，C 键扫描码为 `0x2E`，`flags=0x08` 为按下、`0x0A` 为松开。返回值含义参见 [SendInput 文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)。
- **格式支持**：mp3 / wav / ogg / flac（pygame/SDL_mixer 支持范围）。
- 自动按住说话键、全局快捷键属于按键模拟，请确认目标游戏允许此类辅助工具后再使用。

## 本机预设（2026-08-26）
- 已安装 VB-CABLE（官方 VBCABLE_Driver_Pack45），config.json 输出设备已预设为 "CABLE Input"；
  可在界面“输出设备”下拉框中随时更换。
- 若曾安装又卸载过 Voicemeeter，其 VAIO 音频端点可能残留，重启 Windows 后自动清除，不影响本软件。
- 游戏中语音输入请选 “CABLE Output”（或把 Windows 默认录制设备设为 CABLE Output）。
## 循环播放
- 界面勾选“循环播放”后单曲循环，持续播放并持续按住说话键（声音不中断）；停止键 `;` 随时结束。
- 播放结束判定已做抗波动处理：连续两次检测到未播放才判定结束，避免瞬时卡顿导致误停。
