---
name: oral-history-studio
version: 2.3.0
display_name: 人生之书·口述历史工作室
display_name_en: Life Book · Oral History Studio
description: 口述历史工作室——为家人记录人生故事的完整链路 Skill。用户要做家人/长辈的口述历史采访或代访（"人生之书"类需求）时使用：AI
  只当策划与记录员（用户在场模式），或在用户授权下当温和的代班采访者（代访模式，像孙辈一样陪长辈聊）。采访/聊天后把录音转写稿或对话逐字稿整理成第一人称人生文稿，跨次事实核对，自动备好下一轮问题清单，逐步累积成书。触发词：口述历史、人生之书、代访、采访家人/父母/祖辈、记录人生故事、整理采访文稿、家传。
description_zh: 为家人记录人生故事的完整链路：AI
  采访前出问题清单当抓手，采访后代访陪聊，采访后把录音转写稿或对话逐字稿整理成第一人称人生文稿，跨次事实核对（前后说法有出入自动提醒核实），自动备好下一轮问题清单，逐步累积成书。含豆包采集端提示词，长辈用豆包聊、你在
  WorkBuddy 成书。
description_en: A complete oral-history workflow for your family. AI prepares
  interview question lists, chats with elders on your behalf (gentle grandchild
  style), turns recordings or chat transcripts into first-person life stories,
  cross-checks facts across sessions, and compiles everything into a family
  book. Includes a Douyin/Doubao collector prompt so elders can simply talk.
agent_created: true
disable-model-invocation: false
---

# 口述历史工作室（oral-history-studio）

帮用户记录家人的人生故事。核心理念：**长辈的人生故事必须由长辈亲口讲出**。两种工作形态：

- **标准模式（默认）**：采访是用户与亲人的亲近时刻，AI 绝不替代采访者——AI 只做三件事：采访前出"抓手"问题清单、采访后把转写稿整理成第一人称文稿、根据新文稿备好下一轮问题清单。
- **代访模式（用户明确要求时）**：用户不在场或无法常常陪伴，授权 AI 直接与长辈语音聊天采访。AI 化身"话少、有耐心、招长辈喜欢的孙辈"：一次只问一个问题、问完安静听、每次通话只聊 4-6 个话题、说记不清就放过、说累就收；存档时机自判（约 20 分钟/15 轮/4-6 话题/长辈疲劳/用户叫停），收尾时输出逐字稿（原话照录的口语实录）+ 代访记录；发现前后说法有出入当场温和核实（"是我记岔啦？"）。方法论见 references/question-methodology.md 第六节。

链路一次次往前推进，最终汇成书。

**双端架构说明**：本 skill 是"加工端"（整理、台账、成书），与平台无关（WorkBuddy/Claude/GPT 等任何能读文件的 Agent 均可承载）。包内 assets/doubao-collector.md 是配套的采集端提示词（内含人生全景题库）——粘贴进豆包/MiniMax 等任意带实时语音的多模态产品，即可让 AI 直接语音采访长辈，产出逐字稿/访谈纪要后回流到本 skill 整理。用户问"怎么让爸妈用"或"怎么接入豆包"时，引导看 README.md 的「用法 B」。

## 链路总览（循环；每一步 AI 都介入）

```
①开书 → ②问题清单(AI) → ③线下采访(人与亲人，AI不在场)
              ↑                              │ 录音(MP3)或转写稿
              │                              ▼
      ⑥AI备下一轮问题 ← ⑤AI整理文稿+跨次事实核对(AI) ← ④转写(AI: 录音→文字)
                                        ↓
                                  用户确认细节 → 素材够了 export 成书
```

一次完整循环的对话形态：用户说"要给爸做口述历史" → AI 建书并给出问题清单（"现在这是你的采访问题"）→ 用户线下采访完，把录音 MP3 丢给 AI → AI 转写、整理、指出与此前采访的出入，请用户确认 → AI 自动备好下一轮问题清单 → 等下次采访。如此一圈一圈推进，直到 export 成书。

## 前置：确认模型配置（首次必做）

链路依赖 OpenAI 兼容接口（默认智谱 bigmodel，模型 glm-5.3-flash）。首次使用时向用户确认三项并让用户配置，**不要替用户填 Key**：

1. 接口地址 `OH_BASE_URL`（公司网关或 `https://open.bigmodel.cn/api/paas/v4`）
2. 模型名 `OH_MODEL`（默认 `glm-5.3-flash`）
3. API Key `OH_API_KEY`

配置方式（三选一）：

```bash
# 方式一：一条命令写好持久配置（推荐，写入 ~/.ohrc.json）
python3 scripts/oh_cli.py config --key xxxx --base-url https://open.bigmodel.cn/api/paas/v4 --model glm-5.3-flash
# 转写模型默认 glm-4-asr，如需更换可加：--asr-model 你的ASR模型名
# 之后随时运行 python3 scripts/oh_cli.py config 查看当前生效配置

# 方式二：环境变量
export OH_API_KEY=xxxx OH_BASE_URL=https://... OH_MODEL=glm-5.3-flash

# 方式三：直接编辑 ~/.ohrc.json
# {"apiKey":"xxxx","baseUrl":"https://...","model":"glm-5.3-flash"}
```

**兼容性说明**：文本模型只需 OpenAI 兼容（`/chat/completions` 可用）——智谱 GLM、DeepSeek、通义、Kimi、公司网关均可；转写模型需支持 Whisper 协议（`/audio/transcriptions`），如 glm-4-asr、Groq whisper、硅基流动等。若用户的转写服务无此协议（如飞书妙记、讯飞客户端），改走方式 B：先在那些工具里转出文字，再用 `session` 命令入库，效果相同。

可用无参自检：`python3 scripts/oh_cli.py config`。Key 缺失时脚本会给出明确报错，此时引导用户完成配置即可。

## 工作目录约定

每个"记录项目"（如"我爸的口述历史"）一个目录。初始化：

```bash
python3 scripts/oh_cli.py init <工作目录>
```

## 第 1 步：开故事书

一本书对应一位被访者。收集：称呼、与用户的关系、出生年份、简介（在哪里长大/做过什么/人生节点，可空）。

```bash
python3 scripts/oh_cli.py book <工作目录> "李长顺" "父亲" 1952 "陕西农村长大，后来当工人，喜欢下棋"
```

## 第 2 步：生成采访问题清单（AI 出抓手）

```bash
python3 scripts/oh_cli.py questions <工作目录> <book_id>
```

- 首次依据人物档案生成；之后每次整理完文稿会**自动刷新**（依据：历史文稿主题覆盖 + 待确认事实点）。
- 方法论见 [references/question-methodology.md](references/question-methodology.md)：人生全景题库（七大地带）、具体锚点问法、双模式追问、温柔核实。**清单必须覆盖七大地带再按人物档案具体化**，不能只围绕家人提供的两三个片段出题——清单太薄会导致采访中途没题可问。若用户对清单不满意，按该文件手工调整或补充后再给用户。

## 第 3 步：线下采访（AI 不参与）

把问题清单交给用户，提醒采访要领（同样写在方法论文件第五节）：

- 问题清单是抓手不是剧本，顺序随意、延展随意；
- 每次只问一个问题；对方答得短就换锚点，别恋战；
- 录音用任意工具（手机录音、妙记、会议软件均可）。

## 第 4 步：转写入库（两种方式）

**方式 A（推荐）——用户直接丢录音文件**（MP3/M4A/WAV/AMR 等），超限自动处理，用户无需预处理：

```bash
python3 scripts/oh_cli.py transcribe <工作目录> <book_id> 采访录音.mp3 --duration 1500 --notes "红薯面馍馍,土房"
```

- ASR 接口为 OpenAI 兼容的 `<BASE_URL>/audio/transcriptions`，模型由 `OH_ASR_MODEL` 控制（默认 `glm-4-asr`）；
- **超过 90MB 的录音不需要用户手动处理**：脚本自动压缩（ffmpeg 或 macOS 自带 afconvert，压成 32kbps 单声道 HE-AAC，语音清晰度足够）；压缩后仍超限的超长录音，自动按段切分、逐段转写、拼接成一次采访入库。全部无感完成；
- 本机既无 ffmpeg 也非 macOS 时才会提示安装 ffmpeg（`brew install ffmpeg`）。

**方式 B——用户已有转写文本**（妙记等工具导出，或手动整理）：

```bash
python3 scripts/oh_cli.py session <工作目录> <book_id> transcript.txt --notes "红薯面馍馍,土房,1986年结婚存疑" --duration 1500
```

- `--notes`：采访中的关键词/存疑点（逗号分隔），会作为补充信息交给整理提示词；
- `--duration`：采访时长（秒），用于文稿页脚。

## 第 5 步：AI 整理文稿 + 跨次事实核对 + 自动备下一轮清单

```bash
python3 scripts/oh_cli.py organize <工作目录> <book_id> <session_id>
```

这一条命令内部做三件事：

1. **整理文稿**——按 [references/manuscript-prompt.md](references/manuscript-prompt.md)（三铁律：平实/不编造/第一人称；六步走；三段式输出）。正文所有事实必须能在转写稿找到出处，绝不脑补；采访者的提问全部隐去。
2. **跨次事实核对（记忆与矛盾检测）**——AI 的"记忆"是工作簿里的**事实台账**（factLedger）：每次整理时，一个核查员角色会从转写稿提取关键事实（人名/地名/年份/数字），与台账里此前各次的说法逐项比对。同一事实出现两种说法（如"结婚年份 87 年 vs 86 年"），会在文稿后追加【与此前采访的出入，请核实】区块，注明"此前第 N 次说 A，本次说 B"；确认后的新事实会更新台账。**向用户呈现文稿时，务必把三个区块（未进正文的内容 / 事实模糊点 / 跨次出入）一并列出请其确认。**
3. **刷新下一轮问题清单**——把历史文稿节选 + 事实台账 + 待确认点 + 跨次出入一并喂给出题 AI，让"接续深挖、温柔核实、补白"三类问题自动生成。

产出 `<工作目录>/<称呼>-<session_id>.md`。**把文末两个区块呈现给用户**，请其确认；用户改动的正文如需生效，直接编辑该 md 或让用户告知修改（记录进下次采访的 notes）。

## 第 6 步：循环与成书

一次采访 = 一份文稿。第 5 步已自动生成下一轮问题清单，回到第 3 步继续。素材够了导出成书：

```bash
# 全部文稿合订（md，可再转 docx/pdf）
python3 scripts/oh_cli.py export <工作目录> <book_id> --format md
# 结构化备份
python3 scripts/oh_cli.py export <工作目录> <book_id> --format json
# 查看进度
python3 scripts/oh_cli.py status <工作目录>
```

## 用户直接要文稿、不走脚本时

用户若说"帮我整理这段采访"但不想建目录，可直接读取 references/manuscript-prompt.md，把其中的整理提示词 + 用户的转写稿拼成一次 LLM 对话完成，然后按三段式结构呈现。问题清单同理可单独生成（按方法论文件）。命令行只是链路的持久化形态。

## 常见问题

- **模型调用失败/超时**：检查 `OH_BASE_URL` 是否 OpenAI 兼容（`/chat/completions` 结尾可用）、Key 是否有效、模型名是否正确；脚本已内置 2 次退避重试。
- **输出为空 / finish_reason=length**：思考模型（glm-5.3 系等）的思考 tokens 计入 `max_tokens`，实测思考量 6k~15k，预算不足时 content 会被吃成空或截断。脚本已设 `max_tokens=32768` 并实测稳定；若用户的网关/模型思考更重，可让宿主模型代执行 AI 步骤（见下条），或继续调大该值。`thinking:disabled` 参数在部分网关上会被忽略，不能依赖。
- **宿主有自己模型时（如 WorkBuddy）**：可以完全不配 `OH_API_KEY`——脚本只负责存取与流程，AI 步骤（出题/整理/核对/转写）由宿主模型按 references 里的提示词直接执行，产出由宿主按 workbook.json 的结构写回即可。配置了 Key 时则由脚本调用所配模型，两种模式可混用。
- **转写从哪来**：优先 `transcribe` 命令直连 ASR（`OH_ASR_MODEL`，默认 glm-4-asr）；网关不支持 ASR 接口时，用妙记等工具转出文字走 `session`。
- **长录音**：不用管——`transcribe` 自动压缩（约压到 1/10 大小），压缩后仍超限的自动切段逐段转写再拼接，用户只需丢一个文件。
- **多位家人**：一个工作目录里开多本故事书即可，各书独立（各自的问题清单、文稿、事实台账互不混淆）。
- **改整理风格**：直接编辑 references/manuscript-prompt.md，脚本每次运行动态读取，无需改代码。
