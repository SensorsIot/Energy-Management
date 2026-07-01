# Standard — Testing (portable)

Testing is a first-class part of the build contract, not an afterthought. Every behaviour rule in the
FSD is verifiable, and every change ships with the test that proves it.

## The test pyramid

Four levels, each with a defined home:

| Level | Verifies | Test-spec home (WHAT) | Code home |
|---|---|---|---|
| **Unit** | one function/module in isolation | the owning FSD section (per-topic acceptance criteria) | `tests/` beside the component |
| **Integration** | cross-module behaviour (A talks to B correctly) | the owning FSD's integration section | `tests/` |
| **End-to-end** | the whole system over a realistic scenario | the top-level component FSD | a dedicated E2E test/harness |
| **Live / observer** | the running system, passively, against real data | the owning FSD | an observer test run against production |

Prefer the lowest level that can catch the failure; reserve integration/E2E for behaviour no unit can
reach.

## Rules

- **Every behaviour rule has a test case.** A rule in the FSD without a corresponding test case is an
  untested contract — flag it.
- **Every change ships its test.** A new or changed behaviour is not done until its test case is
  written (in the owning FSD) and its test passes. Bug fixes add the regression test first.
- **Test-case specs live with the behaviour they verify** (in the owning FSD, `ID | Description |
  Setup | Expected | Status` form), never in a separate parallel document — the strategy and the
  index are HOW; the specs are WHAT.
- **Test code lives beside its component** (`tests/`), named `test_<unit>.py`; each case traces to a
  spec ID.
- **Tests pass before commit.** The commands are the project's build-and-release rule; a failing
  suite blocks the commit.
- **No silent gaps.** Unbuilt-but-specified cases are marked (e.g. `Future`) with their prerequisite,
  so the coverage picture is honest.

## Security testing — anchor to international standards

Security is a test dimension of its own, and its cases are grounded in **international standards**,
not ad-hoc checks. Anchor security test cases to (as applicable):

| Standard | Role |
|---|---|
| **OWASP ASVS** (Application Security Verification Standard) | the requirements catalogue — each verification requirement is a test case |
| **OWASP Top 10** + **WSTG** (Web Security Testing Guide) | the common risk classes and the test procedures for them |
| **CWE** (MITRE Common Weakness Enumeration) | classify each finding by weakness ID |
| **CVSS** | score the severity of a finding |
| **NIST SP 800-115** | the methodology for technical security testing |
| **ISO/IEC 27001 / 27034** | the governing security-management / application-security frameworks |

Rules:
- **Every security-relevant behaviour rule has a security test case** — authentication, authorization,
  input validation, secrets handling, cryptography, and the OWASP Top 10 classes relevant to the
  component.
- **Security cases live in the owning FSD's test chapter** like any other, each **tagged with its
  standard reference** (e.g. ASVS requirement ID, CWE ID) so the coverage traces to the standard.
- Score and triage findings by **CVSS**; don't leave a known weakness unclassified.
