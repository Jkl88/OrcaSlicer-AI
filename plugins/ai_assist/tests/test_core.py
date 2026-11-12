import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "orca_ai_assist.py"


def load_plugin(name="orca_ai_assist_core"):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ai = load_plugin()

ALL_FEATURES = ["plugin_storage.v1", "preset_snapshot.v1", "preset_transaction.v1",
                "preset_select.v1", "preset_values.v1"]


def make_workspace():
    return ai.Workspace(
        {"print": "0.20mm Standard", "filament": "Generic PLA", "printer": "MK4 0.4"},
        {"print": {"layer_height": "0.2", "wall_loops": "2", "elefant_foot_compensation": "0"},
         "filament": {"nozzle_temperature": "215", "filament_flow_ratio": "0.98"},
         "printer": {"nozzle_diameter": "0.4", "retraction_length": "0.8"}})


def rec_json(**overrides):
    item = {"preset_type": "print", "key": "layer_height", "proposed_value": "0.16",
            "reason": "Мельче слой", "confidence": "high"}
    item.update(overrides)
    return item


def response(*items, summary="Итог"):
    return json.dumps({"summary": summary, "recommendations": list(items)})


class FeatureDiscoveryTests(unittest.TestCase):
    def host(self, read=True, mutate=True, storage=True, v1=False):
        host = SimpleNamespace()
        if read:
            host.preset_bundle = lambda: None
        if mutate:
            editor = SimpleNamespace(begin_update=lambda *a: None)
            host.preset = SimpleNamespace(process=editor, filament=editor, printer=editor)
        if storage:
            host.plugin = SimpleNamespace(storage=lambda: "/tmp")
        if v1:
            host.api_info = lambda: {"features": ALL_FEATURES}
            host.v1 = SimpleNamespace(plugin_storage=lambda: "/tmp")
        return host

    def test_upstream_host_is_automatic(self):
        features = ai.HostFeatures.discover(self.host())
        self.assertEqual(features.mode, ai.MODE_AUTOMATIC)
        self.assertTrue(features.storage)

    def test_read_only_host_is_manual(self):
        features = ai.HostFeatures.discover(self.host(mutate=False, storage=False))
        self.assertEqual(features.mode, ai.MODE_MANUAL)
        self.assertFalse(features.storage)

    def test_mutation_without_reader_is_unavailable(self):
        self.assertEqual(ai.HostFeatures.discover(self.host(read=False)).mode, ai.MODE_UNAVAILABLE)

    def test_fork_v1_api_is_ignored(self):
        features = ai.HostFeatures.discover(self.host(read=False, mutate=False, storage=False, v1=True))
        self.assertEqual(features.mode, ai.MODE_UNAVAILABLE)
        self.assertFalse(features.storage)

    def test_no_host_is_unavailable(self):
        self.assertEqual(ai.HostFeatures.discover(None).mode, ai.MODE_UNAVAILABLE)
        self.assertEqual(ai.HostFeatures.discover(SimpleNamespace()).mode, ai.MODE_UNAVAILABLE)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, data):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_missing_file_gives_defaults(self):
        config, warning = ai.load_config(self.dir)
        self.assertEqual(config, ai.AiConfig())
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.active.model, "gemini-2.5-flash")
        self.assertEqual(config.active.base_url, "https://generativelanguage.googleapis.com")
        self.assertEqual(config.settings["openai"].base_url, "https://api.openai.com/v1")
        self.assertEqual(config.settings["anthropic"].base_url, "https://api.anthropic.com")
        self.assertIsNone(warning)

    def test_round_trip_is_atomic_and_private(self):
        config = ai.AiConfig()
        config.provider = "openai"
        config.settings["openai"] = ai.ProviderSettings("sk-SECRET", "openai/gpt-4o", "https://openrouter.ai/api/v1",
                                                        True)
        ai.save_config(self.dir, config)
        self.assertEqual(os.listdir(self.dir), ["config.json"])
        self.assertEqual(ai.load_config(self.dir), (config, None))
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(os.path.join(self.dir, "config.json")).st_mode)
            self.assertEqual(mode, 0o600)

    def test_legacy_gemini_config_is_migrated(self):
        self.write({"gemini_api_key": "AIzaOLD", "gemini_model": "gemini-2.5-pro",
                    "base_url": "https://proxy.example/api", "preset_data_consent": True})
        config = ai.load_config(self.dir)[0]
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.active, ai.ProviderSettings("AIzaOLD", "gemini-2.5-pro", "https://proxy.example/api",
                                                            True))

    def test_failed_replace_keeps_old_file_and_no_temp(self):
        old = ai.AiConfig()
        old.active.api_key = "old-key"
        ai.save_config(self.dir, old)
        new = ai.AiConfig()
        new.active.api_key = "new-key"
        with mock.patch.object(ai.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                ai.save_config(self.dir, new)
        self.assertEqual(os.listdir(self.dir), ["config.json"])
        self.assertEqual(ai.load_config(self.dir)[0].active.api_key, "old-key")

    def test_malformed_file_gives_defaults_without_echoing_secret(self):
        self.write('{"gemini_api_key": "AIzaLEAKED123", broken')
        config, warning = ai.load_config(self.dir)
        self.assertEqual(config, ai.AiConfig())
        self.assertIn("повреждён", warning)
        self.assertNotIn("AIzaLEAKED123", warning)

    def test_non_object_file_gives_defaults(self):
        self.write("[1, 2]")
        config, warning = ai.load_config(self.dir)
        self.assertEqual(config, ai.AiConfig())
        self.assertIsNotNone(warning)

    def test_invalid_fields_fall_back_individually(self):
        self.write({"provider": "acme", "settings": {
            "gemini": {"api_key": "bad key\n", "model": "../../evil", "base_url": "http://evil.example",
                       "consent": "yes"},
            "openai": {"api_key": "sk-ok", "model": "gpt-4o", "base_url": "ftp://x", "consent": True},
            "acme": {"api_key": "x"}}})
        config = ai.load_config(self.dir)[0]
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.settings["gemini"], ai.AiConfig().settings["gemini"])
        self.assertEqual(config.settings["openai"],
                         ai.ProviderSettings("sk-ok", "gpt-4o", "https://api.openai.com/v1", True))
        self.assertNotIn("acme", config.settings)

    def test_model_names_of_all_providers_are_accepted(self):
        for name in ("gemini-2.5-flash", "openai/gpt-4o-mini", "llama3.1:8b", "claude-3-5-haiku@20241022",
                     "meta-llama/Llama-3.3-70B-Instruct"):
            self.assertEqual(ai.validate_model(name), name)
        for bad in ("", " ", "bad model", "../x", "-x", "x" * 129, "a\nb"):
            with self.subTest(model=bad), self.assertRaises(ValueError):
                ai.validate_model(bad)

    def test_base_url_validation(self):
        self.assertEqual(ai.validate_base_url("https://x.example/"), "https://x.example")
        self.assertEqual(ai.validate_base_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080")
        self.assertEqual(ai.validate_base_url("http://localhost:1"), "http://localhost:1")
        for bad in ("http://example.com", "ftp://x", "https://u:p@x.example", "https://x.example/?key=1",
                    "https://x.example:99999", "", "x.example"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                ai.validate_base_url(bad)

    def test_redact_removes_key_and_query_credentials(self):
        text = ai.redact("bad AIzaSECRET at https://h/x?key=OTHER&alt=json token=1", ["AIzaSECRET", ""])
        self.assertNotIn("AIzaSECRET", text)
        self.assertNotIn("OTHER", text)
        self.assertIn("alt=json", text)


class PayloadTests(unittest.TestCase):
    def test_allowlist_contains_no_sensitive_keys(self):
        for preset_type, keys in ai.SNAPSHOT_KEYS.items():
            self.assertEqual(len(keys), len(set(keys)), preset_type)
            for key in keys:
                self.assertFalse(ai.is_sensitive_key(key), key)
        for key in ("printhost_apikey", "print_host", "print_host_webui", "printhost_user", "bbl_token",
                    "cloud_user_id", "post_process_path", "Gemini_API_KEY"):
            self.assertTrue(ai.is_sensitive_key(key), key)

    def bundle(self):
        def preset(name, values):
            return SimpleNamespace(name=name, config_value=values.get)

        return SimpleNamespace(
            prints=SimpleNamespace(edited_preset=lambda: preset(
                "Proc", {"layer_height": "0.2", "post_process": "/home/user/script.sh", "wall_loops": 3})),
            filaments=SimpleNamespace(edited_preset=lambda: preset("PLA", {"nozzle_temperature": "215"})),
            printers=SimpleNamespace(edited_preset=lambda: preset(
                "Printer A", {"nozzle_diameter": "0.4", "printhost_apikey": "SECRET", "print_host": "10.0.0.2"})))

    def test_bundle_payload_contains_only_allowlisted_strings(self):
        workspace = ai.workspace_from_bundle(self.bundle())
        payload = ai.build_provider_payload(workspace, "углы задираются")
        self.assertEqual(payload["presets"], {"print": "Proc", "filament": "PLA", "printer": "Printer A"})
        self.assertEqual(payload["settings"], {"print": {"layer_height": "0.2", "wall_loops": "3"},
                                               "filament": {"nozzle_temperature": "215"},
                                               "printer": {"nozzle_diameter": "0.4"}})
        self.assertEqual(payload["problem"], "углы задираются")
        dumped = json.dumps(payload)
        for secret in ("SECRET", "10.0.0.2", "/home/user", "printhost", "post_process"):
            self.assertNotIn(secret, dumped)

    def test_payload_filters_tampered_workspace(self):
        workspace = ai.Workspace({"print": "P"}, {"printer": {"printhost_apikey": "SECRET", "z_hop": "0.4"}})
        payload = ai.build_provider_payload(workspace)
        self.assertEqual(payload["settings"]["printer"], {"z_hop": "0.4"})
        self.assertNotIn("problem", payload)
        self.assertIn(ai.ANALYZE_TASK, ai.build_prompt(payload))

    def test_prompt_contains_problem_task(self):
        payload = ai.build_provider_payload(make_workspace(), "stringing")
        prompt = ai.build_prompt(payload)
        self.assertIn(ai.PROBLEM_TASK, prompt)
        self.assertIn('"stringing"', prompt)

    def test_capture_reads_bundle(self):
        host = SimpleNamespace(preset_bundle=self.bundle)
        workspace = ai.capture_workspace(host, ai.HostFeatures(read=True))
        self.assertEqual(workspace.names, {"print": "Proc", "filament": "PLA", "printer": "Printer A"})
        self.assertEqual(workspace.values["printer"], {"nozzle_diameter": "0.4"})

    def test_capture_without_reader_fails(self):
        with self.assertRaises(RuntimeError):
            ai.capture_workspace(SimpleNamespace(), ai.HostFeatures())


class ParserTests(unittest.TestCase):
    def parse(self, text):
        return ai.parse_recommendations(text, make_workspace(), "r1")

    def assert_rejected(self, text, fragment=None):
        with self.assertRaises(ai.ResponseError) as ctx:
            self.parse(text)
        if fragment:
            self.assertIn(fragment, str(ctx.exception))

    def test_valid_response_joins_old_value_from_snapshot(self):
        analysis = self.parse(response(rec_json(), rec_json(preset_type="filament", key="nozzle_temperature",
                                                            proposed_value="210", reason="", confidence=None)))
        self.assertEqual(analysis.names, make_workspace().names)
        self.assertEqual(analysis.summary, "Итог")
        first, second = analysis.recommendations
        self.assertEqual((first.row_id, first.old_value, first.proposed_value, first.confidence),
                         ("r1-1", "0.2", "0.16", "high"))
        self.assertEqual((second.row_id, second.old_value, second.confidence), ("r1-2", "215", ""))

    def test_empty_recommendations_allowed(self):
        self.assertEqual(self.parse(response()).recommendations, ())

    def test_fence_stripped_once(self):
        self.assertEqual(len(self.parse("```json\n" + response(rec_json()) + "\n```").recommendations), 1)
        self.assertEqual(len(self.parse("```" + response(rec_json()) + "```").recommendations), 1)
        self.assert_rejected("```json\n```json\n" + response() + "\n```\n```", "JSON")

    def test_numeric_value_coerced(self):
        self.assertEqual(self.parse(response(rec_json(proposed_value=0.16))).recommendations[0].proposed_value,
                         "0.16")
        self.assertEqual(self.parse(response(rec_json(key="wall_loops", proposed_value=3)))
                         .recommendations[0].proposed_value, "3")

    def test_empty_and_invalid_json(self):
        self.assert_rejected("", "пустой")
        self.assert_rejected("   ", "пустой")
        self.assert_rejected('{"summary": "x", ', "JSON")
        self.assert_rejected("Sure! Here you go", "JSON")

    def test_top_level_shape(self):
        self.assert_rejected("[]")
        self.assert_rejected(json.dumps({"summary": "x"}))
        self.assert_rejected(json.dumps({"summary": "x", "recommendations": [], "extra": 1}))
        self.assert_rejected(json.dumps({"summary": 5, "recommendations": []}), "summary")
        self.assert_rejected(json.dumps({"summary": "x", "recommendations": {}}), "recommendations")

    def test_item_shape(self):
        self.assert_rejected(response("layer_height"), "объект")
        self.assert_rejected(response(rec_json(extra="x")), "неизвестные поля")
        item = rec_json()
        del item["reason"]
        self.assert_rejected(response(item), "reason")
        self.assert_rejected(response(rec_json(key=5)), "key")
        self.assert_rejected(response(rec_json(reason=1)), "reason")
        self.assert_rejected(response(rec_json(confidence="certain")), "confidence")

    def test_value_types(self):
        for bad in (True, None, [], {}, float("nan"), "", "  ", "x" * 201, "0.2\n; G28"):
            with self.subTest(value=bad):
                self.assert_rejected(response(rec_json(proposed_value=bad)))

    def test_unknown_preset_type(self):
        self.assert_rejected(response(rec_json(preset_type="process")), "категория")
        self.assert_rejected(response(rec_json(preset_type=["print"])), "категория")

    def test_key_not_in_allowlist(self):
        self.assert_rejected(response(rec_json(key="post_process")), "не входит")
        self.assert_rejected(response(rec_json(preset_type="filament", key="layer_height")), "не входит")

    def test_sensitive_key(self):
        self.assert_rejected(response(rec_json(preset_type="printer", key="printhost_apikey")), "секрет")
        self.assert_rejected(response(rec_json(preset_type="printer", key="print_host")), "секрет")

    def test_allowlisted_key_absent_from_snapshot(self):
        self.assert_rejected(response(rec_json(key="travel_speed")), "нет в отправленных")

    def test_duplicate_key(self):
        self.assert_rejected(response(rec_json(), rec_json(proposed_value="0.12")), "повторяется")

    def test_model_supplied_old_value(self):
        self.assert_rejected(response(rec_json(old_value="0.3")), "old_value")

    def test_oversized(self):
        self.assert_rejected(" " * 10 + "x" * (ai.MAX_MODEL_TEXT_CHARS + 1), "длинный")
        self.assert_rejected(response(summary="x" * (ai.MAX_SUMMARY_CHARS + 1)), "summary")
        self.assert_rejected(response(rec_json(reason="x" * (ai.MAX_REASON_CHARS + 1))), "reason")
        many = [rec_json(key=k) for k in ai.SNAPSHOT_KEYS["print"][:ai.MAX_RECOMMENDATIONS + 1]]
        self.assert_rejected(response(*many), "recommendations")


def three_recommendations():
    return (ai.Recommendation("r1-1", "print", "layer_height", "0.2", "0.16", ""),
            ai.Recommendation("r1-2", "print", "wall_loops", "2", "3", ""),
            ai.Recommendation("r1-3", "filament", "nozzle_temperature", "215", "210", ""))


class StaleRowsTests(unittest.TestCase):
    def test_changed_and_missing_values_are_stale(self):
        now = ai.Workspace({}, {"print": {"layer_height": "0.2", "wall_loops": "4"}, "filament": {}})
        stale = ai.stale_rows(now, three_recommendations())
        self.assertEqual([r.row_id for r in stale], ["r1-2", "r1-3"])

    def test_unchanged_values_are_fresh(self):
        self.assertEqual(ai.stale_rows(make_workspace(), three_recommendations()[:1]), [])


class FilamentSlotTests(unittest.TestCase):
    def bundle(self, slots, edited):
        return SimpleNamespace(current_filament_preset_names=lambda: list(slots),
                               filaments=SimpleNamespace(edited_preset=lambda: SimpleNamespace(name=edited)))

    def test_first_assigned_slot_is_one_based(self):
        self.assertEqual(ai.filament_slot_for(self.bundle(["A", "B", "B"], "B")), 2)

    def test_unassigned_edited_preset(self):
        self.assertIsNone(ai.filament_slot_for(self.bundle(["A", "B"], "C")))
        self.assertIsNone(ai.filament_slot_for(self.bundle([], "A")))


class FakeTransaction:
    def __init__(self, host, target_name):
        self.host, self.target_name, self.staged = host, target_name, []

    def set(self, key, value):
        self.staged.append((key, value))

    def commit(self):
        if self.host.commit_error:
            raise self.host.commit_error
        self.host.commits.append(list(self.staged))
        return SimpleNamespace(keys=[k for k, _ in self.staged], target_name=self.target_name, dirty=True)


class ApplyHost:
    def __init__(self, answer="yes", target_name="0.20mm Standard"):
        self.answer, self.target_name = answer, target_name
        self.dialogs, self.begins, self.commits = [], [], []
        self.begin_error = self.commit_error = None
        editor = lambda category: SimpleNamespace(begin_update=lambda *a: self.begin(category, a))
        self.preset = SimpleNamespace(process=editor("process"), filament=editor("filament"),
                                      printer=editor("printer"))
        self.ui = SimpleNamespace(message=self.message)

    def message(self, text, title="OrcaSlicer", buttons="ok", icon="info"):
        self.dialogs.append((text, title, buttons, icon))
        return self.answer

    def begin(self, category, args):
        self.begins.append((category, args))
        if self.begin_error:
            raise self.begin_error
        return FakeTransaction(self, self.target_name)


class ApplyCategoryTests(unittest.TestCase):
    rows = three_recommendations()[:2]

    def apply(self, host, category="print", slot=None, rows=None, name="0.20mm Standard"):
        return ai.apply_category(host, category, slot, rows or self.rows, name)

    def test_confirmed_print_rows_committed_in_one_transaction(self):
        host = ApplyHost()
        outcome = self.apply(host)
        self.assertEqual(host.begins, [("process", ("",))])
        self.assertEqual(host.commits, [[("layer_height", "0.16"), ("wall_loops", "3")]])
        self.assertEqual((outcome.level, outcome.discard_rows), ("ok", True))
        self.assertIn("2", outcome.message)
        text, _title, buttons, _icon = host.dialogs[0]
        self.assertEqual(buttons, "yes_no")
        self.assertIn("layer_height: 0.2 → 0.16", text)
        self.assertIn("Процесс", text)

    def test_filament_targets_slot(self):
        host = ApplyHost(target_name="Generic PLA")
        rows = three_recommendations()[2:]
        self.assertEqual(self.apply(host, "filament", 2, rows, "Generic PLA").level, "ok")
        self.assertEqual(host.begins, [("filament", (2,))])

    def test_printer_uses_printer_editor(self):
        host = ApplyHost(target_name="MK4")
        self.apply(host, "printer", None, [ai.Recommendation("x", "printer", "z_hop", "0.4", "0.2", "")], "MK4")
        self.assertEqual(host.begins, [("printer", ("",))])

    def test_declined_confirmation_applies_nothing(self):
        host = ApplyHost(answer="no")
        outcome = self.apply(host)
        self.assertEqual(host.begins, [])
        self.assertEqual((outcome.level, outcome.discard_rows), ("info", False))
        self.assertIn("отменено", outcome.message)

    def test_cancelled_unsaved_prompt(self):
        host = ApplyHost()
        host.begin_error = RuntimeError("Switching ... was cancelled")
        outcome = self.apply(host)
        self.assertEqual((outcome.level, outcome.discard_rows), ("info", False))
        self.assertIn("отменено", outcome.message)

    def test_unavailable_target(self):
        host = ApplyHost()
        host.begin_error = ValueError("Filament slot 3 is out of range")
        outcome = self.apply(host)
        self.assertEqual((outcome.level, outcome.discard_rows), ("error", False))
        self.assertIn("slot 3", outcome.message)

    def test_target_mismatch_does_not_commit(self):
        host = ApplyHost(target_name="Other")
        outcome = self.apply(host)
        self.assertEqual(host.commits, [])
        self.assertEqual((outcome.level, outcome.discard_rows), ("error", True))
        self.assertIn("заново", outcome.message)

    def test_commit_value_error_applies_nothing(self):
        host = ApplyHost()
        host.commit_error = ValueError("Invalid value for 'layer_height'")
        outcome = self.apply(host)
        self.assertEqual((outcome.level, outcome.discard_rows), ("error", False))
        self.assertIn("ничего не применено", outcome.message)
        self.assertIn("layer_height", outcome.message)

    def test_commit_runtime_error_means_target_changed(self):
        host = ApplyHost()
        host.commit_error = RuntimeError("The edited process preset is \"B\"")
        outcome = self.apply(host)
        self.assertEqual((outcome.level, outcome.discard_rows), ("error", True))
        self.assertIn("ничего не применено", outcome.message)


class ManualInstructionsTests(unittest.TestCase):
    def test_format(self):
        rows = ai.select_rows(three_recommendations(), ["r1-1", "r1-3"])
        self.assertEqual(ai.format_manual_instructions(rows),
                         "Категория / параметр / текущее / предлагаемое\n"
                         "Процесс / layer_height / 0.2 / 0.16\n"
                         "Пластик / nozzle_temperature / 215 / 210")


class PageTests(unittest.TestCase):
    def test_settings_are_not_overwritten_while_edited_and_model_saves_on_change(self):
        page = ai.PAGE_HTML
        self.assertIn("if (document.activeElement !== $('model')) $('model').value = s.model;", page)
        self.assertIn("$('model-list').addEventListener('change'", page)
        self.assertIn("$('model').addEventListener('change'", page)
        self.assertIn("send('load_models')", page)
        self.assertNotIn("$('model').value = s.model; $('base-url').value = s.base_url;", page)

    def test_page_is_self_contained(self):
        page = ai.PAGE_HTML
        self.assertIn("orca.postMessage", page)
        self.assertIn("orca.onMessage", page)
        self.assertIn("elefant_foot_compensation", page)
        self.assertNotIn("__ALLOWED_KEYS__", page)
        for external in ("http://", "https://", "src=", "<link"):
            self.assertNotIn(external, page)
        for excluded in ("Telegram", "Moonraker", "webcam", "камер", "монитор"):
            self.assertNotIn(excluded.lower(), page.lower())


if __name__ == "__main__":
    unittest.main()
