"""
AGE-57 regression test: Xod must ship under one stable signing identity.

Root cause (field evidence, the home Mac, 2026-11-14 and 2026-11-15; Linear
AGE-57): every release is built by electron/scripts/build-mac.sh, which
forced electron-builder identity "-" (ad-hoc). An ad-hoc signature has no
certificate, so the app's designated requirement is its cdhash alone
(v1.1.8 on the Mac: cdhash 1a45fb2dbfcb1c4294f3affb7a6a92a9c704aff5).
macOS binds TCC grants (Accessibility, Screen Recording) and keychain
"Always Allow" entries to the designated requirement, so a grant made on
one build can never match the next: on v1.1.10, three fresh process
generations read desktop status ready false even after the owner switched the
current /Applications/Xod.app entry on under both services, and every
update brings the keychain prompt for the bundled python3.12 back.

What is proven here, without a Mac or a build:
  - the designated-requirement checker (scripts/check_designated_requirement.py)
    rejects the ad-hoc cdhash-only requirement shape from the field evidence,
    rejects any requirement carrying a cdhash term, rejects a missing
    requirement, and accepts certificate-anchored requirements (a self-signed
    shape and a Developer ID shape);
  - the shipped build path can no longer silently produce an ad-hoc build:
    build-mac.sh resolves an identity (XOD_MAC_SIGN_IDENTITY, else the
    CSC_LINK certificate electron-builder imports in CI, else a loud ad-hoc
    fallback), treats an exported-but-empty CSC_LINK as unset, and runs the
    checker on the built app, failing the build when an identity was
    requested but the product is not certificate-anchored;
  - the release workflow hands CSC_LINK and CSC_KEY_PASSWORD from GitHub
    secrets to the Mac build step;
  - the one-time identity setup script exists, refuses to overwrite an
    existing identity, and marks the certificate trusted for code signing;
  - partner/install.sh prefers the same identity and keeps its ad-hoc
    fallback;
  - docs/SIGNING.md documents the setup and the paid Developer ID route.

The live half (grant once on a signed build N, install build N+1, grants
still read true) needs two signed builds and the home Mac; it is the AGE-57
acceptance test and stays open until it runs there.

Run: python3 tests/release/run_signing_requirement_test.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def _load_checker():
    path = ROOT / "scripts" / "check_designated_requirement.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("check_designated_requirement", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The v1.1.8 designated requirement, verbatim shape from the AGE-57 evidence.
ADHOC_DR = 'cdhash H"1a45fb2dbfcb1c4294f3affb7a6a92a9c704aff5"'
SELF_SIGNED_DR = ('identifier "dev.xod.desktop" and certificate leaf = '
                  'H"148a7574576813f28d7f08be80f4882ec9bfba87"')
DEVELOPER_ID_DR = ('identifier "dev.xod.desktop" and anchor apple generic and '
                   'certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and '
                   'certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and '
                   'certificate leaf[subject.OU] = "ABCDE12345"')
CDHASH_AND_CERT_DR = ('identifier "dev.xod.desktop" and cdhash '
                      'H"573a437d00000000000000000000000000000000" and '
                      'certificate leaf = H"148a7574576813f28d7f08be80f4882ec9bfba87"')


def test_checker_fixtures():
    checker = _load_checker()
    check("designated requirement checker exists at scripts/check_designated_requirement.py",
          checker is not None)
    if checker is None:
        for name in ("ad-hoc cdhash-only requirement is rejected",
                     "requirement carrying a cdhash term is rejected",
                     "missing requirement is rejected",
                     "self-signed certificate requirement is accepted",
                     "Developer ID requirement is accepted",
                     "parse_requirement reads real codesign -d -r- output"):
            check(name, False, "checker module missing")
        return
    ok, _ = checker.evaluate(ADHOC_DR, "Signature=adhoc")
    check("ad-hoc cdhash-only requirement is rejected", not ok)
    ok, _ = checker.evaluate(CDHASH_AND_CERT_DR, "")
    check("requirement carrying a cdhash term is rejected", not ok)
    ok, _ = checker.evaluate(None, "")
    check("missing requirement is rejected", not ok)
    ok, problems = checker.evaluate(SELF_SIGNED_DR, "")
    check("self-signed certificate requirement is accepted", ok, "; ".join(problems))
    ok, problems = checker.evaluate(DEVELOPER_ID_DR, "")
    check("Developer ID requirement is accepted", ok, "; ".join(problems))
    sample = ("Executable=/Applications/Xod.app/Contents/MacOS/Xod\n"
              "designated => " + SELF_SIGNED_DR + "\n")
    check("parse_requirement reads real codesign -d -r- output",
          checker.parse_requirement(sample) == SELF_SIGNED_DR)


def test_build_pipeline():
    build_mac = (ROOT / "electron" / "scripts" / "build-mac.sh").read_text(errors="replace")
    check("build-mac.sh resolves XOD_MAC_SIGN_IDENTITY instead of always forcing ad-hoc",
          "XOD_MAC_SIGN_IDENTITY" in build_mac and "CSC_LINK" in build_mac)
    check("build-mac.sh treats an exported-but-empty CSC_LINK as unset",
          "unset CSC_LINK" in build_mac)
    check("build-mac.sh keeps an ad-hoc fallback for cert-less local builds, with a loud warning",
          "-c.mac.identity=-" in build_mac and "no signing identity" in build_mac)
    check("build-mac.sh runs the designated requirement checker on the built app",
          "check_designated_requirement.py" in build_mac)
    package_json = (ROOT / "electron" / "package.json").read_text(errors="replace")
    check("the shipped dist path still routes through build-mac.sh",
          '"dist": "bash scripts/build-mac.sh"' in package_json)


def test_ci_and_setup():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(errors="replace")
    check("release workflow passes CSC_LINK and CSC_KEY_PASSWORD from secrets to the Mac build",
          "secrets.CSC_LINK" in workflow and "secrets.CSC_KEY_PASSWORD" in workflow)
    setup = ROOT / "electron" / "scripts" / "create-signing-identity.sh"
    setup_text = setup.read_text(errors="replace") if setup.exists() else ""
    check("one-time signing identity setup script exists and refuses to overwrite an identity",
          setup.exists() and "already exists" in setup_text)
    check("setup script marks the certificate trusted for code signing",
          "add-trusted-cert" in setup_text and "codeSign" in setup_text)
    docs = ROOT / "docs" / "SIGNING.md"
    docs_text = docs.read_text(errors="replace") if docs.exists() else ""
    check("docs/SIGNING.md documents the setup, the one-time grant reset, and the Developer ID route",
          docs.exists() and "Developer ID" in docs_text and "one last time" in docs_text)


def test_partner_identity():
    install = (ROOT / "partner" / "install.sh").read_text(errors="replace")
    check("partner/install.sh prefers the stable signing identity when present",
          "XOD_MAC_SIGN_IDENTITY" in install and "Xod Release Signing" in install)
    check("partner/install.sh keeps its ad-hoc fallback",
          "--sign -" in install)


def main():
    test_checker_fixtures()
    test_build_pipeline()
    test_ci_and_setup()
    test_partner_identity()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    if failed:
        print("FAILED: " + "; ".join(failed), flush=True)
        sys.exit(1)
    print("PASS: the shipped build path signs under one stable identity or says so loudly (AGE-57)", flush=True)


if __name__ == "__main__":
    main()
