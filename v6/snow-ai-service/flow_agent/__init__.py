"""Flow Designer flows & subflows for the Build App pipeline (ported from the standalone now2.py / now3.py agent).

  actions.py  — plan core.* actions / record.* triggers  <->  Flow Designer UI labels
  browser.py  — Playwright engine that builds subflows and flows in the Flow Designer UI
  kb.py       — subflow knowledge base (reuse a published subflow instead of rebuilding it)
"""
