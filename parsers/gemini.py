"""Gemini (My Activity) export parser.

Format (verified):
  - MyActivity.json is a JSON array of flat activity events (~106 entries)
  - entry: {header, title, time (RFC3339 Z), products, activityControls,
            details?: [{name, url}], safeHtmlItem?: [{html}],
            imageFile?, attachedFiles?, subtitles?}
  - title "Prompted <query>" = user turn; safeHtmlItem[0].html = assistant HTML
  - conversation grouping must be reconstructed by adjacency on details[0].url
"""
import hashlib
import html as html_mod
import json
import os
import re
from html.parser import HTMLParser

from .base import asset_url, file_extension, mime_for, truncate, utc_ts

SERVICE = "gemini"

ACTIVITY_FILE = "MyActivity.json"

PROMPT_PREFIX = "Prompted "

_BLOCK_ORDER = [
    (re.compile(r"</?(h[1-4])[^>]*>", re.I), None),  # handled per-tag below
]


def load_raw(gemini_dir):
    fp = os.path.join(gemini_dir, ACTIVITY_FILE)
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def group_into_threads(entries):
    """Group consecutive entries sharing the same details[0].url.

    An entry with no details breaks adjacency (new group). Entries that carry
    the url but are meta (e.g. "Used an Assistant feature") stay in the group
    and are filtered out during turn building.
    """
    groups = []
    current_url = None
    for e in entries:
        details = e.get("details") or []
        url = (details[0].get("url") if details and isinstance(details[0], dict) else None) or None
        if url and url == current_url:
            groups[-1].append(e)
        else:
            if url is not None:
                groups.append([e])
        current_url = url
    return groups


def _url_id(url):
    """Conversation id derived from the details url hash."""
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1]


def _entry_url(entry):
    details = entry.get("details") or []
    if details and isinstance(details[0], dict):
        return details[0].get("url")
    return None


def normalize_group(group, gemini_dir):
    """Turn one thread group into a normalized conversation dict (or None).

    Each "Prompted <query>" entry carries BOTH the user prompt (title) and its
    own assistant response (safeHtmlItem[0].html) — they are not separate
    entries. Additional non-Prompted entries that share the url (e.g. "Used an
    Assistant feature") are skipped.
    """
    turns = []
    url = _entry_url(group[0])
    for entry in group:
        title = entry.get("title") or ""
        if title.startswith(PROMPT_PREFIX):
            user_text = title[len(PROMPT_PREFIX):].strip()
            if not user_text:
                continue
            safe = entry.get("safeHtmlItem")
            assistant = None
            assistant_time = None
            if safe and isinstance(safe, list) and safe and isinstance(safe[0], dict):
                assistant = html_to_markdown(safe[0].get("html") or "")
                assistant_time = utc_ts(entry.get("time"))
            turn = {
                "user": user_text,
                "time": utc_ts(entry.get("time")),
                "images": [],
                "attachments": [],
                "assistant": assistant,
                "assistant_time": assistant_time,
            }
            img = entry.get("imageFile")
            if img:
                turn["images"].append({
                    "src": asset_url(SERVICE, img),
                    "name": img,
                    "mime": mime_for(img),
                })
            for fn in entry.get("attachedFiles") or []:
                turn["attachments"].append({
                    "name": fn,
                    "mime": mime_for(fn),
                    "src": asset_url(SERVICE, fn),
                    "size": None,
                })
            turns.append(turn)
        else:
            # Non-prompted entries share the url but are meta/tool activity.
            # Capture a response only if the last turn is missing one.
            if turns and not turns[-1].get("assistant"):
                safe = entry.get("safeHtmlItem")
                if safe and isinstance(safe, list) and safe and isinstance(safe[0], dict):
                    turns[-1]["assistant"] = html_to_markdown(safe[0].get("html") or "")
                    turns[-1]["assistant_time"] = utc_ts(entry.get("time"))

    if not turns:
        return None

    messages = []
    title = None
    first_ts = None
    last_ts = None
    for turn in turns:
        t = turn["time"]
        if first_ts is None or (t and t < first_ts):
            first_ts = t
        if t and (last_ts is None or t > last_ts):
            last_ts = t
        if title is None and turn["user"]:
            title = truncate(turn["user"], 60)
        messages.append({
            "role": "user",
            "content": turn["user"],
            "time": t,
            "model": None,
            "kind": "gemini",
            "thoughts": None,
            "recap": None,
            "audio_transcript": None,
            "images": turn["images"],
            "attachments": turn["attachments"],
            "citations": [],
            "search_results": [],
        })
        if turn["assistant"]:
            messages.append({
                "role": "assistant",
                "content": turn["assistant"],
                "time": turn["assistant_time"],
                "model": "gemini",
                "kind": "gemini",
                "thoughts": None,
                "recap": None,
                "audio_transcript": None,
                "images": [],
                "attachments": [],
                "citations": [],
                "search_results": [],
            })

    if not title:
        title = "Gemini chat %s" % ((first_ts or last_ts) or "")

    conv_id = _url_id(url) or hashlib.sha1(f"{title}:{first_ts}".encode()).hexdigest()[:16]

    return {
        "id": conv_id,
        "service": SERVICE,
        "title": title,
        "created_at": first_ts,
        "updated_at": last_ts or first_ts,
        "model": None,
        "msg_count": len(messages),
        "preview": truncate(turns[0]["user"], 140) if turns else "",
        "archived": False,
        "starred": False,
        "messages": messages,
    }


# --------------------------------------------------------------------------- #
# HTML -> Markdown
# --------------------------------------------------------------------------- #

class _MdHtmlParser(HTMLParser):
    """Convert a small safe HTML subset to markdown."""

    BLOCK_TO_MD = {
        "p": None,  # handled via start/end -> newline
        "h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ",
        "li": "- ", "blockquote": "> ", "hr": "\n---\n",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.list_stack = []  # depth -> ordered/unordered
        self.pre_depth = 0
        self.in_code_marker = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = dict(attrs)
        if tag in ("ul", "ol"):
            self.list_stack.append(1 if tag == "ol" else 0)
            self.out.append("\n")
            return
        if tag == "li":
            ordered = bool(self.list_stack) and self.list_stack[-1] == 1
            prefix = "1. " if ordered else "- "
            # strip a leading newline that may precede the li
            if self.out and self.out[-1] == "\n":
                self.out.pop()
            self.out.append(prefix)
            return
        if tag == "br":
            self.out.append("\n")
            return
        if tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code":
            if not self.out or self.out[-1] != "`":
                self.out.append("`")
            else:
                self.out.append("`")
        elif tag == "a":
            href = d.get("href")
            self.out.append("[")
            self._pending_href = href
        elif tag == "img":
            src = d.get("src")
            alt = d.get("alt") or ""
            if src:
                self.out.append(f"![{alt}]({src})")
            elif alt:
                self.out.append(alt)
        elif tag == "pre":
            self.out.append("\n```\n")
            self.pre_depth += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "p":
            # start of paragraph: add a preceding blank line if not at start
            if self.out and not (self.out[-1] == "\n" or self.out[-1] == ""):
                self.out.append("\n\n")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "hr":
            self.out.append("\n---\n")
        elif tag == "div":
            if self.out and self.out[-1] != "\n":
                self.out.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        d = dict(attrs)
        if tag == "img" and d.get("src"):
            pass  # already rendered in starttag

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code":
            self.out.append("`")
        elif tag == "a":
            href = getattr(self, "_pending_href", None)
            self.out.append(f"]({href})" if href else "]")
            self._pending_href = None
        elif tag == "pre":
            self.out.append("\n```\n")
            self.pre_depth = max(0, self.pre_depth - 1)
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n")
        elif tag == "p":
            self.out.append("\n\n")
        elif tag == "li":
            self.out.append("\n")
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self.out.append("\n")
        elif tag == "blockquote":
            self.out.append("\n")

    def handle_data(self, data):
        if self.pre_depth:
            self.out.append(data)
        else:
            self.out.append(data)

    def result(self):
        return "".join(self.out)


def html_to_markdown(raw_html):
    """Convert Gemini safeHtmlItem HTML into markdown text."""
    if not raw_html:
        return ""
    parser = _MdHtmlParser()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        # fallback: strip tags if parser chokes
        text = html_mod.unescape(raw_html)
        return re.sub(r"<[^>]+>", "", text).strip()

    md = parser.result()
    # Clean excess newlines (more than 2) and normalize whitespace
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{2,}(?=- )", "\n", md)
    return md.strip()


# Convenience alias to keep the plan referenceable
html_to_markdown_ = html_to_markdown