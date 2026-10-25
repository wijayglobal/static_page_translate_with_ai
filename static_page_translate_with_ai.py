#!/usr/bin/env python3
# Copyright 2026 PS Wijay
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Static Page Translate with AI - web page -> any language (same HTML layout)
Default: English -> Tamil.  Change with  -s/--source  and  -t/--target.

Engines (groq is the first choice / default):
  groq    Groq API, free key (console.groq.com) -> env GROQ_API_KEY
  gemini  Google Gemini API, FREE key from https://aistudio.google.com/apikey
  claude  Anthropic API (paid key)      -> env ANTHROPIC_API_KEY
  ollama  local LLM, no internet needed -> `ollama serve` + a pulled model
  nllb    offline Meta NLLB model (pip install transformers torch sentencepiece)
  google  free, no key (but rate-limits/blocks easily)

Set keys:   in a .env file next to this script:
              GROQ_API_KEY=xxxx
              GEMINI_API_KEY=xxxx                      (optional, 2nd choice)
              GEMINI_MODEL=model-name-from-AI-Studio   (optional)
  or export GROQ_API_KEY=xxxx        (fish: set -x GROQ_API_KEY xxxx)

Languages:   -s en -t ta   (defaults)      --list-languages shows the table
  python static_page_translate_with_ai.py -t hi --engine groq --url https://example.com/
  python static_page_translate_with_ai.py -s fr -t si --engine groq --url-file url.txt

Usage:
  python static_page_translate_with_ai.py --engine groq intro/tutorial01/
  python static_page_translate_with_ai.py --engine groq --tutorial
  python static_page_translate_with_ai.py intro/tutorial01/        # shows a menu
  python static_page_translate_with_ai.py --engine ollama --model qwen2.5:7b intro/tutorial01/
Output: ./ta_docs/*.html (or docs_<lang>/ ; change with --out); one cache per
        engine and language pair
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://docs.djangoproject.com/en/stable/"
OUT = pathlib.Path("ta_docs")
SRC, TGT = "en", "ta"   # source / target language (set from -s / -t)

# code: (English name, NLLB/FLORES-200 code).  Any other code still works with
# the LLM engines (groq, gemini, claude, ollama) - they only need a name.
LANGS = {
    "en": ("English", "eng_Latn"), "ta": ("Tamil", "tam_Taml"),
    "si": ("Sinhala", "sin_Sinh"), "hi": ("Hindi", "hin_Deva"),
    "te": ("Telugu", "tel_Telu"), "ml": ("Malayalam", "mal_Mlym"),
    "kn": ("Kannada", "kan_Knda"), "mr": ("Marathi", "mar_Deva"),
    "bn": ("Bengali", "ben_Beng"), "gu": ("Gujarati", "guj_Gujr"),
    "pa": ("Punjabi", "pan_Guru"), "ur": ("Urdu", "urd_Arab"),
    "ne": ("Nepali", "npi_Deva"), "fr": ("French", "fra_Latn"),
    "de": ("German", "deu_Latn"), "es": ("Spanish", "spa_Latn"),
    "it": ("Italian", "ita_Latn"), "pt": ("Portuguese", "por_Latn"),
    "ru": ("Russian", "rus_Cyrl"), "uk": ("Ukrainian", "ukr_Cyrl"),
    "pl": ("Polish", "pol_Latn"), "nl": ("Dutch", "nld_Latn"),
    "sv": ("Swedish", "swe_Latn"), "el": ("Greek", "ell_Grek"),
    "tr": ("Turkish", "tur_Latn"), "ar": ("Arabic", "arb_Arab"),
    "fa": ("Persian", "pes_Arab"), "he": ("Hebrew", "heb_Hebr"),
    "id": ("Indonesian", "ind_Latn"), "ms": ("Malay", "zsm_Latn"),
    "th": ("Thai", "tha_Thai"), "vi": ("Vietnamese", "vie_Latn"),
    "ja": ("Japanese", "jpn_Jpan"), "ko": ("Korean", "kor_Hang"),
    "zh": ("Chinese (Simplified)", "zho_Hans"),
    "zh-TW": ("Chinese (Traditional)", "zho_Hant"),
    "sw": ("Swahili", "swh_Latn"),
}
LANG_ALIASES = {"zh-cn": "zh", "zh-hans": "zh", "zh-hant": "zh-TW",
                "zh-tw": "zh-TW", "iw": "he", "jw": "jv"}
GOOGLE_CODES = {"zh": "zh-CN", "he": "iw"}   # codes Google Translate expects
RTL_LANGS = {"ar", "fa", "he", "ur"}


def norm_lang(code):
    c = (code or "").strip()
    low = c.lower()
    if low == "auto":
        return "auto"
    low = LANG_ALIASES.get(low, low)
    for k in LANGS:
        if k.lower() == low:
            return k
    return low


def lang_name(code):
    if code == "auto":
        return "the original language (auto-detect)"
    return LANGS[code][0] if code in LANGS else code


def cache_name(engine):
    """Keeps the old file name for English->Tamil so earlier caches still work."""
    if (SRC, TGT) == ("en", "ta"):
        return f"cache_{engine}.json"
    return f"cache_{engine}_{SRC}-{TGT}.json"


SKIP_TAGS = {"script", "style", "pre", "code", "kbd", "samp", "var", "tt",
             "svg", "textarea", "math"}

KEEP_TERMS = [
    "Django", "Python", "pip", "virtualenv", "manage.py", "settings.py",
    "urls.py", "views.py", "models.py", "admin.py", "forms.py", "apps.py",
    "tests.py", "INSTALLED_APPS", "runserver", "startproject", "startapp",
    "makemigrations", "migrate", "shell", "QuerySet", "queryset", "Model",
    "model", "models", "View", "view", "views", "Template", "template",
    "templates", "URLconf", "URL", "URLs", "migration", "migrations",
    "app", "apps", "project", "request", "response", "HttpRequest",
    "HttpResponse", "ORM", "database", "SQLite", "PostgreSQL", "MySQL",
    "admin", "superuser", "middleware", "form", "forms", "field", "fields",
    "primary key", "foreign key", "ForeignKey", "context", "render",
    "static files", "CSS", "HTML", "JSON", "API", "HTTP", "GET", "POST",
    "CSRF", "session", "cookie", "namespace", "generic views", "test",
    "tests", "TestCase", "debug", "DEBUG", "server", "import", "class",
    "function", "method", "decorator", "slug", "token", "cache",
]
KEEP_TERMS = sorted(set(KEEP_TERMS), key=len, reverse=True)
KEEP_RE = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(t) for t in KEEP_TERMS) + r")(?![\w])"
)
RESTORE_RE = re.compile(r"X\s*Q\s*(\d+)\s*Q\s*X")


def set_keep_terms(extra, replace=False):
    """Add more terms that must stay in English (or replace the defaults)."""
    global KEEP_TERMS, KEEP_RE
    base = set() if (replace and extra) else set(KEEP_TERMS)
    KEEP_TERMS = sorted(base | set(extra), key=len, reverse=True)
    KEEP_RE = re.compile(
        r"(?<![\w])(" + "|".join(re.escape(t) for t in KEEP_TERMS) + r")(?![\w])"
    )


def read_list_file(path):
    """Non-empty lines that do not start with #."""
    out = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def find_file(name, explicit=None):
    """explicit path, else ./name, else next to this script."""
    if explicit:
        f = pathlib.Path(explicit)
        if not f.is_file():
            raise SystemExit(f"File not found: {explicit}")
        return f
    for folder in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        f = folder / name
        if f.is_file():
            return f
    return None


def out_name(url):
    if url.startswith(BASE):
        rel = url[len(BASE):].strip("/")
    else:
        u = urlparse(url)
        rel = (u.netloc + u.path).strip("/")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", rel)[:100] + ".html"


class RateLimited(Exception):
    def __init__(self, msg="", retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


class TooLarge(Exception):
    """HTTP 413 - e.g. Groq's tokens-per-minute cap is smaller than the batch."""


class Fatal(Exception):
    pass


# ---------------------------------------------------------------- helpers
def protect(text):
    terms = []

    def sub(m):
        terms.append(m.group(1))
        return f"XQ{len(terms) - 1}QX"

    return KEEP_RE.sub(sub, text), terms


def restore(text, terms):
    def sub(m):
        i = int(m.group(1))
        return terms[i] if i < len(terms) else m.group(0)

    return RESTORE_RE.sub(sub, text)


def post_json(url, headers, body, timeout=180):
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    if r.status_code == 429 and re.search(r"per ?day|PerDay", r.text, re.I):
        raise Fatal("Daily free quota used up (the reset time depends on the "
                    "provider). Run the same command again later - progress "
                    "is cached - or try another --model / --engine.")
    if r.status_code == 413:
        raise TooLarge(r.text[:200])
    if r.status_code in (429, 503):
        ra = r.headers.get("retry-after")
        try:
            ra = float(ra) if ra else None
        except ValueError:
            ra = None
        raise RateLimited(r.text[:200], ra)
    if r.status_code in (400, 401, 403, 404):
        raise Fatal(f"HTTP {r.status_code}: {r.text[:400]}")
    r.raise_for_status()
    return r.json()


def load_env():
    """Read KEY=value lines from a .env file (cwd or next to this script)."""
    for folder in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        f = folder / ".env"
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
        return


def need_key(var):
    key = os.environ.get(var)
    if not key:
        raise Fatal(f"Set the {var} environment variable first.")
    return key


def llm_prompt(keys):
    src, tgt = lang_name(SRC), lang_name(TGT)
    frm = "its original language" if SRC == "auto" else src
    return (
        f"Translate every string in the JSON array below from {frm} to "
        f"{tgt}. These are pieces of text from a web page "
        "(documentation / tutorial).\n"
        "Rules:\n"
        "- Keep these technical terms exactly as written, in their original "
        "form (do NOT translate or transliterate them): "
        + ", ".join(KEEP_TERMS) + ".\n"
        "- Also keep code, file names, commands, identifiers, URLs and "
        "version numbers unchanged.\n"
        f"- Use clear, natural {tgt} for everything else; keep punctuation.\n"
        "- Return ONLY a JSON array of strings, same length and same order, "
        "no explanations, no markdown.\n\n"
        + json.dumps(keys, ensure_ascii=False)
    )


def parse_json_list(text, n):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    data = None
    for candidate in (text, text[text.find("["): text.rfind("]") + 1]):
        try:
            data = json.loads(candidate)
            break
        except Exception:
            continue
    if isinstance(data, dict) and len(data) == 1:
        data = next(iter(data.values()))
    if isinstance(data, list) and len(data) == n and all(
            isinstance(x, str) for x in data):
        return [x.strip() for x in data]
    return None


# ---------------------------------------------------------------- engines
_google = None


def eng_google(keys, model=None):
    global _google
    from deep_translator import GoogleTranslator
    from deep_translator.exceptions import TooManyRequests
    if _google is None:
        try:
            _google = GoogleTranslator(
                source="auto" if SRC == "auto" else GOOGLE_CODES.get(SRC, SRC),
                target=GOOGLE_CODES.get(TGT, TGT))
        except Exception as e:
            raise Fatal(f"Google Translate does not accept this language "
                        f"code ({SRC} -> {TGT}): {e}")
    prot = [protect(k) for k in keys]
    try:
        out = _google.translate("\n".join(p for p, _ in prot))
    except TooManyRequests as e:
        raise RateLimited(str(e))
    lines = (out or "").split("\n")
    if len(lines) != len(keys):
        return None
    return [restore(l.strip(), t) for l, (_, t) in zip(lines, prot)]


_nllb = None


def eng_nllb(keys, model=None):
    global _nllb
    for c in (SRC, TGT):
        if c not in LANGS:
            raise Fatal(f"NLLB: language '{c}' is not supported here "
                        "(see --list-languages). Use an LLM engine instead.")
    src_code, tgt_code = LANGS[SRC][1], LANGS[TGT][1]
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    if _nllb is None:
        name = model or "facebook/nllb-200-distilled-600M"
        print(f"   loading {name} (first run downloads ~2.5 GB)...")
        tok = AutoTokenizer.from_pretrained(name, src_lang=src_code)
        mdl = AutoModelForSeq2SeqLM.from_pretrained(name)
        _nllb = (tok, mdl)
    tok, mdl = _nllb
    prot = [protect(k) for k in keys]
    results = []
    for i in range(0, len(prot), 8):
        batch = [p for p, _ in prot[i:i + 8]]
        enc = tok(batch, return_tensors="pt", padding=True,
                  truncation=True, max_length=400)
        with torch.no_grad():
            gen = mdl.generate(
                **enc,
                forced_bos_token_id=tok.convert_tokens_to_ids(tgt_code),
                max_new_tokens=512, num_beams=3)
        results += tok.batch_decode(gen, skip_special_tokens=True)
    return [restore(r.strip(), t) for r, (_, t) in zip(results, prot)]


def eng_gemini(keys, model=None):
    key = need_key("GEMINI_API_KEY")
    model = model or os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite"
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    body = {"contents": [{"parts": [{"text": llm_prompt(keys)}]}],
            "generationConfig": {"temperature": 0.2,
                                 "responseMimeType": "application/json"}}
    try:
        data = post_json(url, {"Content-Type": "application/json"}, body,
                         timeout=240)
    except Fatal as e:
        raise Fatal(f"{e}\nHint: run with --engine gemini --list-models to "
                    "see the model names your key can use, then pass one "
                    "with --model.")
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        return None
    return parse_json_list(text, len(keys))


def eng_groq(keys, model=None):
    key = need_key("GROQ_API_KEY")
    body = {"model": model or "openai/gpt-oss-120b", "temperature": 0.2,
            "messages": [{"role": "user", "content": llm_prompt(keys)}]}
    if "gpt-oss" in body["model"]:
        body["reasoning_effort"] = "low"  # less hidden thinking = fewer tokens
    try:
        data = post_json("https://api.groq.com/openai/v1/chat/completions",
                         {"Authorization": f"Bearer {key}",
                          "Content-Type": "application/json"}, body)
    except Fatal as e:
        raise Fatal(f"{e}\nHint: python static_page_translate_with_ai.py --engine "
                    "groq --list-models   (then pass one with --model)")
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        return None
    return parse_json_list(text, len(keys))


def eng_claude(keys, model=None):
    key = need_key("ANTHROPIC_API_KEY")
    body = {"model": model or "claude-sonnet-5-5", "max_tokens": 8000,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": llm_prompt(keys)}]}
    data = post_json("https://api.anthropic.com/v1/messages",
                     {"x-api-key": key, "anthropic-version": "2023-06-01",
                      "content-type": "application/json"}, body)
    text = "".join(b.get("text", "") for b in data.get("content", []))
    return parse_json_list(text, len(keys))


def eng_ollama(keys, model=None):
    body = {"model": model or "gemma3:4b", "stream": False,
            "messages": [{"role": "user", "content": llm_prompt(keys)}],
            # JSON-schema output forces a valid array of strings
            "format": {"type": "array", "items": {"type": "string"}},
            "options": {"temperature": 0.2, "num_ctx": 8192}}
    url = "http://localhost:11434/api/chat"
    try:
        try:
            data = post_json(url, {}, body, timeout=3600)
        except Fatal as e:
            if "model" in str(e).lower() and "not found" in str(e).lower():
                raise Fatal(f"{e}\nPull it first:  ollama pull {body['model']}")
            body.pop("format")  # older Ollama: retry without schema
            data = post_json(url, {}, body, timeout=3600)
    except requests.ConnectionError:
        raise Fatal("Ollama is not running. Start it with: ollama serve")
    return parse_json_list(data["message"]["content"], len(keys))


# name: (function, chunk_chars, chunk_lines, pause_seconds, description)
ENGINES = {
    "groq":   (eng_groq, 1500, 12, 6.0, "Groq API (free key, fast) - first choice"),
    "gemini": (eng_gemini, 3000, 25, 12.0, "Gemini API (free key, good alternative)"),
    "claude": (eng_claude, 6000, 50, 1.0, "Claude API (paid key)"),
    "ollama": (eng_ollama, 1500, 8, 0.0, "Local Ollama LLM (offline)"),
    "nllb":   (eng_nllb, 3500, 60, 0.0, "Offline NLLB model (needs torch)"),
    "google": (eng_google, 3500, 60, 2.0, "Google Translate (free, may be blocked)"),
}

DEFAULT_ENGINE = "groq"
engine_name = DEFAULT_ENGINE
engine_model = None
cache = {}
cache_file = OUT / f"cache_{DEFAULT_ENGINE}.json"


# ---------------------------------------------------------- translation
def run_engine(keys):
    fn, _, _, _, _ = ENGINES[engine_name]
    for attempt in range(1, 7):
        try:
            return fn(keys, engine_model)
        except RateLimited as e:
            wait = 30 * attempt
            if e.retry_after:
                wait = int(e.retry_after) + 2
            print(f"   rate limit / server busy - waiting {wait}s (try {attempt}/6)...")
            if attempt == 1:
                print("   reason:", " ".join(str(e).split())[:160])
            time.sleep(wait)
        except requests.exceptions.Timeout:
            print("   timeout - retrying with a smaller batch")
            return None
        except TooLarge:
            print("   request too large for this model's limit - retrying "
                  "with a smaller batch")
            return None
        except Fatal as e:
            raise SystemExit(f"\nERROR: {e}")
        except Exception as e:
            print(f"   error: {e!r} - retrying in 5s")
            time.sleep(5)
    raise SystemExit("\nThis engine keeps failing. Wait a while, or try "
                     "another --engine. Progress is cached.")


def solve(keys):
    res = run_engine(keys)
    time.sleep(ENGINES[engine_name][3])
    if res is None:  # wrong item count -> split
        if len(keys) == 1:
            print("   skipped (kept English):", keys[0][:50])
            return
        mid = len(keys) // 2
        solve(keys[:mid])
        solve(keys[mid:])
        return
    for k, v in zip(keys, res):
        cache[k] = v
    cache_file.write_text(json.dumps(cache, ensure_ascii=False))


def translate_many(keys):
    _, max_chars, max_lines, _, _ = ENGINES[engine_name]
    todo = [k for k in dict.fromkeys(keys) if k not in cache]
    chunks, cur, size = [], [], 0
    for k in todo:
        if cur and (size + len(k) > max_chars or len(cur) >= max_lines):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(k)
        size += len(k) + 1
    if cur:
        chunks.append(cur)
    for n, ch in enumerate(chunks, 1):
        print(f"   request {n}/{len(chunks)} ({len(ch)} pieces)")
        solve(ch)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False))


def should_translate(node):
    if node.__class__.__name__ != "NavigableString":
        return False
    text = str(node).strip()
    if not text or not re.search(r"[^\W\d_]{2,}", text):
        return False  # needs at least two letters (any alphabet)
    if text.startswith(("http://", "https://")) or \
            re.fullmatch(r"[a-z]{2,3}(-[a-z]+)?|dev", text):
        return False  # URLs and language-switcher codes
    for parent in node.parents:
        if parent.name in SKIP_TAGS:
            return False
        if hasattr(parent, "get"):
            if "notranslate" in (parent.get("class") or []) \
                    or parent.get("translate") == "no":
                return False
    return True


def translate_page(url, outname):
    print("->", url)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.head and not soup.head.find("base"):
        soup.head.insert(0, soup.new_tag("base", href=url))
    if soup.html:
        soup.html["lang"] = TGT
        if TGT in RTL_LANGS:
            soup.html["dir"] = "rtl"

    nodes = [n for n in soup.find_all(string=True) if should_translate(n)]
    keys = [" ".join(str(n).split()) for n in nodes]
    print(f"   {len(nodes)} text pieces")
    translate_many(keys)

    for node, key in zip(nodes, keys):
        original = str(node)
        lead = original[: len(original) - len(original.lstrip())]
        trail = original[len(original.rstrip()):]
        node.replace_with(lead + cache.get(key, key) + trail)

    (OUT / outname).write_text(str(soup), encoding="utf-8")
    print("   saved:", OUT / outname)


def list_groq_models():
    key = need_key("GROQ_API_KEY")
    r = requests.get("https://api.groq.com/openai/v1/models",
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    for m in r.json().get("data", []):
        print(m["id"])


def list_gemini_models():
    key = need_key("GEMINI_API_KEY")
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                     params={"key": key, "pageSize": 100}, timeout=30)
    r.raise_for_status()
    for m in r.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            print(m["name"].removeprefix("models/"))


def choose_engine():
    names = list(ENGINES)
    print("Choose translation engine:")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n:7} {ENGINES[n][4]}")
    pick = input("Number [1]: ").strip() or "1"
    return names[int(pick) - 1]


def main():
    global engine_name, engine_model, cache, cache_file, SRC, TGT, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="*", help="e.g. intro/tutorial01/")
    ap.add_argument("--url", nargs="+",
                    help="one or more full URLs of any web page")
    ap.add_argument("--url-file", metavar="FILE",
                    help="text file with one URL per line (default: url.txt "
                         "when no other pages are given)")
    ap.add_argument("--keep-file", metavar="FILE",
                    help="text file with extra terms to keep in English, one "
                         "per line (default: KEEP_TERMS.txt if it exists)")
    ap.add_argument("--replace-terms", action="store_true",
                    help="use ONLY the terms from the keep file instead of "
                         "adding them to the built-in list")
    ap.add_argument("--skip-done", action="store_true",
                    help="skip pages whose output .html already exists")
    ap.add_argument("--keep",
                    help="extra terms to keep in English, comma-separated")
    ap.add_argument("-s", "--source", default="en", metavar="LANG",
                    help="source language code (default: en; 'auto' = "
                         "detect, for google/LLM engines)")
    ap.add_argument("-t", "--target", default="ta", metavar="LANG",
                    help="target language code (default: ta = Tamil)")
    ap.add_argument("--out", metavar="DIR",
                    help="output folder (default: ta_docs, or docs_<lang>)")
    ap.add_argument("--list-languages", action="store_true",
                    help="show the built-in language table, then exit")
    ap.add_argument("--tutorial", action="store_true", help="tutorial 1-7")
    ap.add_argument("--engine", choices=list(ENGINES),
                    help="translation engine (default: a menu in a terminal, "
                         "otherwise groq)")
    ap.add_argument("--model", help="override the engine's default model")
    ap.add_argument("--batch", type=int,
                    help="pieces per request (bigger = fewer requests; "
                         "good when the daily request quota is small, try 80)")
    ap.add_argument("--seed", choices=list(ENGINES),
                    help="reuse finished translations from another engine's "
                         "cache (e.g. --engine gemini --seed groq)")
    ap.add_argument("--list-models", action="store_true",
                    help="list models your key can use (Groq by default, or "
                         "Gemini with --engine gemini), then exit")
    args = ap.parse_args()
    if args.list_languages:
        print(f"{'code':7}{'language':24}NLLB code")
        for k, (n, f) in LANGS.items():
            print(f"{k:7}{n:24}{f}")
        print("\nOther codes also work with the LLM engines "
              "(groq, gemini, claude, ollama).")
        return
    SRC, TGT = norm_lang(args.source), norm_lang(args.target)
    if TGT == "auto":
        raise SystemExit("--target cannot be 'auto'.")
    if SRC == TGT:
        raise SystemExit("--source and --target are the same language.")
    for c in (SRC, TGT):
        if c != "auto" and c not in LANGS:
            print(f"note: '{c}' is not in the built-in table; LLM engines "
                  "will still try it.")
    OUT = pathlib.Path(args.out or ("ta_docs" if TGT == "ta"
                                    else f"docs_{TGT}"))
    load_env()
    if args.list_models:
        (list_gemini_models if args.engine == "gemini"
         else list_groq_models)()
        return

    engine_name = args.engine or (choose_engine() if sys.stdin.isatty()
                                  else DEFAULT_ENGINE)
    engine_model = args.model
    # extra keep-terms: KEEP_TERMS.txt (empty file = built-in list only)
    n_default = len(KEEP_TERMS)
    kf = find_file("KEEP_TERMS.txt", args.keep_file)
    if kf:
        terms = [t.strip() for line in read_list_file(kf)
                 for t in line.split(",") if t.strip()]
        set_keep_terms(terms, replace=args.replace_terms)
        print(f"keep terms: {len(KEEP_TERMS)} "
              f"({n_default} built-in, {len(terms)} from {kf.name})")
    if args.keep:
        set_keep_terms([t.strip() for t in args.keep.split(",") if t.strip()])
    if args.batch:
        fn, _, _, pause, desc = ENGINES[engine_name]
        ENGINES[engine_name] = (fn, args.batch * 250, args.batch, pause, desc)
    OUT.mkdir(parents=True, exist_ok=True)
    cache_file = OUT / cache_name(engine_name)
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())
        stale = [k for k, v in cache.items()
                 if k == v and (" " in k or len(k) > 20)
                 and not k.startswith("http")]
        for k in stale:  # earlier run left these in English: retry them
            del cache[k]
        if stale:
            print(f"retrying {len(stale)} pieces that stayed in English")
    if args.seed and args.seed != engine_name:
        sf = OUT / cache_name(args.seed)
        if sf.exists():
            seed = json.loads(sf.read_text())
            added = 0
            for k, v in seed.items():
                if k not in cache and k != v:
                    cache[k] = v
                    added += 1
            print(f"seeded {added} translations from {args.seed}")
    print(f"engine: {engine_name}   language: {lang_name(SRC)} -> "
          f"{lang_name(TGT)}   output: {OUT}/")

    urls = []
    if args.tutorial:
        urls += [f"{BASE}intro/tutorial0{i}/" for i in range(1, 8)]
    urls += [BASE + p for p in args.path]
    urls += args.url or []
    explicit = bool(urls)
    uf = find_file("url.txt", args.url_file) if (args.url_file or not explicit) \
        else None
    if uf:
        lines = read_list_file(uf)
        print(f"{len(lines)} URLs from {uf}")
        urls += [l if l.startswith("http") else BASE + l.lstrip("/")
                 for l in lines]
    if not urls:
        ap.print_help()
        print("\nTip: put one URL per line in url.txt and just run the script.")
        return

    urls = list(dict.fromkeys(urls))  # remove duplicates, keep order
    failed = []
    for n, u in enumerate(urls, 1):
        name = out_name(u)
        print(f"[{n}/{len(urls)}]")
        if args.skip_done and (OUT / name).exists():
            print("-> skipped (already done):", name)
            continue
        try:
            translate_page(u, name)
        except Exception as e:  # one bad URL must not stop the batch
            print(f"   FAILED: {e!r}")
            failed.append(u)
    if failed:
        print("\nFailed URLs (fix and rerun):")
        for u in failed:
            print("  ", u)
    print("\nDone. Open the .html files in", OUT)


if __name__ == "__main__":
    main()
