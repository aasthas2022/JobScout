"""Browser adapter for Ashby-hosted applications (Playwright sync API)."""
from urllib.parse import urlparse

HOSTS = {"jobs.ashbyhq.com"}


def is_ashby_url(url):
    return (urlparse(str(url or "")).hostname or "").lower() in HOSTS


def prepare(page):
    """Mark controls with stable IDs and resolve nearby question labels."""
    return page.evaluate(r"""() => {
      const text = el => (el?.innerText || el?.textContent || '').replace(/\s+/g,' ').trim();
      const visible = el => {if (!el || el.disabled) return false;
        const s = getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && !!el.getClientRects().length;};
      const questionSelector = 'fieldset,[role="group"],[role="radiogroup"],[class*="field"],[class*="Field"],[class*="question"],[class*="Question"],[class*="form-control"]';
      const labelSelector = 'legend,label,[class*="label"],[class*="Label"],[class*="question"],[class*="Question"]';
      const labelFor = el => {
        const aria = el.getAttribute('aria-label'); if (aria) return aria.trim();
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {let t = labelledBy.split(/\s+/).map(id=>text(document.getElementById(id))).filter(Boolean).join(' ');if(t)return t;}
        if (el.type !== 'radio' && el.type !== 'checkbox' && el.labels?.length) {const t=[...el.labels].map(text).join(' ');if(t)return t;}
        const parent = el.closest(questionSelector);
        if (parent) {const lab = parent.querySelector(labelSelector); if(lab && text(lab)) return text(lab);}
        for(let n=el,k=0;n&&k<6;n=n.parentElement,k++) {
          let prev=n.previousElementSibling;
          for(let j=0;prev&&j<3;prev=prev.previousElementSibling,j++) {
            if(prev.matches(labelSelector)&&text(prev))return text(prev);
          }
        }
        if(el.labels?.length){const t=[...el.labels].map(text).join(' ');if(t)return t;}
        return el.getAttribute('placeholder') || el.name || el.id || '';
      };
      let sequence=window.__jobscoutAshbySeq||0;
      const fields=[...document.querySelectorAll('input,textarea,select')].filter(el=>{
        if(el.disabled || ['hidden','button','reset','submit'].includes((el.type||'').toLowerCase()))return false;
        return visible(el)||el.type==='file';
      }).map(el=>{
        if(!el.hasAttribute('data-jobscout-ashby'))el.setAttribute('data-jobscout-ashby',String(++sequence));
        const parent=el.closest(questionSelector), lab=labelFor(el);
        const required=el.required||el.getAttribute('aria-required')==='true'||
          !!parent?.querySelector('[aria-required="true"]') || /\*/.test(text(parent?.querySelector(labelSelector)));
        return {control_id:el.getAttribute('data-jobscout-ashby'), id:el.id||'',name:el.name||'',
          label:lab,context:lab,type:(el.type||'').toLowerCase(),tag:el.tagName.toLowerCase(),
          required:!!required,options:el.tagName==='SELECT'?[...el.options].filter(x=>!x.disabled&&x.value!=='').map(x=>({label:text(x),value:x.value})):[]};
      });
      window.__jobscoutAshbySeq=sequence;
      return fields;
    }""")


def locator(page, field):
    return page.locator(f'[data-jobscout-ashby="{field["control_id"]}"]')


def filled(page, field):
    el=locator(page,field)
    if el.count()!=1:return False
    try:
        if field['type'] in ('radio','checkbox'):return el.is_checked()
        if field['type']=='file':return bool(el.evaluate('el => el.files && el.files.length'))
        value=el.input_value().strip()
        return bool(value and not (field['tag']=='select' and value.casefold() in ('select','choose')))
    except Exception:return False


def fill(page,field,value):
    if value is None:return False
    el=locator(page,field)
    if el.count()!=1:return False
    if field['type']=='file':el.set_input_files(str(value))
    elif field['tag']=='select':
        matched=[x for x in field['options'] if x['label'].strip().casefold()==str(value).strip().casefold()]
        if len(matched)!=1:return False
        el.select_option(value=matched[0]['value'])
    elif field['type'] in ('text','email','tel','url','number','textarea','search','date') or field['tag']=='textarea':
        el.fill(str(value))
    else:return False
    return filled(page,field)


def has_form(page):
    fields=prepare(page)
    identifiers=' '.join((x['label']+' '+x['name']).casefold() for x in fields)
    return len(fields)>=2 and ('email' in identifiers or 'resume' in identifiers) and ('name' in identifiers or 'resume' in identifiers)


def open_form(page):
    """Open Ashby's Application tab, including non-semantic React tabs.

    Stops if the application form is not actually mounted. Does not
    mistake the Overview page for a complete application.
    """
    if has_form(page):
        return True

    def wait_for_form():
        for _ in range(16):
            if has_form(page):
                print("Ashby application form detected.")
                return True
            page.wait_for_timeout(350)
        return False

    # First use accessible controls (best when the site supplies roles).
    for role in ("tab", "button", "link"):
        try:
            candidates = page.get_by_role(role, name="Application", exact=True)
            for i in range(candidates.count()):
                target = candidates.nth(i)
                if not target.is_visible() or not target.is_enabled():
                    continue
                print(f"Opening Ashby Application via {role}.")
                target.click(timeout=8000)
                if wait_for_form():
                    return True
        except Exception as exc:
            print(f"Ashby {role} navigation: {type(exc).__name__}")

    # Many Ashby pages render a simple clickable div/span with text
    # Application, without role=tab or a 'tab' class. Playwright's
    # exact text locator also finds these elements.
    try:
        targets = page.get_by_text("Application", exact=True)
        print("Visible Application labels:", targets.count())
        for i in range(min(targets.count(), 8)):
            target = targets.nth(i)
            if not target.is_visible():
                continue
            print("Opening Ashby Application via exact text.")
            target.click(timeout=8000)
            if wait_for_form():
                return True
    except Exception as exc:
        print("Ashby text-tab navigation:", type(exc).__name__, str(exc)[:100])

    # Last fallback: click the smallest visible exact-text element or
    # its nearest interactive ancestor. Do not click a broad container
    # containing the whole page's text.
    try:
        clicked = page.evaluate(r"""() => {
          const visible = el => {
            const s = getComputedStyle(el);
            return s.display !== 'none' &&
              s.visibility !== 'hidden' &&
              !!el.getClientRects().length;
          };
          const nodes = [...document.querySelectorAll('body *')]
            .filter(el => visible(el) &&
              el.children.length === 0 &&
              (el.textContent || '').trim() === 'Application');
          for (const el of nodes) {
            const target = el.closest(
              '[role="tab"],button,a,[role="button"],[tabindex],'
              + '[class*="Tab"],[class*="tab"]'
            ) || el;
            target.click();
            return true;
          }
          return false;
        }""")
        if clicked and wait_for_form():
            return True
    except Exception as exc:
        print("Ashby DOM fallback:", type(exc).__name__)

    # Give actionable data if future Ashby templates change.
    try:
        fields = prepare(page)
        print("Ashby fields after navigation:", len(fields))
        print("Sample labels:", [f.get("label", "")[:65] for f in fields[:8]])
    except Exception:
        pass
    print("Application tab/form not detected. Stopping without submission.")
    return False


def choice_groups(page):
    """Detect native and segmented Yes/No questions as complete groups.

    Segmented Ashby controls are often plain buttons without native inputs.
    The question is recovered from the nearest preceding field label.
    """
    return page.evaluate(r"""() => {
      const marker = 'data-jobscout-ashby-choice';
      const idxMarker = 'data-jobscout-ashby-choice-index';
      const text = e => (e?.innerText || e?.textContent || '')
        .replace(/\s+/g, ' ').trim();
      const visible = e => {
        if (!e || e.disabled || e.getAttribute('aria-disabled') === 'true') return false;
        const s = getComputedStyle(e);
        return s.display !== 'none' && s.visibility !== 'hidden' && !!e.getClientRects().length;
      };
      const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
      const yesNo = s => /^(yes|no)$/i.test(clean(s));
      const questionLike = s => {
        const t = clean(s);
        return t.length > 8 && t.length < 500 && !/^(yes|no)$/i.test(t)
          && !/^(overview|application|select)$/i.test(t);
      };
      const questionSelectors = [
        'legend','label','[class*="label"]','[class*="Label"]',
        '[class*="question"]','[class*="Question"]',
        '[data-testid*="label"]','[data-testid*="question"]'
      ].join(',');
      function questionFor(el) {
        const own = el.getAttribute('aria-describedby');
        if (own) {
          const v = clean(own.split(/\s+/).map(id => text(document.getElementById(id))).join(' '));
          if (questionLike(v)) return v;
        }
        // Prefer a nearby question label, not the label wrapped around Yes/No.
        for (let n = el, depth = 0; n && depth < 9; n = n.parentElement, depth++) {
          let previous = n.previousElementSibling;
          for (let j = 0; previous && j < 5; j++, previous = previous.previousElementSibling) {
            const v = text(previous);
            if (questionLike(v) && (previous.matches(questionSelectors) || v.length < 280)) {
              return v;
            }
          }
          const labels = [...n.querySelectorAll(questionSelectors)];
          for (const candidate of labels) {
            if (candidate.contains(el)) continue;
            const v = text(candidate);
            if (questionLike(v)) return v;
          }
        }
        return '';
      }
      const results = [];
      const used = new Set();
      let serial = 0;
      function add(items, q, required, kind) {
        if (items.length !== 2 || items.some(x => used.has(x))) return;
        const labels = items.map(el => {
          if (el.matches('input')) {
            if (el.labels?.length) return clean(text(el.labels[0]));
            return clean(text(el.closest('label')) || el.value);
          }
          return clean(el.getAttribute('aria-label') || text(el));
        });
        if (!(labels.some(x => /^yes$/i.test(x)) && labels.some(x => /^no$/i.test(x)))) return;
        if (!questionLike(q)) return;
        const id = 'ashby_choice_' + (serial++);
        items.forEach((el,i) => {
          used.add(el); el.setAttribute(marker,id); el.setAttribute(idxMarker,String(i));
        });
        const selected = items.map((el,i) => selectedOf(el) ? labels[i] : null).filter(Boolean);
        results.push({id,question:q,identified:true,required:!!required,
          kind,selected,options:items.map((el,i) => ({label:labels[i],value:el.value || labels[i],selected:!!selectedOf(el)}))});
      }
      function selectedOf(el) {
        if (el.matches('input')) return !!el.checked;
        if (el.getAttribute('aria-pressed') != null) return el.getAttribute('aria-pressed') === 'true';
        if (el.getAttribute('aria-checked') != null) return el.getAttribute('aria-checked') === 'true';
        if (el.getAttribute('aria-selected') != null) return el.getAttribute('aria-selected') === 'true';
        if (el.getAttribute('data-state') != null) return el.getAttribute('data-state') === 'checked' || el.getAttribute('data-state') === 'on';
        const child = el.querySelector('input[type="radio"],input[type="checkbox"]');
        if (child) return child.checked;
        // Don't guess state purely from a decorative CSS class.
        return false;
      }
      const inputs = [...document.querySelectorAll('input[type="radio"],input[type="checkbox"]')]
        .filter(visible);
      const byName = new Map();
      for (const el of inputs) {
        const key = el.name || el.closest('[role="radiogroup"],fieldset,[class*="Field"],[class*="field"]');
        if (!key) continue;
        if (!byName.has(key)) byName.set(key,[]);
        byName.get(key).push(el);
      }
      for (const items of byName.values()) {
        if (items.length !== 2) continue;
        const q = questionFor(items[0]);
        add(items,q,items.some(x=>x.required) || /\*$/.test(q),'native');
      }
      // Group adjacent buttons representing Yes/No as a single question.
      const controls = [...document.querySelectorAll('button,[role="radio"],[role="button"],label,div,span')]
        .filter(el => visible(el) && yesNo(el.getAttribute('aria-label') || text(el)) && ![...el.children].some(child => yesNo(text(child))));
      const parents = new Map();
      for (const el of controls) {
        const parent = el.parentElement;
        if (!parent) continue;
        if (!parents.has(parent)) parents.set(parent,[]);
        parents.get(parent).push(el);
      }
      for (const items of parents.values()) {
        if (items.length !== 2) continue;
        const q = questionFor(items[0]);
        add(items,q,/\*$/.test(q) || !!items[0].closest('[aria-required="true"]'),'segmented');
      }
      return results;
    }""")


def select_choice(page, group, answer):
    """Select and verify a single choice, including segmented Ashby buttons."""
    matches = [i for i, option in enumerate(group['options'])
               if option['label'].strip().casefold() == str(answer).strip().casefold()]
    if len(matches) != 1:
        return False
    controls = page.locator(f'[data-jobscout-ashby-choice="{group["id"]}"]')
    if controls.count() != 2:
        return False
    index = matches[0]
    target = controls.nth(index)
    if group.get('kind') == 'native':
        for i in range(2):
            other = controls.nth(i)
            if i != index and other.is_checked() and other.get_attribute('type') == 'checkbox':
                other.uncheck(timeout=4000)
        target.check(timeout=4000)
    else:
        target.click(timeout=5000)
    # Re-read the DOM; a click alone does not prove a selection succeeded.
    return bool(controls.evaluate_all("""(els, index) => {
      const active = e => {
        if (e.matches('input')) return !!e.checked;
        for (const attr of ['aria-pressed','aria-checked','aria-selected']) {
          if (e.hasAttribute(attr)) return e.getAttribute(attr) === 'true';
        }
        if (e.hasAttribute('data-state')) return ['checked','on'].includes(e.getAttribute('data-state'));
        const child = e.querySelector('input[type=radio],input[type=checkbox]');
        return child ? child.checked : false;
      };
      return els.length === 2 && active(els[index]) && !active(els[1-index]);
    }""", index))


def choice_validation(page):
    """Return unresolved required choice questions, including segmented controls."""
    return [group['question'] for group in choice_groups(page)
            if group.get('required') and not group.get('selected')]


def find_submit(page):
    found=page.evaluate(r"""() => {
      document.querySelectorAll('[data-jobscout-ashby-submit]').forEach(x=>x.removeAttribute('data-jobscout-ashby-submit'));
      const good=e=>{const s=getComputedStyle(e);return !e.disabled&&s.display!=='none'&&s.visibility!=='hidden'&&!!e.getClientRects().length;};
      const controls=[...document.querySelectorAll('button,input[type="submit"],[role="button"]')].filter(e=>{
        if(!good(e))return false;
        const label=(e.innerText||e.value||e.getAttribute('aria-label')||'').trim().toLowerCase();
        return e.type==='submit'||/^(submit|submit application|send application|submit your application)$/.test(label);
      });
      if(controls.length!==1)return false;
      controls[0].setAttribute('data-jobscout-ashby-submit','yes');return true;
    }""")
    return page.locator('[data-jobscout-ashby-submit="yes"]') if found else None


def verification_present(page):
    try:
        return page.evaluate(r"""() => {
          const body=(document.body?.innerText||'').toLowerCase();
          if(document.querySelector('iframe[src*="captcha"],iframe[src*="challenge"],iframe[src*="turnstile"],iframe[src*="arkoselabs"],iframe[src*="hcaptcha"]'))return true;
          return /verify you are human|human verification|complete the security check/.test(body);
        }""")
    except Exception:return False


def fill_searchable_dropdown(page, field, answer):
    """Choose a real Ashby suggestion for a location/state combobox.

    Never equate merely typing into an autocomplete with selecting an option.
    Return False for ambiguous or unverified choices.
    """
    if answer is None:
        return False
    element = locator(page, field)
    if element.count() != 1:
        return False

    requested = str(answer).strip()
    abbreviations = {
        "GA": "Georgia", "CA": "California", "NY": "New York",
        "TX": "Texas", "FL": "Florida", "WA": "Washington",
    }
    question = (field.get("label") or "").casefold()
    state_field = any(token in question for token in
                      ("state", "province", "region"))
    search = abbreviations.get(requested.upper(), requested) if state_field else requested

    # For location fields use city first (e.g. "Atlanta"), because
    # Ashby suggestions often contain "Atlanta, Georgia, United States".
    if not state_field and "," in search:
        search = search.split(",", 1)[0].strip()

    try:
        element.click(timeout=5000)
        element.fill(search, timeout=5000)
        page.wait_for_timeout(700)

        candidates = page.locator(
            '[role="option"], [cmdk-item], '
            '[data-radix-collection-item], [data-state="open"] [role="option"]'
        )
        possibilities = []
        for index in range(min(candidates.count(), 40)):
            option = candidates.nth(index)
            if not option.is_visible():
                continue
            title = " ".join(option.inner_text().split())
            if title and search.casefold() in title.casefold():
                possibilities.append((option, title))

        if not possibilities:
            print("Ashby location: no matching visible suggestion:", search)
            return False

        # Prefer an exact option for state questions, or a city/state
        # match over another city with the same name.
        target = None
        if state_field:
            exact = [entry for entry in possibilities
                     if entry[1].casefold() == search.casefold()]
            if len(exact) == 1:
                target = exact[0][0]
        else:
            state = requested.split(",", 1)[1].strip() if "," in requested else ""
            state_long = abbreviations.get(state.upper(), state)
            scored = [entry for entry in possibilities if
                      entry[1].casefold().startswith(search.casefold())
                      and (not state or state.casefold() in entry[1].casefold()
                           or state_long.casefold() in entry[1].casefold())]
            if len(scored) == 1:
                target = scored[0][0]
        if target is None and len(possibilities) == 1:
            target = possibilities[0][0]
        if target is None:
            print("Ashby location: ambiguous suggestions:",
                  [text for _, text in possibilities[:5]])
            return False

        target.click(timeout=5000)
        page.wait_for_timeout(300)
        # Confirm that React accepted a selection rather than the typed
        # query remaining in an open popup.
        state = element.evaluate("""el => ({
            value: el.value || '',
            expanded: el.getAttribute('aria-expanded'),
            controls: el.getAttribute('aria-controls')
        })""")
        if not state["value"].strip():
            return False
        if state["expanded"] == "true":
            return False
        return True
    except Exception as exc:
        print("Ashby location selection failed:", type(exc).__name__)
        return False


def is_searchable_location(page, field):
    """Check whether location uses an autocomplete rather than plain text."""
    q = (field.get("label") or "").casefold()
    if not any(part in q for part in ("location", "city", "state", "province", "region")):
        return False
    el = locator(page, field)
    if el.count() != 1:
        return False
    return bool(el.evaluate("""e =>
        e.getAttribute('role') === 'combobox' ||
        e.getAttribute('aria-autocomplete') === 'list' ||
        !!e.getAttribute('aria-controls') ||
        !!e.closest('[role="combobox"]')
    """))

def unresolved_segmented_questions(page):
    """Conservative fallback: surface unanswered Yes/No rows missed by group extraction."""
    return page.evaluate(r"""() => {
      const txt=e => (e?.innerText||e?.textContent||'').replace(/\s+/g,' ').trim();
      const shown=e => !!e?.getClientRects().length;
      const choice=e => /^(yes|no)$/i.test(txt(e));
      const elms=[...document.querySelectorAll('button,[role="radio"],label,div,span')]
        .filter(e => shown(e) && choice(e) && ![...e.children].some(choice));
      const questions=[];
      const used=new Set();
      for(const a of elms){
        const p=a.parentElement;
        if(!p || used.has(p)) continue;
        const siblings=elms.filter(e=>e.parentElement===p);
        if(siblings.length!==2 || !siblings.some(e=>/^yes$/i.test(txt(e))) ||
           !siblings.some(e=>/^no$/i.test(txt(e)))) continue;
        used.add(p);
        let question='';
        for(let n=p,d=0;n&&d<6;n=n.parentElement,d++){
          const prev=n.previousElementSibling;
          if(prev){
            const s=txt(prev);
            if(s.length>12 && s.length<350 && /[?*]/.test(s)){
              question=s;break;
            }
          }
        }
        if(!question) continue;
        const active=e=>{
          if(e.matches('input')) return e.checked;
          for(const x of ['aria-pressed','aria-checked','aria-selected'])
            if(e.getAttribute(x)==='true') return true;
          if(['on','checked','true'].includes(e.getAttribute('data-state'))) return true;
          const inp=e.querySelector('input[type="radio"],input[type="checkbox"]');
          return !!inp?.checked;
        };
        if(!siblings.some(active)) questions.push(question);
      }
      return [...new Set(questions)];
    }""")
