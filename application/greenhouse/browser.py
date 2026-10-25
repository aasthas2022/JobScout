
"""Greenhouse browser adapter with React Select support."""

import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Page

MARKER = "data-jobscout-control"


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def get_questions(url):
    """Retrieve public Greenhouse application metadata."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")

    if len(parts) < 3 or parts[1] != "jobs":
        return []

    board, job_id = parts[0], parts[2]

    if not job_id.isdigit():
        return []

    try:
        response = httpx.get(
            "https://boards-api.greenhouse.io/"
            f"v1/boards/{board}/jobs/{job_id}",
            params={"questions": "true"},
            timeout=15,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()

        result = []
        for key in (
            "questions",
            "location_questions",
            "compliance",
        ):
            entries = data.get(key)
            if isinstance(entries, list):
                result.extend(
                    entry for entry in entries
                    if isinstance(entry, dict)
                )
        return result

    except (httpx.HTTPError, ValueError):
        return []


def visible_fields(page: Page):
    """Discover real controls, excluding React validation inputs."""
    return page.evaluate(
        r"""
        ({ marker, prefix }) => {
            const clean = x =>
                String(x || "").replace(/\s+/g, " ").trim();

            const selector = [
                'input:not([type="hidden"])',
                'select',
                'textarea',
                '[role="combobox"]:not(input):not(select)'
            ].join(",");

            const nodes = [
                ...document.querySelectorAll(selector)
            ];

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);

                return rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden";
            }

            function getLabel(el) {
                if (el.labels?.length) {
                    const label = clean(
                        [...el.labels]
                            .map(e => e.innerText)
                            .join(" ")
                    );
                    if (label) return label;
                }

                const labelledBy =
                    el.getAttribute("aria-labelledby");

                if (labelledBy) {
                    const text = clean(
                        labelledBy.split(/\s+/)
                            .map(id =>
                                document.getElementById(id)
                                    ?.innerText
                            )
                            .join(" ")
                    );
                    if (text) return text;
                }

                if (el.id) {
                    const label = document.querySelector(
                        `label[for="${CSS.escape(el.id)}"]`
                    );
                    if (label) {
                        const text = clean(label.innerText);
                        if (text) return text;
                    }
                }

                let parent = el.parentElement;
                let fallback = "";

                for (
                    let depth = 0;
                    parent && depth < 7;
                    depth++, parent = parent.parentElement
                ) {
                    const labels = parent.querySelectorAll(
                        ':scope > label,' +
                        ':scope > legend,' +
                        ':scope > [class*="question-label"]'
                    );

                    for (const node of labels) {
                        const text = clean(node.innerText);
                        if (text && text.length < 220) {
                            return text;
                        }
                    }

                    const text = clean(parent.innerText);
                    if (
                        text.length > 3 &&
                        text.length < 220
                    ) {
                        fallback = text;
                        if (
                            text.includes("?") ||
                            text.endsWith("*")
                        ) {
                            return text;
                        }
                    }
                }

                return fallback;
            }

            const fields = [];
            const seen = new Set();

            for (let i = 0; i < nodes.length; i++) {
                const el = nodes[i];
                const type = (el.type || "").toLowerCase();
                const tag = el.tagName.toLowerCase();

                if (el.disabled) continue;

                // Critical Affirm fix:
                // skip required, invisible React Select
                // validation inputs.
                if (
                    el.getAttribute("aria-hidden") === "true" ||
                    el.getAttribute("tabindex") === "-1"
                ) continue;

                if ([
                    "hidden", "password",
                    "button", "submit", "reset"
                ].includes(type)) continue;

                // Real file inputs may be visually hidden.
                if (type !== "file" && !visible(el)) {
                    continue;
                }

                const isCombo =
                    el.getAttribute("role") === "combobox" ||
                    el.getAttribute("aria-autocomplete") === "list";

                const kind = type === "file"
                    ? "file"
                    : isCombo
                        ? "combobox"
                        : type || "text";

                let label = getLabel(el);

                const identity = (
                    el.id + " " + el.name
                ).toLowerCase();

                if (type === "file") {
                    if (/resume/.test(identity)) {
                        label = "Resume/CV";
                    } else if (/cover.?letter/.test(identity)) {
                        label = "Cover Letter";
                    }
                }

                label = clean(
                    label ||
                    el.getAttribute("aria-label") ||
                    el.getAttribute("placeholder") ||
                    el.name ||
                    el.id ||
                    `Field ${i}`
                );

                // Avoid duplicate representations of
                // the same React Select control.
                const logicalKey = isCombo
                    ? `combobox:${el.id || label}`
                    : null;

                if (logicalKey && seen.has(logicalKey)) {
                    continue;
                }

                if (logicalKey) seen.add(logicalKey);

                let token = el.getAttribute(marker);

                if (!token) {
                    token = `${prefix}-${i}`;
                    el.setAttribute(marker, token);
                }

                const options = tag === "select"
                    ? [...el.options].map(o => ({
                        label: clean(o.label),
                        value: o.value
                    }))
                    : [];

                fields.push({
                    control_id: token,
                    id: el.id || "",
                    name: el.name || "",
                    label,
                    context: label,
                    type: kind,
                    tag,
                    required: Boolean(
                        el.required ||
                        el.getAttribute("aria-required")
                            === "true" ||
                        label.endsWith("*")
                    ),
                    options,
                });
            }

            return fields;
        }
        """,
        {
            "marker": MARKER,
            "prefix": uuid.uuid4().hex[:12],
        },
    )


def locator_for(page, item):
    token = item.get("control_id")
    if not token:
        return None

    loc = page.locator(
        f'[{MARKER}="{token}"]'
    )

    return loc if loc.count() == 1 else None


def selected_value(page, item):
    """Read committed values, not React Select search text."""
    loc = locator_for(page, item)

    if loc is None:
        return ""

    kind = item.get("type", "")

    try:
        if kind == "file":
            return loc.evaluate(
                "el => el.files?.length "
                "? el.files[0].name : ''"
            )

        if kind in ("checkbox", "radio"):
            return (
                "checked"
                if loc.is_checked()
                else ""
            )

        if kind == "combobox":
            return clean(
                loc.evaluate(
                    r"""
                    el => {
                        const clean = s =>
                            String(s || "").trim();

                        let parent = el.parentElement;

                        for (
                            let i = 0;
                            parent && i < 5;
                            i++, parent = parent.parentElement
                        ) {
                            const node =
                                parent.querySelector(
                                    '[class*="singleValue"],' +
                                    '[class*="single-value"]'
                                );

                            if (
                                node &&
                                clean(node.textContent)
                            ) {
                                return clean(
                                    node.textContent
                                );
                            }
                        }

                        return (
                            el.getAttribute("aria-valuetext") ||
                            el.getAttribute("data-value") ||
                            ""
                        );
                    }
                    """
                )
            )

        return clean(loc.input_value())

    except Exception:
        return ""


def already_filled(page, item):
    value = selected_value(page, item)

    placeholders = {
        "",
        "select",
        "select...",
        "please select",
        "choose",
        "choose...",
        "search...",
    }

    return value.casefold() not in placeholders


def _visible_menu_options(page):
    """Extract options from the currently open dropdown."""
    return page.evaluate(
        r"""
        () => {
            const clean = s =>
                String(s || "").replace(/\s+/g, " ").trim();

            const nodes = document.querySelectorAll(
                '[role="option"]'
            );

            const results = [];
            const seen = new Set();

            for (const el of nodes) {
                const rect = el.getBoundingClientRect();

                if (
                    rect.width <= 0 ||
                    rect.height <= 0
                ) continue;

                const text = clean(
                    el.getAttribute("aria-label") ||
                    el.innerText ||
                    el.textContent
                );

                if (!text || text.length > 220) {
                    continue;
                }

                const key = text.toLowerCase();

                if (seen.has(key)) continue;

                seen.add(key);
                results.push({ label: text });
            }

            return results;
        }
        """
    )


def dropdown_options(page, item):
    """Open a dropdown, inspect options, close without selecting."""
    if item.get("tag") == "select":
        return item.get("options", [])

    if item.get("type") != "combobox":
        return []

    loc = locator_for(page, item)

    if loc is None:
        return []

    try:
        loc.click(timeout=5000)
        page.wait_for_timeout(200)

        options = _visible_menu_options(page)

        # Close without altering the selected value.
        loc.press("Escape", timeout=2000)

        return options

    except Exception as exc:
        print(
            f"Dropdown inspection failed for "
            f"{item.get('label')}: {exc}"
        )
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return []


def _option_locator(page, expected):
    """Find exactly one visible React Select option."""
    loc = page.get_by_role(
        "option",
        name=expected,
        exact=True,
    )

    visible = [
        loc.nth(i)
        for i in range(loc.count())
        if loc.nth(i).is_visible()
    ]

    return visible[0] if len(visible) == 1 else None


def fill_field(page, item, answer):
    """Fill a field and verify its committed value."""
    if answer is None:
        return False

    loc = locator_for(page, item)

    if loc is None:
        return False

    kind = item.get("type", "")
    tag = item.get("tag", "")
    expected = clean(answer)

    try:
        if kind == "file":
            path = Path(str(answer)).resolve()

            if not path.is_file():
                print("Resume file missing:", path)
                return False

            loc.set_input_files(
                str(path),
                timeout=15000,
            )

            return already_filled(page, item)

        if kind in ("checkbox", "radio"):
            return False

        if tag == "select":
            options = item.get("options", [])

            matching = [
                opt for opt in options
                if clean(opt["label"]).casefold()
                    == expected.casefold()
                or clean(opt["value"]).casefold()
                    == expected.casefold()
            ]

            if len(matching) != 1:
                return False

            loc.select_option(
                value=matching[0]["value"]
            )

            return (
                loc.input_value()
                == matching[0]["value"]
            )

        if kind == "combobox":
            loc.click(timeout=5000)
            page.wait_for_timeout(200)

            option = _option_locator(
                page, expected
            )

            if option is None and tag == "input":
                # React Select can filter options.
                loc.fill(expected, timeout=4000)
                page.wait_for_timeout(250)

                option = _option_locator(
                    page, expected
                )

            if option is None:
                print(
                    f"No matching option: "
                    f"{item.get('label')} -> {expected}"
                )
                try:
                    loc.press("Escape")
                except Exception:
                    pass
                return False

            option.click(timeout=5000)
            page.wait_for_timeout(200)

            actual = selected_value(page, item)

            if actual.casefold() == expected.casefold():
                return True

            print(
                f"Selection not verified: "
                f"{item.get('label')} "
                f"expected={expected!r} actual={actual!r}"
            )
            return False

        if tag == "textarea" or kind in (
            "text",
            "email",
            "tel",
            "url",
            "number",
            "date",
            "search",
            "",
        ):
            actual_type = loc.evaluate(
                "el => (el.type || '').toLowerCase()"
            )

            if actual_type == "file":
                return False

            loc.fill(str(answer), timeout=10000)

            return (
                loc.input_value()
                == str(answer)
            )

        return False

    except Exception as exc:
        print(
            f"Fill failed: {item.get('label')} "
            f"({type(exc).__name__}: {str(exc)[:200]})"
        )
        return False
