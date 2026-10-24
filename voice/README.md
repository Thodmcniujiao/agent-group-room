# AI 娘声音库与文字转语音工作台 · Voice Studio

为四大 AI 娘（**ChatGPT 娘、Claude 娘、Gemini 娘、DeepSeek 娘**）提供角色预设与文字转语音（TTS）。快速引擎使用需要联网的 Edge TTS；离线合成需要另行部署 GPT-SoVITS 和相应模型。

源码安装：先在项目根目录运行 `./Install.ps1`，再运行 `.\.venv\Scripts\python.exe -m pip install -r voice\requirements.txt`。随后进入 `voice` 目录启动本模块。本仓库包含服务、界面、角色预设和示范音频，运行库、训练数据及模型按需在本机准备。

---

## 🚀 你现在要做什么（极简 2 步上手）

完成上述依赖安装后，可按以下步骤启动：

### 第一步：双击启动发音器
在 PowerShell 中运行以下命令，或在文件夹中双击运行脚本：
```powershell
.\Start-VoiceStudio.ps1
```
*启动后会自动在浏览器中打开工作台界面：`http://127.0.0.1:5005`。*

### 第二步：体验四大 AI 娘发音
1. 在顶部卡片中点击 **ChatGPT 娘 / Claude 娘 / Gemini 娘 / DeepSeek 娘** 进行切换。
2. 点击卡片底部的 **🔊 试听人设音**，试听各角色的经典声线。
3. 点击 **填入角色经典台词**，或在输入框中打入任意你想让她说的话。
4. 点击 **✨ 合成语音并播放**，即可听到对应的声音（秒级生成并可下载保存）。

---

## 🎙️ 四大 AI 娘的声音与人设定义

| 角色 | 二创人设标签 | 声线特征 | 专属台词范例 |
| :--- | :--- | :--- | :--- |
| **ChatGPT 娘** | 知性少御 · 团队长女 · 优雅秘书 | 温婉端庄、知性清晰、充满包容感与可靠感的大姐姐音 | *“我是 ChatGPT。今天有什么项目或疑问需要我为您分析吗？请放心交给我吧。”* |
| **Claude 娘** | 文学少女 · 哲思诗人 · 细腻严谨 | 清澈书卷气、尾音轻柔、克制而富有诗意的文艺少女音 | *“文字中蕴含的思考与温度，往往比答案本身更动人。很高兴能与您一同探讨。”* |
| **Gemini 娘** | 元气极客 · 双子灵动 · 活力 JK | 节奏轻快、思维跳脱、高辨识度的元气少女音 | *“快看快看！我又发现了一个超级酷炫的新点子！要不要现在就和我一起动手试试看？”* |
| **DeepSeek 娘** | 工科学霸 · 微无口理科娘 · 傲娇反差萌 | 清冷平调、略带慵懒低调、务实理性的学霸吐槽音 | *“显存优化已完成，算力消耗降低 80%。……别误会，我只是不想浪费本机的 5080 显卡而已。”* |

---

## 🧠 RTX 5080 算力与双引擎架构

本系统采用**双引擎智能调度架构（Dual-Engine Architecture）**：

```
                    ┌───────────────────────────┐
                    │  用户输入文字 / 角色选择    │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │    Voice Studio 调度层     │
                    └──────┬─────────────┬──────┘
                           │             │
        (检测到本地 9880 端口)│             │(默认免等待 / 自动降级)
                           ▼             ▼
              ┌──────────────────┐  ┌──────────────────┐
              │  RTX 5080 离线   │  │   内置快速合成   │
              │  GPT-SoVITS 深度 │  │  高保真拟人引擎  │
              │     克隆引擎     │  │  (开箱即用零显存) │
              └──────────────────┘  └──────────────────┘
```

1. **快速引擎（Edge TTS，需联网）**：
   - **特点**：无需本机显存，通过 Edge 在线语音服务合成，提供四个角色的音调（Pitch）、语速（Rate）与音色预设。
2. **RTX 5080 离线 GPT-SoVITS 深度克隆（进阶开启）**：
   - **特点**：充分释放 RTX 5080 算力，加载特定二创配音的深度神经网络权重（`.pth` 与 `.ckpt`）。
   - **开启方法**：
     1. 下载 GPT-SoVITS 整合包（建议放置在 D盘或 E盘等非 OneDrive 路径）。
     2. 启动 API 服务：`runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880`。
     3. 将你训练或收集的四个角色模型放入 `voice/models/` 目录。
     4. Voice Studio 页面顶部的指示灯会自动点亮为 **🟢 绿色**，发音时将全自动转由 5080 进行深度克隆！

---

## 💻 命令行与代码调用

除了网页工作台外，本模块还可以直接在命令行中调用，或供其他 Python 程序集成：

### 命令行调用：
```powershell
# 让 DeepSeek 娘说话并保存为 test.mp3
.venv\Scripts\python.exe voice\speak.py -r deepseek -t "算力指标正常，代码已成功构建。" -o voice\output\ds.mp3

# 让 Gemini 娘以 1.2 倍语速发音
.venv\Scripts\python.exe voice\speak.py -r gemini -t "今天天气真好，去探索新项目吧！" -s 1.2
```

### Python 脚本集成（接入群聊室）：
```python
from pathlib import Path
from voice.engine.router import VoiceRouter

router = VoiceRouter()
result = router.speak(
    character_id="claude",
    text="很高兴与您在协作室相遇。",
    engine_type="auto"
)
print("音频已生成至:", result["file_path"])
```

---

## 📁 目录结构说明

- `Start-VoiceStudio.ps1`：一键启动桌面控制台与发音器界面。
- `server.py`：本地 Web/API 服务后端（基于轻量 Bottle）。
- `speak.py`：CLI 命令行发音小工具。
- `web/`：现代化前端交互界面（纯离线原生运行）。
- `characters/`：四大 AI 娘的专属配置（`character.json`）与示范音频（`sample.mp3`）。
- `engine/`：双引擎架构（EdgeEngine 与 GPTSoVITSEngine 桥接）。
- `models/`：存放 5080 离线微调模型的目录（`.pth` 与 `.ckpt`）。
- `output/`：生成的所有音频自动保存在此。
- `scripts/Setup-GPTSoVITS.ps1`：5080 离线大模型连接诊断向导。
