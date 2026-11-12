# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "AI Assist"
# description = "Анализ и правка пресетов с помощью Gemini"
# author = "OrcaSlicer-AI"
# version = "1.0.0"
# ///
"""AI Assist: Gemini-based analysis of the current OrcaSlicer presets.

The plugin reads an allowlisted subset of the edited print/filament/printer
presets, asks Gemini for recommendations, validates the answer against a strict
schema and applies only the rows the user checked, through the host's
user-confirmed preset transaction API. Hosts without that API get a manual mode
that produces copyable instructions instead.

Everything except the Orca capability classes at the bottom is plain Python so
it can be tested without OrcaSlicer.
"""

import contextlib
import html
import http.client
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import orca
except ImportError:
    orca = None
if orca is not None and not hasattr(orca, "pages"):
    orca = None  # an unrelated "orca" package (e.g. the GNOME screen reader) outside OrcaSlicer

PLUGIN_NAME = "AI Assist"
CONFIG_FILE = "config.json"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

REQUEST_TIMEOUT_S = 60.0
IO_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 2.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_MODEL_TEXT_CHARS = 100_000
MAX_SUMMARY_CHARS = 4000
MAX_REASON_CHARS = 1000
MAX_VALUE_CHARS = 200
MAX_NAME_CHARS = 200
MAX_RECOMMENDATIONS = 20
MAX_PROBLEM_CHARS = 4000

FEATURE_STORAGE = "plugin_storage.v1"
FEATURE_SNAPSHOT = "preset_snapshot.v1"
FEATURE_TRANSACTION = "preset_transaction.v1"
FEATURE_VALUES = "preset_values.v1"

MODE_AUTOMATIC = "automatic"
MODE_MANUAL = "manual"
MODE_UNAVAILABLE = "unavailable"

MODE_TEXT = {
    MODE_AUTOMATIC: "Автоматический режим: отмеченные изменения применяются через OrcaSlicer после "
                    "подтверждения в отдельном окне. Пресеты не сохраняются автоматически.",
    MODE_MANUAL: "Ручной режим: эта версия OrcaSlicer не позволяет плагинам менять пресеты. "
                 "Рекомендации можно скопировать и перенести вручную.",
    MODE_UNAVAILABLE: "Эта версия OrcaSlicer не даёт плагинам читать пресеты — анализ недоступен.",
}
STORAGE_OK_TEXT = "Настройки хранятся в личной папке плагина."
STORAGE_MISSING_TEXT = ("Настройки нельзя сохранить: OrcaSlicer не предоставляет хранилище плагина "
                        "(plugin_storage.v1). Ключ и согласие действуют только до перезапуска программы.")

PRESET_TYPES = ("print", "filament", "printer")
CATEGORY_LABELS = {"print": "Процесс", "filament": "Пластик", "printer": "Принтер"}
LEGACY_COLLECTIONS = {"print": "prints", "filament": "filaments", "printer": "printers"}
CONFIDENCE_LABELS = {"low": "низкая", "medium": "средняя", "high": "высокая"}

# Settings most often adjusted for print defects. Only these keys are ever read
# from the presets, sent to Gemini or accepted back from it.
SNAPSHOT_KEYS: Dict[str, Tuple[str, ...]] = {
    "print": (
        "layer_height", "initial_layer_print_height", "wall_loops", "top_shell_layers",
        "bottom_shell_layers", "sparse_infill_density", "sparse_infill_pattern",
        "outer_wall_speed", "inner_wall_speed", "sparse_infill_speed", "top_surface_speed",
        "initial_layer_speed", "initial_layer_infill_speed", "travel_speed",
        "elefant_foot_compensation", "brim_type", "brim_width", "brim_object_gap",
        "enable_support", "support_type", "raft_layers", "reduce_crossing_wall",
        "detect_overhang_wall", "overhang_1_4_speed", "overhang_2_4_speed",
        "overhang_3_4_speed", "overhang_4_4_speed", "bridge_speed", "bridge_flow",
        "thick_bridges", "seam_position", "wipe_on_loops", "enable_arc_fitting",
    ),
    "filament": (
        "filament_type", "filament_diameter", "filament_density", "nozzle_temperature",
        "nozzle_temperature_initial_layer", "hot_plate_temp", "hot_plate_temp_initial_layer",
        "cool_plate_temp", "eng_plate_temp", "textured_plate_temp", "chamber_temperature",
        "fan_min_speed", "fan_max_speed", "fan_cooling_layer_time", "close_fan_the_first_x_layers",
        "reduce_fan_stop_start_freq", "full_fan_speed_layer", "slow_down_for_layer_cooling",
        "slow_down_layer_time", "slow_down_min_speed", "filament_flow_ratio",
        "filament_max_volumetric_speed", "filament_retraction_length", "filament_retraction_speed",
        "filament_deretraction_speed", "filament_retract_before_wipe",
        "filament_retract_restart_extra", "filament_retraction_minimum_travel", "filament_wipe",
        "filament_wipe_distance", "filament_z_hop",
    ),
    "printer": (
        "printer_model", "nozzle_diameter", "printer_structure", "gcode_flavor",
        "extruder_clearance_radius", "extruder_clearance_height_to_lid", "machine_max_speed_x",
        "machine_max_speed_y", "machine_max_speed_z", "machine_max_speed_e",
        "machine_max_acceleration_x", "machine_max_acceleration_y",
        "machine_max_acceleration_extruding", "retraction_length", "retraction_speed",
        "deretraction_speed", "retract_length_toolchange", "retract_before_wipe",
        "retraction_minimum_travel", "wipe", "wipe_distance", "z_hop", "z_hop_types",
        "retract_lift_above", "retract_lift_below",
    ),
}

SENSITIVE_MARKERS = ("apikey", "api_key", "token", "password", "passwd", "secret", "credential",
                     "username", "user_name", "print_host", "printhost", "webui", "url", "path",
                     "cloud", "auth")

_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_API_KEY_RE = re.compile(r"[\x21-\x7e]{1,256}")
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token)=)[^&#\s]+")
_FENCE_RE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.S | re.I)


def is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(marker in lower for marker in SENSITIVE_MARKERS)


def redact(text: Any, secrets: Iterable[str] = ()) -> str:
    """Remove every given secret and any credential-looking URL query value from text."""
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return _QUERY_SECRET_RE.sub(r"\1***", result)


def _short(text: Any, limit: int = 80) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def validate_model(model: str) -> str:
    model = model.strip()
    if not _MODEL_RE.fullmatch(model):
        raise ValueError("Имя модели может содержать только латинские буквы, цифры, точку, дефис "
                         "и подчёркивание (например, gemini-2.5-flash).")
    return model


def validate_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # raises ValueError for a malformed port
    except ValueError:
        raise ValueError("Некорректный адрес API.") from None
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Адрес API должен быть вида https://host[/путь] без логина, параметров и якоря.")
    if parts.scheme != "https" and not (parts.scheme == "http" and host in LOCAL_HOSTS):
        raise ValueError("Адрес API должен начинаться с https:// (http:// допускается только для localhost).")
    return url


def validate_api_key(key: str) -> str:
    key = key.strip()
    if not _API_KEY_RE.fullmatch(key):
        raise ValueError("Ключ API должен состоять из латинских символов без пробелов (не длиннее 256).")
    return key


@dataclass
class AiConfig:
    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    preset_data_consent: bool = False

    @classmethod
    def from_dict(cls, data: Mapping) -> "AiConfig":
        """Take every valid field from data; invalid or missing fields keep their defaults."""
        config = cls()
        for name, validator in (("gemini_api_key", validate_api_key), ("gemini_model", validate_model),
                                ("base_url", validate_base_url)):
            value = data.get(name)
            if isinstance(value, str):
                with contextlib.suppress(ValueError):
                    setattr(config, name, validator(value))
        config.preset_data_consent = data.get("preset_data_consent") is True
        return config


def load_config(directory: str) -> Tuple[AiConfig, Optional[str]]:
    """Return (config, warning). A damaged file yields defaults; its content is never echoed."""
    try:
        with open(os.path.join(directory, CONFIG_FILE), encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return AiConfig(), None
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return AiConfig(), ("Файл настроек повреждён или недоступен — используются значения по умолчанию. "
                            "Введите ключ Gemini заново.")
    return AiConfig.from_dict(data), None


def save_config(directory: str, config: AiConfig) -> None:
    """Atomically replace config.json; the file is readable only by the owner on POSIX."""
    fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(config), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, os.path.join(directory, CONFIG_FILE))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# --------------------------------------------------------------------------- #
# Host feature discovery and preset reading
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HostFeatures:
    features: FrozenSet[str] = frozenset()
    legacy_read: bool = False

    @classmethod
    def discover(cls, host: Any) -> "HostFeatures":
        legacy = callable(getattr(host, "preset_bundle", None))
        try:
            info = host.api_info()
        except Exception:
            return cls(frozenset(), legacy)
        features = info.get("features") if isinstance(info, dict) else None
        if not isinstance(features, (list, tuple)):
            return cls(frozenset(), legacy)
        return cls(frozenset(f for f in features if isinstance(f, str)), legacy)

    @property
    def storage(self) -> bool:
        return FEATURE_STORAGE in self.features

    @property
    def snapshot(self) -> bool:
        return FEATURE_SNAPSHOT in self.features

    @property
    def mode(self) -> str:
        if {FEATURE_SNAPSHOT, FEATURE_TRANSACTION, FEATURE_VALUES} <= self.features:
            return MODE_AUTOMATIC
        if self.snapshot or self.legacy_read:
            return MODE_MANUAL
        return MODE_UNAVAILABLE


@dataclass(frozen=True)
class Workspace:
    """Allowlisted values of the edited presets; revision is None for the legacy read API."""
    revision: Optional[str]
    names: Dict[str, str]
    values: Dict[str, Dict[str, str]]


def _allowed_values(raw: Any, preset_type: str) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    values = {}
    for key in SNAPSHOT_KEYS[preset_type]:
        value = raw.get(key)
        if isinstance(value, str) and len(value) <= MAX_VALUE_CHARS and not is_sensitive_key(key):
            values[key] = value
    return values


def workspace_from_snapshot(snapshot: Any) -> Workspace:
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("revision"), str):
        raise ValueError("OrcaSlicer вернул некорректный снимок пресетов.")

    def section(name: str) -> Mapping:
        value = snapshot.get(name)
        return value if isinstance(value, Mapping) else {}

    edited, selected, requested = section("edited"), section("selected"), section("requested_values")
    names = {}
    for preset_type in PRESET_TYPES:
        summary = edited.get(preset_type)
        name = summary.get("name") if isinstance(summary, Mapping) else None
        if not isinstance(name, str):
            name = selected.get(preset_type)
        names[preset_type] = _short(name, MAX_NAME_CHARS) if isinstance(name, str) else ""
    values = {t: _allowed_values(requested.get(t), t) for t in PRESET_TYPES}
    return Workspace(snapshot["revision"], names, values)


def workspace_from_bundle(bundle: Any) -> Workspace:
    """Read the edited presets through the legacy read-only preset_bundle() bridge."""
    names, values = {}, {}
    for preset_type in PRESET_TYPES:
        preset = getattr(bundle, LEGACY_COLLECTIONS[preset_type]).edited_preset()
        names[preset_type] = _short(preset.name, MAX_NAME_CHARS)
        raw = {}
        for key in SNAPSHOT_KEYS[preset_type]:
            value = preset.config_value(key)
            if value is not None:
                raw[key] = str(value)
        values[preset_type] = _allowed_values(raw, preset_type)
    return Workspace(None, names, values)


def capture_workspace(host: Any, features: HostFeatures) -> Workspace:
    if features.snapshot:
        keys = {t: list(SNAPSHOT_KEYS[t]) for t in PRESET_TYPES}
        return workspace_from_snapshot(host.v1.presets.snapshot(config_keys=keys))
    if features.legacy_read:
        return workspace_from_bundle(host.preset_bundle())
    raise RuntimeError(MODE_TEXT[MODE_UNAVAILABLE])


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
ANALYZE_TASK = "Review the current settings and suggest only concrete, high-impact improvements."
PROBLEM_TASK = ('The user describes a print defect in "problem" (Russian or English). Diagnose the likely '
                "causes and propose corrective changes, preferring process and filament settings over "
                "printer hardware limits.")
PROMPT_TEMPLATE = """You are an expert FDM 3D printing process engineer who knows OrcaSlicer setting keys.
{task}
The input JSON contains the names of the current presets ("presets") and the current serialized values of the
only settings you may change ("settings", grouped by preset_type: print, filament, printer).
Respond with exactly one JSON object and nothing else:
{{"summary": "<short explanation in Russian>", "recommendations": [{{"preset_type": "print|filament|printer",
"key": "<key from settings>", "proposed_value": "<value as a string>", "reason": "<why, in Russian>",
"confidence": "low|medium|high"}}]}}
Rules:
- Use only preset_type/key pairs present in "settings"; never invent keys.
- Each preset_type/key pair at most once; at most 12 recommendations.
- proposed_value uses the same serialization as the current value (e.g. "0.2", "45%", "1", "220,220").
- Do not repeat current values and do not add any other fields.
- If nothing needs to change, return an empty recommendations list and say so in summary.

INPUT JSON:
{payload}"""


def build_provider_payload(workspace: Workspace, problem: Optional[str] = None) -> Dict[str, Any]:
    """The complete data sent to Gemini: preset names, allowlisted values and the defect text."""
    payload: Dict[str, Any] = {
        "presets": {t: workspace.names.get(t, "") for t in PRESET_TYPES},
        "settings": {t: {k: v for k, v in workspace.values.get(t, {}).items()
                         if k in SNAPSHOT_KEYS[t] and not is_sensitive_key(k)}
                     for t in PRESET_TYPES},
    }
    if problem:
        payload["problem"] = problem
    return payload


def build_prompt(payload: Mapping) -> str:
    return PROMPT_TEMPLATE.format(task=PROBLEM_TASK if payload.get("problem") else ANALYZE_TASK,
                                  payload=json.dumps(payload, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- #
# Gemini transport
# --------------------------------------------------------------------------- #
class TaskCancelled(Exception):
    pass


class GeminiError(Exception):
    """Transport/provider failure; the message is user-facing and already redacted."""


HTTP_HINTS = {
    400: "некорректный запрос или ключ API",
    401: "ключ API отклонён",
    403: "ключ API отклонён или нет доступа к модели",
    404: "модель не найдена — проверьте её имя",
    429: "превышен лимит запросов, повторите позже",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects would resend the x-goog-api-key header to another URL; refuse them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GeminiClient:
    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = REQUEST_TIMEOUT_S,
                 io_timeout: float = IO_TIMEOUT_S, max_bytes: int = MAX_RESPONSE_BYTES):
        self._api_key = api_key
        self._model = validate_model(model)
        self._base_url = validate_base_url(base_url)
        self._timeout = timeout
        self._io_timeout = min(io_timeout, timeout)
        self._max_bytes = max_bytes
        self._opener = urllib.request.build_opener(_NoRedirect)

    def generate(self, prompt: str, cancel: threading.Event) -> str:
        """POST the prompt and return the model text; raises GeminiError or TaskCancelled."""
        if not self._api_key:
            raise GeminiError("Не задан ключ Gemini API.")
        if cancel.is_set():
            raise TaskCancelled()
        url = f"{self._base_url}/v1beta/models/{urllib.parse.quote(self._model, safe='')}:generateContent"
        body = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": self._api_key})
        deadline = time.monotonic() + self._timeout
        try:
            with self._opener.open(request, timeout=self._io_timeout) as response:
                raw = self._read_bounded(response, deadline, cancel)
        except urllib.error.HTTPError as exc:
            raise GeminiError(self._http_error_message(exc)) from None
        except TimeoutError:
            raise GeminiError(self._timeout_message()) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise GeminiError(self._timeout_message()) from None
            raise GeminiError(self._redact(f"Не удалось подключиться к Gemini: {exc.reason}")) from None
        except (OSError, http.client.HTTPException) as exc:
            raise GeminiError(self._redact(f"Ошибка сети при обращении к Gemini: {exc}")) from None
        if cancel.is_set():
            raise TaskCancelled()
        return self._extract_text(raw)

    def _redact(self, text: str) -> str:
        return redact(text, [self._api_key])

    def _timeout_message(self) -> str:
        return f"Gemini не ответил вовремя (лимит {int(self._timeout)} с)."

    def _read_bounded(self, response, deadline: float, cancel: threading.Event) -> bytes:
        too_big = GeminiError(f"Ответ Gemini больше {self._max_bytes // 1024} КиБ — запрос прерван.")
        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > self._max_bytes:
            raise too_big
        chunks, total = [], 0
        while True:
            if cancel.is_set():
                raise TaskCancelled()
            if time.monotonic() > deadline:
                raise TimeoutError()
            chunk = response.read(64 * 1024)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self._max_bytes:
                raise too_big
            chunks.append(chunk)

    def _http_error_message(self, exc: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            error = json.loads(exc.read(MAX_ERROR_BODY_BYTES).decode("utf-8", "replace")).get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = _short(error["message"], 300)
        except Exception:
            pass
        finally:
            exc.close()
        code = exc.code
        if 300 <= code < 400:
            hint = "перенаправление запросов запрещено — проверьте адрес API"
        else:
            hint = HTTP_HINTS.get(code, "сервер Gemini временно недоступен" if code >= 500 else "запрос отклонён")
        return self._redact(f"Ошибка Gemini (HTTP {code}): {hint}." + (f" {detail}" if detail else ""))

    def _extract_text(self, raw: bytes) -> str:
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GeminiError("Gemini вернул ответ не в формате JSON.") from None
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            feedback = data.get("promptFeedback") if isinstance(data, dict) else None
            reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            if reason:
                raise GeminiError(self._redact(f"Gemini заблокировал запрос: {_short(reason)}."))
            raise GeminiError("Gemini не вернул ни одного варианта ответа.")
        content = candidates[0].get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        text = "".join(p["text"] for p in parts or () if isinstance(p, dict) and isinstance(p.get("text"), str))
        if not text.strip():
            finish = candidates[0].get("finishReason")
            raise GeminiError(self._redact(f"Gemini вернул пустой ответ (finishReason: {_short(finish)})."))
        return text


# --------------------------------------------------------------------------- #
# Strict response schema
# --------------------------------------------------------------------------- #
class ResponseError(ValueError):
    """The model answer violates the schema; the message is user-facing."""


@dataclass(frozen=True)
class Recommendation:
    row_id: str
    preset_type: str
    key: str
    old_value: str
    proposed_value: str
    reason: str
    confidence: str = ""


@dataclass(frozen=True)
class Analysis:
    revision: Optional[str]
    summary: str
    recommendations: Tuple[Recommendation, ...] = field(default_factory=tuple)


_TOP_FIELDS = frozenset({"summary", "recommendations"})
_ITEM_FIELDS = frozenset({"preset_type", "key", "proposed_value", "reason", "confidence"})
_ITEM_REQUIRED = ("preset_type", "key", "proposed_value", "reason")


def _coerce_value(value: Any, where: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ResponseError(f"{where}: proposed_value должно быть строкой.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ResponseError(f"{where}: недопустимое числовое значение.")
    text = value.strip() if isinstance(value, str) else str(value)
    if not text or len(text) > MAX_VALUE_CHARS or any(ord(ch) < 32 for ch in text):
        raise ResponseError(f"{where}: пустое, слишком длинное или многострочное значение.")
    return text


def _parse_item(item: Any, where: str, workspace: Workspace, seen: set, row_id: str) -> Recommendation:
    if not isinstance(item, dict):
        raise ResponseError(f"{where}: ожидался JSON-объект.")
    if "old_value" in item:
        raise ResponseError(f"{where}: модель не должна передавать old_value — текущие значения берутся из пресета.")
    extra = set(item) - _ITEM_FIELDS
    if extra:
        raise ResponseError(f"{where}: неизвестные поля {_short(', '.join(sorted(extra)))}.")
    missing = [name for name in _ITEM_REQUIRED if name not in item]
    if missing:
        raise ResponseError(f"{where}: нет обязательных полей {', '.join(missing)}.")
    preset_type, key = item["preset_type"], item["key"]
    if not isinstance(preset_type, str) or preset_type not in PRESET_TYPES:
        raise ResponseError(f"{where}: неизвестная категория пресета «{_short(preset_type, 40)}».")
    if not isinstance(key, str):
        raise ResponseError(f"{where}: key должно быть строкой.")
    if is_sensitive_key(key):
        raise ResponseError(f"{where}: параметр «{_short(key, 60)}» относится к секретным данным.")
    if key not in SNAPSHOT_KEYS[preset_type]:
        raise ResponseError(f"{where}: параметр {preset_type}/{_short(key, 60)} не входит в список разрешённых.")
    old_value = workspace.values.get(preset_type, {}).get(key)
    if old_value is None:
        raise ResponseError(f"{where}: параметра {preset_type}/{key} нет в отправленных данных.")
    if (preset_type, key) in seen:
        raise ResponseError(f"{where}: параметр {preset_type}/{key} повторяется.")
    seen.add((preset_type, key))
    proposed = _coerce_value(item["proposed_value"], where)
    reason = item["reason"]
    if not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS:
        raise ResponseError(f"{where}: reason должно быть строкой не длиннее {MAX_REASON_CHARS} символов.")
    confidence = item.get("confidence")
    if confidence is not None and confidence not in CONFIDENCE_LABELS:
        raise ResponseError(f"{where}: confidence должно быть low, medium или high.")
    return Recommendation(row_id, preset_type, key, old_value, proposed, reason.strip(), confidence or "")


def parse_recommendations(text: str, workspace: Workspace, id_prefix: str = "r") -> Analysis:
    """Validate the model text; any violation rejects the whole answer."""
    if not isinstance(text, str) or not text.strip():
        raise ResponseError("Модель вернула пустой ответ.")
    if len(text) > MAX_MODEL_TEXT_CHARS:
        raise ResponseError("Ответ модели слишком длинный.")
    body = text.strip()
    fence = _FENCE_RE.match(body)
    if fence:
        body = fence.group(1)
    try:
        data = json.loads(body)
    except ValueError:
        raise ResponseError("Ответ модели не является корректным JSON. Повторите запрос.") from None
    if not isinstance(data, dict) or set(data) != _TOP_FIELDS:
        raise ResponseError("Ответ модели должен быть объектом ровно с полями summary и recommendations.")
    summary, items = data["summary"], data["recommendations"]
    if not isinstance(summary, str) or len(summary) > MAX_SUMMARY_CHARS:
        raise ResponseError(f"summary должно быть строкой не длиннее {MAX_SUMMARY_CHARS} символов.")
    if not isinstance(items, list) or len(items) > MAX_RECOMMENDATIONS:
        raise ResponseError(f"recommendations должно быть списком не длиннее {MAX_RECOMMENDATIONS} элементов.")
    seen: set = set()
    recommendations = tuple(
        _parse_item(item, f"Рекомендация №{index}", workspace, seen, f"{id_prefix}-{index}")
        for index, item in enumerate(items, 1))
    return Analysis(workspace.revision, summary.strip(), recommendations)


# --------------------------------------------------------------------------- #
# Review and apply
# --------------------------------------------------------------------------- #
def select_rows(recommendations: Sequence[Recommendation], selected_ids: Any) -> List[Recommendation]:
    """Canonical records for the selected IDs; unknown, duplicate and non-string IDs are ignored."""
    if not isinstance(selected_ids, (list, tuple)):
        return []
    wanted = {row_id for row_id in selected_ids if isinstance(row_id, str)}
    return [rec for rec in recommendations if rec.row_id in wanted]


def build_commit_request(revision: str, recommendations: Sequence[Recommendation], selected_ids: Any) -> dict:
    """One set_values operation per preset type, containing only the checked rows."""
    values: Dict[str, Dict[str, str]] = {t: {} for t in PRESET_TYPES}
    for rec in select_rows(recommendations, selected_ids):
        values[rec.preset_type][rec.key] = rec.proposed_value
    operations = [{"op": "set_values", "preset_type": t, "scope": {"edited": True}, "values": v}
                  for t, v in values.items() if v]
    if not operations:
        raise ValueError("Отметьте хотя бы одну рекомендацию.")
    return {"expected_revision": revision, "interaction": "require_user_confirmation", "operations": operations}


@dataclass(frozen=True)
class ApplyOutcome:
    level: str
    message: str
    discard_rows: bool


def _host_details(result: Mapping) -> str:
    diagnostics = result.get("diagnostics")
    messages = [_short(d["message"], 200) for d in (diagnostics if isinstance(diagnostics, list) else [])[:5]
                if isinstance(d, dict) and isinstance(d.get("message"), str)]
    if not messages and isinstance(result.get("message"), str) and result["message"]:
        messages.append(_short(result["message"], 200))
    return "; ".join(messages)


def describe_commit_result(result: Any) -> ApplyOutcome:
    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
        return ApplyOutcome("error", "OrcaSlicer вернул некорректный ответ на запрос изменения пресетов.", False)
    status = result["status"]
    details = _host_details(result)
    suffix = f" Подробности: {details}" if details else ""
    if status == "applied":
        affected = result.get("affected")
        count = len(affected) if isinstance(affected, list) else 0
        if count == 0:
            return ApplyOutcome("info", "Изменений не потребовалось: значения уже совпадают.", True)
        return ApplyOutcome("ok", f"Применено изменений: {count}. Пресеты помечены как изменённые — проверьте "
                                  "и сохраните их. Для новых рекомендаций запустите анализ снова.", True)
    if status == "cancelled":
        return ApplyOutcome("info", "Применение отменено, пресеты не изменены.", False)
    if status == "conflict":
        return ApplyOutcome("error", "Пресеты изменились после анализа. Рекомендации сброшены — "
                                     "запустите анализ заново.", True)
    if status == "invalid":
        return ApplyOutcome("error", "OrcaSlicer отклонил изменения, ничего не применено. Снимите отметку с "
                                     "проблемных строк и повторите." + suffix, False)
    if status == "unsupported":
        return ApplyOutcome("error", "OrcaSlicer сейчас не может применить изменения (например, идёт нарезка "
                                     "или закрытие программы). Повторите позже." + suffix, False)
    if status == "error":
        return ApplyOutcome("error", "Ошибка при применении, изменения отменены." + suffix, False)
    return ApplyOutcome("error", f"Неожиданный ответ OrcaSlicer ({_short(status, 40)}), ничего не применено."
                        + suffix, False)


def format_manual_instructions(rows: Sequence[Recommendation]) -> str:
    lines = ["Категория / параметр / текущее / предлагаемое"]
    lines += [f"{CATEGORY_LABELS[r.preset_type]} / {r.key} / {r.old_value} / {r.proposed_value}" for r in rows]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Background tasks
# --------------------------------------------------------------------------- #
class TaskController:
    """Runs one request at a time in a worker thread.

    deliver(generation, kind, ok, value) is called on the worker thread, under the
    controller lock, only for the newest request, only if it was not cancelled and
    only before shutdown() — so nothing reaches the page after unload.
    """

    def __init__(self, deliver: Callable[[int, str, bool, Any], None]):
        self._deliver = deliver
        self._lock = threading.RLock()
        self._generation = 0
        self._cancel: Optional[threading.Event] = None
        self._threads: List[threading.Thread] = []
        self._closed = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._cancel is not None

    def submit(self, kind: str, work: Callable[[threading.Event], Any]) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("task controller is shut down")
            if self._cancel is not None:
                self._cancel.set()
            self._generation += 1
            generation, cancel = self._generation, threading.Event()
            self._cancel = cancel
            thread = threading.Thread(target=self._run, args=(generation, kind, work, cancel),
                                      name=f"ai-assist-{kind}-{generation}", daemon=True)
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
            thread.start()
        return generation

    def _run(self, generation: int, kind: str, work: Callable, cancel: threading.Event) -> None:
        try:
            ok, value = True, work(cancel)
        except Exception as exc:
            ok, value = False, exc
        with self._lock:
            if generation != self._generation:
                return
            self._cancel = None
            if self._closed or cancel.is_set():
                return
            self._deliver(generation, kind, ok, value)

    def cancel_current(self) -> bool:
        with self._lock:
            if self._cancel is None:
                return False
            self._cancel.set()
            self._cancel = None
            return True

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_S) -> bool:
        """Cancel and join workers for at most timeout seconds; True if all stopped."""
        with self._lock:
            self._closed = True
            if self._cancel is not None:
                self._cancel.set()
                self._cancel = None
            threads, self._threads = self._threads, []
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads)


# --------------------------------------------------------------------------- #
# Page controller (host-independent; the Orca capability delegates to it)
# --------------------------------------------------------------------------- #
class AiAssistApp:
    def __init__(self, host: Any, post: Callable[[dict], None]):
        self._host = host
        self._post = post
        self._features = HostFeatures()
        self._storage_dir: Optional[str] = None
        self._config = AiConfig()
        self._config_warning: Optional[str] = None
        self._tasks: Optional[TaskController] = None
        self._analysis: Optional[Analysis] = None
        self._applying = False
        self._loaded = False
        self._request_counter = 0

    # lifecycle (GUI thread) -------------------------------------------------
    def load(self) -> None:
        if self._loaded:
            return
        self._features = HostFeatures.discover(self._host)
        self._storage_dir = self._resolve_storage()
        if self._storage_dir:
            self._config, self._config_warning = load_config(self._storage_dir)
        else:
            self._config, self._config_warning = AiConfig(), None
        self._tasks = TaskController(self._on_task_done)
        self._analysis = None
        self._applying = False
        self._loaded = True

    def unload(self) -> bool:
        if not self._loaded:
            return True
        self._loaded = False
        stopped = self._tasks.shutdown(SHUTDOWN_TIMEOUT_S)
        self._analysis = None
        return stopped

    def _resolve_storage(self) -> Optional[str]:
        # plugin_storage() only works inside a GUI-thread plugin callback, so it is resolved once here.
        if not self._features.storage:
            return None
        try:
            path = self._host.v1.plugin_storage()
            if not isinstance(path, str) or not os.path.isabs(path):
                return None
            os.makedirs(path, exist_ok=True)
        except Exception:
            return None
        return path

    # messages from the page (GUI thread) -------------------------------------
    def handle(self, message: Any) -> None:
        if not self._loaded or not isinstance(message, dict):
            return
        kind = message.get("type")
        handler = {
            "init": self._on_init, "save_settings": self._on_save_settings, "consent": self._on_consent,
            "analyze": self._on_analyze, "diagnose": self._on_diagnose, "cancel": self._on_cancel,
            "apply": self._on_apply,
        }.get(kind)
        if handler is None:
            return
        if self._applying and kind not in ("init", "cancel"):
            self._status("error", "Дождитесь завершения применения изменений в окне подтверждения.")
            return
        handler(message)

    def _on_init(self, _message: dict) -> None:
        self._post_state()
        if self._analysis is not None:
            self._post_analysis(self._analysis)

    def _on_save_settings(self, message: dict) -> None:
        updated = replace(self._config)
        try:
            key = message.get("api_key")
            if message.get("clear_key") is True:
                updated.gemini_api_key = ""
            elif isinstance(key, str) and key.strip():
                updated.gemini_api_key = validate_api_key(key)
            updated.gemini_model = validate_model(str(message.get("model") or DEFAULT_MODEL))
            updated.base_url = validate_base_url(str(message.get("base_url") or DEFAULT_BASE_URL))
        except ValueError as exc:
            self._status("error", str(exc))
            return
        self._store_config(updated, "Настройки сохранены.")

    def _on_consent(self, message: dict) -> None:
        granted = message.get("value") is True
        self._store_config(replace(self._config, preset_data_consent=granted),
                           "Согласие на отправку данных в Gemini получено." if granted
                           else "Согласие отозвано: запросы к Gemini отключены.")

    def _store_config(self, updated: AiConfig, ok_text: str) -> None:
        if self._storage_dir is None:
            self._config = updated
            self._status("info", ok_text + " " + STORAGE_MISSING_TEXT)
        else:
            try:
                save_config(self._storage_dir, updated)
            except OSError as exc:
                self._status("error", f"Не удалось сохранить настройки: {exc.strerror or type(exc).__name__}.")
            else:
                self._config, self._config_warning = updated, None
                self._status("ok", ok_text)
        self._post_state()

    def _on_analyze(self, _message: dict) -> None:
        self._start_request("analyze", None)

    def _on_diagnose(self, message: dict) -> None:
        problem = message.get("problem")
        problem = problem.strip() if isinstance(problem, str) else ""
        if not problem:
            self._status("error", "Опишите дефект печати (например, «слоновья нога», «задираются углы»).")
        elif len(problem) > MAX_PROBLEM_CHARS:
            self._status("error", f"Описание слишком длинное: не больше {MAX_PROBLEM_CHARS} символов.")
        else:
            self._start_request("diagnose", problem)

    def _start_request(self, kind: str, problem: Optional[str]) -> None:
        config = self._config
        if self._features.mode == MODE_UNAVAILABLE:
            return self._status("error", MODE_TEXT[MODE_UNAVAILABLE])
        if not config.gemini_api_key:
            return self._status("error", "Сначала укажите ключ Gemini API в настройках.")
        if not config.preset_data_consent:
            return self._status("error", "Подтвердите согласие на отправку данных в Gemini.")
        try:
            workspace = capture_workspace(self._host, self._features)
        except Exception as exc:
            return self._status("error", f"Не удалось прочитать пресеты: {_short(exc, 200)}")
        if not any(workspace.values.values()):
            return self._status("error", "Не удалось прочитать ни одного параметра текущих пресетов.")
        prompt = build_prompt(build_provider_payload(workspace, problem))
        client = GeminiClient(config.gemini_api_key, config.gemini_model, config.base_url)
        self._request_counter += 1
        prefix = f"r{self._request_counter}"
        self._analysis = None
        self._post({"type": "analysis", "summary": "", "rows": []})
        # Posted before submit() so a fast worker's result can never be followed by a stale busy state.
        self._status("info", "Запрос к Gemini отправлен, ждём ответ…")
        self._post_state(busy=True)
        self._tasks.submit(kind, lambda cancel: parse_recommendations(client.generate(prompt, cancel),
                                                                      workspace, prefix))

    def _on_cancel(self, _message: dict) -> None:
        if self._tasks.cancel_current():
            self._status("info", "Запрос отменён.")
            self._post_state()

    def _on_apply(self, message: dict) -> None:
        analysis = self._analysis
        if analysis is None or not analysis.recommendations:
            return self._status("error", "Нет рекомендаций — сначала запустите анализ.")
        if self._tasks.busy:
            return self._status("error", "Дождитесь завершения текущего запроса к Gemini.")
        rows = select_rows(analysis.recommendations, message.get("selected"))
        if not rows:
            return self._status("error", "Отметьте хотя бы одну рекомендацию.")
        if self._features.mode != MODE_AUTOMATIC or analysis.revision is None:
            self._status("info", "Перенесите значения в пресеты вручную: OrcaSlicer не поддерживает "
                                 "применение изменений плагинами.")
            return self._post({"type": "instructions", "text": format_manual_instructions(rows)})
        request = build_commit_request(analysis.revision, analysis.recommendations, message.get("selected"))
        self._applying = True
        try:
            result = self._host.v1.presets.commit(request)
        except Exception as exc:
            result = {"status": "error", "message": _short(exc, 200)}
        finally:
            self._applying = False
        if not self._loaded:
            return
        outcome = describe_commit_result(result)
        if outcome.discard_rows:
            self._analysis = None
            self._post({"type": "analysis", "summary": analysis.summary, "rows": []})
        self._status(outcome.level, outcome.message)

    # worker results (worker thread, under the task controller lock) ----------
    def _on_task_done(self, _generation: int, _kind: str, ok: bool, value: Any) -> None:
        if ok:
            self._analysis = value
            self._post_analysis(value)
            count = len(value.recommendations)
            self._status("ok", f"Получено рекомендаций: {count}. Отметьте нужные строки." if count
                         else "Модель не предложила изменений.")
        elif isinstance(value, (GeminiError, ResponseError)):
            self._status("error", redact(value, [self._config.gemini_api_key]))
        else:
            self._status("error", f"Непредвиденная ошибка: {type(value).__name__}.")
        self._post_state()

    # messages to the page -----------------------------------------------------
    def _status(self, level: str, text: str) -> None:
        self._post({"type": "status", "level": level, "text": text})

    def _post_state(self, busy: Optional[bool] = None) -> None:
        mode = self._features.mode
        self._post({
            "type": "state",
            "mode": mode,
            "mode_text": MODE_TEXT[mode],
            "storage_ok": self._storage_dir is not None,
            "storage_text": STORAGE_OK_TEXT if self._storage_dir else STORAGE_MISSING_TEXT,
            "config_warning": self._config_warning or "",
            "key_set": bool(self._config.gemini_api_key),
            "model": self._config.gemini_model,
            "base_url": self._config.base_url,
            "consent": self._config.preset_data_consent,
            "busy": self._tasks.busy if busy is None else busy,
            "apply_label": "Применить выбранные" if mode == MODE_AUTOMATIC else "Скопировать инструкции",
        })

    def _post_analysis(self, analysis: Analysis) -> None:
        self._post({"type": "analysis", "summary": analysis.summary, "rows": [{
            "id": r.row_id, "category": CATEGORY_LABELS[r.preset_type], "key": r.key, "old": r.old_value,
            "proposed": r.proposed_value, "reason": r.reason,
            "confidence": CONFIDENCE_LABELS.get(r.confidence, ""),
        } for r in analysis.recommendations]})


# --------------------------------------------------------------------------- #
# Page UI
# --------------------------------------------------------------------------- #
_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>AI Assist</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; padding: 14px; font: 13px/1.45 system-ui, sans-serif;
  color: var(--orca-fg, #eee); background: var(--orca-bg, #222); }
h1 { font-size: 17px; margin: 0 0 8px; }
h2 { font-size: 14px; margin: 0 0 6px; }
section { border: 1px solid var(--orca-border, #666); border-radius: 6px; padding: 10px; margin: 0 0 10px; }
.banner { padding: 8px 10px; border-radius: 6px; margin: 0 0 10px; border: 1px solid var(--orca-border, #666); }
.banner.automatic { border-color: var(--orca-accent, #4a89e8); }
.banner.unavailable, .status.error { color: #e86a5a; }
.status.ok { color: #5ab46a; }
.muted { opacity: .75; }
label { display: block; margin: 6px 0 2px; }
input[type=text], input[type=password], textarea { box-sizing: border-box; width: 100%; padding: 5px;
  color: inherit; background: transparent; border: 1px solid var(--orca-border, #666); border-radius: 4px; }
textarea { resize: vertical; min-height: 60px; font: inherit; }
button { margin: 8px 6px 0 0; padding: 5px 12px; border-radius: 4px; cursor: pointer; color: inherit;
  background: transparent; border: 1px solid var(--orca-border, #666); }
button.primary { color: var(--orca-accent-fg, #fff); background: var(--orca-accent, #4a89e8);
  border-color: var(--orca-accent, #4a89e8); }
button:disabled { opacity: .45; cursor: default; }
table { width: 100%; border-collapse: collapse; margin-top: 6px; }
th, td { text-align: left; vertical-align: top; padding: 4px 6px; border-bottom: 1px solid var(--orca-border, #666); }
td.mono { font-family: ui-monospace, monospace; word-break: break-all; }
ul { margin: 4px 0; padding-left: 20px; }
#summary { white-space: pre-wrap; margin: 0 0 6px; }
</style></head>
<body>
<h1>AI Assist</h1>
<div id="mode" class="banner">Загрузка…</div>

<section>
  <h2>Настройки Gemini</h2>
  <div id="storage" class="muted"></div>
  <label for="api-key">Ключ Gemini API (<span id="key-state">не задан</span>)</label>
  <input id="api-key" type="password" autocomplete="off" placeholder="Вставьте ключ из Google AI Studio">
  <label for="model">Модель</label>
  <input id="model" type="text" autocomplete="off">
  <label for="base-url">Адрес API</label>
  <input id="base-url" type="text" autocomplete="off">
  <button id="save">Сохранить</button><button id="clear-key">Удалить ключ</button>
</section>

<section>
  <h2>Какие данные уходят в Google Gemini</h2>
  <ul>
    <li>имена текущих пресетов процесса, пластика и принтера;</li>
    <li>текущие значения разрешённых параметров (список ниже);</li>
    <li>текст описания дефекта, если вы его ввели.</li>
  </ul>
  <div class="muted">Не отправляются: ключи и пароли, адреса хостов печати, пути к файлам, G-code и модели.</div>
  <details><summary>Разрешённые параметры</summary>__ALLOWED_KEYS__</details>
  <label><input id="consent" type="checkbox"> Я согласен отправлять эти данные в Google Gemini</label>
</section>

<section>
  <h2>Анализ</h2>
  <button id="analyze" class="primary">Анализировать пресеты</button>
  <label for="problem">Опишите дефект печати</label>
  <textarea id="problem" maxlength="4000" placeholder="Например: слоновья нога на первом слое, задираются углы"></textarea>
  <button id="diagnose">Подобрать исправления</button><button id="cancel" hidden>Отменить запрос</button>
  <div id="status" class="status"></div>
</section>

<section id="review" hidden>
  <h2>Рекомендации</h2>
  <div id="summary"></div>
  <table><thead><tr><th><input id="all" type="checkbox" title="Выбрать все"></th><th>Категория</th>
    <th>Параметр</th><th>Текущее</th><th>Предлагаемое</th><th>Причина</th><th>Уверенность</th></tr></thead>
    <tbody id="rows"></tbody></table>
  <button id="apply" class="primary" disabled>Применить выбранные</button>
  <div id="instructions-box" hidden>
    <label for="instructions">Инструкции для ручного переноса</label>
    <textarea id="instructions" readonly rows="8"></textarea>
  </div>
</section>

<script>
(function () {
  var state = {};
  function $(id) { return document.getElementById(id); }
  function send(type, extra) { var m = extra || {}; m.type = type; window.orca.postMessage(m); }
  function checked() {
    return Array.prototype.map.call(document.querySelectorAll('input.row:checked'),
      function (c) { return c.getAttribute('data-id'); });
  }
  function setStatus(level, text) { var s = $('status'); s.textContent = text || ''; s.className = 'status ' + (level || ''); }
  function updateApply() { $('apply').disabled = !!state.busy || checked().length === 0; }
  function cell(tr, text, cls) { var td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); }

  function renderState(s) {
    state = s;
    $('mode').textContent = s.mode_text; $('mode').className = 'banner ' + s.mode;
    $('storage').textContent = s.storage_text + (s.config_warning ? ' ' + s.config_warning : '');
    $('key-state').textContent = s.key_set ? 'задан' : 'не задан';
    $('model').value = s.model; $('base-url').value = s.base_url;
    $('consent').checked = !!s.consent;
    var blocked = s.busy || s.mode === 'unavailable';
    $('analyze').disabled = blocked; $('diagnose').disabled = blocked;
    $('cancel').hidden = !s.busy;
    $('apply').textContent = s.apply_label;
    updateApply();
  }
  function renderAnalysis(d) {
    var body = $('rows'); body.textContent = '';
    (d.rows || []).forEach(function (r) {
      var tr = document.createElement('tr'), td = document.createElement('td'), box = document.createElement('input');
      box.type = 'checkbox'; box.className = 'row'; box.setAttribute('data-id', r.id);
      box.addEventListener('change', updateApply);
      td.appendChild(box); tr.appendChild(td);
      cell(tr, r.category); cell(tr, r.key, 'mono'); cell(tr, r.old, 'mono'); cell(tr, r.proposed, 'mono');
      cell(tr, r.reason); cell(tr, r.confidence);
      body.appendChild(tr);
    });
    $('summary').textContent = d.summary || '';
    $('review').hidden = !d.summary && !(d.rows || []).length;
    $('all').checked = false; $('instructions-box').hidden = true;
    updateApply();
  }
  function showInstructions(text) {
    var area = $('instructions'); area.value = text; $('instructions-box').hidden = false;
    area.focus(); area.select();
    var copied = false;
    try { copied = document.execCommand('copy'); } catch (e) {}
    setStatus('info', copied ? 'Инструкции скопированы в буфер обмена — перенесите значения в пресеты вручную.'
                             : 'Текст выделен — скопируйте его (Ctrl+C) и перенесите значения в пресеты вручную.');
  }

  $('save').addEventListener('click', function () {
    send('save_settings', { api_key: $('api-key').value, model: $('model').value, base_url: $('base-url').value });
    $('api-key').value = '';
  });
  $('clear-key').addEventListener('click', function () {
    send('save_settings', { clear_key: true, model: $('model').value, base_url: $('base-url').value });
    $('api-key').value = '';
  });
  $('consent').addEventListener('change', function () { send('consent', { value: $('consent').checked }); });
  $('analyze').addEventListener('click', function () { send('analyze'); });
  $('diagnose').addEventListener('click', function () { send('diagnose', { problem: $('problem').value }); });
  $('cancel').addEventListener('click', function () { send('cancel'); });
  $('apply').addEventListener('click', function () { send('apply', { selected: checked() }); });
  $('all').addEventListener('change', function () {
    var on = $('all').checked;
    Array.prototype.forEach.call(document.querySelectorAll('input.row'), function (c) { c.checked = on; });
    updateApply();
  });

  window.orca.onMessage(function (d) {
    if (!d || typeof d !== 'object') return;
    if (d.type === 'state') renderState(d);
    else if (d.type === 'status') setStatus(d.level, d.text);
    else if (d.type === 'analysis') renderAnalysis(d);
    else if (d.type === 'instructions') showInstructions(d.text);
  });
  send('init');
})();
</script>
</body></html>
"""


def _allowed_keys_html() -> str:
    return "".join(f"<div><b>{CATEGORY_LABELS[t]}:</b> <span class=\"muted\">"
                   f"{html.escape(', '.join(SNAPSHOT_KEYS[t]))}</span></div>" for t in PRESET_TYPES)


PAGE_HTML = _PAGE_TEMPLATE.replace("__ALLOWED_KEYS__", _allowed_keys_html())


# --------------------------------------------------------------------------- #
# OrcaSlicer registration
# --------------------------------------------------------------------------- #
if orca is not None:
    class AiAssistPage(orca.pages.PagesPluginCapabilityBase):
        def __init__(self):
            super().__init__()
            self._app = AiAssistApp(orca.host, self.post_message)

        def get_name(self):
            return PLUGIN_NAME

        def get_ui(self):
            return PAGE_HTML

        def on_load(self):
            self._app.load()

        def on_unload(self):
            self._app.unload()

        def on_message(self, data):
            self._app.handle(data)

    @orca.plugin
    class AiAssistPlugin(orca.base):
        def register_capabilities(self):
            orca.register_capability(AiAssistPage)
