"""MockJobs: a local fake job board for testing Xod's browser missions. Stdlib only."""
import cgi
import html
import json
import secrets
import sys
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
USERS = {}            # email -> password
SESSIONS = {}         # token -> email
DRAFTS = {}           # token -> {job_id: {...fields}}
SUBMISSIONS = []
CART = []
MESSAGES = []
SHOP_JS = """
<script>
function toggleSize(){document.getElementById('sizeList').style.display=document.getElementById('sizeList').style.display==='block'?'none':'block'}
function pickSize(v){document.getElementById('sizeBtn').innerText='Size: '+v;document.getElementById('sizeBtn').dataset.size=v;document.getElementById('sizeList').style.display='none'}
async function addToCart(){const s=document.getElementById('sizeBtn').dataset.size;
  if(!s){document.getElementById('msg').innerText='Please choose a size first.';return}
  await fetch('/shopb/cart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item:'Trail Runner 2',size:s,price:79.50})});
  document.getElementById('msg').innerText='Added to cart: Trail Runner 2, size '+s+'.'}
async function sendMsg(){const t=document.getElementById('msgBox').value;if(!t.trim())return;
  await fetch('/messages/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to:'Dana (Acme Robotics)',text:t})});
  document.getElementById('thread').innerHTML+='<p><b>You:</b> '+t.replace(/</g,'&lt;')+'</p>';document.getElementById('msgBox').value=''}
</script>"""
JOBS = {
    "1": ("Junior Python Developer", "Acme Robotics", "Remote (US)"),
    "2": ("Senior Data Engineer", "Globex", "New York, NY"),
    "3": ("Frontend Engineer (React)", "Initech", "Remote"),
}
CSS = "body{font-family:sans-serif;max-width:720px;margin:40px auto}label{display:block;margin:12px 0 4px}input,select,textarea{padding:6px;width:320px}button{margin-top:16px;padding:8px 16px}.job{border:1px solid #ccc;padding:12px;margin:8px 0}"


def page(title, body):
    return f"<!doctype html><html><head><title>{html.escape(title)}</title><style>{CSS}</style></head><body><nav><a href='/'>MockJobs</a></nav>{body}</body></html>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _session(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        tok = c["mj_session"].value if "mj_session" in c else None
        return tok if tok in SESSIONS else None

    def _send(self, body, status=200, cookie=None, ctype="text/html"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", f"mj_session={cookie}; Path=/; Max-Age=86400; SameSite=Lax")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", f"mj_session={cookie}; Path=/; Max-Age=86400; SameSite=Lax")
        self.end_headers()

    def _form(self):
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype})
            out = {}
            for k in fs.keys():
                item = fs[k]
                if getattr(item, "filename", None):
                    out[k] = {"filename": item.filename, "bytes": len(item.file.read())}
                else:
                    out[k] = item.value
            return out
        n = int(self.headers.get("Content-Length", 0))
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        tok = self._session()
        who = f"<p>Signed in as {html.escape(SESSIONS[tok])} · <a href='/logout'>Sign out</a></p>" if tok else ""
        if u.path == "/":
            return self._send(page("MockJobs - Find work", who + "<h1>Find your next job</h1><form action='/jobs'>"
                                   "<input name='q' placeholder='Job title, keywords, or company'><button>Find jobs</button></form>"))
        if u.path == "/jobs":
            term = (q.get("q", [""])[0]).lower()
            items = "".join(f"<div class='job'><a href='/job/{i}'>{t}</a><br>{c} · {l}</div>"
                            for i, (t, c, l) in JOBS.items() if not term or any(w in t.lower() for w in term.split()))
            return self._send(page("Jobs - MockJobs", who + f"<h1>Results</h1>{items or '<p>No jobs found.</p>'}"))
        if u.path.startswith("/job/"):
            jid = u.path.split("/")[-1]
            t, c, l = JOBS[jid]
            return self._send(page(f"{t} - MockJobs", who + f"<h1>{t}</h1><p>{c} · {l}</p><p>Build things in Python.</p>"
                                   f"<a href='/apply/{jid}'><button>Apply now</button></a>"))
        if u.path == "/login":
            nxt = html.escape(q.get("next", ["/"])[0])
            err = "<p style='color:red'>Wrong email or password.</p>" if q.get("err") else ""
            return self._send(page("Sign in - MockJobs", f"<h1>Sign in</h1>{err}<form method='post' action='/login?next={nxt}'>"
                                   "<label>Email</label><input name='email' type='email'>"
                                   "<label>Password</label><input name='password' type='password'>"
                                   "<br><button type='submit'>Sign in</button></form>"
                                   f"<p>New here? <a href='/signup?next={nxt}'>Create an account</a></p>"))
        if u.path == "/signup":
            nxt = html.escape(q.get("next", ["/"])[0])
            err = html.escape(q.get("err", [""])[0])
            em_prev = html.escape(q.get("email", [""])[0])
            return self._send(page("Create account - MockJobs", f"<h1>Create your account</h1><p style='color:red'>{err}</p>"
                                   f"<form method='post' action='/signup?next={nxt}'>"
                                   f"<label>Email</label><input name='email' type='email' value='{em_prev}'>"
                                   "<label>Password</label><input name='password' type='password'>"
                                   "<label>Confirm password</label><input name='password2' type='password'>"
                                   "<label><input type='checkbox' name='terms' style='width:auto'> I agree to the Terms</label>"
                                   "<button type='submit'>Create account</button></form>"))
        if u.path == "/logout":
            return self._redirect("/", cookie="x")
        if u.path == "/admin/state":
            return self._send(json.dumps({"users": list(USERS), "submissions": SUBMISSIONS, "cart": CART, "messages": MESSAGES}), ctype="application/json")
        if u.path == "/shopa":
            banner = ("<div id='cookie' style='position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center'>"
                      "<div style='background:#fff;padding:24px'><p>We use cookies to improve your experience.</p>"
                      "<button onclick=\"document.getElementById('cookie').remove()\">Accept all</button> "
                      "<button onclick=\"document.getElementById('cookie').remove()\">Reject non-essential</button></div></div>")
            return self._send(page("Shop A - Running", "<h1>Shop A</h1><div class='job'><a href='/shopa/p/trail-runner-2'>Trail Runner 2</a></div>"
                                   "<div class='job'><a href='/shopa/p/road-glide'>Road Glide</a></div>" + banner))
        if u.path == "/shopa/p/trail-runner-2":
            return self._send(page("Trail Runner 2 - Shop A", "<h1>Trail Runner 2</h1><p>Lightweight trail shoe.</p>"
                                   + "<p>Reviews: great grip.</p>" * 3 + "<p class='price'>Price: $89.99</p><p>In stock</p>"))
        if u.path == "/shopa/p/road-glide":
            return self._send(page("Road Glide - Shop A", "<h1>Road Glide</h1><p>Price: $120.00</p>"))
        if u.path == "/shopb":
            return self._send(page("Shop B - Outlet", "<h1>Shop B Outlet</h1><p>Click a product to open it.</p>"
                                   "<div class='job'><a href='/shopb/p/trail-runner-2' target='_blank'>Trail Runner 2 (opens in new tab)</a></div>"))
        if u.path == "/shopb/p/trail-runner-2":
            sizes = "".join(f"<div role='option' onclick=\"pickSize('{z}')\" style='padding:4px;cursor:pointer'>{z}</div>" for z in ["8", "9", "10", "11"])
            return self._send(page("Trail Runner 2 - Shop B", "<h1>Trail Runner 2</h1><p>Outlet price: <b>$79.50</b></p>"
                                   "<div role='button' id='sizeBtn' onclick='toggleSize()' style='border:1px solid #888;padding:6px;width:160px;cursor:pointer'>Select size</div>"
                                   f"<div id='sizeList' role='listbox' style='display:none;border:1px solid #ccc;width:160px'>{sizes}</div>"
                                   "<button onclick='addToCart()'>Add to cart</button><p id='msg'></p><p><a href='/shopb/cart'>View cart</a></p>" + SHOP_JS))
        if u.path == "/shopb/cart":
            rows = "".join(f"<li>{html.escape(c['item'])} - size {html.escape(str(c['size']))} - ${c['price']}</li>" for c in CART) or "<li>Your cart is empty.</li>"
            return self._send(page("Cart - Shop B", f"<h1>Your cart</h1><ul>{rows}</ul>"))
        if u.path == "/messages":
            thread = "".join(f"<p><b>You:</b> {html.escape(m['text'])}</p>" for m in MESSAGES)
            return self._send(page("Messages - MockJobs", "<h1>Messages</h1><h3>Dana (Recruiter, Acme Robotics)</h3>"
                                   f"<div id='thread'><p><b>Dana:</b> Hi! Thanks for looking at our Junior Python Developer role.</p>{thread}</div>"
                                   "<textarea id='msgBox' rows='3' placeholder='Write a message'></textarea><br>"
                                   "<button type='button' onclick='sendMsg()'>Send</button>" + SHOP_JS))
        if u.path.startswith("/apply/"):
            parts = u.path.strip("/").split("/")
            jid = parts[1]
            if not tok:
                return self._redirect(f"/login?next=/apply/{jid}")
            step = parts[2] if len(parts) > 2 else "1"
            t = JOBS[jid][0]
            d = DRAFTS.setdefault(tok, {}).setdefault(jid, {})
            if step == "1":
                return self._send(page(f"Apply: {t} (1 of 2)", who + f"<h1>Apply: {t}</h1><h3>Step 1 of 2 - Contact</h3>"
                                       f"<form method='post' action='/apply/{jid}/1'>"
                                       "<label>Full name</label><input name='name' required>"
                                       "<label>Email</label><input name='email' type='email' required>"
                                       "<label>Phone</label><input name='phone' type='tel'>"
                                       "<label>Years of experience</label><select name='years'><option value=''>Choose…</option>"
                                       "<option>0-1</option><option>1-3</option><option>3-5</option><option>5+</option></select>"
                                       "<br><button type='submit'>Continue</button></form>"))
            if step == "2":
                return self._send(page(f"Apply: {t} (2 of 2)", who + f"<h1>Apply: {t}</h1><h3>Step 2 of 2 - Details</h3>"
                                       f"<form method='post' action='/apply/{jid}/2' enctype='multipart/form-data'>"
                                       "<label>Resume (PDF)</label><input name='resume' type='file' required>"
                                       "<label>Are you authorized to work in the US?</label><select name='auth'><option value=''>Choose…</option><option>Yes</option><option>No</option></select>"
                                       "<label>Expected salary (USD)</label><input name='salary'>"
                                       "<label>Do you hold an active security clearance?</label><select name='clearance'><option value=''>Choose…</option><option>Yes</option><option>No</option></select>"
                                       "<br><button type='submit'>Review application</button></form>"))
            if step == "review":
                rows = "".join(f"<li>{k}: {html.escape(str(v))}</li>" for k, v in d.items())
                return self._send(page(f"Review: {t}", who + f"<h1>Review your application</h1><ul>{rows}</ul>"
                                       f"<form method='post' action='/apply/{jid}/submit'><button type='submit'>Submit application</button></form>"
                                       f"<p><a href='/apply/{jid}'>Edit</a></p>"))
            if step == "done":
                return self._send(page("Application sent", who + f"<h1>Application received!</h1><p>Reference #MJ-{1000+len(SUBMISSIONS)}</p>"))
        self._send(page("Not found", "<h1>404</h1>"), 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/shopb/cart", "/messages/send"):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            (CART if u.path == "/shopb/cart" else MESSAGES).append(body)
            return self._send(json.dumps({"ok": True}), ctype="application/json")
        q = urllib.parse.parse_qs(u.query)
        nxt = q.get("next", ["/"])[0]
        f = self._form()
        if u.path == "/login":
            if USERS.get(f.get("email")) == f.get("password"):
                tok = secrets.token_hex(8)
                SESSIONS[tok] = f["email"]
                return self._redirect(nxt, cookie=tok)
            return self._redirect(f"/login?next={urllib.parse.quote(nxt)}&err=1")
        if u.path == "/signup":
            em, p1, p2 = f.get("email", ""), f.get("password", ""), f.get("password2", "")
            err = ("Email required" if not em else "Passwords don't match" if p1 != p2 else
                   "Password must be 12+ characters" if len(p1) < 12 else "Please accept the terms" if not f.get("terms") else
                   "Account already exists" if em in USERS else "")
            if err:
                return self._redirect(f"/signup?next={urllib.parse.quote(nxt)}&err={urllib.parse.quote(err)}&email={urllib.parse.quote(em)}")
            USERS[em] = p1
            tok = secrets.token_hex(8)
            SESSIONS[tok] = em
            return self._redirect(nxt, cookie=tok)
        tok = self._session()
        if u.path.startswith("/apply/") and tok:
            parts = u.path.strip("/").split("/")
            jid, step = parts[1], parts[2]
            d = DRAFTS.setdefault(tok, {}).setdefault(jid, {})
            if step in ("1", "2"):
                d.update({k: v for k, v in f.items()})
                return self._redirect(f"/apply/{jid}/{'2' if step == '1' else 'review'}")
            if step == "submit":
                SUBMISSIONS.append({"job": JOBS[jid][0], "user": SESSIONS[tok], **d})
                return self._redirect(f"/apply/{jid}/done")
        self._send(page("Error", "<h1>Bad request</h1>"), 400)


if __name__ == "__main__":
    # Seed one account so the "saved login" path can be tested.
    if len(sys.argv) > 3:
        USERS[sys.argv[2]] = sys.argv[3]
    print(f"MockJobs on http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
