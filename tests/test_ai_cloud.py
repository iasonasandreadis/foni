"""Claude and OpenAI-style services: requests, fallbacks to the Mac model, and spend tracking."""
import http.server
import json
import threading
import types

import anthropic
import pytest

from foni.ai_cloud import ClaudeSpend, CloudEditor, OpenAIEditor, fmt_usd


class FakeLocal:
    def __init__(self):
        self.used = 0

    def clean(self, text, *a, **k):
        self.used += 1
        return "LOCAL: " + text

    def command(self, *a):
        self.used += 1
        return "LOCAL command"


def _err(cls):  # an SDK error without a real HTTP response
    e = cls.__new__(cls)
    Exception.__init__(e, cls.__name__)
    return e


class FakeMessages:
    def __init__(self):
        self.calls, self.mode = [], "ok"

    def create(self, **kw):
        self.calls.append(kw)
        if self.mode == "auth":
            raise _err(anthropic.AuthenticationError)
        if self.mode == "offline":
            raise _err(anthropic.APIConnectionError)
        usage = types.SimpleNamespace(input_tokens=700, output_tokens=60,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=None)
        return types.SimpleNamespace(stop_reason="refusal" if self.mode == "refuse" else "end_turn",
                                     content=[types.SimpleNamespace(type="text", text="OK")], usage=usage)


@pytest.fixture
def claude(tmp_path):
    local, fake = FakeLocal(), FakeMessages()
    ed = CloudEditor("not-a-real-key", "claude-haiku-5-5", lambda: local, meter=ClaudeSpend(tmp_path / "s.json"))
    ed.client = types.SimpleNamespace(messages=fake)
    return ed, fake, local


def test_claude_request(claude):
    ed, fake, _ = claude
    assert ed.clean("hello there friend", "general", []) == "OK"
    kw = fake.calls[-1]
    assert kw["model"] == "claude-haiku-5-5" and kw["thinking"] == {"type": "disabled"}
    assert "<transcript>" in kw["messages"][-1]["content"]


def test_claude_falls_back_to_mac_model(claude):
    ed, fake, local = claude
    fake.mode = "offline"
    assert ed.clean("hello there", "general", []).startswith("LOCAL") and not ed.broken
    fake.mode = "refuse"
    assert ed.clean("hello there", "general", []).startswith("LOCAL")
    fake.mode = "auth"
    assert ed.clean("hello there", "general", []).startswith("LOCAL") and ed.broken
    n = len(fake.calls)
    ed.clean("again", "general", [])
    assert len(fake.calls) == n  # stops using a rejected key


def test_claude_spend(claude):
    ed, _, _ = claude
    ed.clean("hello there friend", "general", [])
    s = ed.meter.summary()
    assert abs(s["today"][0] - (700 * 0.10 + 60 * 0.50) / 1e6) < 1e-12 and s["today"][1] == 1
    assert fmt_usd(0.0007) == "$0.0007" and fmt_usd(1.5) == "$1.50"


# ---- OpenAI-style service, against a tiny local server
class Server:
    def __init__(self):
        self.requests, self.reject_temperature, self.status = [], False, 200
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, dict(self.headers), body))
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    self.wfile.write(b'{"error": "nope"}')
                    return
                if outer.reject_temperature and "temperature" in body:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "temperature is not supported with this model"}')
                    return
                reply = {"choices": [{"message": {"role": "assistant", "content": " Cleaned. "}}]}
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()


def test_openai_request(server):
    ed = OpenAIEditor(server.url, "some-model", "sk-test", lambda: FakeLocal())
    assert ed.clean("um hello there friend", "general", []) == "Cleaned."
    path, headers, body = server.requests[-1]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "some-model" and body["messages"][0]["role"] == "system"
    assert body["temperature"] == 0


def test_openai_without_key_for_local_servers(server):
    ed = OpenAIEditor(server.url, "llama", None, lambda: FakeLocal())
    assert ed.ping() is None
    assert "Authorization" not in server.requests[-1][1]


def test_openai_adapts_to_models_without_temperature(server):
    server.reject_temperature = True
    ed = OpenAIEditor(server.url, "reasoning-model", "k", lambda: FakeLocal())
    assert ed.clean("um hello there friend", "general", []) == "Cleaned."
    assert "temperature" not in server.requests[-1][2]


def test_openai_bad_key_falls_back_and_stops(server):
    server.status = 401
    local = FakeLocal()
    ed = OpenAIEditor(server.url, "m", "bad", lambda: local)
    assert ed.ping() == "the key was rejected"
    assert ed.clean("hello there", "general", []).startswith("LOCAL") and ed.broken


def test_openai_unreachable_falls_back():
    local = FakeLocal()
    ed = OpenAIEditor("http://127.0.0.1:9/v1", "m", None, lambda: local)
    assert ed.command("make it formal", "hi") == "LOCAL command" and not ed.broken
