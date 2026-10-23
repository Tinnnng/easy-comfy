import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.templates import load_templates
from app.translate import (CachedTranslator, GoogleTranslator, MyMemoryTranslator, TranslateError,
                           needs_translation, translate_fields)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def run(coro):
    return asyncio.run(coro)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeTranslator:
    name = "fake"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def translate(self, text):
        self.calls.append(text)
        if self.fail:
            raise TranslateError("額度用完")
        return "a cat on the moon"


def test_needs_translation():
    assert needs_translation("一隻貓")
    assert needs_translation("a cat 在月球")
    assert not needs_translation("a cat on the moon, 4k")


def test_mymemory_success_and_params():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"responseStatus": 200, "quotaFinished": False,
                                         "responseData": {"translatedText": "A cat on the moon"}})

    t = MyMemoryTranslator(client_for(handler), email="me@example.com")
    assert run(t.translate("月球上的貓")) == "A cat on the moon"
    assert seen["langpair"] == "zh-TW|en" and seen["de"] == "me@example.com"


@pytest.mark.parametrize("body", [
    {"responseStatus": 200, "quotaFinished": True, "responseData": {"translatedText": "x"}},
    {"responseStatus": 403, "responseDetails": "INVALID LANGUAGE PAIR"},
    {"responseStatus": 200, "responseData": {"translatedText": "月球上的貓"}},  # 沒翻成英文
])
def test_mymemory_failures(body):
    t = MyMemoryTranslator(client_for(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(TranslateError):
        run(t.translate("月球上的貓"))


def test_google_success():
    def handler(request):
        assert request.url.params["key"] == "k"
        return httpx.Response(200, json={"data": {"translations": [{"translatedText": "A cat"}]}})

    assert run(GoogleTranslator("k", client_for(handler)).translate("一隻貓")) == "A cat"


def test_cache_calls_inner_once():
    inner = FakeTranslator()
    t = CachedTranslator(inner)
    run(t.translate("一隻貓"))
    run(t.translate(" 一隻貓 "))
    assert inner.calls == ["一隻貓"]


def test_translate_fields_keeps_source():
    flux = load_templates(TEMPLATES_DIR)["flux_schnell_t2i"]
    resolved = flux.resolve_params({"prompt": "月球上的貓", "seed": 1})
    out, sources = run(translate_fields(flux, resolved, FakeTranslator()))
    assert out["prompt"] == "a cat on the moon"
    assert sources == {"prompt": "月球上的貓"}
    assert out["seed"] == 1


def test_english_prompt_is_not_translated():
    flux = load_templates(TEMPLATES_DIR)["flux_schnell_t2i"]
    translator = FakeTranslator()
    out, sources = run(translate_fields(flux, flux.resolve_params({"prompt": "a cat"}), translator))
    assert out["prompt"] == "a cat" and sources == {} and translator.calls == []


def test_api_reports_translation_failure(tmp_path):
    settings = Settings(data_dir=tmp_path, comfy_url="http://127.0.0.1:9", start_worker=False)
    with TestClient(create_app(settings, translator=FakeTranslator(fail=True))) as c:
        r = c.post("/api/jobs", json={"template_id": "flux_schnell_t2i", "params": {"prompt": "一隻貓"}})
    assert r.status_code == 503
    assert "翻譯" in r.json()["detail"]


def test_old_database_gets_sources_column(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    with create_engine(url).begin() as conn:
        conn.exec_driver_sql("CREATE TABLE job (id VARCHAR PRIMARY KEY, template_id VARCHAR)")
        conn.exec_driver_sql("INSERT INTO job (id, template_id) VALUES ('a', 't')")
    db = Database(url)
    db.init()
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT sources FROM job").scalar() == "{}"
