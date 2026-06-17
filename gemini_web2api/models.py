"""Model definitions and mapping from Gemini frontend JS source."""

# MODE_CATEGORY enum from 028-6eb337387583.js:
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE

MODELS = {
    "gemini-3.5-flash": {
        "mode": 1, "think": 4,
        "desc": "快速通用模型",
    },
    "gemini-3.5-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "深度思考模式，最长输出（约2万字符）",
    },
    "gemini-3.1-pro": {
        "mode": 3, "think": 4,
        "desc": "Pro 模型（需要 Cookie 才能真实路由）",
    },
    "gemini-3.1-pro-enhanced": {
        "mode": 3, "think": 4, "extra": {31: 2, 80: 3},
        "desc": "Pro 增强输出（实验性）",
    },
    "gemini-auto": {
        "mode": 4, "think": 4,
        "desc": "自动模型选择",
    },
    "gemini-3.5-flash-thinking-lite": {
        "mode": 5, "think": 0,
        "desc": "自适应深度思考",
    },
    "gemini-flash-lite": {
        "mode": 6, "think": 4,
        "desc": "轻量快速模型",
    },
}


def resolve_model(model_name: str, default: str = "gemini-3.5-flash"):
    """Resolve model name to (name, mode_id, think_mode, error, extra_fields).

    Unknown model names fall back to default rather than erroring,
    since upstream clients may request arbitrary model identifiers.
    """
    think_override = None
    if "@think=" in model_name:
        model_name, think_str = model_name.rsplit("@think=", 1)
        try:
            think_override = int(think_str)
        except ValueError:
            return None, None, None, f"无效的思考级别: {think_str}", None
    cfg = MODELS.get(model_name)
    if not cfg:
        from .gemini import log
        log(f"未知模型 '{model_name}'，回退到 '{default}'")
        model_name = default
        cfg = MODELS[default]
    mode_id = cfg["mode"]
    think_mode = think_override if think_override is not None else cfg["think"]
    extra = cfg.get("extra")
    return model_name, mode_id, think_mode, None, extra
