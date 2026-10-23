"""
Run every offline test in one go (Windows, Mac, Linux):

    python run_all.py            # backend + story-count + browser tests
    python run_all.py --no-ui    # skip the browser tests

Starts a stand-in ServiceNow (HTTPS) and a stand-in Flow Server, runs the suites, stops everything.
Nothing touches a real ServiceNow instance. Uses ports 9443, 9998 and 8011 (not 8001, so your real
agent can keep running).
"""
import datetime
import ipaddress
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
ENV = dict(os.environ, SNOW_VERIFY_TLS="false", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
           NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", AGENT_TEST_PORT="8011")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def say(msg):
    print(msg, flush=True)


def pip(*pkgs):
    subprocess.run([PY, "-m", "pip", "install", "-q", *pkgs], check=False)


def make_cert():
    key, cert = HERE / "key.pem", HERE / "cert.pem"
    if key.exists() and cert.exists():
        return key, cert
    try:
        from cryptography import x509
    except ImportError:
        pip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=30))
         .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                     x509.DNSName("localhost")]), critical=False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return key, cert


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def start(args, port, name):
    if port_open(port):
        raise SystemExit(f"Port {port} is already in use — close whatever uses it and run again ({name}).")
    log = open(HERE / f"_{name}.log", "w", encoding="utf-8")
    p = subprocess.Popen(args, cwd=HERE, env=ENV, stdout=log, stderr=subprocess.STDOUT)
    if not wait_port(port):
        p.kill()
        raise SystemExit(f"{name} did not start — see tests/_{name}.log")
    return p


def run_suite(args, title, timeout=1500):
    say(f"\n▶ {title}")
    t = time.time()
    r = subprocess.run(args, cwd=HERE, env=ENV, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    lines = [l for l in r.stdout.splitlines() if l.startswith(("PASS", "FAIL", "===="))]
    for l in lines:
        if l.startswith(("FAIL", "====")):
            say("   " + l)
    m = [re.search(r"(\d+)/(\d+)", l) for l in lines if l.startswith("====")]
    if not m or not m[-1]:
        say("   (suite did not finish — last output below)")
        say("\n".join(("   " + x) for x in (r.stdout + r.stderr).splitlines()[-15:]))
        return 0, 1
    passed, total = int(m[-1].group(1)), int(m[-1].group(2))
    say(f"   {passed}/{total} passed in {time.time() - t:.0f}s")
    return passed, total


def main():
    ui = "--no-ui" not in sys.argv
    say("SoftProdigy Snow Agent — offline test run (stand-in ServiceNow, simulated AI)")
    key, cert = make_cert()
    procs = []
    results = []
    try:
        sn_args = [PY, "-m", "uvicorn", "fake_sn2:app", "--host", "127.0.0.1", "--port", "9443",
                   "--ssl-keyfile", str(key), "--ssl-certfile", str(cert), "--log-level", "warning"]
        procs.append(start(sn_args, 9443, "fake_servicenow"))
        procs.append(start([PY, "-m", "uvicorn", "fake_flow:app", "--host", "127.0.0.1", "--port", "9998",
                            "--log-level", "warning"], 9998, "fake_flow_server"))
        results.append(("Backend (e2e_v6.py)",) + run_suite([PY, "e2e_v6.py"], "Backend checks"))
        results.append(("Story counts",) + run_suite([PY, "story_count_test.py"], "Story count checks"))
        results.append(("Flows & subflows",) + run_suite([PY, "flows_subflows_test.py"], "Flow / subflow checks"))
        results.append(("Scripting fixes",) + run_suite([PY, "scripting_fixes_test.py"], "Scripting fix checks"))
        results.append(("Requirement pipeline",) + run_suite([PY, "requirement_pipeline_test.py"],
                                                             "Requirement pipeline checks"))
        results.append(("Vendored scripting tools",) + run_suite([PY, "vendored_scripting_audit.py"],
                                                                 "Vendored scripting write-path checks"))
        results.append(("AI Agent artifacts",) + run_suite([PY, "ai_agent_artifact_test.py"],
                                                           "AI Agent chat: script include / client script / BR routing"))
        results.append(("Requirement wiring",) + run_suite([PY, "requirement_wiring_test.py"],
                                                           "Requirement wiring checks (REST, MCP Chat, Implement, UI)"))
        if ui:
            try:
                import playwright  # noqa: F401
            except ImportError:
                pip("playwright")
            subprocess.run([PY, "-m", "playwright", "install", "chromium"], env=ENV, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # fresh stand-in ServiceNow for the browser run
            procs[0].terminate(); procs[0].wait(timeout=10)
            time.sleep(1)
            procs[0] = start(sn_args, 9443, "fake_servicenow")
            procs.append(start([PY, "run_agent_mock.py"], 8011, "agent_under_test"))
            (HERE / "shots").mkdir(exist_ok=True)
            results.append(("Browser (ui_test.py)",) + run_suite([PY, "ui_test.py"], "Browser checks"))
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                p.kill()
    say("\n══════════════ SUMMARY ══════════════")
    ok_all = True
    for name, p, t in results:
        ok_all &= p == t
        say(f"  {'✅' if p == t else '❌'} {name}: {p}/{t}")
    say("  Screenshots: tests\\shots\\   Logs: tests\\_*.log" if ui else "")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
