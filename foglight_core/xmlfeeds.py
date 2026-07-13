"""Namespace-safe RSS, Atom, and RDF feed parsing."""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


def first_find(parent, *paths):
    for path in paths:
        element = parent.find(path)
        if element is not None:
            return element
    return None


def iter_local(parent, *names):
    wanted = set(names)
    return (
        element
        for element in parent.iter()
        if isinstance(element.tag, str) and element.tag.rsplit("}", 1)[-1] in wanted
    )


def parse_rss_items(xml_bytes):
    output = []
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, TypeError):
        return output
    for item in iter_local(root, "item", "entry"):
        title_element = first_find(item, "{*}title", "title")
        link_element = first_find(item, "{*}link", "link")
        published_element = first_find(
            item,
            "{*}pubDate",
            "pubDate",
            "{*}published",
            "published",
            "{*}updated",
            "updated",
            "{http://purl.org/dc/elements/1.1/}date",
            "{*}date",
            "date",
        )
        description_element = first_find(
            item, "{*}description", "description", "{*}summary", "summary"
        )
        title = (
            (title_element.text or "").strip()
            if title_element is not None and title_element.text
            else ""
        )
        link = ""
        if link_element is not None:
            link = (link_element.text or link_element.get("href") or "").strip()
        timestamp = 0
        if published_element is not None and published_element.text:
            text = published_element.text.strip()
            try:
                timestamp = int(parsedate_to_datetime(text).timestamp())
            except (TypeError, ValueError, OverflowError):
                try:
                    timestamp = int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
                except (TypeError, ValueError, OverflowError):
                    pass
        summary = ""
        if description_element is not None and description_element.text:
            summary = re.sub(r"<[^>]+>", "", description_element.text).strip()[:280]
        if title:
            output.append({"ts": timestamp, "title": title, "link": link, "summary": summary})
    return output
