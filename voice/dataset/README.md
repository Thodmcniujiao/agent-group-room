# AI 娘数据集强化训练指南 (基于 RTX 5080)

本目录为四大 AI 娘的专属训练音频数据集存放处：
- `chatgpt/`：存放 ChatGPT 娘的原声音频
- `claude/`：存放 Claude 娘的原声音频
- `gemini/`：存放 Gemini 娘的原声音频
- `deepseek/`：存放 DeepSeek 娘的原声音频

---

## 1. 数据集搜集与要求

* **音源推荐途径**：
  - B 站搜索关键词：`DeepSeek娘 配音` / `四大AI娘 手书` / `AI娘 吵架` / `GPT娘 角色配音` 等热门二创视频。
  - 或者根据人设挑选你心目中符合该声线的动漫 CV / 广播剧原声。
* **音频时长要求**：
  - **1 分钟 ~ 3 分钟即可！**（GPT-SoVITS 属于少样本克隆，1 分钟高质量纯净干音的效果远好于 10 分钟带杂音的音频）。
* **格式要求**：
  - `.wav` 或 `.mp3` 均可，单声道或立体声，采样率不限（训练时会自动重采样）。
* **人声纯净度（最重要的一步）**：
  - 音频内**绝不能有背景音乐（BGM）和游戏音效**！
  - 若视频自带 BGM，请用 **UVR5 (Ultimate Vocal Remover)** 选择 `MDX-Net / VR Architecture` 模型一键分离，只保留纯人声干音。

---

## 2. 训练流程 (在 RTX 5080 上大约 5 分钟)

1. 打开 GPT-SoVITS WebUI 训练控制台。
2. **第一步：音频切片与 ASR 自动打标**
   - 填入对应角色的数据集文件夹（如 `voice/dataset/deepseek`）。
   - 开启自动切片（切成 3~10 秒的小句子）。
   - 开启 Whisper ASR 自动转文字打标。
3. **第二步：一键三连预处理**
   - 提取文本特征、SSL（HuBERT）特征与语义 Token（在 5080 上 10 秒内完成）。
4. **第三步：模型微调（Fine-Tuning）**
   - **SoVITS 训练**：Batch Size 设为 12 或 16，Epoch 设为 8。在 5080 上约 **2 分钟** 跑完。
   - **GPT 训练**：Batch Size 设为 12 或 16，Epoch 设为 15。在 5080 上约 **2 分钟** 跑完。

---

## 3. 部署到本工作台

训练完毕后，从 GPT-SoVITS 的 weights 文件夹中找到产物：
- 将生成的 `xxxx.pth` 改名为 `deepseek_sovits.pth`，放入 `voice/models/`
- 将生成的 `xxxx.ckpt` 改名为 `deepseek_gpt.ckpt`，放入 `voice/models/`
- 挑选一句最代表声线的 5 秒音频作为 `characters/deepseek/reference.wav`

完成上述步骤后，刷新网页，DeepSeek 娘的发音就会立刻变成百分之百高保真、充满动漫情感的二创原声！
