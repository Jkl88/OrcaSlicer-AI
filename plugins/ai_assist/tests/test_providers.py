import importlib.util
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "orca_ai_assist.py"
API_KEY = "AIzaTESTKEY-123"


def load_plugin(name="orca_ai_assist_providers"):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ai = load_plugin()


def gemini_body(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def openai_body(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def anthropic_body(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


ANSWER_BODIES = {"gemini": gemini_body, "openai": openai_body, "anthropic": anthropic_body}


class FakeProvider:
    """Local HTTP server; `reply(handler)` returns (status, body bytes or JSON value, extra headers)."""

    def __init__(self):
        self.requests = []
        self.reply = lambda handler: (200, gemini_body('{"summary": "ok", "recommendations": []}'), {})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self, body):
                fake.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                      "body": body})
                reply = fake.reply(self)
                if reply is None:  # the reply function already wrote the response
                    return
                status, data, headers = reply
                data = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.handle_request(json.loads(self.rfile.read(length) or b"null"))

            def do_GET(self):
                self.handle_request(None)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True)
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


FakeGemini = FakeProvider


class ProviderTestCase(unittest.TestCase):
    provider = "gemini"
    model = "gemini-2.5-flash"

    def setUp(self):
        self.fake = FakeProvider()
        self.addCleanup(self.fake.close)

    def client(self, key=API_KEY, model=None, base_url=None, **kwargs):
        return ai.AiClient(self.provider, key, model or self.model, base_url or self.fake.base_url, **kwargs)

    def generate(self, client=None, cancel=None):
        return (client or self.client()).generate("PROMPT", cancel or threading.Event())

    def assert_error(self, fragment, client=None):
        with self.assertRaises(ai.AiError) as ctx:
            self.generate(client)
        message = str(ctx.exception)
        self.assertIn(fragment, message)
        self.assertNotIn(API_KEY, message)
        return message

    def headers(self, index=0):
        return {k.lower(): v for k, v in self.fake.requests[index]["headers"].items()}


class GeminiClientTests(ProviderTestCase):
    def test_request_shape_and_key_header(self):
        self.fake.reply = lambda h: (200, gemini_body("part1"), {})
        self.assertEqual(self.generate(), "part1")
        request = self.fake.requests[0]
        self.assertEqual(request["path"], "/v1beta/models/gemini-2.5-flash:generateContent")
        self.assertNotIn(API_KEY, request["path"])
        headers = self.headers()
        self.assertEqual(headers["x-goog-api-key"], API_KEY)
        self.assertEqual(headers["content-type"], "application/json")
        body = request["body"]
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "PROMPT")
        self.assertNotIn(API_KEY, json.dumps(body))

    def test_multiple_parts_joined(self):
        body = {"candidates": [{"content": {"parts": [{"text": "a"}, {"inline": 1}, {"text": "b"}]}}]}
        self.fake.reply = lambda h: (200, body, {})
        self.assertEqual(self.generate(), "ab")

    def test_http_errors_are_actionable_and_redacted(self):
        for code, fragment in ((400, "400"), (403, "ключ API"), (404, "модель не найдена"), (429, "лимит"),
                               (503, "временно недоступен")):
            with self.subTest(code=code):
                error = {"error": {"message": f"API key {API_KEY} rejected, see ?key={API_KEY}"}}
                self.fake.reply = lambda h, c=code, e=error: (c, e, {})
                message = self.assert_error(fragment)
                self.assertIn(f"HTTP {code}", message)
                self.assertIn("rejected", message)
                self.assertIn("Google Gemini", message)

    def test_redirect_not_followed(self):
        self.fake.reply = lambda h: (302, b"", {"Location": self.fake.base_url + "/elsewhere"})
        self.assert_error("перенаправление")
        self.assertEqual(len(self.fake.requests), 1)

    def test_non_json_body(self):
        self.fake.reply = lambda h: (200, b"<html>proxy error</html>", {})
        self.assert_error("не в формате JSON")

    def test_missing_candidates_and_block_reason(self):
        self.fake.reply = lambda h: (200, {"promptFeedback": {"blockReason": "SAFETY"}}, {})
        self.assert_error("SAFETY")
        self.fake.reply = lambda h: (200, {"candidates": []}, {})
        self.assert_error("ни одного варианта")
        self.fake.reply = lambda h: (200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]},
                                     {})
        self.assert_error("MAX_TOKENS")

    def test_oversized_body(self):
        self.fake.reply = lambda h: (200, b"x" * 5000, {})
        self.assert_error("больше", self.client(max_bytes=1024))

    def test_oversized_body_without_content_length(self):
        def reply(handler):
            handler.send_response(200)
            handler.end_headers()
            handler.close_connection = True
            try:
                handler.wfile.write(b"y" * 200_000)
            except OSError:
                pass

        self.fake.reply = reply
        self.assert_error("больше", self.client(max_bytes=1024))

    def test_timeout(self):
        def slow(handler):
            time.sleep(1.0)
            return 200, gemini_body("late"), {}

        self.fake.reply = slow
        started = time.monotonic()
        self.assert_error("не ответил", self.client(timeout=0.3))
        self.assertLess(time.monotonic() - started, 0.9)

    def test_connection_refused(self):
        with self.assertRaises(ai.AiError) as ctx:
            self.client(base_url="http://127.0.0.1:1").generate("p", threading.Event())
        self.assertIn("подключиться", str(ctx.exception))

    def test_cancel_before_send(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ai.TaskCancelled):
            self.generate(cancel=cancel)
        self.assertEqual(self.fake.requests, [])

    def test_cancel_after_response(self):
        cancel = threading.Event()

        def reply(handler):
            cancel.set()
            return 200, gemini_body("ignored"), {}

        self.fake.reply = reply
        with self.assertRaises(ai.TaskCancelled):
            self.generate(cancel=cancel)

    def test_missing_key(self):
        with self.assertRaises(ai.AiError):
            self.client(key="").generate("p", threading.Event())
        self.assertEqual(self.fake.requests, [])

    def test_insecure_remote_url_rejected(self):
        with self.assertRaises(ValueError):
            ai.AiClient("gemini", API_KEY, "m", "http://generativelanguage.googleapis.com")

    def test_list_models_keeps_generate_content_models(self):
        models = {"models": [
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/bad name", "supportedGenerationMethods": ["generateContent"]}]}
        self.fake.reply = lambda h: (200, models, {})
        self.assertEqual(self.client().list_models(threading.Event()), ["gemini-2.5-flash", "gemini-2.5-pro"])
        request = self.fake.requests[0]
        self.assertEqual((request["method"], request["path"]), ("GET", "/v1beta/models?pageSize=1000"))
        self.assertEqual(self.headers()["x-goog-api-key"], API_KEY)


class OpenAiCompatibleClientTests(ProviderTestCase):
    provider = "openai"
    model = "openai/gpt-4o-mini"

    def test_chat_completion_shape(self):
        self.fake.reply = lambda h: (200, openai_body("answer"), {})
        self.assertEqual(self.generate(), "answer")
        request = self.fake.requests[0]
        self.assertEqual((request["method"], request["path"]), ("POST", "/chat/completions"))
        self.assertEqual(self.headers()["authorization"], "Bearer " + API_KEY)
        body = request["body"]
        self.assertEqual(body["model"], "openai/gpt-4o-mini")
        self.assertEqual(body["messages"], [{"role": "user", "content": "PROMPT"}])
        self.assertNotIn(API_KEY, json.dumps(body))

    def test_local_server_without_key(self):
        self.fake.reply = lambda h: (200, openai_body("local"), {})
        self.assertEqual(self.client(key="", model="llama3.1:8b").generate("p", threading.Event()), "local")
        self.assertNotIn("authorization", self.headers())

    def test_remote_server_needs_key(self):
        with self.assertRaises(ai.AiError):
            ai.AiClient("openai", "", "m", "https://api.openai.com/v1").generate("p", threading.Event())

    def test_empty_choice_is_reported(self):
        self.fake.reply = lambda h: (200, {"choices": []}, {})
        self.assert_error("ни одного варианта")
        self.fake.reply = lambda h: (200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}, {})
        self.assert_error("length")

    def test_error_message_is_redacted(self):
        self.fake.reply = lambda h: (401, {"error": {"message": f"Incorrect API key {API_KEY}"}}, {})
        message = self.assert_error("401")
        self.assertIn("Incorrect API key", message)

    def test_list_models(self):
        self.fake.reply = lambda h: (200, {"data": [{"id": "gpt-4o"}, {"id": "llama3.1:8b"}, {"id": 5}]}, {})
        self.assertEqual(self.client().list_models(threading.Event()), ["gpt-4o", "llama3.1:8b"])
        request = self.fake.requests[0]
        self.assertEqual((request["method"], request["path"]), ("GET", "/models"))
        self.assertEqual(self.headers()["authorization"], "Bearer " + API_KEY)


class AnthropicClientTests(ProviderTestCase):
    provider = "anthropic"
    model = "claude-model"

    def test_messages_shape(self):
        self.fake.reply = lambda h: (200, anthropic_body("claude"), {})
        self.assertEqual(self.generate(), "claude")
        request = self.fake.requests[0]
        self.assertEqual((request["method"], request["path"]), ("POST", "/v1/messages"))
        headers = self.headers()
        self.assertEqual(headers["x-api-key"], API_KEY)
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        body = request["body"]
        self.assertEqual(body["model"], "claude-model")
        self.assertEqual(body["messages"], [{"role": "user", "content": "PROMPT"}])
        self.assertGreater(body["max_tokens"], 0)

    def test_empty_content_is_reported(self):
        self.fake.reply = lambda h: (200, {"content": [], "stop_reason": "max_tokens"}, {})
        self.assert_error("max_tokens")

    def test_list_models(self):
        self.fake.reply = lambda h: (200, {"data": [{"id": "claude-b"}, {"id": "claude-a"}]}, {})
        self.assertEqual(self.client().list_models(threading.Event()), ["claude-a", "claude-b"])
        request = self.fake.requests[0]
        self.assertEqual((request["method"], request["path"]), ("GET", "/v1/models?limit=1000"))
        self.assertEqual(self.headers()["x-api-key"], API_KEY)


class UnknownProviderTests(unittest.TestCase):
    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError):
            ai.AiClient("acme", API_KEY, "m", "https://acme.example")


class TaskControllerTests(unittest.TestCase):
    def setUp(self):
        self.delivered = []
        self.delivered_event = threading.Event()

        def deliver(generation, kind, ok, value):
            self.delivered.append((kind, ok, value))
            self.delivered_event.set()

        self.tasks = ai.TaskController(deliver)
        self.addCleanup(self.tasks.shutdown, 1.0)

    def test_late_result_of_a_does_not_overwrite_b(self):
        release_a = threading.Event()

        def work_a(cancel):
            release_a.wait(2)
            return "A"

        self.tasks.submit("a", work_a)
        self.tasks.submit("b", lambda cancel: "B")
        self.assertTrue(self.delivered_event.wait(2))
        release_a.set()
        self.assertTrue(self.tasks.shutdown(2))
        self.assertEqual(self.delivered, [("b", True, "B")])

    def test_errors_are_delivered(self):
        def fail(cancel):
            raise ai.AiError("boom")

        self.tasks.submit("x", fail)
        self.assertTrue(self.delivered_event.wait(2))
        kind, ok, value = self.delivered[0]
        self.assertFalse(ok)
        self.assertIsInstance(value, ai.AiError)
        self.assertFalse(self.tasks.busy)

    def test_cancel_suppresses_result(self):
        release = threading.Event()
        self.tasks.submit("x", lambda cancel: release.wait(2) and "late")
        self.assertTrue(self.tasks.busy)
        self.assertTrue(self.tasks.cancel_current())
        self.assertFalse(self.tasks.busy)
        self.assertFalse(self.tasks.cancel_current())
        release.set()
        self.assertTrue(self.tasks.shutdown(2))
        self.assertEqual(self.delivered, [])

    def test_shutdown_is_bounded_and_silences_blocked_worker(self):
        finished = threading.Event()

        def blocking(cancel):
            time.sleep(0.8)  # ignores cancellation, like a blocking socket call
            finished.set()
            return "late"

        self.tasks.submit("x", blocking)
        started = time.monotonic()
        self.assertFalse(self.tasks.shutdown(0.1))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(finished.wait(2))
        time.sleep(0.05)
        self.assertEqual(self.delivered, [])
        with self.assertRaises(RuntimeError):
            self.tasks.submit("y", lambda cancel: None)

    def test_shutdown_joins_cooperative_worker(self):
        self.tasks.submit("x", lambda cancel: cancel.wait(5))
        started = time.monotonic()
        self.assertTrue(self.tasks.shutdown(2))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.delivered, [])


if __name__ == "__main__":
    unittest.main()
