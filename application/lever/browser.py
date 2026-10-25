"""Lever-specific Playwright DOM adapter; Greenhouse remains untouched."""

from urllib.parse import urljoin, urlparse

HOSTS = {"jobs.lever.co", "jobs.eu.lever.co"}


def is_lever_url(url):
    return (urlparse(str(url or "")).hostname or "").lower() in HOSTS


def prepare(page):
    """Discover inputs, stable DOM IDs, question labels and select choices."""
    return page.evaluate(r"""() => {
      const key = 'data-jobscout-lever';
      const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g,' ').trim();
      const visible = e => !!e && !e.disabled && !!e.getClientRects().length &&
        getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
      const containers = 'fieldset,.application-question,.custom-question,.application-field,.form-field,' +
        '[class*="question-container"],[class*="custom-question"],[class*="application-question"],' +
        '[class*="application-form-field"],[class*="question-block"]';
      const prompts = 'legend,.application-question-label,.question-label,.application-label,' +
        '[class*="question-label"],[class*="field-label"],label';
      const choiceText = e => (e.labels?.length ? text(e.labels[0]) : text(e.closest('label'))) || e.value || '';
      function question(e) {
        const by = e.getAttribute('aria-labelledby');
        if (by) {
          const x = by.split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim();
          if (x) return x;
        }
        const a = e.getAttribute('aria-label');
        if (a) return a.trim();
        if (e.type !== 'checkbox' && e.type !== 'radio' && e.labels?.length) {
          const x = text(e.labels[0]); if (x) return x;
        }
        const box = e.closest(containers);
        if (box) {
          const heading = box.querySelector(prompts);
          if (heading && text(heading)) return text(heading);
        }
        for (let n = e, depth = 0; n && depth < 7; n = n.parentElement, depth++) {
          for (let s = n.previousElementSibling, k = 0; s && k < 4; s = s.previousElementSibling, k++) {
            if (s.matches(prompts) && text(s)) return text(s);
            const l = s.querySelector?.(prompts);
            if (l && text(l)) return text(l);
          }
        }
        if (e.type === 'checkbox' || e.type === 'radio') return choiceText(e);
        return e.getAttribute('placeholder') || e.name || e.id || '';
      }
      let serial = Number(window.__jobscoutLeverSerial || 0);
      const fields = [...document.querySelectorAll('input,textarea,select')]
        .filter(e => !e.disabled && !['hidden','submit','button','reset'].includes((e.type||'').toLowerCase()))
        .filter(e => visible(e) || e.type === 'file');
      const result = fields.map(e => {
        if (!e.hasAttribute(key)) e.setAttribute(key, String(++serial));
        const box = e.closest(containers);
        const label = question(e);
        const required = !!e.required || e.getAttribute('aria-required') === 'true' ||
          !!box?.querySelector('[aria-required="true"]') ||
          /\*/.test(text(box?.querySelector(prompts)));
        return {
          control_id: e.getAttribute(key), id:e.id || '', name:e.name || '',
          label, context:label, tag:e.tagName.toLowerCase(), type:(e.type||'').toLowerCase(),
          placeholder:e.getAttribute('placeholder') || '', required:!!required,
          option_label:(e.type === 'checkbox' || e.type === 'radio') ? choiceText(e) : '',
          options:e.tagName === 'SELECT' ? [...e.options].filter(o=>!o.disabled && o.value !== '')
            .map(o=>({label:text(o)||o.textContent.trim(),value:o.value})) : []
        };
      });
      window.__jobscoutLeverSerial = serial;
      return result;
    }""")


def locator(page, field):
    return page.locator(f'[data-jobscout-lever="{field["control_id"]}"]')


def filled(page, field):
    target = locator(page, field)
    if target.count() != 1:
        return False
    try:
        if field["type"] in ("checkbox", "radio"):
            return target.is_checked()
        if field["type"] == "file":
            return bool(target.evaluate("e => e.files && e.files.length"))
        if field["tag"] == "select":
            return bool(target.input_value().strip())
        return bool(target.input_value().strip())
    except Exception:
        return False


def fill(page, field, answer):
    if answer is None:
        return False
    target = locator(page, field)
    if target.count() != 1:
        return False
    if field["type"] == "file":
        target.set_input_files(str(answer))
    elif field["tag"] == "select":
        matches = [o for o in field["options"] if o["label"].strip().casefold() == str(answer).strip().casefold()]
        if len(matches) != 1:
            return False
        target.select_option(value=matches[0]["value"])
    elif field["type"] in ("text", "email", "tel", "url", "number", "search", "textarea", "date") or field["tag"] == "textarea":
        target.fill(str(answer))
    else:
        return False
    return filled(page, field)


def has_form(page):
    fields = prepare(page)
    identity = " ".join((f["label"] + " " + f["name"]).lower() for f in fields)
    return len(fields) >= 3 and "email" in identity and ("name" in identity or "resume" in identity)


def open_form(page):
    if has_form(page):
        return True
    for selector in ('a[href*="/apply"]', 'a:has-text("Apply for this job")', 'button:has-text("Apply for this job")'):
        candidates = page.locator(selector)
        for i in range(candidates.count()):
            button = candidates.nth(i)
            if not button.is_visible():
                continue
            href = button.get_attribute("href")
            if href and is_lever_url(urljoin(page.url, href)):
                page.goto(urljoin(page.url, href), wait_until="domcontentloaded", timeout=45000)
            else:
                button.click(timeout=10000)
            page.wait_for_timeout(900)
            if has_form(page):
                return True
    parsed = urlparse(page.url)
    if is_lever_url(page.url) and not parsed.path.rstrip("/").endswith("/apply"):
        page.goto(page.url.split("?")[0].rstrip("/") + "/apply", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(900)
    return has_form(page)


def choice_groups(page):
    """Recognize Yes/No checkbox pairs and native radio groups, even with opaque names."""
    return page.evaluate(r"""() => {
      const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g,' ').trim();
      const visible = e => !e.disabled && !!e.getClientRects().length;
      const inputs = [...document.querySelectorAll('input[type="radio"],input[type="checkbox"]')].filter(visible);
      const parents = 'fieldset,.application-question,.custom-question,[class*="question-container"],' +
        '[class*="application-question"],[class*="custom-question"],[role="radiogroup"]';
      const prompt = 'legend,.application-question-label,.question-label,[class*="question-label"]';
      const grouped = new Map();
      for (const el of inputs) {
        let box = el.closest(parents);
        if (!box) {
          // Locate the smallest ancestor containing the Yes/No siblings.
          for (let p = el.parentElement, d = 0; p && d < 6; p = p.parentElement,d++) {
            const count = p.querySelectorAll('input[type="checkbox"],input[type="radio"]').length;
            if (count >= 2 && count <= 8) {box = p;break;}
          }
        }
        const key = box || (el.name ? 'name:' + el.name : null);
        if (!key) continue;
        if (!grouped.has(key)) grouped.set(key,{box,els:[]});
        grouped.get(key).els.push(el);
      }
      let n = 0, result = [];
      for (const {box,els} of grouped.values()) {
        if (els.length < 2 || els.length > 10) continue;
        const options = els.map(e => ({
          el:e, label: (e.labels?.length ? text(e.labels[0]) : text(e.closest('label'))) || e.value || ''
        }));
        const labels = options.map(o=>o.label.toLowerCase().trim());
        const yesno = labels.length === 2 && labels.includes('yes') && labels.includes('no');
        if (!yesno && !els.every(e=>e.type==='radio')) continue;
        let heading = box?.querySelector(prompt);
        let question = text(heading);
        if (!question && box) {
          let p = box;
          for (let i=0; p && i<4 && !question;i++,p=p.parentElement) {
            let previous = p.previousElementSibling;
            if (previous) question = text(previous.querySelector?.(prompt) || previous);
          }
        }
        if (!question) question = els[0].name || '';
        const id = 'lever_choice_' + n++;
        options.forEach((o,i)=>{
          o.el.setAttribute('data-jobscout-lever-choice-group',id);
          o.el.setAttribute('data-jobscout-lever-choice-index',String(i));
        });
        result.push({id,question,identified:!!question && !/^cards\[/.test(question),
          required:els.some(e=>e.required) || /\*/.test(text(heading)),
          options:options.map(o=>({label:o.label,value:o.el.value,selected:o.el.checked})),
          selected:options.filter(o=>o.el.checked).map(o=>o.label)});
      }
      return result;
    }""")


def select_choice(page, group, answer):
    matches = [i for i, option in enumerate(group["options"]) if option["label"].strip().casefold() == str(answer).strip().casefold()]
    if len(matches) != 1:
        return False
    controls = page.locator(f'[data-jobscout-lever-choice-group="{group["id"]}"]')
    if controls.count() != len(group["options"]):
        return False
    index = matches[0]
    for i in range(controls.count()):
        if i != index and controls.nth(i).is_checked():
            controls.nth(i).uncheck(timeout=4000)
    controls.nth(index).check(timeout=4000)
    return controls.nth(index).is_checked() and all(not controls.nth(i).is_checked() for i in range(controls.count()) if i != index)


def find_submit(page):
    """Mark one DOM-unique submit control; don't mistake a job-listing Apply link."""
    result = page.evaluate(r"""() => {
      const marker='data-jobscout-lever-submit';
      document.querySelectorAll('['+marker+']').forEach(e=>e.removeAttribute(marker));
      const all=[...document.querySelectorAll('button,input[type="submit"],a.postings-btn,[role="button"]')];
      const allowed=all.filter(e=>{
        const s=getComputedStyle(e);
        if(e.disabled || s.display==='none' || s.visibility==='hidden' || !e.getClientRects().length)return false;
        const t=(e.innerText||e.value||e.getAttribute('aria-label')||'').trim().toLowerCase();
        return e.type==='submit' || /^(submit|submit application|send application|submit your application)$/.test(t);
      });
      const unique=[...new Set(allowed)];
      if(unique.length!==1) return unique.length;
      unique[0].setAttribute(marker,'true');
      return 1;
    }""")
    if result != 1:
        print(f"Submit control ambiguous: {result} candidates")
        return None
    control = page.locator('[data-jobscout-lever-submit="true"]')
    return control if control.count() == 1 else None
