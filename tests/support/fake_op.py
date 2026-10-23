#!/usr/bin/env python3
"""A stand-in for 1Password's `op` for tests: answers from the JSON file named by FAKE_OP_DATA and records each call
(arguments only) in FAKE_OP_LOG. Signed out when the data says "signed_in": false."""
import json
import os
import sys

data = json.load(open(os.environ["FAKE_OP_DATA"]))
args = sys.argv[1:]
with open(os.environ["FAKE_OP_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")


def fail(msg):
    sys.stderr.write(f"[ERROR] 2026/10/22 12:00:00 {msg}\n")
    sys.exit(1)


if args[:2] == ["account", "list"]:
    print(json.dumps(data["accounts"]))
    sys.exit(0)
if not data.get("signed_in", True):
    fail("you are not currently signed in. Please run `op signin --help` for instructions")
if args[:1] == ["whoami"]:
    print(json.dumps(data["accounts"][0]))
elif args[:2] == ["item", "list"]:
    print(json.dumps([{k: v for k, v in i.items() if k != "fields"} for i in data["items"]]))
elif args[:2] == ["item", "get"]:
    item = next((i for i in data["items"] if i["id"] == args[2]), None) or fail(f'"{args[2]}" isn\'t an item.')
    if "--fields" in args:
        want = [w.split("=", 1)[1] for w in args[args.index("--fields") + 1].split(",")]
        print(json.dumps([{"label": f["label"], "value": f["value"] if "--reveal" in args else "[use --reveal]"}
                          for f in item["fields"] if f["label"] in want]))
    else:
        print(json.dumps({**item, "fields": [{**f, "reference": f"op://{item['vault']['name']}/{item['title']}/{f['label']}"}
                                             for f in item["fields"]]}))
elif args[:1] == ["read"]:
    ref = args[-1]
    for i in data["items"]:
        for f in i["fields"]:
            if ref == f"op://{i['vault']['name']}/{i['title']}/{f['label']}":
                sys.stdout.write(f["value"])
                sys.exit(0)
    fail(f'could not read secret "{ref}": isn\'t an item')
else:
    fail(f"unknown command {args}")
