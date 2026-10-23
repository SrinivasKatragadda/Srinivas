"""
Flow Designer browser engine (ported from the standalone now2.py agent).

Drives the Flow Designer UI with Playwright to build subflows (inputs/outputs + actions + publish) and flows
(trigger + actions or a subflow call + activate). Used by sn_build.Builder.step_flows when no Flow Server is
configured, and always when a plan contains subflows.

Differences from now2.py:
  * one browser per build, logged in as the user who started the build (no hard-coded or cached credentials);
  * every `input()` pause is a `pause()` that is answered from the Build App screen (Continue / Skip / Retry /
    Cancel) instead of the terminal;
  * no "publish" by PATCHing sys_hub_flow.state — publishing is only done through the UI, and the caller
    verifies the result in sys_hub_flow;
  * flows are created in the application + update set the build made current, so they are captured in the
    update set like every other artifact.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .actions import MANDATORY_FIELDS, TRIGGER_EVENTS, describe, fields_of, inp, ui_action_name

AskFn = Callable[[dict], dict]
LogFn = Callable[[str, str], None]


class FlowCancelled(Exception):
    """The user cancelled (or did not answer) a pause."""


class FlowUIError(Exception):
    """Flow Designer did not behave as expected."""


def playwright_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


def _css(text: str) -> str:
    return json.dumps(str(text))  # safe quoted string for :has-text() / :text-is()


# ── JavaScript helpers (from now2.py) ────────────────────────────────────────────

# Fill the "Flow name" / "Subflow name" input of the New dialog, walking shadow roots.
_JS_SET_NAME = r"""
(name) => {
  function inputForLabel(root, texts) {
    for (const text of texts) {
      for (const lbl of root.querySelectorAll('label, .now-form-label, [class*="label"]')) {
        if ((lbl.textContent || '').toLowerCase().includes(text)) {
          const forId = lbl.getAttribute('for');
          if (forId) { const i = root.querySelector('#' + CSS.escape(forId)); if (i) return i; }
          const sib = lbl.nextElementSibling;
          if (sib && (sib.tagName === 'INPUT' || sib.tagName === 'TEXTAREA')) return sib;
          const p = lbl.parentElement; if (p) { const i2 = p.querySelector('input'); if (i2) return i2; }
        }
      }
      for (const ni of root.querySelectorAll('now-input, now-text-input')) {
        const l = (ni.getAttribute('label') || ni.getAttribute('aria-label') || '').toLowerCase();
        if (l.includes(text) && ni.shadowRoot) { const i = ni.shadowRoot.querySelector('input'); if (i) return i; }
      }
    }
    return null;
  }
  function search(root, texts) {
    const f = inputForLabel(root, texts); if (f) return f;
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { const r = search(el.shadowRoot, texts); if (r) return r; }
    return null;
  }
  const t = search(document, ['subflow name', 'flow name', 'name']);
  if (!t) return 'NOT_FOUND';
  t.focus();
  const proto = t.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const d = Object.getOwnPropertyDescriptor(proto, 'value');
  if (d && d.set) d.set.call(t, name); else t.value = name;
  ['input', 'change', 'blur', 'keyup'].forEach(e => t.dispatchEvent(new Event(e, {bubbles: true})));
  return t.value;
}
"""

# Click a now-button / button / role=button by label, including inside shadow roots.
_JS_BTN = r"""
(lbl) => {
  function scan(root) {
    for (const nb of root.querySelectorAll('now-button')) {
      const l = (nb.getAttribute('label') || nb.getAttribute('aria-label') || '').trim();
      if (l === lbl) { nb.dispatchEvent(new MouseEvent('click', {bubbles: true})); return 'now-button:' + l; }
      if (nb.shadowRoot) { const b = nb.shadowRoot.querySelector('button');
        if (b && (b.innerText || '').trim() === lbl) { b.click(); return 'now-button-inner:' + lbl; } }
    }
    for (const el of root.querySelectorAll('button,[role="button"]')) {
      const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
      if (t === lbl) { el.scrollIntoView({block: 'center'}); el.click(); return el.tagName + ':' + t; }
    }
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { const r = scan(el.shadowRoot); if (r) return r; }
    return null;
  }
  return scan(document);
}
"""

# Click the visible element whose text equals (or contains) the given text.
_JS_CLICK_TEXT = r"""
([text, partial]) => {
  const roles = new Set(['button', 'menuitem', 'option', 'tab', 'listitem', 'treeitem']);
  const clickable = el => ['BUTTON', 'A', 'LI'].includes(el.tagName) || roles.has(el.getAttribute('role'))
                         || getComputedStyle(el).cursor === 'pointer';
  for (const el of document.querySelectorAll('*')) {
    if (!el.offsetParent && el.tagName !== 'BODY') continue;
    const t = (el.innerText || '').trim(); if (!t) continue;
    if (partial ? t.includes(text) : t === text) {
      let n = el, d = 0;
      while (n && d < 8) { if (clickable(n)) { n.click(); return n.tagName; } n = n.parentElement; d++; }
      el.click(); return 'leaf';
    }
  }
  return null;
}
"""

# Fill the input/textarea that follows a "Message" style label in an action panel (never the canvas name field).
_JS_FILL_AFTER_LABEL = r"""
([labels, value]) => {
  function scan(root) {
    for (const el of root.querySelectorAll('label,span,div')) {
      const t = (el.textContent || '').trim().toLowerCase();
      if (!labels.includes(t)) continue;
      let sib = el.nextElementSibling;
      for (let d = 0; d < 5 && sib; d++) {
        const i = sib.matches('input,textarea') ? sib : sib.querySelector('input,textarea');
        if (i && i.getBoundingClientRect().y >= 120) {
          i.focus();
          const proto = i.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const pd = Object.getOwnPropertyDescriptor(proto, 'value');
          if (pd && pd.set) pd.set.call(i, value); else i.value = value;
          ['input', 'change', 'blur'].forEach(e => i.dispatchEvent(new Event(e, {bubbles: true})));
          return 'ok';
        }
        sib = sib.nextElementSibling;
      }
    }
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { const r = scan(el.shadowRoot); if (r) return r; }
    return null;
  }
  return scan(document);
}
"""

CANVAS_ADD_STEP = ["button:has-text('Add an Action, Flow Logic, or Subflow')",
                   "a:has-text('Add an Action, Flow Logic, or Subflow')", "button:has-text('Add an Action')"]
NAME_FIELD_MIN_Y = 120  # the flow name field at the top of the canvas — never overwrite it with action values


class FlowDesignerUI:
    def __init__(self, instance: str, username: str, password: str, log: LogFn, ask: Optional[AskFn] = None,
                 pause_steps: bool = True, headless: Optional[bool] = None, shots_dir: Optional[Path] = None,
                 verify_tls: bool = True):
        self.instance = instance.rstrip("/")
        self.username, self.password = username, password
        self._log, self.ask = log, ask
        if headless is None:
            headless = os.getenv("FLOW_BROWSER_HEADLESS", "false").lower() == "true"
        self.headless = headless
        # In a hidden browser nobody can fix a field, so only the pauses that need a decision remain.
        self.pause_steps = pause_steps and not headless
        self.shots_dir = shots_dir
        self.verify_tls = verify_tls
        self._pw = self.browser = self.ctx = self.page = None

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        if not playwright_available():
            raise FlowUIError("Playwright is not installed — run: pip install playwright && "
                              "python -m playwright install chromium")
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self.browser = self._pw.chromium.launch(
                headless=self.headless, slow_mo=60,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
        except Exception as e:
            self.close()
            raise FlowUIError(f"Could not start Chromium ({str(e)[:160]}) — run: python -m playwright install "
                              "chromium")
        self.ctx = self.browser.new_context(viewport={"width": 1920, "height": 1080},
                                            ignore_https_errors=not self.verify_tls)
        self.ctx.set_default_timeout(30000)
        self.page = self.ctx.new_page()
        self.login()

    def close(self):
        for obj, meth in ((self.ctx, "close"), (self.browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._pw = self.browser = self.ctx = self.page = None

    # ── logging / pauses ───────────────────────────────────────────────────────
    def log(self, msg: str, status: str = "info"):
        self._log(msg, status)

    def shot(self, tag: str) -> Optional[str]:
        if not (self.shots_dir and self.page):
            return None
        try:
            self.shots_dir.mkdir(parents=True, exist_ok=True)
            p = self.shots_dir / f"{datetime.now().strftime('%H%M%S')}_{re.sub(r'[^A-Za-z0-9]+', '_', tag)[:40]}.png"
            self.page.screenshot(path=str(p), full_page=True)
            return str(p)
        except Exception:
            return None

    def pause(self, title: str, message: str, fields: Iterable[str] = (), prefilled: Optional[dict] = None,
              options: Iterable[str] = ("continue", "skip", "cancel"), required: bool = False) -> str:
        """Wait for the user's answer in the Build App screen. Returns the chosen action."""
        if not required and not self.pause_steps:
            return "continue"
        if not self.ask:
            if required:
                raise FlowUIError(f"{title}: {message}")
            return "continue"
        ans = self.ask({"title": title, "message": message, "fields": list(fields),
                        "prefilled": {k: (v if isinstance(v, (str, int, float)) else json.dumps(v))
                                      for k, v in (prefilled or {}).items()},
                        "options": list(options), "screenshot": self.shot(title)}) or {}
        action = ans.get("action", "continue")
        if action in ("cancel", "timeout"):
            raise FlowCancelled("cancelled by the user" if action == "cancel" else "no answer before the timeout")
        return action

    # ── low-level helpers ──────────────────────────────────────────────────────
    def frames(self) -> list:
        try:
            return list(self.page.frames)
        except Exception:
            return [self.page]

    def visible(self, sel: str, last: bool = False):
        for f in self.frames():
            try:
                loc = f.locator(sel).last if last else f.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def click_any(self, sels: Iterable[str], label: str = "", last: bool = False, timeout: int = 3000) -> bool:
        for sel in sels:
            loc = self.visible(sel, last=last)
            if loc:
                try:
                    loc.click(timeout=timeout)
                    if label:
                        self.log(f"   · {label}")
                    return True
                except Exception:
                    continue
        return False

    def js_click(self, text: str, partial: bool = False) -> bool:
        for f in self.frames():
            try:
                if f.evaluate(_JS_CLICK_TEXT, [text, partial]):
                    return True
            except Exception:
                continue
        return False

    def js_button(self, label: str) -> bool:
        for f in self.frames():
            try:
                if f.evaluate(_JS_BTN, label):
                    return True
            except Exception:
                continue
        return False

    def wait_for(self, sels: Iterable[str], timeout: float = 20):
        sels = list(sels)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for sel in sels:
                loc = self.visible(sel)
                if loc:
                    return loc
            time.sleep(0.7)
        return None

    def fill(self, loc, value: str) -> bool:
        for fn in (lambda: (loc.click(timeout=2000), loc.press("Control+a"), loc.type(value, delay=15)),
                   lambda: loc.fill(value)):
            try:
                fn()
                time.sleep(0.2)
                try:
                    if loc.input_value() == value:
                        return True
                except Exception:
                    return True
            except Exception:
                continue
        return False

    def fill_by_label(self, label: str, value: str) -> bool:
        """Fill an action-panel input by its label; never touches the canvas name field at the top."""
        q = _css(label)
        sels = [f"input[aria-label={q}]", f"input[aria-label*={q} i]", f"input[placeholder*={q} i]",
                f"textarea[aria-label*={q} i]", f"textarea[placeholder*={q} i]",
                f"label:has-text({q}) ~ input", f"tr:has-text({q}) input"]
        for f in self.frames():
            for sel in sels:
                try:
                    loc = f.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        if (loc.bounding_box() or {}).get("y", 999) < NAME_FIELD_MIN_Y:
                            continue
                        if self.fill(loc, value):
                            return True
                except Exception:
                    continue
        for f in self.frames():
            try:
                if f.evaluate(_JS_FILL_AFTER_LABEL, [[label.lower()], value]):
                    return True
            except Exception:
                continue
        return False

    def dismiss_tours(self):
        self.click_any(["button:has-text('Skip Tour')", "button:has-text('Maybe later')",
                        "button:has-text('Got it')", "button:has-text('Dismiss')",
                        # the "New version available" banner sits over Workflow Studio's New button
                        "[role='alert'] button[aria-label*='close' i]", "now-alert button[aria-label*='close' i]",
                        "[role='status'] button[aria-label*='close' i]"], timeout=800)

    # ── session ────────────────────────────────────────────────────────────────
    def _on_login_page(self) -> bool:
        try:
            return self.page.locator("input#user_name").count() > 0
        except Exception:
            return False

    def login(self):
        self.page.goto(self.instance + "/login.do", wait_until="domcontentloaded", timeout=60000)
        if self._on_login_page():
            self.page.locator("input#user_name").fill(self.username)
            self.page.locator("input#user_password").fill(self.password)
            self.page.locator("button#sysverb_login").click()
            try:
                self.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
        if self._on_login_page() or "login" in (self.page.url or "").lower():
            self.pause("Sign in to ServiceNow in the browser window",
                       "The automatic sign-in did not finish (SSO, MFA or a password prompt). Complete the "
                       "sign-in in the Chromium window the agent opened, then press Continue.",
                       options=("continue", "cancel"), required=True)
            if self._on_login_page():
                raise FlowUIError("still on the ServiceNow login page")
        self.log(f"🌐 Flow Designer browser signed in as {self.username}", "ok")

    # ── navigation ─────────────────────────────────────────────────────────────
    def _new_button(self):
        return self.visible("button:has-text('New')")

    def goto_list(self, kind: str):
        """Open the Workflow Studio list for flows or subflows and wait for the New button."""
        for path in (f"/now/workflow-studio/home/{kind.lower()}", "/now/workflow-studio/home",
                     "/$flow-designer.do?sysparm_nostack=true"):
            try:
                self.page.goto(self.instance + path, wait_until="domcontentloaded", timeout=60000)
                try:
                    self.page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                if self._on_login_page():
                    self.login()
                    continue
                self.dismiss_tours()
                if self.wait_for(["button:has-text('New')"], timeout=15):
                    return
            except Exception:
                continue
        raise FlowUIError("Workflow Studio did not show a 'New' button — check the user has the flow_designer "
                          "or admin role")

    def open_new(self, kind: str, name: str, description: str = ""):
        """New → Flow/Subflow → name + description → Build. kind is 'Flow' or 'Subflow'."""
        self.goto_list(kind)

        def dialog_open() -> bool:
            return any(self.visible(s) for s in ("[role='dialog']", "now-modal", ".modal-dialog", "dialog"))

        for _ in range(5):
            btn = self._new_button()
            if btn:
                try:
                    btn.click(timeout=5000)
                except Exception:
                    try:   # something (a banner, a toast) covers the button: click the element itself
                        btn.evaluate("e => e.click()")
                    except Exception:
                        pass
            time.sleep(2)
            if not self.click_any([f"li:text-is({_css(kind)})", f"[role='option']:text-is({_css(kind)})",
                                   f"[role='menuitem']:text-is({_css(kind)})"]):
                self.js_click(kind)
            time.sleep(2)
            if dialog_open():
                break
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
        else:
            raise FlowUIError(f"Could not open the New {kind} dialog")

        self._set_name(name)
        if description:
            loc = self.visible("textarea[aria-label*='description' i]") or self.visible("textarea")
            if loc:
                self.fill(loc, description)

        for attempt in range(10):
            clicked = self.click_any(["button:has-text('Build')", "button:has-text('Submit')",
                                      "button[type='submit']"])
            if not clicked:
                clicked = any(self.js_button(lbl) for lbl in ("Build", "Build flow", "Build subflow", "Submit"))
            time.sleep(2.5)
            if not dialog_open():
                break
            body = ""
            try:
                body = self.page.locator("body").inner_text(timeout=2000).lower()
            except Exception:
                pass
            if "name is required" in body or "invalid values" in body or attempt % 3 == 2:
                self._set_name(name)
        if dialog_open():
            raise FlowUIError(f"The New {kind} dialog did not accept the name '{name}'")

        if not self.wait_for(CANVAS_ADD_STEP + ["text='Add a trigger'"], timeout=60):
            raise FlowUIError(f"The {kind} designer canvas did not load")
        self.dismiss_tours()
        self.log(f"   · {kind} canvas open: {name}")

    def _set_name(self, name: str):
        for _ in range(6):
            try:
                if self.page.evaluate(_JS_SET_NAME, name) == name:
                    return
            except Exception:
                pass
            for sel in ("input[aria-label='Subflow name']", "input[aria-label='Flow name']",
                        "input[aria-label*='name' i]", "input[placeholder*='name' i]"):
                loc = self.visible(sel)
                if loc and self.fill(loc, name):
                    return
            time.sleep(0.7)

    # ── subflow inputs / outputs ───────────────────────────────────────────────
    def fill_io(self, inputs: List[dict], outputs: List[dict]):
        if not inputs and not outputs:
            return
        if not self.click_any(["button:has-text('Inputs')", "text='Select to create the inputs & outputs of your "
                                                            "subflow'", "button[aria-label*='inputs' i]"]):
            self.js_click("Select to create the inputs", partial=True)
        time.sleep(2)

        def add_row(button: str, row: dict):
            if not self.click_any([f"button:has-text({_css(button)})", f"a:has-text({_css(button)})"]):
                self.js_click(button)
            time.sleep(1.2)
            for f in self.frames():
                try:
                    vis = [i for i in f.locator("input[type='text']:visible, input:not([type]):visible").all()
                           if (i.bounding_box() or {}).get("width", 0) > 60
                           and (i.bounding_box() or {}).get("y", 0) >= NAME_FIELD_MIN_Y]
                    empty = [i for i in vis if not i.input_value()]
                    target = empty if len(empty) >= 2 else vis
                    if len(target) >= 2:
                        self.fill(target[-2], row.get("label") or row.get("name"))
                        self.fill(target[-1], row.get("name") or "")
                        sels = f.locator("select:visible").all()
                        if sels:
                            try:
                                sels[-1].select_option(label=row.get("type", "String"))
                            except Exception:
                                pass
                        return
                except Exception:
                    continue

        for row in inputs:
            add_row("Add input", row)
        for row in outputs:
            add_row("Add output", row)
        fmt = lambda rows: ", ".join(f"{r.get('label')} ({r.get('name')}: {r.get('type', 'String')})" for r in rows)
        self.pause("Check the subflow inputs & outputs",
                   "The agent added the rows below. Correct any label, name or type in the browser, then press "
                   "Continue.",
                   fields=[f"Inputs: {fmt(inputs) or 'none'}", f"Outputs: {fmt(outputs) or 'none'}"],
                   options=("continue", "cancel"))
        self.click_any(["button:has-text('Done')", "button:has-text('Save')"], last=True)
        time.sleep(1.5)

    # ── trigger ────────────────────────────────────────────────────────────────
    def set_trigger(self, trigger: dict):
        ttype = (trigger.get("type") or "record.created").lower()
        event = TRIGGER_EVENTS.get(ttype, "Created")
        table, condition = trigger.get("table") or "", trigger.get("condition") or ""
        if not self.click_any(["button:has-text('Add a trigger')", "a:has-text('Add a trigger')"]):
            self.js_click("Add a trigger")
        time.sleep(3)
        # newer releases list "Created" etc. directly; older ones need "Record" first
        if not self.click_any([f"li:text-is({_css(event)})", f"[role='option']:text-is({_css(event)})"]):
            if not self.click_any(["li:text-is('Record')", "[role='option']:text-is('Record')"]):
                self.js_click("Record")
            time.sleep(2)
            if not self.click_any([f"li:text-is({_css(event)})", f"[role='option']:text-is({_css(event)})"]):
                self.js_click(event)
        time.sleep(2.5)
        tbl = self.visible("input[placeholder*='table' i]") or self.visible("input[aria-label*='table' i]")
        if tbl and table:
            self.fill(tbl, table)
            time.sleep(2.5)
            if not self.click_any([f"li:has-text({_css(table)})", f"div[role='option']:has-text({_css(table)})"]):
                try:
                    self.page.keyboard.press("ArrowDown")
                    self.page.keyboard.press("Enter")
                except Exception:
                    pass
        self.pause("Check the trigger",
                   f"Trigger: Record {event} on '{table}'." +
                   (f" Add this condition in the trigger's condition builder: {condition}" if condition else ""),
                   fields=["Table", "Trigger event"] + (["Condition"] if condition else []),
                   prefilled={"Table": table, "Event": event, **({"Condition": condition} if condition else {})})
        self.click_any(["button:has-text('Done')", "button[aria-label='Done']"], last=True)
        time.sleep(2)

    # ── actions ────────────────────────────────────────────────────────────────
    def _add_step(self, tab: str):
        for _ in range(10):
            if self.click_any(CANVAS_ADD_STEP):
                break
            time.sleep(1.5)
        else:
            raise FlowUIError("'Add an Action, Flow Logic, or Subflow' is not on the canvas")
        time.sleep(1.2)
        self.click_any([f"button[role='tab']:has-text({_css(tab)})", f"button:has-text({_css(tab)})"])
        time.sleep(1.2)

    def _search_pick(self, name: str) -> bool:
        srch = self.wait_for(["input[placeholder*='search' i]"], timeout=12)
        if not srch:
            return False
        for _ in range(3):
            try:
                srch.click()
                srch.press("Control+a")
                srch.press("Delete")
                srch.type(name, delay=30)
            except Exception:
                self.fill(srch, name)
            time.sleep(3)
            if self.click_any([f"li:has-text({_css(name)})", f"div[role='option']:has-text({_css(name)})",
                               f"td:has-text({_css(name)})", f"span:text-is({_css(name)})"]):
                time.sleep(2.5)
                return True
            if self.js_click(name, partial=True):
                time.sleep(2.5)
                return True
        return False

    def add_action(self, action: dict):
        ui = ui_action_name(action.get("type", ""))
        self._add_step("Action")
        if not self._search_pick(ui):
            a = self.pause(f"Pick the '{ui}' action",
                           f"The agent could not find '{ui}' in the action picker. Select it in the browser, then "
                           "press Continue — or Skip to leave it out.", options=("continue", "skip", "cancel"),
                           required=True)
            if a == "skip":
                self.click_any(["button:has-text('Cancel')", "button:has-text('Close')"])
                return "skipped"
        time.sleep(2)
        pre = describe(action)
        self._prefill(ui, action)
        a = self.pause(f"Action: {ui}",
                       "Check the action in the browser. Fill every mandatory field (data pills such as "
                       "Trigger ▸ Record must be dragged in by hand), then press Continue.",
                       fields=MANDATORY_FIELDS.get(ui, []), prefilled=pre)
        self.click_any(["button:has-text('Done')", "button[aria-label='Done']", "footer button:has-text('Done')"],
                       last=True)
        time.sleep(2)
        return "skipped" if a == "skip" else "added"

    def _prefill(self, ui: str, action: dict):
        if ui == "Log":
            self.fill_by_label("Message", str(inp(action, "log_message", "message", default="Flow executed")))
        elif ui in ("Send Email", "Send Notification"):
            for label, keys in (("To", ("to", "recipients", "recipient")), ("Subject", ("subject",))):
                v = inp(action, *keys)
                if v:
                    self.fill_by_label(label, ", ".join(v) if isinstance(v, list) else str(v))
            body = inp(action, "body", "html", "message")
            ed = self.visible("div[contenteditable='true']", last=True)
            if body and ed:
                try:
                    ed.click()
                    ed.type(str(body))
                except Exception:
                    pass
        elif ui == "Create Task":
            sd = inp(action, "short_description", "description")
            if sd:
                self.fill_by_label("Short Description", str(sd))
        elif ui in ("Create Record", "Update Record", "Delete Record", "Look Up Record", "Look Up Records",
                    "Ask For Approval"):
            tbl = inp(action, "table", "table_name")
            loc = self.visible("input[placeholder*='table' i]") or self.visible("input[aria-label*='table' i]")
            if tbl and loc and self.fill(loc, str(tbl)):
                time.sleep(2)
                self.click_any([f"li:has-text({_css(tbl)})", f"div[role='option']:has-text({_css(tbl)})"])
            for k, v in fields_of(action).items():
                self.fill_by_label(k, v)
        elif ui == "Script":
            sc = inp(action, "script")
            ed = self.visible("div[contenteditable='true']") or self.visible("textarea[aria-label*='script' i]")
            if sc and ed:
                try:
                    ed.click()
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.type(str(sc))
                except Exception:
                    pass

    def call_subflow(self, subflow_name: str, inputs: Optional[dict] = None):
        """Add a 'Subflow' step and select the (published) subflow by name."""
        self._add_step("Subflow")
        picked = False
        for wait in (0, 15, 30):  # a freshly published subflow can take a moment to appear in the picker
            if wait:
                self.log(f"   · waiting {wait}s for ServiceNow to list the subflow")
                time.sleep(wait)
            if self._search_pick(subflow_name):
                picked = True
                break
        if not picked:
            self.pause("Select the subflow",
                       f"'{subflow_name}' was not found in the subflow picker. Select it in the browser (it must be "
                       "published), then press Continue.", options=("continue", "cancel"), required=True)
        self.pause(f"Map the inputs of '{subflow_name}'",
                   "Drag the data pills into the subflow inputs (for example Trigger ▸ Record ▸ Sys ID), then press "
                   "Continue.", fields=[f"{k} ← {v}" for k, v in (inputs or {}).items()] or ["Subflow inputs"],
                   options=("continue", "cancel"))
        self.click_any(["button:has-text('Done')", "button[aria-label='Done']"], last=True)
        time.sleep(2)

    # ── save / publish / activate ──────────────────────────────────────────────
    def save(self):
        if not self.click_any(["header button:has-text('Save')", "button[aria-label='Save']",
                               "button:has-text('Save')"]):
            if not self.js_button("Save"):
                self.page.keyboard.press("Control+s")
        time.sleep(3)

    def publish(self, kind: str):
        """Subflow → Publish; Flow → Activate. UI buttons only; the caller verifies in sys_hub_flow."""
        label = "Publish" if kind == "Subflow" else "Activate"
        try:
            self.page.evaluate("window.scrollTo(0,0)")
        except Exception:
            pass
        self.save()
        if not self.click_any([f"header button:has-text({_css(label)})", f"button[aria-label={_css(label)}]",
                               f"now-button[label={_css(label)}]", f"button:has-text({_css(label)})"]):
            self.js_button(label)
        time.sleep(2.5)
        self.click_any([f"div[role='dialog'] button:has-text({_css(label)})", "div[role='dialog'] button:has-text('OK')",
                        "div[role='dialog'] button:has-text('Confirm')"], last=True, timeout=5000)
        time.sleep(4)
