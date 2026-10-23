"""把中文描述翻成英文再送給模型（Flux 的文字編碼器幾乎只看得懂英文）。

有設定 Google Cloud Translation 金鑰就用 Google，否則用免費的 MyMemory。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Protocol

import httpx

from .templates import Template

# 中日韓統一表意文字（含擴充 A 與相容區）
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


class TranslateError(RuntimeError):
    pass


class Translator(Protocol):
    name: str

    async def translate(self, text: str) -> str: ...


def needs_translation(text: str) -> bool:
    return bool(CJK.search(text))


class GoogleTranslator:
    """Google Cloud Translation v2（需要 API 金鑰）。"""

    name = "google"
    url = "https://translation.googleapis.com/language/translate/v2"

    def __init__(self, api_key: str, http: httpx.AsyncClient):
        self.api_key = api_key
        self.http = http

    async def translate(self, text: str) -> str:
        r = await self.http.post(self.url, params={"key": self.api_key},
                                 json={"q": text, "target": "en", "format": "text"})
        if r.status_code != 200:
            raise TranslateError(f"Google 翻譯回應 {r.status_code}")
        return r.json()["data"]["translations"][0]["translatedText"]


class MyMemoryTranslator:
    """MyMemory 免費翻譯：匿名每天約 5,000 字元，帶 email 可提高到 50,000。"""

    name = "mymemory"
    url = "https://api.mymemory.translated.net/get"

    def __init__(self, http: httpx.AsyncClient, email: str | None = None):
        self.http = http
        self.email = email

    async def translate(self, text: str) -> str:
        params = {"q": text, "langpair": "zh-TW|en"}
        if self.email:
            params["de"] = self.email
        r = await self.http.get(self.url, params=params)
        if r.status_code != 200:
            raise TranslateError(f"MyMemory 回應 {r.status_code}")
        body: dict[str, Any] = r.json()
        if body.get("quotaFinished"):
            raise TranslateError("今天的免費翻譯額度用完了")
        if body.get("responseStatus") != 200:
            raise TranslateError(str(body.get("responseDetails") or "MyMemory 翻譯失敗"))
        translated = (body.get("responseData") or {}).get("translatedText", "").strip()
        if not translated or needs_translation(translated):
            raise TranslateError("翻譯結果不完整")
        return translated


class CachedTranslator:
    """同一句話不重複呼叫外部 API，省額度也省時間。"""

    def __init__(self, inner: Translator, size: int = 256):
        self.inner = inner
        self.name = inner.name
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._size = size

    async def translate(self, text: str) -> str:
        key = text.strip()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        result = await self.inner.translate(key)
        self._cache[key] = result
        if len(self._cache) > self._size:
            self._cache.popitem(last=False)
        return result


def create_translator(http: httpx.AsyncClient, google_api_key: str | None,
                      mymemory_email: str | None) -> Translator:
    inner: Translator = (GoogleTranslator(google_api_key, http) if google_api_key
                         else MyMemoryTranslator(http, mymemory_email))
    return CachedTranslator(inner)


async def translate_fields(template: Template, resolved: dict[str, Any],
                           translator: Translator) -> tuple[dict[str, Any], dict[str, str]]:
    """把標記 translate: true 且含中文的欄位翻成英文。

    回傳（送給模型的欄位值, 原文）。原文另外保存，讓使用者之後還看得到自己寫的中文。
    """
    out = dict(resolved)
    sources: dict[str, str] = {}
    for f in template.fields:
        value = out.get(f.key)
        if f.translate and isinstance(value, str) and needs_translation(value):
            try:
                out[f.key] = await translator.translate(value)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                raise TranslateError("連不上翻譯服務") from e
            sources[f.key] = value
    return out, sources
