#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oral-history-studio CLI —— 口述历史工作台链路脚本

循环链路：
  init → book → questions(AI出抓手) → 线下采访(人只管录)
  → transcribe(MP3转文字) 或 session(已有文字稿)
  → organize(AI整理文稿 + 事实台账跨次核对 + 自动备下一轮问题)
  → …循环… → export 成书

用法：
  python3 oh_cli.py init      <工作目录>
  python3 oh_cli.py book      <工作目录> "<称呼>" "<关系>" [出生年份] [简介]
  python3 oh_cli.py questions <工作目录> <book_id>
  python3 oh_cli.py transcribe <工作目录> <book_id> <录音文件.mp3> [--duration 秒] [--notes a,b]
  python3 oh_cli.py session   <工作目录> <book_id> "<转写文本文件>" [--notes a,b] [--duration 秒]
  python3 oh_cli.py organize  <工作目录> <book_id> <session_id>
  python3 oh_cli.py export    <工作目录> <book_id> [--format md|json]
  python3 oh_cli.py status    <工作目录>

模型配置（优先级：环境变量 > ~/.ohrc.json > 默认值）：
  OH_API_KEY    API Key（必填才能调用 AI）
  OH_BASE_URL   OpenAI 兼容接口地址，默认 https://open.bigmodel.cn/api/paas/v4
  OH_MODEL      文本模型，默认 glm-5.3-flash
  OH_ASR_MODEL  语音转写模型，默认 glm-4-asr（接口 <BASE_URL>/audio/transcriptions）

~/.ohrc.json 示例：
  {"apiKey":"xxxx","baseUrl":"https://your-gateway/v4","model":"glm-5.3-flash","asrModel":"glm-4-asr"}

依赖：仅 Python 3.8+ 标准库。
"""
import sys, os, json, argparse, urllib.request, urllib.error, datetime, time, re, uuid, mimetypes
import subprocess, shutil, tempfile

DEF_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEF_MODEL = "glm-5.3-flash"
DEF_ASR = "glm-4-asr"
WB = "workbook.json"
AUDIO_LIMIT = 90 * 1024 * 1024  # ASR 单次上传上限


# ---------- 配置 ----------
def cfg():
    key = os.environ.get("OH_API_KEY", "")
    base = os.environ.get("OH_BASE_URL", DEF_BASE).rstrip("/")
    model = os.environ.get("OH_MODEL", DEF_MODEL)
    asr = os.environ.get("OH_ASR_MODEL", DEF_ASR)
    rc = os.path.expanduser("~/.ohrc.json")
    if os.path.exists(rc):
        try:
            d = json.load(open(rc, encoding="utf-8"))
            key = d.get("apiKey") or key
            base = (d.get("baseUrl") or base).rstrip("/")
            model = d.get("model") or model
            asr = d.get("asrModel") or asr
        except Exception:
            pass
    return {"key": key, "base": base, "model": model, "asr": asr}


def _post(url, body, headers, timeout=180):
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(messages, temperature=0.6, max_retry=2):
    c = cfg()
    if not c["key"]:
        sys.exit("[错误] 未配置 API Key。设置 OH_API_KEY，或写入 ~/.ohrc.json（见 --help）。\n"
                 "      也可在 WorkBuddy 等宿主里直接让宿主模型执行本步骤（SKILL.md 有说明）。")
    url = c["base"] + "/chat/completions"
    payload = {"model": c["model"], "messages": messages,
               "temperature": temperature,
               # 思考模型（如 glm-5.3 系）的思考计入 max_tokens，实测思考量 6k~15k，
               # 预算 <16k 时 content 常被吃成空/截断；给足 32768 后稳定完整出字。
               "max_tokens": 32768}
    body = json.dumps(payload).encode("utf-8")
    last = None
    for i in range(max_retry + 1):
        try:
            data = _post(url, body, {"Content-Type": "application/json",
                                     "Authorization": "Bearer " + c["key"]}, timeout=600)
            content = data["choices"][0]["message"]["content"]
            if content and content.strip():
                return content
            last = "模型返回空 content（finish_reason=%s）。若为思考模型请调大 max_tokens 或禁用 thinking。" % data["choices"][0].get("finish_reason")
        except urllib.error.HTTPError as e:
            last = "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300])
            if e.code < 500 and e.code != 429:
                break  # 4xx 参数错误不重试
        except Exception as e:
            last = str(e)
        if i < max_retry:
            time.sleep(2 * (i + 1))
    sys.exit("[错误] 模型调用失败：%s\n       检查 OH_BASE_URL / OH_MODEL / Key。" % last)


def asr_transcribe(audio_path):
    """调用 OpenAI 兼容 ASR 接口把录音转成文字（multipart 上传）。"""
    c = cfg()
    if not c["key"]:
        sys.exit("[错误] 未配置 API Key，无法自动转写。可先用妙记等工具转出文字，再用 session 命令入库。")
    size = os.path.getsize(audio_path)
    if size > AUDIO_LIMIT:
        sys.exit("[错误] 录音 %dMB 仍超过 90MB 上限。" % (size // 1048576))
    mime = mimetypes.guess_type(audio_path)[0] or "application/octet-stream"
    boundary = "----oh" + uuid.uuid4().hex
    parts = []
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n%s\r\n"
                  % (boundary, c["asr"])).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio%s\"\r\nContent-Type: %s\r\n\r\n"
                  % (boundary, os.path.splitext(audio_path)[1] or ".mp3", mime)).encode("utf-8"))
    parts.append(open(audio_path, "rb").read())
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(parts)
    url = c["base"] + "/audio/transcriptions"
    last = None
    for i in range(3):
        try:
            data = _post(url, body, {"Content-Type": "multipart/form-data; boundary=" + boundary,
                                     "Authorization": "Bearer " + c["key"]}, timeout=600)
            return (data.get("text") or data.get("data", {}).get("text") or "").strip()
        except urllib.error.HTTPError as e:
            last = "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300])
            if e.code < 500: break  # 4xx 不重试
        except Exception as e:
            last = str(e)
        time.sleep(2 * (i + 1))
    sys.exit("[错误] 语音转写失败：%s\n       若网关不支持 ASR 接口（/audio/transcriptions），"
             "可改用妙记等工具转出文字后走 session 命令。" % last)


# ---------- 工作簿 ----------
def wpath(wd): return os.path.join(wd, WB)
def load(wd):
    if not os.path.exists(wpath(wd)):
        sys.exit("[错误] %s 还不是口述历史工作目录，先运行 init。" % wd)
    return json.load(open(wpath(wd), encoding="utf-8"))
def save(wd, wb):
    tmp = wpath(wd) + ".tmp"
    json.dump(wb, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, wpath(wd))
def now(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
def uid(): return format(int(time.time() * 1000), "x") + format(int(time.time()) % 97, "02d")
def parse_json_loose(txt):
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m: raise ValueError("模型未返回 JSON：" + txt[:300])
    return json.loads(m.group(0))


# ---------- 子命令 ----------
def cmd_config(a):
    """查看当前生效配置；带参数则写入 ~/.ohrc.json（环境变量优先级更高）。"""
    rc = os.path.expanduser("~/.ohrc.json")
    if a.key or a.base_url or a.model or a.asr_model:
        d = {}
        if os.path.exists(rc):
            try: d = json.load(open(rc, encoding="utf-8"))
            except Exception: d = {}
        if a.key: d["apiKey"] = a.key
        if a.base_url: d["baseUrl"] = a.base_url
        if a.model: d["model"] = a.model
        if a.asr_model: d["asrModel"] = a.asr_model
        json.dump(d, open(rc, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("✅ 已写入 %s" % rc)
    c = cfg()
    key = c["key"]
    masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("已设置" if key else "未设置")
    print("\n当前生效配置（优先级：环境变量 > ~/.ohrc.json > 默认值）：")
    print("  接口地址 OH_BASE_URL    :", c["base"])
    print("  文本模型 OH_MODEL       :", c["model"], "← 问题清单/整理文稿/事实核对")
    print("  转写模型 OH_ASR_MODEL   :", c["asr"], "← 录音转文字（需支持 /audio/transcriptions）")
    print("  API Key  OH_API_KEY     :", masked)
    if os.environ.get("OH_API_KEY") and os.path.exists(rc):
        print("  ⚠ 检测到环境变量 OH_API_KEY，它的优先级高于配置文件。")


def cmd_init(a):
    os.makedirs(a.dir, exist_ok=True)
    if os.path.exists(wpath(a.dir)):
        print("已初始化，无需重复。"); return
    save(a.dir, {"books": [], "createdAt": now()})
    print("✅ 工作目录已创建：%s" % os.path.abspath(a.dir))
    print("下一步：book %s \"<称呼>\" \"<关系>\"" % a.dir)


def cmd_book(a):
    wb = load(a.dir)
    b = {"id": uid(), "name": a.name, "relation": a.relation, "birthYear": a.year or "",
         "bio": a.bio or "", "createdAt": now(), "questionList": None, "sessions": [],
         "factLedger": []}
    wb["books"].append(b); save(a.dir, wb)
    print("✅ 故事书已开：[%s] %s（%s）" % (b["id"], b["name"], b["relation"]))
    print("下一步：questions %s %s  生成采访问题清单" % (a.dir, b["id"]))


def _material_summary(b):
    """给下一轮问题清单看的"这本书目前写到哪了"：文稿节选 + 待确认点 + 事实台账。"""
    lines = []
    for i, s in enumerate(b["sessions"], 1):
        m = s.get("manuscript")
        if m:
            lines.append("—— 第%d次采访文稿《%s》正文节选：" % (i, m.get("title") or "未命名"))
            lines.append((m.get("body") or "")[:500])
            facts = [x for x in (m.get("facts") or []) if x and x != "无"]
            if facts:
                lines.append("【需确认的事实模糊点】" + "；".join(facts[:6]))
            cons = m.get("conflicts") or []
            if cons:
                lines.append("【与此前采访的出入】" + "；".join(
                    "%s：此前说「%s」，本次说「%s」" % (x.get("topic"), x.get("old"), x.get("new")) for x in cons[:6]))
    ledger = b.get("factLedger") or []
    if ledger:
        lines.append("【事实台账】（跨次累积，出核实问题时参考）：" +
                     "；".join("%s=%s(第%d次)" % (f["topic"], f["value"], f["session"]) for f in ledger[-15:]))
    return "\n".join(lines) if lines else ""


def cmd_questions(a):
    wb = load(a.dir)
    b = next((x for x in wb["books"] if x["id"] == a.book_id), None)
    if not b: sys.exit("[错误] 找不到故事书 %s" % a.book_id)
    mats = _material_summary(b)
    role = ("你是一位口述历史的访谈策划人。我将作为家人去采访「%s」（%s%s），录下 TA 的人生故事。"
            "我不是专业记者，需要你帮我准备一份采访问题清单——它是我在采访时的抓手：我照着问就行，"
            "也可以顺着问题自由延展。AI 不会参与采访本身，采访是我和亲人的对话。"
            % (b["name"], b["relation"], ("，约" + b["birthYear"] + "年出生") if b["birthYear"] else ""))
    task = ("请生成 12 个左右的问题，要求：\n"
            "1. 大致按「童年—家族溯源—求学工作—成家—中年转折—晚年回望」排序；若已有素材，优先覆盖还没聊到的、或标记需核实的内容；\n"
            "2. 多用「具体锚点」问法（具体场景、物件、一天、一顿饭、一件旧物），少问大而空的问题；\n"
            "3. 每个问题配 hint 字段：TA 答得简短时我可以顺着往下问的延展追问，1-2 条，口语化；\n"
            "4. 素材中【需确认的事实模糊点】和【与此前采访的出入】要变成自然的核实问题放进去，别让 TA 觉得在被纠错；\n"
            "5. 全部口语化，像家里人聊天。tag 填所属人生阶段。\n\n"
            "人物背景：" + (b["bio"] or "（暂无）") + "\n\n已有素材：\n" + (mats or "（首次采访，尚无素材）") +
            "\n\n只输出 JSON：{\"questions\":[{\"q\":\"…\",\"hint\":\"…\",\"tag\":\"童年\"}]}")
    print("正在生成问题清单（%s）…" % cfg()["model"])
    txt = chat([{"role": "system", "content": role}, {"role": "user", "content": task}])
    qs = parse_json_loose(txt).get("questions", [])
    b["questionList"] = {"items": [{"q": q.get("q",""), "hint": q.get("hint",""), "tag": q.get("tag",""), "done": False} for q in qs],
                         "updatedAt": now(), "basis": "历史文稿+事实台账+待确认点" if mats else "人物背景档案"}
    save(a.dir, wb)
    print("✅ 问题清单已备好（%d 个）：" % len(qs))
    for i, q in enumerate(b["questionList"]["items"], 1):
        print("%2d.[%s] %s\n     ↳ %s" % (i, q["tag"], q["q"], q["hint"] or "—"))


def _ingest(a, wb, b, text, notes, duration):
    s = {"id": uid(), "bookId": b["id"], "date": now(), "duration": duration,
         "transcript": text, "notes": notes, "status": "review", "manuscript": None}
    b["sessions"].append(s); save(a.dir, wb)
    print("✅ 采访已入库：[%s] %s · %s · 转写 %d 字" % (s["id"], b["name"], s["date"], len(text)))
    print("下一步：organize %s %s %s" % (a.dir, b["id"], s["id"]))
    return s


# ---------- 音频预处理：压缩与自动切段 ----------
def _find_ffmpeg():
    return shutil.which("ffmpeg")


def _compress_audio(src, workdir):
    """把录音压缩为 32kbps 单声道 HE-AAC（语音清晰度足够），返回新文件路径；失败返回 None。

    优先 ffmpeg（全平台），macOS 兜底系统自带 afconvert。
    """
    dst = os.path.join(workdir, "compressed.m4a")
    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src,
                            "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k", dst],
                           capture_output=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
    if sys.platform == "darwin" and os.path.exists("/usr/bin/afconvert"):
        r = subprocess.run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aach", "-b", "32000",
                            "-c", "1", src, dst], capture_output=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
    return None


def _get_duration_sec(path):
    """探测音频时长（秒），探测失败返回 None。"""
    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            h, mnt, s = m.groups()
            return int(h) * 3600 + int(mnt) * 60 + float(s)
    if sys.platform == "darwin" and os.path.exists("/usr/bin/afinfo"):
        r = subprocess.run(["/usr/bin/afinfo", path], capture_output=True, text=True)
        m = re.search(r"estimated duration:\s*([\d.]+)", r.stdout)
        if m:
            return float(m.group(1))
    return None


def _split_audio(src, n_chunks, workdir):
    """把录音均分为 n 段（mp3/m4a，各自 ≤90MB），返回段文件路径列表。"""
    ffmpeg = _find_ffmpeg()
    ext = ".m4a" if not ffmpeg else ".mp3"
    paths = []
    if ffmpeg:
        for i in range(n_chunks):
            p = os.path.join(workdir, "part%02d%s" % (i + 1, ext))
            r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src,
                                "-f", "segment", "-segment_time", "3600",
                                "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1",
                                os.path.join(workdir, "part%02d.mp3" % (i + 1))], capture_output=True)
            break
        paths = sorted(os.path.join(workdir, f) for f in os.listdir(workdir)
                       if f.startswith("part") and f.endswith(".mp3"))
        if paths:
            return paths
    # 无 ffmpeg：mac 用 afconvert 输出分段 m4a（按 3600 秒一段粗略估算不可行——afconvert 无分段能力，
    # 此时按"先压再切不动"策略：压缩产物直接返回单段，由调用方决定是否报错）
    return []


def prepare_audio_for_asr(src, limit=None):
    """确保音频满足 ASR 上传限制：压缩优先，压完仍超限则切段。返回 (路径列表, 处理说明)。"""
    limit = limit or AUDIO_LIMIT
    size = os.path.getsize(src)
    if size <= limit:
        return [src], "原文件直接上传"
    workdir = tempfile.mkdtemp(prefix="oh-audio-")
    print("录音 %dMB 超过 %dMB 上限，正在压缩（32kbps 单声道，语音清晰度不受影响）…" % (size // 1048576, limit // 1048576))
    comp = _compress_audio(src, workdir)
    if comp and os.path.getsize(comp) <= limit:
        cs = os.path.getsize(comp)
        print("✅ 压缩完成：%dMB → %.1fMB" % (size // 1048576, cs / 1048576))
        return [comp], "已压缩（%s）" % os.path.basename(comp)
    # 压缩不可用或压完仍超限：估算时长切段
    dur = _get_duration_sec(src)
    if comp:
        src2, size2 = comp, os.path.getsize(comp)
    else:
        src2, size2 = src, size
        print("（自动压缩不可用，改为直接切段）")
    if not dur:
        # 无法探测时长：按 40MB/段保守估算
        est_chunks = max(2, int(size2 / (40 * 1024 * 1024)) + 1)
    else:
        est_chunks = max(2, int(size2 / limit) + 1)
    print("压缩后仍超限（%.1fMB），自动切段、逐段转写后拼接…" % (size2 / 1048576))
    parts = _split_audio(src2, est_chunks, workdir)
    if not parts:
        sys.exit("[错误] 无法自动压缩或切段（需要 ffmpeg，或 macOS 系统）。"
                 "请安装 ffmpeg（brew install ffmpeg）后重试，或手动切段。")
    big = [p for p in parts if os.path.getsize(p) > limit]
    if big:
        sys.exit("[错误] 切段后仍有 %d 段超过 %dMB，录音过长。请联系开发者或改用更细的分段策略。" % (len(big), limit // 1048576))
    print("✅ 已切分为 %d 段" % len(parts))
    return parts, "已切分为 %d 段" % len(parts)


def cmd_transcribe(a):
    """MP3 等录音 → (自动压缩/切段) → ASR 转文字 → 拼接 → 直接入库。"""
    wb = load(a.dir)
    b = next((x for x in wb["books"] if x["id"] == a.book_id), None)
    if not b: sys.exit("[错误] 找不到故事书 %s" % a.book_id)
    if not os.path.exists(a.audio): sys.exit("[错误] 录音文件不存在：%s" % a.audio)
    parts, note = prepare_audio_for_asr(a.audio)
    texts = []
    for i, p in enumerate(parts, 1):
        if len(parts) > 1:
            print("正在转写第 %d/%d 段（%s，%.1fMB）…" % (i, len(parts), os.path.basename(p), os.path.getsize(p) / 1048576))
        else:
            print("正在转写录音（%s，%dMB）…" % (cfg()["asr"], os.path.getsize(p) // 1048576))
        texts.append(asr_transcribe(p))
    text = "\n".join(t for t in texts if t).strip()
    if len(parts) > 1:
        shutil.rmtree(os.path.dirname(parts[0]), ignore_errors=True)  # 清理临时段文件
    if len(text) < 20: sys.exit("[错误] 转写结果太短，请检查录音是否有效。")
    notes = [x.strip() for x in (a.notes or "").split(",") if x.strip()]
    print("（%s，转写共 %d 字）" % (note, len(text)))
    _ingest(a, wb, b, text, notes, a.duration or 0)


def cmd_session(a):
    wb = load(a.dir)
    b = next((x for x in wb["books"] if x["id"] == a.book_id), None)
    if not b: sys.exit("[错误] 找不到故事书 %s" % a.book_id)
    if not os.path.exists(a.transcript): sys.exit("[错误] 转写文件不存在：%s" % a.transcript)
    text = open(a.transcript, encoding="utf-8").read().strip()
    if len(text) < 20: sys.exit("[错误] 转写文本太短，请检查文件内容。")
    notes = [x.strip() for x in (a.notes or "").split(",") if x.strip()]
    _ingest(a, wb, b, text, notes, a.duration or 0)


_ORG_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "manuscript-prompt.md")


def ai_factcheck(ledger, transcript, session_no):
    """跨次事实核对：提取本次事实 + 与台账比对找矛盾。失败时返回 (None, None) 不阻塞主流程。"""
    role = "你是口述历史项目的记录核查员，负责跨次采访的事实一致性。"
    task = ("以下是这位老人此前采访累积的【事实台账】（JSON，可能为空）和最新一次（第%d次）采访的转写稿。\n"
            "1. 从转写稿提取关键事实：人名、称谓、地名、年份、日期、数字、事件结果。每条 {\"topic\":\"主题\",\"value\":\"本次的说法\"}，最多 15 条，topic 用简短名词（如\"结婚年份\"\"老大爷姓名\"）；\n"
            "2. 与台账逐项比对：只找**同一事实出现两种不一致说法**的情况（年份对不上、人名写法不同、数字矛盾等）。台账为空则 conflicts 必须为空数组；新增事实不是冲突，不要报。\n"
            "只输出 JSON：{\"newFacts\":[{\"topic\":\"…\",\"value\":\"…\"}],\"conflicts\":[{\"topic\":\"…\",\"old\":\"此前的说法\",\"oldSession\":1,\"new\":\"本次的说法\"}]}"
            % session_no)
    try:
        txt = chat([{"role": "system", "content": role}, {"role": "user",
                    "content": "事实台账：\n" + json.dumps(ledger, ensure_ascii=False) + "\n\n转写稿：\n" + transcript}], 0.2)
        j = parse_json_loose(txt)
        return j.get("newFacts") or [], j.get("conflicts") or []
    except SystemExit:
        return None, None
    except Exception:
        return None, None


def _split_manuscript(txt):
    """把模型输出的三段式文稿拆成 (标题, 正文, 未进正文列表, 待确认列表)。

    按【…】区块头切分，避免正文里的 --- 分隔线干扰；区块标题残行不会混进条目。
    """
    m1 = re.search(r"【未进入整理文稿[^】]*】", txt)
    m2 = re.search(r"【需确认的事实模糊点[^】]*】", txt)
    body, ex_raw, fa_raw = txt, "", ""
    if m1:
        body = txt[:m1.start()]
        if m2:
            ex_raw, fa_raw = txt[m1.end():m2.start()], txt[m2.end():]
        else:
            ex_raw = txt[m1.end():]
    elif m2:
        body, fa_raw = txt[:m2.start()], txt[m2.end():]
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    title = ""
    if lines and lines[0].lstrip().startswith("#"):
        title = lines.pop(0).lstrip().lstrip("#").strip()
    while lines and lines[-1].strip() in ("", "---"):
        lines.pop()

    def _items(raw):
        out = []
        for l in raw.splitlines():
            l = l.strip()
            if not l or l in ("---", "```", "#", "##", "###"):
                continue
            if l.startswith("#"):  # 区块 markdown 标题残留
                continue
            if l.startswith("（") and l.endswith("）"):  # 提示性说明行
                continue
            l = re.sub(r"^(?:[-*]|\d+[.、)])\s*", "", l)  # 去列表序号
            if l in ("无", "无。"):
                continue
            out.append(l)
        return out

    return title, "\n".join(lines).strip(), _items(ex_raw), _items(fa_raw)


def cmd_organize(a):
    wb = load(a.dir)
    b = next((x for x in wb["books"] if x["id"] == a.book_id), None)
    if not b: sys.exit("[错误] 找不到故事书 %s" % a.book_id)
    s = next((x for x in b["sessions"] if x["id"] == a.session_id), None)
    if not s: sys.exit("[错误] 找不到采访 %s" % a.session_id)
    prompt = ""
    if os.path.exists(_ORG_REF):
        prompt = open(_ORG_REF, encoding="utf-8").read()
        prompt = prompt.split("---", 2)[-1]
    if not prompt: sys.exit("[错误] 找不到 references/manuscript-prompt.md")
    head = "被访者：%s（%s）。\n" % (b["name"], b["relation"])
    if s.get("notes"):
        head += "补充信息——采访中记录的关键词：\n" + "\n".join("%d. %s" % (i, n) for i, n in enumerate(s["notes"], 1)) + "\n"
    print("正在整理文稿（%s，三铁律/六步走/三段式）…" % cfg()["model"])
    txt = chat([{"role": "user", "content": head + "\n" + prompt + "\n\n===== 转写稿开始 =====\n" + s["transcript"] + "\n===== 转写稿结束 ====="}], 0.4)
    title, body, excluded, facts = _split_manuscript(txt)
    # —— 跨次事实台账核对 ——
    sess_no = b["sessions"].index(s) + 1  # 本次是第几次采访（按入库顺序）
    print("正在核对跨次事实台账（与此前 %d 次采访比对）…" % (len(b["sessions"]) - 1))
    no, sn = ai_factcheck(b.get("factLedger") or [], s["transcript"], sess_no)
    conflicts = []
    if no is not None:
        for f in no:
            if f.get("topic") and f.get("value"):
                b.setdefault("factLedger", []).append({"topic": f["topic"], "value": f["value"],
                                                       "session": sess_no, "date": s["date"]})
        conflicts = sn or []
        for cf in conflicts:
            # 台账里标记冲突
            for entry in b["factLedger"]:
                if entry["topic"] == cf.get("topic") and entry["value"] == cf.get("old") and "conflictWith" not in entry:
                    entry["conflictWith"] = sess_no; entry["newValue"] = cf.get("new")
    else:
        print("（事实核对跳过：模型调用不可用，不影响文稿）")
    s["manuscript"] = {"title": title or "未命名文稿", "body": body or txt,
                       "excluded": excluded, "facts": facts, "conflicts": conflicts,
                       "generatedAt": now()}
    s["status"] = "done"
    save(a.dir, wb)
    out = os.path.join(a.dir, "%s-%s.md" % (b["name"], s["id"]))
    with open(out, "w", encoding="utf-8") as f:
        f.write("# %s\n\n%s\n\n---\n\n## 【未进入整理文稿的采访内容，请确认】\n%s\n\n## 【需确认的事实模糊点与矛盾】\n%s\n"
                % (s["manuscript"]["title"], body,
                   "\n".join("%d. %s" % (i, x) for i, x in enumerate(excluded, 1)) or "无",
                   "\n".join("%d. %s" % (i, x) for i, x in enumerate(facts, 1)) or "无"))
        if conflicts:
            f.write("\n## 【与此前采访的出入，请核实】\n" +
                    "\n".join("%d. %s：此前第%d次说「%s」，本次说「%s」"
                              % (i, x.get("topic"), x.get("oldSession", 0), x.get("old"), x.get("new"))
                              for i, x in enumerate(conflicts, 1)) + "\n")
    print("✅ 文稿已生成：%s" % out)
    print("   标题：%s｜正文 %d 字｜待确认 %d 条｜跨次出入 %d 处" % (s["manuscript"]["title"], len(body), len(facts), len(conflicts)))
    for i, x in enumerate(conflicts, 1):
        print("   ⚠ 出入%d：%s——此前第%s次说「%s」，本次说「%s」" % (i, x.get("topic"), x.get("oldSession", "?"), x.get("old"), x.get("new")))
    # —— 自动刷新下一轮问题清单 ——
    print("正在根据本次文稿刷新下一轮问题清单…")
    try:
        class A: pass
        na = A(); na.dir = a.dir; na.book_id = b["id"]
        cmd_questions(na)
    except (SystemExit, Exception) as e:
        print("（问题清单刷新失败，可稍后手动重试 questions：%s）" % e)
    print("下一步：把【出入】和【需确认】带进下次采访自然核实；确认后 export 导出。")


def cmd_export(a):
    wb = load(a.dir)
    b = next((x for x in wb["books"] if x["id"] == a.book_id), None)
    if not b: sys.exit("[错误] 找不到故事书 %s" % a.book_id)
    if a.format == "json":
        out = os.path.join(a.dir, "%s-故事书.json" % b["name"])
        json.dump(b, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    else:
        parts = ["# 《%s》人生故事集\n\n%s（%s%s）\n" % (b["name"], b["relation"],
                 ("，约" + b["birthYear"] + "年生") if b["birthYear"] else "",
                 ("。" + b["bio"]) if b["bio"] else "")]
        for i, s in enumerate(b["sessions"], 1):
            m = s.get("manuscript")
            if not m: continue
            parts.append("\n\n---\n\n# %s\n\n*第 %d 次采访 · %s · 时长 %s*\n\n%s\n"
                         % (m.get("title") or ("第%d篇" % i), i, s["date"],
                            "%d分%02d秒" % ((s["duration"] or 0) // 60, (s["duration"] or 0) % 60), m.get("body") or ""))
        out = os.path.join(a.dir, "%s-人生故事集.md" % b["name"])
        open(out, "w", encoding="utf-8").write("\n".join(parts))
    print("✅ 已导出：%s" % out)


def cmd_status(a):
    wb = load(a.dir)
    print("工作簿：%s（建于 %s）" % (os.path.abspath(wpath(a.dir)), wb.get("createdAt")))
    for b in wb["books"]:
        done = sum(1 for s in b["sessions"] if s.get("manuscript"))
        ql = b.get("questionList")
        print("· [%s] %s（%s）｜%d 次采访｜%d 份文稿｜事实台账 %d 条｜问题清单：%s"
              % (b["id"], b["name"], b["relation"], len(b["sessions"]), done,
                 len(b.get("factLedger") or []),
                 ("%d 个，备于 %s" % (len(ql["items"]), ql["updatedAt"])) if ql else "未生成"))
        for i, s in enumerate(b["sessions"], 1):
            st = "已整理" if s.get("manuscript") else ("待整理（session %s）" % s["id"])
            print("    %d. %s · %d 字 · %s" % (i, s["date"], len(s["transcript"]), st))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("config", help="查看/写入模型配置（写 ~/.ohrc.json）")
    s.add_argument("--key", default="", help="API Key")
    s.add_argument("--base-url", dest="base_url", default="", help="OpenAI 兼容接口地址")
    s.add_argument("--model", default="", help="文本模型名")
    s.add_argument("--asr-model", dest="asr_model", default="", help="语音转写模型名")
    s.set_defaults(f=cmd_config)
    s = sub.add_parser("init", help="初始化工作目录"); s.add_argument("dir"); s.set_defaults(f=cmd_init)
    s = sub.add_parser("book", help="开一本故事书"); s.add_argument("dir"); s.add_argument("name"); s.add_argument("relation")
    s.add_argument("year", nargs="?"); s.add_argument("bio", nargs="?"); s.set_defaults(f=cmd_book)
    s = sub.add_parser("questions", help="AI 生成问题清单"); s.add_argument("dir"); s.add_argument("book_id"); s.set_defaults(f=cmd_questions)
    s = sub.add_parser("transcribe", help="录音文件(MP3等)→自动压缩/切段→AI转文字→入库")
    s.add_argument("dir"); s.add_argument("book_id"); s.add_argument("audio")
    s.add_argument("--notes", default=""); s.add_argument("--duration", type=int, default=0)
    s.set_defaults(f=cmd_transcribe)
    s = sub.add_parser("session", help="已有转写文本入库")
    s.add_argument("dir"); s.add_argument("book_id"); s.add_argument("transcript")
    s.add_argument("--notes", default=""); s.add_argument("--duration", type=int, default=0)
    s.set_defaults(f=cmd_session)
    s = sub.add_parser("organize", help="AI 整理文稿 + 跨次事实核对 + 刷新问题清单")
    s.add_argument("dir"); s.add_argument("book_id"); s.add_argument("session_id"); s.set_defaults(f=cmd_organize)
    s = sub.add_parser("export", help="导出成书"); s.add_argument("dir"); s.add_argument("book_id")
    s.add_argument("--format", default="md", choices=["md", "json"]); s.set_defaults(f=cmd_export)
    s = sub.add_parser("status", help="查看进度"); s.add_argument("dir"); s.set_defaults(f=cmd_status)
    a = p.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
