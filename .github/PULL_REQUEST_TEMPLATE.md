## What this changes

<!-- One or two sentences, in plain words. Link the issue if there is one. -->

## How I checked it

- [ ] `.venv/bin/python tests/smoke/run_smoke_test.py`
- [ ] `.venv/bin/python tests/features/run_features_test.py`
- [ ] Other suites that touch this area (see CONTRIBUTING.md)
- [ ] Tried it by hand in the app

## Safety

- [ ] No keys, personal paths or real data in the diff (`python3 scripts/check_release.py --staged`)
- [ ] Anything that sends, pays, posts or deletes still asks first
