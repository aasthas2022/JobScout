
"""Detect and interact with grouped Greenhouse form controls.

Supported:
- Native radio groups
- ARIA radio groups containing native radio inputs
- Checkbox groups in fieldsets

Single consent checkboxes remain independent controls.
Ranking widgets are detected but never guessed automatically.
"""

import re


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def extract_groups(page):
    """Return actual groups and mark their controls in the DOM."""
    return page.evaluate(
        r"""() => {
            const visible = el => {
                if (!el || el.disabled) return false;
                if (el.getAttribute('aria-hidden') === 'true')
                    return false;
                return !!(el.offsetWidth ||
                          el.offsetHeight ||
                          el.getClientRects().length);
            };

            const text = el =>
                (el?.innerText || el?.textContent || '')
                    .replace(/\s+/g, ' ').trim();

            const inputLabel = el => {
                if (el.labels?.length)
                    return text(el.labels[0]);
                const aria = el.getAttribute('aria-label');
                if (aria) return aria.trim();
                const labelledBy =
                    el.getAttribute('aria-labelledby');
                if (labelledBy) {
                    const value = labelledBy.split(/\s+/)
                        .map(id => text(
                            document.getElementById(id)
                        )).filter(Boolean).join(' ');
                    if (value) return value;
                }
                const wrapper = el.closest('label');
                if (wrapper) return text(wrapper);
                return String(el.value || '');
            };

            const heading = container => {
                if (!container) return '';
                if (container.tagName === 'FIELDSET') {
                    const legend = container.querySelector(
                        ':scope > legend'
                    );
                    if (legend) return text(legend);
                }
                const aria = container.getAttribute('aria-label');
                if (aria) return aria.trim();
                const labelledBy =
                    container.getAttribute('aria-labelledby');
                if (labelledBy) {
                    const value = labelledBy.split(/\s+/)
                        .map(id => text(
                            document.getElementById(id)
                        )).filter(Boolean).join(' ');
                    if (value) return value;
                }
                const h = container.querySelector(
                    ':scope > h1, :scope > h2, :scope > h3,' +
                    ':scope > h4, :scope > p,' +
                    ':scope > [class*="question"],' +
                    ':scope > [class*="label"]'
                );
                return text(h);
            };

            const controls = Array.from(
                document.querySelectorAll(
                    'input[type="radio"],input[type="checkbox"]'
                )
            ).filter(visible);

            const collections = new Map();

            for (const input of controls) {
                const type = input.type;

                const container = input.closest(
                    'fieldset, [role="radiogroup"], ' +
                    '[role="group"]'
                );

                let siblings = [];
                let key = '';

                if (container) {
                    siblings = Array.from(
                        container.querySelectorAll(
                            `input[type="${type}"]`
                        )
                    ).filter(visible);
                    key = type + ':container:' +
                        Array.from(
                            document.querySelectorAll(
                                'fieldset,[role="radiogroup"],' +
                                '[role="group"]'
                            )
                        ).indexOf(container);
                } else if (type === 'radio' && input.name) {
                    siblings = controls.filter(
                        other =>
                            other.type === type &&
                            other.name === input.name
                    );
                    key = type + ':name:' + input.name;
                } else {
                    continue;
                }

                // A single checkbox is often a declaration
                // and should not be treated as a choice group.
                if (siblings.length < 2) continue;

                if (!collections.has(key)) {
                    collections.set(key, {
                        type,
                        container,
                        inputs: siblings
                    });
                }
            }

            const groups = [];
            let number = 0;

            for (const group of collections.values()) {
                const id = 'jobscout_group_' + number++;

                let question = heading(group.container);

                if (!question) {
                    const first = group.inputs[0];
                    const parent = first.closest(
                        '[class*="field"],[class*="question"]'
                    );
                    if (parent) {
                        question = heading(parent);
                    }
                }

                if (!question) {
                    const name = group.inputs[0].name || '';
                    question = name
                        .replace(/[_-]+/g, ' ')
                        .trim();
                }

                const options = group.inputs.map(
                    (input, index) => {
                        input.setAttribute(
                            'data-jobscout-group', id
                        );
                        input.setAttribute(
                            'data-jobscout-option', String(index)
                        );

                        return {
                            label: inputLabel(input),
                            checked: input.checked,
                            disabled: input.disabled,
                            value: input.value
                        };
                    }
                );

                const type = group.type === 'radio'
                    ? 'single_choice'
                    : 'multiple_choice';

                const required = group.inputs.some(
                    input => input.required
                );

                groups.push({
                    id,
                    name: group.inputs[0].name || '',
                    question,
                    type,
                    required,
                    options,
                    selected: options
                        .filter(option => option.checked)
                        .map(option => option.label),
                    // A group lacking a real question label
                    // is NOT safe to answer automatically.
                    identified: Boolean(
                        heading(group.container)
                    )
                });
            }

            return groups;
        }"""
    )


def selected_values(group):
    return list(group.get("selected", []))


def choose_single(page, group, answer):
    """Click an exact option and verify the resulting state."""
    if group.get("type") != "single_choice":
        return False

    if not group.get("identified"):
        return False

    wanted = clean(answer).casefold()
    matches = [
        (index, option)
        for index, option in enumerate(group["options"])
        if clean(option["label"]).casefold() == wanted
    ]

    if len(matches) != 1:
        return False

    index, option = matches[0]

    if option.get("disabled"):
        return False

    locator = page.locator(
        f'input[data-jobscout-group="{group["id"]}"]'
        f'[data-jobscout-option="{index}"]'
    )

    if locator.count() != 1:
        return False

    try:
        locator.check(timeout=4000)

        return locator.is_checked()

    except Exception:
        return False


def completed(group):
    if group.get("type") == "single_choice":
        return len(group.get("selected", [])) == 1

    if group.get("type") == "multiple_choice":
        return bool(group.get("selected", []))

    return False
