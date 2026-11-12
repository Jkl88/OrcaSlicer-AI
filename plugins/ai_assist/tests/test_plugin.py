import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .test_gemini import FakeGemini, gemini_body

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "orca_ai_assist.py"
API_KEY = "AIzaPLUGINKEY-987"


def load_plugin(name, fake_orca=None):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with mock.patch.dict(sys.modules, {"orca": fake_orca} if fake_orca else {}):
        spec.loader.exec_module(module)
    return module


ai = load_plugin("orca_ai_assist_plugin")

PRESET_VALUES = {
    "print": {"layer_height": "0.2", "wall_loops": "2"},
    "filament": {"nozzle_temperature": "215"},
    "printer": {"nozzle_diameter": "0.4", "printhost_apikey": "PRINTER-SECRET"},
}


def model_answer(*items):
    return gemini_body(json.dumps({"summary": "Сводка", "recommendations": list(items)}))


GOOD_ANSWER = model_answer(
    {"preset_type": "print", "key": "layer_height", "proposed_value": "0.16", "reason": "детали"},
    {"preset_type": "filament", "key": "nozzle_temperature", "proposed_value": "210", "reason": "нити",
     "confidence": "medium"})

EDITORS = {"process": "print", "filament": "filament", "printer": "printer"}


class FakeTransaction:
    def __init__(self, host, category, target_name):
        self.host, self.category, self.target_name, self.staged = host, category, target_name, []

    def set(self, key, value):
        self.host.off_ui("set")
        self.staged.append((key, value))

    def commit(self):
        self.host.off_ui("commit")
        if self.host.commit_error:
            raise self.host.commit_error
        self.host.commits.append((self.category, list(self.staged)))
        self.host.values[self.category].update(self.staged)
        return SimpleNamespace(keys=[k for k, _ in self.staged], target_name=self.target_name, dirty=True)


class FakeEditor:
    def __init__(self, host, category):
        self.host, self.category = host, category

    def begin_update(self, *args):
        self.host.off_ui("begin_update")
        self.host.begins.append((self.category, args))
        if self.host.on_begin:
            self.host.on_begin()
        if self.host.begin_error:
            raise self.host.begin_error
        name = self.host.target_override or self.host.names[self.category]
        return FakeTransaction(self.host, self.category, name)


class FakeHost:
    """Upstream OrcaSlicer 2.5.0 surface; mutations raise when called on the page (UI) thread."""

    def __init__(self, storage):
        self.ui_thread = threading.current_thread()
        self.storage_dir = storage
        self.values = json.loads(json.dumps(PRESET_VALUES))
        self.names = {t: f"{t} preset" for t in PRESET_VALUES}
        self.slots = ["filament preset"]
        self.answer = "yes"
        self.dialogs, self.begins, self.commits, self.violations = [], [], [], []
        self.on_begin = self.begin_error = self.commit_error = self.target_override = None
        self.preset = SimpleNamespace(**{attr: FakeEditor(self, cat) for attr, cat in EDITORS.items()})
        self.plugin = SimpleNamespace(storage=lambda: self.storage_dir)
        self.ui = SimpleNamespace(message=self.message)

    def off_ui(self, call):
        if threading.current_thread() is self.ui_thread:
            self.violations.append(call)
            raise AssertionError(f"{call} called on the UI thread")

    def message(self, text, title="OrcaSlicer", buttons="ok", icon="info"):
        self.dialogs.append((text, buttons))
        return self.answer

    def preset_bundle(self):
        def preset(category):
            return SimpleNamespace(name=self.names[category], config_value=self.values[category].get)

        def collection(category):
            return SimpleNamespace(edited_preset=lambda: preset(category))

        return SimpleNamespace(prints=collection("print"), filaments=collection("filament"),
                               printers=collection("printer"),
                               current_filament_preset_names=lambda: list(self.slots))


class LegacyHost:
    """Pre-2.5 official host: only the read-only preset bridge."""

    def preset_bundle(self):
        def collection(preset_type):
            preset = SimpleNamespace(name=f"{preset_type} legacy", config_value=PRESET_VALUES[preset_type].get)
            return SimpleNamespace(edited_preset=lambda: preset)

        return SimpleNamespace(prints=collection("print"), filaments=collection("filament"),
                               printers=collection("printer"))


class Page:
    def __init__(self):
        self.messages = []
        self._cond = threading.Condition()

    def post(self, message):
        with self._cond:
            self.messages.append(json.loads(json.dumps(message)))
            self._cond.notify_all()

    def wait_for(self, predicate, timeout=5.0):
        with self._cond:
            self._cond.wait_for(lambda: any(predicate(m) for m in self.messages), timeout)
            matches = [m for m in self.messages if predicate(m)]
        if not matches:
            raise AssertionError(f"message not posted; got {self.messages}")
        return matches[-1]

    def last(self, kind):
        with self._cond:
            matches = [m for m in self.messages if m.get("type") == kind]
        return matches[-1] if matches else None

    def clear(self):
        with self._cond:
            self.messages.clear()


def has_rows(message):
    return message.get("type") == "analysis" and message["rows"]


class AppTestCase(unittest.TestCase):
    def make_host(self):
        return FakeHost(self._tmp.name)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake = FakeGemini()
        self.addCleanup(self.fake.close)
        self.fake.reply = lambda h: (200, GOOD_ANSWER, {})
        self.host = self.make_host()
        self.page = Page()
        self.app = ai.AiAssistApp(self.host, self.page.post)
        self.app.load()
        self.addCleanup(self.app.unload)

    def configure(self, consent=True):
        self.app.handle({"type": "save_settings", "api_key": API_KEY, "model": "gemini-2.5-flash",
                         "base_url": self.fake.base_url})
        if consent:
            self.app.handle({"type": "consent", "value": True})

    def analyze(self, message=None):
        self.page.clear()
        self.app.handle(message or {"type": "analyze"})
        return self.page.wait_for(has_rows)

    def status(self):
        return self.page.last("status")

    def apply(self, category, ids):
        """Send apply and wait for the worker's final state."""
        self.page.clear()
        self.app.handle({"type": "apply", "category": category, "selected": ids})
        self.page.wait_for(lambda m: m.get("type") == "state" and not m["busy"])
        return self.status()

    def ids(self, rows, category=None):
        return [r["id"] for r in rows if category is None or r["preset_type"] == category]


class AutomaticModeTests(AppTestCase):
    def test_initial_state(self):
        self.app.handle({"type": "init"})
        state = self.page.last("state")
        self.assertEqual(state["mode"], "automatic")
        self.assertIn("по отдельности", state["mode_text"])
        self.assertTrue(state["storage_ok"])
        self.assertFalse(state["key_set"])
        self.assertFalse(state["consent"])

    def test_settings_saved_privately_and_key_never_posted(self):
        self.configure()
        with open(os.path.join(self._tmp.name, "config.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved, {"gemini_api_key": API_KEY, "gemini_model": "gemini-2.5-flash",
                                 "base_url": self.fake.base_url, "preset_data_consent": True})
        state = self.page.last("state")
        self.assertTrue(state["key_set"])
        self.assertTrue(state["consent"])
        self.analyze()
        self.assertNotIn(API_KEY, json.dumps(self.page.messages))

    def test_invalid_settings_rejected(self):
        self.app.handle({"type": "save_settings", "api_key": API_KEY, "base_url": "http://evil.example"})
        self.assertEqual(self.status()["level"], "error")
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "config.json")))

    def test_clear_key(self):
        self.configure()
        self.app.handle({"type": "save_settings", "clear_key": True, "base_url": self.fake.base_url})
        self.assertFalse(self.page.last("state")["key_set"])

    def test_requires_key_then_consent(self):
        self.app.handle({"type": "analyze"})
        self.assertIn("ключ", self.status()["text"])
        self.configure(consent=False)
        self.app.handle({"type": "analyze"})
        self.assertIn("согласие", self.status()["text"])
        self.assertEqual(self.fake.requests, [])

    def test_analyze_sends_only_allowlisted_data(self):
        self.configure()
        analysis = self.analyze()
        self.assertEqual(analysis["summary"], "Сводка")
        self.assertEqual([(r["category"], r["key"], r["old"], r["proposed"], r["confidence"]) for r in analysis["rows"]],
                         [("Процесс", "layer_height", "0.2", "0.16", ""),
                          ("Пластик", "nozzle_temperature", "215", "210", "средняя")])
        prompt = self.fake.requests[0]["body"]["contents"][0]["parts"][0]["text"]
        self.assertIn('"layer_height": "0.2"', prompt)
        self.assertIn("print preset", prompt)
        self.assertNotIn("PRINTER-SECRET", prompt)
        self.assertNotIn("printhost", prompt)
        self.assertFalse(self.page.last("state")["busy"])

    def test_diagnose_sends_problem_text(self):
        self.configure()
        self.app.handle({"type": "diagnose", "problem": "   "})
        self.assertIn("Опишите", self.status()["text"])
        self.app.handle({"type": "diagnose", "problem": "x" * (ai.MAX_PROBLEM_CHARS + 1)})
        self.assertIn("длинное", self.status()["text"])
        self.analyze({"type": "diagnose", "problem": "слоновья нога"})
        prompt = self.fake.requests[0]["body"]["contents"][0]["parts"][0]["text"]
        self.assertIn("слоновья нога", prompt)
        self.assertIn(ai.PROBLEM_TASK, prompt)

    def test_apply_runs_off_ui_thread_and_commits_one_category(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.assertEqual([r["preset_type"] for r in rows], ["print", "filament"])
        status = self.apply("print", self.ids(rows))
        self.assertEqual(self.host.violations, [])
        self.assertEqual(self.host.begins, [("print", ("",))])
        self.assertEqual(self.host.commits, [("print", [("layer_height", "0.16")])])
        self.assertEqual(status["level"], "ok")
        text, buttons = self.host.dialogs[0]
        self.assertEqual(buttons, "yes_no")
        self.assertIn("layer_height: 0.2 → 0.16", text)
        self.assertNotIn("nozzle_temperature", text)

    def test_successful_apply_removes_only_that_category_rows(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.apply("print", self.ids(rows))
        remaining = self.page.last("analysis")["rows"]
        self.assertEqual([r["preset_type"] for r in remaining], ["filament"])
        self.apply("filament", self.ids(remaining))
        self.assertEqual(self.host.begins[-1], ("filament", (1,)))
        self.assertEqual(self.page.last("analysis")["rows"], [])

    def test_apply_declined_confirmation_applies_nothing(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.host.answer = "no"
        status = self.apply("print", self.ids(rows))
        self.assertEqual(self.host.begins, [])
        self.assertEqual(status["level"], "info")
        self.assertIn("отменено", status["text"])
        self.assertIsNone(self.page.last("analysis"))

    def test_apply_rejects_stale_values(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.host.values["print"]["layer_height"] = "0.28"
        self.page.clear()
        self.app.handle({"type": "apply", "category": "print", "selected": self.ids(rows)})
        status = self.status()
        self.assertEqual(status["level"], "error")
        self.assertIn("layer_height", status["text"])
        self.assertEqual(self.host.dialogs, [])
        self.assertEqual(self.host.begins, [])
        self.assertIsNone(self.page.last("analysis"))

    def test_apply_rejects_switched_preset(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.host.names["print"] = "another preset"
        self.app.handle({"type": "apply", "category": "print", "selected": self.ids(rows)})
        self.assertIn("заново", self.status()["text"])
        self.assertEqual(self.host.begins, [])

    def test_apply_target_mismatch_does_not_commit(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.host.target_override = "unexpected"
        status = self.apply("print", self.ids(rows))
        self.assertEqual(self.host.commits, [])
        self.assertEqual(status["level"], "error")

    def test_filament_apply_uses_first_assigned_slot(self):
        self.host.slots = ["A", "filament preset", "filament preset"]
        self.configure()
        rows = self.analyze()["rows"]
        self.apply("filament", self.ids(rows, "filament"))
        self.assertEqual(self.host.begins, [("filament", (2,))])
        self.assertEqual(self.host.commits, [("filament", [("nozzle_temperature", "210")])])

    def test_filament_apply_without_assigned_slot_refuses(self):
        self.host.slots = ["A", "B"]
        self.configure()
        rows = self.analyze()["rows"]
        self.app.handle({"type": "apply", "category": "filament", "selected": self.ids(rows)})
        self.assertEqual(self.status()["level"], "error")
        self.assertIn("слот", self.status()["text"])
        self.assertEqual(self.host.begins, [])

    def test_commit_value_error_reports_invalid(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.host.commit_error = ValueError("bad value")
        status = self.apply("print", self.ids(rows))
        self.assertIn("ничего не применено", status["text"])
        self.assertIn("bad value", status["text"])
        self.assertIsNone(self.page.last("analysis"))

    def test_begin_update_cancel_reports_cancelled(self):
        self.configure()
        rows = self.analyze()["rows"]
        self.host.begin_error = RuntimeError("cancelled by user")
        status = self.apply("print", self.ids(rows))
        self.assertEqual(status["level"], "info")
        self.assertIn("отменено", status["text"])

    def test_apply_without_selection(self):
        self.configure()
        rows = self.analyze()["rows"]
        for category, selected in (("print", []), ("print", ["unknown"]), ("print", "all"), ("print", None),
                                   ("filament", self.ids(rows, "print"))):
            self.app.handle({"type": "apply", "category": category, "selected": selected})
            self.assertIn("Отметьте", self.status()["text"])
        for category in (None, "process", 5):
            self.app.handle({"type": "apply", "category": category, "selected": self.ids(rows)})
            self.assertIn("категор", self.status()["text"])
        self.assertEqual(self.host.dialogs, [])

    def test_messages_during_apply_are_refused(self):
        self.configure()
        rows = self.analyze()["rows"]
        entered, release = threading.Event(), threading.Event()

        def block():
            entered.set()
            release.wait(5)

        self.host.on_begin = block
        self.page.clear()
        self.app.handle({"type": "apply", "category": "print", "selected": self.ids(rows)})
        self.assertTrue(entered.wait(5))
        for message in ({"type": "apply", "category": "filament", "selected": self.ids(rows)},
                        {"type": "analyze"}, {"type": "cancel"}):
            self.app.handle(message)
            self.assertIn("Дождитесь", self.status()["text"])
        release.set()
        self.page.wait_for(lambda m: m.get("type") == "state" and not m["busy"])
        self.assertEqual(len(self.host.begins), 1)
        self.assertEqual(len(self.fake.requests), 1)

    def test_unload_during_apply_drops_result(self):
        self.configure()
        rows = self.analyze()["rows"]
        entered, release = threading.Event(), threading.Event()

        def block():
            entered.set()
            release.wait(5)

        self.host.on_begin = block
        self.app.handle({"type": "apply", "category": "print", "selected": self.ids(rows)})
        self.assertTrue(entered.wait(5))
        started = time.monotonic()
        self.app.unload()
        self.assertLess(time.monotonic() - started, ai.SHUTDOWN_TIMEOUT_S + 1)
        count = len(self.page.messages)
        release.set()
        time.sleep(0.3)
        self.assertEqual(len(self.page.messages), count)

    def test_gemini_permission_error_message(self):
        self.configure()
        denied = urllib.error.URLError(PermissionError("plugin is not allowed to connect"))
        with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=denied):
            self.app.handle({"type": "analyze"})
            status = self.page.wait_for(lambda m: m.get("type") == "status" and m["level"] == "error")
        self.assertIn("«Плагины»", status["text"])
        self.assertIn("127.0.0.1", status["text"])
        self.assertNotIn("Traceback", status["text"])

    def test_invalid_model_answer_shows_error_without_rows(self):
        self.configure()
        self.fake.reply = lambda h: (200, model_answer(
            {"preset_type": "printer", "key": "printhost_apikey", "proposed_value": "x", "reason": ""}), {})
        self.app.handle({"type": "analyze"})
        status = self.page.wait_for(lambda m: m.get("type") == "status" and m["level"] == "error")
        self.assertIn("секрет", status["text"])
        self.assertFalse(any(has_rows(m) for m in self.page.messages))

    def test_gemini_error_is_redacted(self):
        self.configure()
        self.fake.reply = lambda h: (403, {"error": {"message": f"key {API_KEY} invalid"}}, {})
        self.app.handle({"type": "analyze"})
        status = self.page.wait_for(lambda m: m.get("type") == "status" and m["level"] == "error")
        self.assertIn("403", status["text"])
        self.assertNotIn(API_KEY, json.dumps(self.page.messages))

    def test_cancel_suppresses_late_answer(self):
        self.configure()
        released = threading.Event()

        def slow(handler):
            released.wait(2)
            return 200, GOOD_ANSWER, {}

        self.fake.reply = slow
        self.app.handle({"type": "analyze"})
        self.assertTrue(self.page.last("state")["busy"])
        self.app.handle({"type": "cancel"})
        self.assertIn("отменён", self.status()["text"])
        self.assertFalse(self.page.last("state")["busy"])
        released.set()
        time.sleep(0.3)
        self.assertFalse(any(has_rows(m) for m in self.page.messages))

    def test_second_request_supersedes_first(self):
        self.configure()
        first_released = threading.Event()
        answers = iter([GOOD_ANSWER, model_answer()])

        def reply(handler):
            answer = next(answers)
            if answer is GOOD_ANSWER:
                first_released.wait(2)
            return 200, answer, {}

        self.fake.reply = reply
        self.app.handle({"type": "analyze"})
        deadline = time.monotonic() + 2
        while not self.fake.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        self.page.clear()
        self.app.handle({"type": "analyze"})
        self.page.wait_for(lambda m: m.get("type") == "status" and "не предложила" in m["text"])
        first_released.set()
        time.sleep(0.3)
        self.assertFalse(any(has_rows(m) for m in self.page.messages))

    def test_unload_during_request_is_bounded_and_silent(self):
        self.configure()

        def slow(handler):
            time.sleep(0.6)
            return 200, GOOD_ANSWER, {}

        self.fake.reply = slow
        self.app.handle({"type": "analyze"})
        started = time.monotonic()
        self.app.unload()
        self.assertLess(time.monotonic() - started, ai.SHUTDOWN_TIMEOUT_S + 0.5)
        count = len(self.page.messages)
        time.sleep(0.9)
        self.assertEqual(len(self.page.messages), count)
        self.app.handle({"type": "init"})
        self.assertEqual(len(self.page.messages), count)

    def test_garbage_messages_ignored(self):
        for message in (None, "analyze", 5, [], {"type": "unknown"}, {"no": "type"}):
            self.app.handle(message)
        self.assertEqual(self.page.messages, [])


class StorageMissingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        del self.host.plugin
        self.app.unload()
        self.app.load()

    def test_settings_kept_in_memory_only(self):
        self.app.handle({"type": "init"})
        state = self.page.last("state")
        self.assertFalse(state["storage_ok"])
        self.assertIn("нельзя сохранить", state["storage_text"])
        self.configure()
        self.assertIn("до перезапуска", self.status()["text"])
        self.assertTrue(self.page.last("state")["key_set"])
        self.assertEqual(os.listdir(self._tmp.name), [])
        self.analyze()


class ManualModeTests(AppTestCase):
    def make_host(self):
        return LegacyHost()

    def test_copy_instructions_instead_of_commit(self):
        self.app.handle({"type": "init"})
        state = self.page.last("state")
        self.assertEqual(state["mode"], "manual")
        self.assertFalse(state["storage_ok"])
        self.configure()
        rows = self.analyze()["rows"]
        prompt = self.fake.requests[0]["body"]["contents"][0]["parts"][0]["text"]
        self.assertIn("print legacy", prompt)
        self.assertNotIn("PRINTER-SECRET", prompt)
        self.app.handle({"type": "apply", "selected": [rows[0]["id"]]})
        self.assertEqual(self.page.last("instructions")["text"],
                         "Категория / параметр / текущее / предлагаемое\nПроцесс / layer_height / 0.2 / 0.16")
        self.assertEqual(self.status()["level"], "info")


def make_fake_orca(host):
    fake = types.ModuleType("orca")

    class PagesPluginCapabilityBase:
        def __init__(self):
            self.posted = []
            self._lock = threading.Lock()

        def post_message(self, data):
            with self._lock:
                self.posted.append(json.loads(json.dumps(data)))

    class Base:
        pass

    fake.pages = SimpleNamespace(PagesPluginCapabilityBase=PagesPluginCapabilityBase)
    fake.base = Base
    fake.plugins, fake.capabilities = [], []
    fake.plugin = lambda cls: fake.plugins.append(cls) or cls
    fake.register_capability = fake.capabilities.append
    fake.permission_requests = []
    fake.request_permissions = lambda **kwargs: fake.permission_requests.append(kwargs)
    fake.host = host
    return fake


class OrcaLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake = FakeGemini()
        self.addCleanup(self.fake.close)
        self.fake.reply = lambda h: (200, GOOD_ANSWER, {})
        self.host = FakeHost(self._tmp.name)
        self.orca = make_fake_orca(self.host)
        self.module = load_plugin("orca_ai_assist_lifecycle", self.orca)

    def wait_posted(self, page, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [m for m in list(page.posted) if predicate(m)]
            if matches:
                return matches[-1]
            time.sleep(0.02)
        raise AssertionError(f"message not posted; got {page.posted}")

    def test_registration_and_two_load_cycles(self):
        self.assertEqual(len(self.orca.plugins), 1)
        self.orca.plugins[0]().register_capabilities()
        self.assertEqual(self.orca.capabilities, [self.module.AiAssistPage])
        self.assertEqual(self.orca.permission_requests,
                         [{"network": ["https://generativelanguage.googleapis.com"], "threading": True}])
        page = self.module.AiAssistPage()
        self.assertEqual(page.get_name(), "AI Assist")
        self.assertIn("<html", page.get_ui())

        for cycle in range(2):
            with self.subTest(cycle=cycle):
                page.posted.clear()
                page.on_load()
                page.on_message({"type": "init"})
                self.assertEqual(page.posted[-1]["key_set"], cycle == 1)
                if cycle == 0:
                    page.on_message({"type": "save_settings", "api_key": API_KEY, "base_url": self.fake.base_url})
                    page.on_message({"type": "consent", "value": True})
                page.on_message({"type": "analyze"})
                rows = self.wait_posted(page, has_rows)["rows"]
                page.on_message({"type": "apply", "category": "print", "selected": [rows[0]["id"]]})
                self.wait_posted(page, lambda m: m.get("type") == "status" and m["level"] == "ok")
                self.assertEqual(self.host.commits[-1], ("print", [("layer_height", "0.16")]))
                self.host.values["print"]["layer_height"] = "0.2"
                page.on_unload()
                page.on_unload()
                page.on_message({"type": "init"})
                self.assertNotIn(API_KEY, json.dumps(page.posted))

        self.assertEqual(len(self.host.commits), 2)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("ai-assist-")])

    def test_registration_without_permission_api(self):
        del self.orca.request_permissions
        self.orca.plugins[0]().register_capabilities()
        self.assertEqual(self.orca.capabilities, [self.module.AiAssistPage])


if __name__ == "__main__":
    unittest.main()
